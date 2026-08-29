"""Contract tests against the real atlassian-python-api, with no network.

Every other test in this suite talks to a hand-written fake, which means it
validates our own idea of the library rather than the library. That gap is
exactly how a call broke in the field while `make check` stayed green: the
project's dependency range spanned two majors that disagreed about a parameter
name, and no test could see it.

These drive the genuine `Confluence` object and stub only the HTTP transport,
so the library's own signatures, URL building and parameter mapping are what
gets exercised. A rename or a moved method fails here, against whichever
version is actually installed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from atlassian.rest_client import AtlassianRestAPI

from atlassian_agent import confluence, runtime

SPACE = "GEG"
PAGE_ID = "514558999"


class FakeResponse:
    status_code = 200
    headers: ClassVar[dict[str, str]] = {"Content-Type": "application/json"}

    def __init__(self, body: dict) -> None:
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        return None


def _route(path: str, method: str) -> dict:
    """Answer like a Confluence would, keyed on the path the library built."""
    if "/space/" in path:
        return {"key": SPACE, "name": "Geothermal Energy and Geofluids"}
    if path.rstrip("/").endswith("rest/api/content") and method == "GET":
        return {"results": []}  # no page holds this title
    if method == "POST":
        return {
            "id": "900100",
            "_links": {"base": "https://wiki.example.test", "webui": "/x/new"},
        }
    if path.endswith("/history"):
        return {
            "createdBy": {"displayName": "Ada Lovelace"},
            "createdDate": "2026-01-04T09:00:00.000Z",
            "lastUpdated": {
                "by": {"displayName": "Grace Hopper"},
                "when": "2026-08-01T14:30:00.000Z",
                "number": 6,
                "message": "",
            },
        }
    if path.endswith("/label"):
        return {"results": [{"name": "runbook"}]}
    if "child/attachment" in path:
        return {"results": []}
    if "child/comment" in path or "/comment" in path:
        return {"results": []}
    if "content/search" in path:
        return {"results": [], "totalSize": 0, "_links": {"base": ""}}
    return {
        "id": PAGE_ID,
        "title": "PRO: Janna Blaume Miro",
        "space": {"key": SPACE},
        "version": {"number": 6},
        "body": {"storage": {"value": "<p>body</p>"}},
    }


@pytest.fixture
def wire(monkeypatch) -> Iterator[list[dict]]:
    """Record every HTTP call the real library would make."""
    calls: list[dict] = []

    def fake_request(
        self, method: str = "GET", path: str = "/", params: Any = None, **kwargs: Any
    ) -> FakeResponse:
        calls.append({"method": method, "path": str(path), "params": params or {}})
        return FakeResponse(_route(str(path), method))

    monkeypatch.setattr(AtlassianRestAPI, "request", fake_request)
    monkeypatch.setattr(confluence, "load_dotenv", lambda: None)
    monkeypatch.setenv("CONFLUENCE_URL", "https://wiki.example.test")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "token-value-that-must-not-appear")
    runtime.set_apply_mode(False)
    yield calls
    runtime.set_apply_mode(False)


def _params_for(calls: list[dict], needle: str) -> dict:
    for call in calls:
        if needle in call["path"] or needle in str(call["params"]):
            return call["params"]
    raise AssertionError(f"no call matching {needle!r} in {[c['path'] for c in calls]}")


def test_duplicate_title_probe_actually_sends_the_space_to_confluence(wire) -> None:
    """The regression that reached production.

    `get_page_by_title`'s first parameter is `space` up to v4 and `space_key`
    in v5, so either keyword raises TypeError against one of them. Calling it
    positionally works on both — and this asserts the space reaches the wire as
    `spaceKey`, so a silently dropped filter (which would search every space
    for the title) fails too.
    """
    result = confluence.create_confluence_page(
        title="Trial page", body="<p>x</p>", parent_url_or_id=PAGE_ID
    )

    assert result["status"] == "dry_run"
    assert result["space_key"] == SPACE
    assert _params_for(wire, "spaceKey")["spaceKey"] == SPACE
    assert _params_for(wire, "spaceKey")["title"] == "Trial page"


def test_create_page_posts_storage_representation_under_the_parent(wire) -> None:
    result = confluence.create_confluence_page(
        space_key=SPACE,
        title="Trial page",
        body="<p>x</p>",
        parent_url_or_id=PAGE_ID,
        apply=True,
    )

    assert result["status"] == "success"
    assert result["page_id"] == "900100"
    posts = [c for c in wire if c["method"] == "POST"]
    assert len(posts) == 1


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: confluence.get_confluence_page(PAGE_ID), id="get_page"),
        pytest.param(
            lambda: confluence.get_confluence_page(PAGE_ID, body_format="text"),
            id="get_page_text",
        ),
        pytest.param(
            lambda: confluence.get_confluence_page_history(PAGE_ID), id="history"
        ),
        pytest.param(
            lambda: confluence.get_confluence_comments(PAGE_ID), id="comments"
        ),
        pytest.param(lambda: confluence.get_confluence_labels(PAGE_ID), id="labels"),
        pytest.param(
            lambda: confluence.get_confluence_attachments(PAGE_ID), id="attachments"
        ),
        pytest.param(lambda: confluence.search_confluence("type = page"), id="search"),
        pytest.param(
            lambda: confluence.get_confluence_page_family(PAGE_ID, depth=0), id="family"
        ),
        pytest.param(
            lambda: confluence.add_confluence_comment(PAGE_ID, "hi", apply=True),
            id="add_comment",
        ),
        pytest.param(
            lambda: confluence.add_confluence_labels(PAGE_ID, ["new"], apply=True),
            id="add_labels",
        ),
        pytest.param(
            lambda: confluence.update_confluence_page(
                PAGE_ID, "PRO: Janna Blaume Miro", "<p>new</p>", 6, apply=True
            ),
            id="update_page",
        ),
    ],
)
def test_every_confluence_call_binds_against_the_installed_library(call, wire) -> None:
    """A moved method or renamed parameter raises TypeError here, not in the field."""
    result = call()

    assert result["status"] in {"success", "dry_run", "unchanged"}
    assert wire, "the tool made no HTTP call at all"
