from __future__ import annotations

import asyncio
import os
from typing import ClassVar

import pytest
from dotenv import dotenv_values

from atlassian_agent import confluence, jira, local_files, mcp_server, runtime, tools

# Only the base-URL keys are read out of .env — never the tokens, which have no
# business being in a test process at all.
_DOTENV_URLS = {
    key: value
    for key, value in dotenv_values().items()
    if key.endswith("_URL") and value
}


def _base_url(name: str, fallback: str) -> str:
    """Resolve a configured base URL from the environment or .env.

    Hostnames are not written into this repository. An internal Atlassian host
    hardcoded in a fixture is a leak that outlives every later cleanup, and it
    is never what the assertion is actually about. CI has no .env and runs
    against the example fallbacks.
    """
    value = (os.getenv(name) or _DOTENV_URLS.get(name) or "").strip().rstrip("/")
    return value or fallback


JIRA_BASE_URL = _base_url("JIRA_URL", "https://jira.example.test")
CONFLUENCE_BASE_URL = _base_url("CONFLUENCE_URL", "https://wiki.example.test")

# Deliberately not the configured host: this is the one the guard must refuse.
UNTRUSTED_BASE_URL = "https://untrusted.example.test"


class FakeResponse:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.body


class FakeSession:
    # Shared on the class on purpose: the tools build their own session, so the
    # recorded calls have to be reachable without holding the instance.
    calls: ClassVar[list[dict]] = []
    response = FakeResponse({})

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def request(self, method, url, *, params=None, json=None, timeout=None):
        headers = dict(self.headers)
        if "Authorization" in headers:
            headers["Authorization"] = "<redacted>"
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "timeout": timeout,
                "headers": headers,
            }
        )
        return self.response


@pytest.fixture(autouse=True)
def clean_fake_session(monkeypatch) -> None:
    FakeSession.calls = []
    FakeSession.response = FakeResponse({})
    monkeypatch.setattr(jira, "load_dotenv", lambda: None)
    monkeypatch.setattr(confluence, "load_dotenv", lambda: None)


@pytest.fixture
def fake_structure_http(monkeypatch):
    monkeypatch.setenv("JIRA_URL", JIRA_BASE_URL)
    monkeypatch.setenv("JIRA_TOKEN", "token-value-that-must-not-appear")
    monkeypatch.setattr(jira.requests, "Session", FakeSession)
    return FakeSession


def test_resolve_structure_id_accepts_numeric_id_and_structure_board_url() -> None:
    assert jira._resolve_structure_id("1413") == "1413"
    assert (
        jira._resolve_structure_id(
            f"{JIRA_BASE_URL}/secure/StructureBoard.jspa?s=1413#"
        )
        == "1413"
    )


def test_missing_jira_env_errors_name_variables_without_token_value(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_TOKEN", raising=False)

    missing_url = tools.jira_get_structure("1413")

    assert missing_url["status"] == "error"
    assert "JIRA_URL" in missing_url["message"]
    assert "token-value-that-must-not-appear" not in missing_url["message"]

    missing_token = tools.jira_get_structure(
        f"{JIRA_BASE_URL}/secure/StructureBoard.jspa?s=1413#"
    )

    assert missing_token["status"] == "error"
    assert "JIRA_TOKEN" in missing_token["message"]
    assert "token-value-that-must-not-appear" not in missing_token["message"]


def test_read_local_file_reads_workspace_relative_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(local_files, "_WORKSPACE_ROOT", tmp_path.resolve())
    (tmp_path / "services.md").write_text("# Services\n", encoding="utf-8")

    result = tools.read_local_file("services.md")

    assert result == {
        "status": "success",
        "path": "services.md",
        "text": "# Services\n",
        "characters": 11,
    }


def test_write_local_file_dry_run_does_not_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(local_files, "_WORKSPACE_ROOT", tmp_path.resolve())
    runtime.set_apply_mode(False)

    result = tools.write_local_file("services.md", "# Services\n")

    assert result["status"] == "dry_run"
    assert result["path"] == "services.md"
    assert result["diff"]
    assert not (tmp_path / "services.md").exists()


def test_write_local_file_apply_writes_workspace_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(local_files, "_WORKSPACE_ROOT", tmp_path.resolve())
    runtime.set_apply_mode(True)

    result = tools.write_local_file("services.md", "# Services\n")

    assert result["status"] == "success"
    assert result["path"] == "services.md"
    assert (tmp_path / "services.md").read_text(encoding="utf-8") == "# Services\n"
    runtime.set_apply_mode(False)


def test_local_file_tools_reject_outside_and_sensitive_paths(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(local_files, "_WORKSPACE_ROOT", tmp_path.resolve())

    outside = tools.read_local_file("../outside.md")
    sensitive_read = tools.read_local_file(".env")
    sensitive_write = tools.write_local_file(".env", "SECRET=value\n")

    assert outside["status"] == "error"
    assert "inside the workspace" in outside["message"]
    assert sensitive_read["status"] == "error"
    assert sensitive_write["status"] == "error"
    assert "sensitive" in sensitive_read["message"]
    assert "sensitive" in sensitive_write["message"]
    assert "SECRET=value" not in sensitive_write["message"]


def test_jira_write_tools_are_dry_run_by_default() -> None:
    runtime.set_apply_mode(False)

    create = tools.jira_create_issue({"project": {"key": "ABC"}})
    update = tools.jira_update_issue_fields("ABC-123", {"summary": "New summary"})
    comment = tools.jira_add_comment("ABC-123", "Ready for review.")
    transition = tools.jira_transition_issue("ABC-123", "Done")

    assert create["status"] == "dry_run"
    assert update["status"] == "dry_run"
    assert comment["status"] == "dry_run"
    assert transition["status"] == "dry_run"


def test_mcp_server_registers_domain_tools_without_duplicate_wrappers() -> None:
    tools_by_name = {
        tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())
    }
    expected_names = [mcp_server._tool_name(fn) for fn in mcp_server.MCP_TOOLS]

    assert sorted(tools_by_name) == sorted(expected_names)
    assert "apply" in tools_by_name["jira_create_issue"].parameters["properties"]
    assert "apply" in tools_by_name["confluence_update_page"].parameters["properties"]
    assert not [
        name
        for name in dir(mcp_server)
        if name.startswith(("mcp_jira_", "mcp_confluence_"))
    ]


def test_confluence_tiny_link_rejects_untrusted_host_before_token_send(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CONFLUENCE_URL", CONFLUENCE_BASE_URL)
    monkeypatch.setenv("CONFLUENCE_TOKEN", "token-value-that-must-not-appear")

    def fail_get(*args, **kwargs):
        raise AssertionError("requests.get must not be called")

    monkeypatch.setattr(confluence.requests, "get", fail_get)

    with pytest.raises(RuntimeError) as error:
        confluence._resolve_tiny_link(f"{UNTRUSTED_BASE_URL}/x/abc123")

    assert "host must match" in str(error.value)
    assert "token-value-that-must-not-appear" not in str(error.value)


def test_jira_get_structure_fetches_metadata_with_permissions(
    fake_structure_http,
) -> None:
    fake_structure_http.response = FakeResponse({"id": 1413, "name": "Delivery"})

    result = tools.jira_get_structure("1413")

    assert result == {
        "status": "success",
        "structure_id": "1413",
        "metadata": {"id": 1413, "name": "Delivery"},
    }
    assert fake_structure_http.calls == [
        {
            "method": "GET",
            "url": f"{JIRA_BASE_URL}/rest/structure/2.0/structure/1413",
            "params": {"withPermissions": "true", "withOwner": "true"},
            "json": None,
            "timeout": 30,
            "headers": {
                "Authorization": "<redacted>",
                "Accept": "application/json",
            },
        }
    ]


def test_jira_get_structure_derives_base_url_from_structure_url(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.setenv("JIRA_TOKEN", "token-value-that-must-not-appear")
    monkeypatch.setattr(jira.requests, "Session", FakeSession)
    FakeSession.response = FakeResponse({"id": 1413, "name": "Delivery"})

    result = tools.jira_get_structure(
        f"{JIRA_BASE_URL}/secure/StructureBoard.jspa?s=1413#"
    )

    assert result["status"] == "success"
    assert FakeSession.calls[0]["url"] == (
        f"{JIRA_BASE_URL}/rest/structure/2.0/structure/1413"
    )
    assert FakeSession.calls[0]["headers"]["Authorization"] == "<redacted>"


def test_jira_get_structure_forest_posts_and_parses_rows(fake_structure_http) -> None:
    fake_structure_http.response = FakeResponse(
        {
            "version": 7,
            "formula": "11:0:4/root:manual,12:1:10001",
            "itemTypes": {"4": "Folder"},
        }
    )

    result = tools.jira_get_structure_forest("1413", limit=1)

    assert result["version"] == 7
    assert result["truncated"] is True
    assert result["count"] == 1
    assert result["rows"] == [
        {
            "row_id": 11,
            "depth": 0,
            "item_identity": "4/root",
            "semantic": "manual",
            "item_type": "Folder",
        }
    ]
    assert fake_structure_http.calls[0]["method"] == "POST"
    assert (
        fake_structure_http.calls[0]["url"]
        == f"{JIRA_BASE_URL}/rest/structure/2.0/forest/latest"
    )
    assert fake_structure_http.calls[0]["json"] == {"structureId": 1413}


def test_jira_get_structure_values_wraps_request_and_text_attributes(
    fake_structure_http,
) -> None:
    fake_structure_http.response = FakeResponse({"responses": [{"values": []}]})

    result = tools.jira_get_structure_values("1413", [11, 12], ["key", "summary"])

    assert result["values"] == {"responses": [{"values": []}]}
    assert fake_structure_http.calls[0]["method"] == "POST"
    assert (
        fake_structure_http.calls[0]["url"]
        == f"{JIRA_BASE_URL}/rest/structure/2.0/value"
    )
    assert fake_structure_http.calls[0]["json"] == {
        "requests": [
            {
                "forestSpec": {"structureId": 1413},
                "rows": [11, 12],
                "attributes": [
                    {"id": "key", "format": "text"},
                    {"id": "summary", "format": "text"},
                ],
            }
        ]
    }
