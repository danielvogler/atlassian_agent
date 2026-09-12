from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import re
from typing import ClassVar

import pytest
from dotenv import dotenv_values

import atlassian_agent
from atlassian_agent import (
    confluence,
    jira,
    local_files,
    mcp_server,
    runtime,
    tools,
)

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
    # These two need no read to describe themselves, so they dry-run without
    # credentials. jira_create_issue and jira_transition_issue resolve their
    # target first and are covered separately, against a faked client.
    runtime.set_apply_mode(False)

    update = tools.jira_update_issue_fields("ABC-123", {"summary": "New summary"})
    comment = tools.jira_add_comment("ABC-123", "Ready for review.")

    assert update["status"] == "dry_run"
    assert comment["status"] == "dry_run"


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


class FakeConfluence:
    """Minimal stand-in for the Confluence client used by the create-page tests."""

    def __init__(
        self,
        space: dict | None = None,
        existing_titles: dict | None = None,
        pages: dict | None = None,
    ) -> None:
        self.space = space if space is not None else {"key": "DOCS"}
        self.existing_titles = existing_titles or {}
        self.pages = pages or {}
        self.created: list[dict] = []
        self.cql_calls: list[dict] = []
        self.cql_response: dict = {"results": [], "totalSize": 0}

        self.comments: dict = {"results": []}
        self.labels: dict = {"results": []}
        self.attachments: dict = {"results": []}
        self.content_history: dict = {}
        self.added_comments: list[tuple[str, str]] = []
        self.added_labels: list[tuple[str, str]] = []

    def cql(self, cql: str, limit: int | None = None, **kwargs) -> dict:
        self.cql_calls.append({"cql": cql, "limit": limit})
        return self.cql_response

    def get_content_history(self, content_id: str) -> dict:
        return self.content_history

    def get_page_comments(self, content_id: str, **kwargs) -> dict:
        return self.comments

    def get_page_labels(self, page_id: str, **kwargs) -> dict:
        return self.labels

    def get_attachments_from_content(self, page_id: str, **kwargs) -> dict:
        return self.attachments

    def add_comment(self, page_id: str, text: str) -> dict:
        self.added_comments.append((str(page_id), text))
        return {"id": 4242}

    def set_page_label(self, page_id: str, label: str) -> dict:
        self.added_labels.append((str(page_id), label))
        return {"ok": True}

    def get_space(self, space_key: str, **kwargs) -> dict | None:
        return self.space if self.space and self.space["key"] == space_key else None

    def get_page_by_title(self, space: str, title: str, **kwargs) -> dict | None:
        page_id = self.existing_titles.get((space, title))
        if not page_id:
            # What the real library does, and the reason _page_in_space is quiet.
            logging.getLogger("atlassian.confluence").error(
                "Can't find '%s' page on %s", title, CONFLUENCE_BASE_URL
            )
            return None
        return {"id": page_id}

    def get_page_by_id(self, page_id: str, expand: str | None = None) -> dict:
        return self.pages[str(page_id)]

    def create_page(self, **kwargs) -> dict:
        self.created.append(kwargs)
        return {
            "id": 900100,
            "_links": {"base": f"{CONFLUENCE_BASE_URL}/display", "webui": "/DOCS/New"},
        }


PARENT_PAGE = {
    "id": 12345,
    "title": "Runbooks",
    "version": {"number": 7},
    "space": {"key": "DOCS"},
    "body": {"storage": {"value": "<p>Parent</p>"}},
}


@pytest.fixture
def fake_confluence(monkeypatch):
    client = FakeConfluence(pages={"12345": PARENT_PAGE})
    monkeypatch.setattr(confluence, "_confluence", lambda: client)
    runtime.set_apply_mode(False)
    yield client
    runtime.set_apply_mode(False)


def test_create_confluence_page_dry_run_creates_nothing(fake_confluence) -> None:
    result = tools.create_confluence_page(
        space_key="DOCS",
        title="Incident review",
        body="<p>Draft</p>",
    )

    assert result["status"] == "dry_run"
    assert result["space_key"] == "DOCS"
    assert result["title"] == "Incident review"
    assert result["parent_id"] is None
    assert "+<p>Draft</p>" in result["diff"]
    assert fake_confluence.created == []


def test_create_confluence_page_derives_space_from_parent(fake_confluence) -> None:
    result = tools.create_confluence_page(
        title="Incident review",
        body="<p>Draft</p>",
        parent_url_or_id=f"{CONFLUENCE_BASE_URL}/pages/12345",
    )

    assert result["status"] == "dry_run"
    assert result["space_key"] == "DOCS"
    assert result["parent_id"] == "12345"
    assert result["parent_title"] == "Runbooks"
    assert fake_confluence.created == []


def test_create_confluence_page_refuses_duplicate_title(fake_confluence) -> None:
    fake_confluence.existing_titles = {("DOCS", "Incident review"): 555}

    result = tools.create_confluence_page(
        space_key="DOCS",
        title="Incident review",
        body="<p>Draft</p>",
        apply=True,
    )

    assert result["status"] == "error"
    assert result["existing_page_id"] == "555"
    assert "already exists" in result["message"]
    assert fake_confluence.created == []


def test_create_confluence_page_refuses_unknown_space(fake_confluence) -> None:
    # An unresolvable target fails at the read, like every other Confluence
    # tool here; the MCP layer is what shapes it into an error status.
    with pytest.raises(RuntimeError) as error:
        tools.create_confluence_page(
            space_key="NOPE",
            title="Incident review",
            body="<p>Draft</p>",
            apply=True,
        )

    assert "NOPE" in str(error.value)
    assert fake_confluence.created == []


def test_create_confluence_page_requires_a_space_or_parent(fake_confluence) -> None:
    result = tools.create_confluence_page(title="Incident review", body="<p>x</p>")

    assert result["status"] == "error"
    assert "space_key" in result["message"]
    assert fake_confluence.created == []


def test_create_confluence_page_requires_a_title(fake_confluence) -> None:
    result = tools.create_confluence_page(space_key="DOCS", title="  ", body="<p>x</p>")

    assert result["status"] == "error"
    assert "title is required" in result["message"]
    assert fake_confluence.created == []


def test_create_confluence_page_apply_creates_page_under_parent(
    fake_confluence,
) -> None:
    result = tools.create_confluence_page(
        title="Incident review",
        body="<p>Draft</p>",
        parent_url_or_id="12345",
        apply=True,
    )

    assert result["status"] == "success"
    assert result["page_id"] == "900100"
    assert result["url"] == f"{CONFLUENCE_BASE_URL}/display/DOCS/New"
    assert fake_confluence.created == [
        {
            "space": "DOCS",
            "title": "Incident review",
            "body": "<p>Draft</p>",
            "parent_id": "12345",
            "representation": "storage",
        }
    ]


def test_create_confluence_page_publishes_under_cli_apply_mode(fake_confluence) -> None:
    runtime.set_apply_mode(True)

    result = tools.create_confluence_page(
        space_key="DOCS",
        title="Incident review",
        body="<p>Draft</p>",
    )

    assert result["status"] == "success"
    assert len(fake_confluence.created) == 1


def test_create_confluence_page_is_registered_as_a_non_destructive_write() -> None:
    tools_by_name = {
        tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())
    }
    create_page = tools_by_name["confluence_create_page"]
    annotations = create_page.annotations

    assert "apply" in create_page.parameters["properties"]
    assert annotations is not None
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is False


class FakeJira:
    """Minimal stand-in for the Jira client used by the guarded-write tests."""

    def __init__(
        self,
        transitions: list[dict] | None = None,
        createmeta: dict | None = None,
    ) -> None:
        self.transitions = transitions if transitions is not None else []
        self.createmeta = createmeta if createmeta is not None else {}
        self.created: list[dict] = []
        self.transitioned: list[tuple[str, str]] = []

    def get_issue_transitions(self, issue_key: str) -> list[dict]:
        return self.transitions

    def issue_createmeta(self, project: str, expand: str | None = None) -> dict:
        return self.createmeta

    def create_issue(self, fields: dict, update: dict | None = None) -> dict:
        self.created.append({"fields": fields, "update": update})
        return {"key": "ABC-456"}

    def issue_transition(self, issue_key: str, status: str) -> dict:
        self.transitioned.append((issue_key, status))
        return {"ok": True}


BUG_CREATEMETA = {
    "projects": [
        {
            "key": "ABC",
            "name": "Alphabet",
            "issuetypes": [
                {
                    "name": "Bug",
                    "fields": {
                        "summary": {"required": True, "hasDefaultValue": False},
                        "project": {"required": True, "hasDefaultValue": False},
                        "issuetype": {"required": True, "hasDefaultValue": False},
                        "priority": {"required": True, "hasDefaultValue": True},
                        "description": {"required": False},
                    },
                }
            ],
        }
    ]
}

BUG_FIELDS = {
    "project": {"key": "ABC"},
    "issuetype": {"name": "Bug"},
    "summary": "Login times out",
}


@pytest.fixture
def fake_jira(monkeypatch):
    client = FakeJira(
        transitions=[
            {"id": 21, "name": "Start work", "to": "In Progress"},
            {"id": 31, "name": "Resolve", "to": "Done"},
        ],
        createmeta=BUG_CREATEMETA,
    )
    monkeypatch.setattr(jira, "_jira", lambda: client)
    runtime.set_apply_mode(False)
    yield client
    runtime.set_apply_mode(False)


def test_jira_get_transitions_reports_target_status_and_transition_name(
    fake_jira,
) -> None:
    result = tools.jira_get_transitions("ABC-123")

    assert result["status"] == "success"
    assert result["issue_key"] == "ABC-123"
    assert [row["to"] for row in result["transitions"]] == ["In Progress", "Done"]
    assert [row["name"] for row in result["transitions"]] == ["Start work", "Resolve"]


def test_jira_transition_dry_run_lists_what_the_workflow_offers(fake_jira) -> None:
    result = tools.jira_transition_issue("ABC-123", "Done")

    assert result["status"] == "dry_run"
    assert result["target_status"] == "Done"
    assert len(result["available_transitions"]) == 2
    assert fake_jira.transitioned == []


def test_jira_transition_matches_target_status_case_insensitively(fake_jira) -> None:
    result = tools.jira_transition_issue("ABC-123", "  in progress ")

    assert result["status"] == "dry_run"
    assert fake_jira.transitioned == []


def test_jira_transition_refuses_a_status_the_workflow_does_not_offer(
    fake_jira,
) -> None:
    # "Closed" is a plausible guess that this workflow simply does not have;
    # catching it here is the whole point of reading before the dry run.
    result = tools.jira_transition_issue("ABC-123", "Closed", apply=True)

    assert result["status"] == "error"
    assert "no transition to 'Closed'" in result["message"]
    assert len(result["available_transitions"]) == 2
    assert fake_jira.transitioned == []


def test_jira_transition_refuses_transition_name_instead_of_target_status(
    fake_jira,
) -> None:
    result = tools.jira_transition_issue("ABC-123", "Resolve", apply=True)

    assert result["status"] == "error"
    assert fake_jira.transitioned == []


def test_jira_transition_apply_transitions_the_issue(fake_jira) -> None:
    result = tools.jira_transition_issue("ABC-123", "Done", apply=True)

    assert result["status"] == "success"
    assert fake_jira.transitioned == [("ABC-123", "Done")]


def test_jira_create_issue_dry_run_names_the_resolved_target(fake_jira) -> None:
    result = tools.jira_create_issue(BUG_FIELDS)

    assert result["status"] == "dry_run"
    assert result["project_key"] == "ABC"
    assert result["project_name"] == "Alphabet"
    assert result["issue_type"] == "Bug"
    assert result["fields"] == BUG_FIELDS
    assert fake_jira.created == []


def test_jira_create_issue_requires_project_and_issue_type_in_fields(
    fake_jira,
) -> None:
    result = tools.jira_create_issue({"summary": "No target"})

    assert result["status"] == "error"
    assert "project.key" in result["message"]
    assert fake_jira.created == []


def test_jira_create_issue_refuses_an_issue_type_the_project_lacks(fake_jira) -> None:
    result = tools.jira_create_issue(
        {**BUG_FIELDS, "issuetype": {"name": "Epic"}},
        apply=True,
    )

    assert result["status"] == "error"
    assert "no issue type 'Epic'" in result["message"]
    assert "Bug" in result["message"]
    assert fake_jira.created == []


def test_jira_create_issue_refuses_when_a_required_field_is_missing(fake_jira) -> None:
    # priority is required but has a default, so only summary should be named.
    result = tools.jira_create_issue(
        {"project": {"key": "ABC"}, "issuetype": {"name": "Bug"}},
        apply=True,
    )

    assert result["status"] == "error"
    assert "summary" in result["message"]
    assert "priority" not in result["message"]
    assert fake_jira.created == []


def test_jira_create_issue_refuses_an_invisible_project(fake_jira) -> None:
    fake_jira.createmeta = {"projects": []}

    result = tools.jira_create_issue(BUG_FIELDS, apply=True)

    assert result["status"] == "error"
    assert "ABC" in result["message"]
    assert fake_jira.created == []


def test_jira_create_issue_apply_creates_the_issue(fake_jira) -> None:
    result = tools.jira_create_issue(BUG_FIELDS, apply=True)

    assert result["status"] == "success"
    assert result["project_key"] == "ABC"
    assert result["result"] == {"key": "ABC-456"}
    assert fake_jira.created == [{"fields": BUG_FIELDS, "update": None}]


def test_search_confluence_returns_compact_rows_with_ids(fake_confluence) -> None:
    fake_confluence.cql_response = {
        "totalSize": 9,
        "_links": {"base": f"{CONFLUENCE_BASE_URL}/display"},
        "results": [
            {
                "url": "/DOCS/Runbook",
                "excerpt": "rollback <b>steps</b>",
                "content": {
                    "id": 7788,
                    "title": "Deployment runbook",
                    "type": "page",
                    "space": {"key": "DOCS"},
                },
            }
        ],
    }

    result = tools.search_confluence('space = DOCS and title ~ "runbook"', limit=5)

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["total"] == 9
    assert result["truncated"] is True
    assert result["results"] == [
        {
            "id": "7788",
            "title": "Deployment runbook",
            "type": "page",
            "space_key": "DOCS",
            "url": f"{CONFLUENCE_BASE_URL}/display/DOCS/Runbook",
            "excerpt": "rollback steps",
        }
    ]
    assert fake_confluence.cql_calls == [
        {"cql": 'space = DOCS and title ~ "runbook"', "limit": 5}
    ]


def test_search_confluence_clamps_the_limit(fake_confluence) -> None:
    tools.search_confluence("type = page", limit=5000)
    tools.search_confluence("type = page", limit=0)

    assert [call["limit"] for call in fake_confluence.cql_calls] == [100, 1]


def test_append_sentence_honours_the_cli_apply_flag(monkeypatch) -> None:
    # Every other write checks apply_enabled(); this one used to check only its
    # own argument, so --apply silently did nothing.
    published: list[dict] = []
    page = {
        "status": "success",
        "id": "12345",
        "title": "Runbooks",
        "version": 7,
        "body": "<p>Parent</p>",
    }
    monkeypatch.setattr(confluence, "get_confluence_page", lambda _: page)

    def fake_update(*args, **kwargs) -> dict:
        published.append({"args": args, "kwargs": kwargs})
        return {"status": "success"}

    monkeypatch.setattr(confluence, "update_confluence_page", fake_update)
    runtime.set_apply_mode(True)

    result = confluence.append_confluence_sentence("12345", "Rollback is documented.")

    runtime.set_apply_mode(False)
    assert result["status"] == "success"
    assert len(published) == 1


# AGENTS.md §B4 asks for the README tool tables and §A4 to be kept in step with
# the *_MCP_TOOLS tuples. Until now nothing enforced that, and the counts had
# already drifted: the docs claimed nine read and four write against a registry
# holding seven and six. Prose mentions are ignored on purpose — §B1 names
# `confluence_get_labels` as a naming *example*, and it is not a real tool.
_DOC_PATHS = (
    pathlib.Path(__file__).resolve().parent.parent / "README.md",
    pathlib.Path(__file__).resolve().parent.parent / "AGENTS.md",
)

_NUMBER_WORDS = {
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-one": 21,
    "twenty-two": 22,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
}


def _documented_tool_names(text: str) -> set[str]:
    """Tool names from markdown table rows only, not from prose."""
    return set(re.findall(r"^\|\s*`((?:confluence|jira)_[a-z_]+)`\s*\|", text, re.M))


def _spelled_numbers(text: str, suffix: str) -> list[int]:
    """Every spelled-out count in front of `suffix`, so a stale one anywhere fails."""
    # Longest first, so "twenty-two" is not shadowed by "twenty".
    words = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    return [
        _NUMBER_WORDS[word.lower()]
        for word in re.findall(rf"\b({words})\s+{suffix}", text, re.I)
    ]


@pytest.fixture(scope="module")
def registry() -> dict:
    read = [fn for fn in mcp_server.MCP_TOOLS if mcp_server._is_read_tool(fn)]
    write = [fn for fn in mcp_server.MCP_TOOLS if not mcp_server._is_read_tool(fn)]
    return {
        "names": {mcp_server._tool_name(fn) for fn in mcp_server.MCP_TOOLS},
        "total": len(mcp_server.MCP_TOOLS),
        "read": len(read),
        "write": len(write),
    }


@pytest.mark.parametrize("doc_path", _DOC_PATHS, ids=lambda p: p.name)
def test_docs_list_exactly_the_registered_tools(doc_path, registry) -> None:
    documented = _documented_tool_names(doc_path.read_text(encoding="utf-8"))

    assert documented - registry["names"] == set(), "documented tool does not exist"
    assert registry["names"] - documented == set(), "registered tool is undocumented"


@pytest.mark.parametrize("doc_path", _DOC_PATHS, ids=lambda p: p.name)
def test_docs_state_the_registry_tool_counts(doc_path, registry) -> None:
    text = doc_path.read_text(encoding="utf-8")

    for suffix, expected in (
        (r"tools\b", registry["total"]),
        (r"read\.", registry["read"]),
        (r"write[.,]", registry["write"]),
    ):
        found = _spelled_numbers(text, suffix)
        assert found, f"no spelled-out count before {suffix!r}"
        assert set(found) == {expected}, f"{suffix!r} says {found}, registry {expected}"


def test_jira_transition_read_failure_explains_itself_without_leaking_token(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.setenv("JIRA_TOKEN", "token-value-that-must-not-appear")

    result = tools.jira_transition_issue("ABC-123", "Done")

    assert result["status"] == "error"
    assert "JIRA_URL" in result["message"]
    assert "needs Jira credentials" in result["message"]
    assert "token-value-that-must-not-appear" not in result["message"]


def test_jira_create_read_failure_explains_itself_without_leaking_token(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.setenv("JIRA_TOKEN", "token-value-that-must-not-appear")

    result = tools.jira_create_issue(BUG_FIELDS)

    assert result["status"] == "error"
    assert "JIRA_URL" in result["message"]
    assert "needs Jira credentials" in result["message"]
    assert "token-value-that-must-not-appear" not in result["message"]


PAGE_WITH_MACRO = {
    "id": 12345,
    "title": "Runbooks",
    "version": {"number": 7},
    "space": {"key": "DOCS"},
    "body": {
        "storage": {
            "value": (
                '<p>Roll back with <ac:structured-macro ac:name="code">'
                "<ac:plain-text-body><![CDATA[make undo]]></ac:plain-text-body>"
                "</ac:structured-macro> first.</p>"
            )
        }
    },
}


def test_get_page_returns_storage_by_default_for_round_tripping(
    fake_confluence,
) -> None:
    fake_confluence.pages["12345"] = PAGE_WITH_MACRO

    result = tools.get_confluence_page("12345")

    assert result["body_format"] == "storage"
    assert "ac:structured-macro" in result["body"]


def test_get_page_as_text_strips_the_macros_a_model_would_mangle(
    fake_confluence,
) -> None:
    fake_confluence.pages["12345"] = PAGE_WITH_MACRO

    result = tools.get_confluence_page("12345", body_format="text")

    assert result["body_format"] == "text"
    assert "ac:structured-macro" not in result["body"]
    assert result["body"] == "Roll back with make undo first."


def test_get_page_rejects_an_unknown_body_format(fake_confluence) -> None:
    with pytest.raises(RuntimeError) as error:
        tools.get_confluence_page("12345", body_format="markdown")

    assert "markdown" in str(error.value)


def test_update_page_still_reads_storage_when_diffing(fake_confluence) -> None:
    # The format argument must not change what the guarded write compares.
    fake_confluence.pages["12345"] = PAGE_WITH_MACRO

    result = tools.update_confluence_page("12345", "Runbooks", "<p>New</p>", 7)

    assert result["status"] == "dry_run"
    assert "ac:structured-macro" in result["diff"]


def test_get_page_history_names_who_last_changed_the_page(fake_confluence) -> None:
    fake_confluence.content_history = {
        "createdBy": {"displayName": "Ada Lovelace"},
        "createdDate": "2026-01-04T09:00:00.000Z",
        "lastUpdated": {
            "by": {"displayName": "Grace Hopper"},
            "when": "2026-08-01T14:30:00.000Z",
            "number": 7,
            "message": "clarified rollback",
        },
    }

    result = tools.get_confluence_page_history("12345")

    assert result["created"]["by"] == "Ada Lovelace"
    assert result["last_updated"] == {
        "by": "Grace Hopper",
        "when": "2026-08-01T14:30:00.000Z",
        "version": 7,
        "message": "clarified rollback",
    }


def test_get_page_history_survives_a_sparse_response(fake_confluence) -> None:
    result = tools.get_confluence_page_history("12345")

    assert result["status"] == "success"
    assert result["last_updated"]["version"] == 0


def test_get_comments_returns_text_not_markup(fake_confluence) -> None:
    fake_confluence.comments = {
        "results": [
            {
                "id": 991,
                "body": {"view": {"value": "<p>Needs a <b>rollback</b> step.</p>"}},
                "version": {
                    "by": {"displayName": "Grace Hopper"},
                    "when": "2026-08-02T10:00:00.000Z",
                },
            }
        ]
    }

    result = tools.get_confluence_comments("12345")

    assert result["count"] == 1
    assert result["comments"][0] == {
        "id": "991",
        "by": "Grace Hopper",
        "when": "2026-08-02T10:00:00.000Z",
        "text": "Needs a rollback step.",
    }


def test_add_comment_dry_run_posts_nothing(fake_confluence) -> None:
    fake_confluence.pages["12345"] = PARENT_PAGE

    result = tools.add_confluence_comment("12345", "Rollback step is missing.")

    assert result["status"] == "dry_run"
    assert result["title"] == "Runbooks"
    assert "+Rollback step is missing." in result["diff"]
    assert fake_confluence.added_comments == []


def test_add_comment_apply_posts_the_comment(fake_confluence) -> None:
    fake_confluence.pages["12345"] = PARENT_PAGE

    result = tools.add_confluence_comment(
        "12345", "Rollback step is missing.", apply=True
    )

    assert result["status"] == "success"
    assert result["comment_id"] == "4242"
    assert fake_confluence.added_comments == [("12345", "Rollback step is missing.")]


def test_add_comment_refuses_an_empty_body(fake_confluence) -> None:
    result = tools.add_confluence_comment("12345", "   ", apply=True)

    assert result["status"] == "error"
    assert fake_confluence.added_comments == []


def test_get_labels_returns_names(fake_confluence) -> None:
    fake_confluence.labels = {"results": [{"name": "runbook"}, {"name": "postmortem"}]}

    assert tools.get_confluence_labels("12345")["labels"] == ["runbook", "postmortem"]


def test_add_labels_dry_run_reports_only_the_new_ones(fake_confluence) -> None:
    fake_confluence.labels = {"results": [{"name": "runbook"}]}

    result = tools.add_confluence_labels("12345", ["runbook", "postmortem"])

    assert result["status"] == "dry_run"
    assert result["would_add"] == ["postmortem"]
    assert fake_confluence.added_labels == []


def test_add_labels_is_unchanged_when_every_label_is_present(fake_confluence) -> None:
    fake_confluence.labels = {"results": [{"name": "runbook"}]}

    result = tools.add_confluence_labels("12345", ["runbook"], apply=True)

    assert result["status"] == "unchanged"
    assert fake_confluence.added_labels == []


def test_add_labels_apply_adds_only_the_missing_labels(fake_confluence) -> None:
    fake_confluence.labels = {"results": [{"name": "runbook"}]}

    result = tools.add_confluence_labels("12345", ["runbook", "postmortem"], apply=True)

    assert result["status"] == "success"
    assert result["added"] == ["postmortem"]
    assert result["labels"] == ["postmortem", "runbook"]
    assert fake_confluence.added_labels == [("12345", "postmortem")]


def test_add_labels_refuses_an_empty_list(fake_confluence) -> None:
    result = tools.add_confluence_labels("12345", ["", "  "], apply=True)

    assert result["status"] == "error"
    assert fake_confluence.added_labels == []


def test_get_attachments_lists_metadata_only(fake_confluence) -> None:
    fake_confluence.attachments = {
        "results": [
            {
                "id": "att77",
                "title": "topology.png",
                "metadata": {"mediaType": "image/png"},
                "extensions": {"fileSize": 20481},
            }
        ]
    }

    result = tools.get_confluence_attachments("12345")

    assert result["attachments"] == [
        {
            "id": "att77",
            "filename": "topology.png",
            "media_type": "image/png",
            "bytes": 20481,
        }
    ]


def test_additive_confluence_writes_are_not_marked_destructive() -> None:
    by_name = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}

    for name in ("confluence_add_comment", "confluence_add_labels"):
        annotations = by_name[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is False


def test_no_delete_or_move_tool_is_registered() -> None:
    # AGENTS.md §A6 says why these are absent; this keeps the claim true.
    names = [mcp_server._tool_name(fn) for fn in mcp_server.MCP_TOOLS]

    assert not [n for n in names if "delete" in n or "remove" in n or "move" in n]


def test_duplicate_title_probe_does_not_log_a_false_error(
    fake_confluence, caplog
) -> None:
    # The library logs a free title at ERROR. That line landed in a transcript
    # right before a good dry run and sent an agent hunting a bug that was not
    # there, so the probe is silenced and the result carries the answer.
    caplog.set_level(logging.ERROR)

    result = tools.create_confluence_page(
        space_key="DOCS",
        title="Nothing uses this title",
        body="<p>Draft</p>",
    )

    assert result["status"] == "dry_run"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_quiet_restores_the_logger_level_afterwards() -> None:
    logger = logging.getLogger("atlassian.confluence")
    logger.setLevel(logging.DEBUG)

    with confluence._quiet("atlassian.confluence"):
        assert logger.level == logging.CRITICAL

    assert logger.level == logging.DEBUG
    logger.setLevel(logging.NOTSET)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        pytest.param(None, None, id="v4-title-is-free"),
        pytest.param({"id": 555}, "555", id="v4-title-taken"),
        pytest.param({"results": []}, None, id="v5-title-is-free"),
        pytest.param({"results": [{"id": 555}]}, "555", id="v5-title-taken"),
    ],
)
def test_first_page_id_reads_both_library_response_shapes(response, expected) -> None:
    # Up to v4 the library returns the first result or None; v5 returns the raw
    # {"results": [...]} envelope, which is truthy even when empty. Treating the
    # v5 empty envelope as a hit would report a duplicate that is not there.
    assert confluence._first_page_id(response) == expected


def test_package_version_matches_pyproject() -> None:
    pyproject = (
        pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    ).read_text(encoding="utf-8")

    assert f'version = "{atlassian_agent.__version__}"' in pyproject
