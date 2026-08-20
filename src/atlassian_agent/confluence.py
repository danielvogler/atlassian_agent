"""Confluence page tools with guarded storage-body writes."""

from __future__ import annotations

import os
import re
from html import escape, unescape
from urllib.parse import urlparse

import requests
from atlassian import Confluence
from dotenv import load_dotenv

from atlassian_agent.common import render_diff
from atlassian_agent.runtime import apply_enabled


def get_confluence_page(page_url_or_id: str) -> dict:
    """Fetch a Confluence page by URL or numeric page ID."""
    confluence = _confluence()
    page_id = _resolve_page_id(page_url_or_id)
    page = confluence.get_page_by_id(page_id, expand="body.storage,version")
    return {
        "status": "success",
        "id": str(page["id"]),
        "title": page["title"],
        "version": int(page["version"]["number"]),
        "body": page["body"]["storage"]["value"],
    }


def get_confluence_child_pages(page_url_or_id: str, limit: int = 50) -> dict:
    """List direct child pages below a Confluence page."""
    confluence = _confluence()
    page_id = _resolve_page_id(page_url_or_id)
    parent = get_confluence_page(page_id)
    children = confluence.get_page_child_by_type(
        page_id,
        type="page",
        limit=limit,
        expand="version",
    )
    return {
        "status": "success",
        "parent_id": parent["id"],
        "parent_title": parent["title"],
        "children": [_page_summary(child) for child in children],
    }


def get_confluence_page_family(
    page_url_or_id: str,
    depth: int = 2,
    child_limit: int = 25,
    include_body_preview: bool = True,
) -> dict:
    """Read a page plus descendants so the agent can choose where to edit."""
    confluence = _confluence()
    root_id = _resolve_page_id(page_url_or_id)
    safe_depth = max(0, min(depth, 4))
    safe_limit = max(1, min(child_limit, 50))

    def walk(page_id: str, remaining_depth: int) -> dict:
        page = get_confluence_page(page_id)
        node = {
            "id": page["id"],
            "title": page["title"],
            "version": page["version"],
            "children": [],
        }
        if include_body_preview:
            node["body_preview"] = _plain_text_preview(page["body"])

        if remaining_depth <= 0:
            return node

        children = confluence.get_page_child_by_type(
            page_id,
            type="page",
            limit=safe_limit,
            expand="version",
        )
        node["children"] = [
            walk(str(child["id"]), remaining_depth - 1) for child in children
        ]
        return node

    return {
        "status": "success",
        "root": walk(root_id, safe_depth),
        "depth": safe_depth,
        "child_limit": safe_limit,
    }


def update_confluence_page(
    page_id: str,
    title: str,
    body: str,
    expected_version: int,
    *,
    apply: bool = False,
) -> dict:
    """Update a Confluence page, guarded by apply mode and version check."""
    latest = get_confluence_page(page_id)
    if latest["version"] != expected_version:
        return {
            "status": "error",
            "message": "Page changed since it was read. Refusing to update.",
            "expected_version": expected_version,
            "current_version": latest["version"],
        }

    diff = render_diff(latest["body"], body)
    if not (apply or apply_enabled()):
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with --apply to publish.",
            "page_id": page_id,
            "title": title,
            "diff": diff,
        }

    confluence = _confluence()
    confluence.update_page(
        page_id=page_id,
        title=title,
        body=body,
        representation="storage",
        minor_edit=True,
    )
    return {
        "status": "success",
        "message": "Page updated.",
        "page_id": page_id,
        "title": title,
        "diff": diff,
    }


def append_confluence_sentence(
    page_url_or_id: str,
    sentence: str,
    *,
    apply: bool = False,
) -> dict:
    """Append one paragraph to a Confluence page with useful diagnostics."""
    page = get_confluence_page(page_url_or_id)
    paragraph = f"<p>{escape(sentence, quote=False)}</p>"

    if sentence in page["body"]:
        return {
            "status": "unchanged",
            "message": "Sentence is already present.",
            "page_id": page["id"],
            "title": page["title"],
            "version": page["version"],
            "diff": "",
        }

    proposed_body = f"{page['body'].rstrip()}\n\n{paragraph}"
    diff = render_diff(page["body"], proposed_body)

    if not apply:
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with --apply to publish.",
            "page_id": page["id"],
            "title": page["title"],
            "version": page["version"],
            "diff": diff,
        }

    result = update_confluence_page(
        page["id"],
        page["title"],
        proposed_body,
        page["version"],
        apply=apply,
    )
    result["version"] = page["version"]
    return result


def _confluence() -> Confluence:
    load_dotenv()
    url = _required_env("CONFLUENCE_URL")
    token = _required_env("CONFLUENCE_TOKEN")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    return Confluence(url=url, session=session)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _resolve_page_id(page_url_or_id: str) -> str:
    value = page_url_or_id.strip()
    if value.isdigit():
        return value

    page_path = re.search(r"/pages/(\d+)", value)
    if page_path:
        return page_path.group(1)

    page_query = re.search(r"[?&]pageId=(\d+)", value)
    if page_query:
        return page_query.group(1)

    if re.search(r"/x/[^/?#]+", value):
        return _resolve_tiny_link(value)

    raise RuntimeError(f"Could not resolve Confluence page ID from: {value}")


def _resolve_tiny_link(url: str) -> str:
    load_dotenv()
    confluence_url = _required_env("CONFLUENCE_URL").rstrip("/")
    token = _required_env("CONFLUENCE_TOKEN")
    _validate_same_origin(url, confluence_url, "Confluence tiny link")
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        allow_redirects=True,
        timeout=30,
    )
    response.raise_for_status()
    page_id = _find_page_id(response.url) or _find_page_id(response.text)
    if page_id is None:
        raise RuntimeError("Could not resolve Confluence tiny link to page ID")
    return page_id


def _validate_same_origin(url: str, base_url: str, label: str) -> None:
    parsed_url = urlparse(url)
    parsed_base = urlparse(base_url)
    if (
        parsed_url.scheme != parsed_base.scheme
        or parsed_url.netloc != parsed_base.netloc
    ):
        raise RuntimeError(f"{label} host must match configured service URL")


def _find_page_id(text: str) -> str | None:
    for pattern in (
        r"/pages/(\d+)",
        r"[?&]pageId=(\d+)",
        r"pageId[\"'=:\s]+(\d+)",
        r"contentId[\"'=:\s]+(\d+)",
        r"ajs-page-id[\"'=:\s]+(\d+)",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _page_summary(page: dict) -> dict:
    return {
        "id": str(page["id"]),
        "title": page["title"],
        "version": int(page.get("version", {}).get("number", 0)),
    }


def _plain_text_preview(storage_body: str, max_characters: int = 1200) -> str:
    text = re.sub(r"<[^>]+>", " ", storage_body)
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) <= max_characters:
        return text
    return f"{text[:max_characters].rstrip()}..."


CONFLUENCE_MCP_TOOLS = (
    get_confluence_page,
    get_confluence_page_family,
    update_confluence_page,
    append_confluence_sentence,
)
