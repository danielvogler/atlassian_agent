"""Confluence page tools with guarded storage-body writes."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from html import escape, unescape
from urllib.parse import urlparse

import requests
from atlassian import Confluence
from dotenv import load_dotenv

from atlassian_agent.common import render_diff
from atlassian_agent.runtime import apply_enabled

_BODY_FORMATS = ("storage", "text")


def search_confluence(cql: str, limit: int = 25) -> dict:
    """Find Confluence pages with CQL, so a page can be located by name.

    ``cql`` is a Confluence Query Language string. Useful shapes::

        title ~ "deployment runbook"
        space = DOCS and title ~ "runbook"
        space = DOCS and text ~ "rollback" and type = page
        label = "postmortem" and lastmodified > now("-30d")

    Returns compact rows; feed an ``id`` straight into ``confluence_get_page``.
    """
    safe_limit = max(1, min(limit, 100))
    response = _confluence().cql(cql, limit=safe_limit, excerpt="highlight")
    payload = response if isinstance(response, dict) else {}
    rows = payload.get("results", [])
    base = str(payload.get("_links", {}).get("base", "")).rstrip("/")
    return {
        "status": "success",
        "cql": cql,
        "count": len(rows),
        "total": payload.get("totalSize", len(rows)),
        "truncated": int(payload.get("totalSize", 0) or 0) > len(rows),
        "results": [_search_result(row, base) for row in rows],
    }


def get_confluence_page(page_url_or_id: str, body_format: str = "storage") -> dict:
    """Fetch a Confluence page by URL or numeric page ID.

    ``body_format`` is ``storage`` (the raw XHTML you must pass back to
    ``confluence_update_page``) or ``text`` (macros and markup stripped).
    Read as ``text`` when the goal is to understand the page: storage bodies are
    full of ``<ac:structured-macro>`` that a model will happily mangle on the
    way back out. Read as ``storage`` only when about to write.
    """
    if body_format not in _BODY_FORMATS:
        raise RuntimeError(
            f"Unknown body_format {body_format!r}; expected one of {_BODY_FORMATS}"
        )
    confluence = _confluence()
    page_id = _resolve_page_id(page_url_or_id)
    page = confluence.get_page_by_id(page_id, expand="body.storage,version")
    storage = page["body"]["storage"]["value"]
    return {
        "status": "success",
        "id": str(page["id"]),
        "title": page["title"],
        "version": int(page["version"]["number"]),
        "body_format": body_format,
        "body": storage if body_format == "storage" else _plain_text(storage),
    }


def get_confluence_page_history(page_url_or_id: str) -> dict:
    """Read who created a page and who last changed it, and when.

    This is the question ``confluence_update_page`` raises when it refuses a
    stale version: the page moved underneath you, and this says who moved it.
    """
    page_id = _resolve_page_id(page_url_or_id)
    history = _confluence().get_content_history(page_id)
    history = history if isinstance(history, dict) else {}
    last = history.get("lastUpdated", {})
    return {
        "status": "success",
        "page_id": page_id,
        "created": {
            "by": _person_name(history.get("createdBy", {})),
            "when": str(history.get("createdDate", "")),
        },
        "last_updated": {
            "by": _person_name(last.get("by", {})),
            "when": str(last.get("when", "")),
            "version": int(last.get("number", 0) or 0),
            "message": str(last.get("message", "")),
        },
    }


def get_confluence_comments(page_url_or_id: str, limit: int = 25) -> dict:
    """Read the comments on a page, where the review feedback usually lives."""
    page_id = _resolve_page_id(page_url_or_id)
    safe_limit = max(1, min(limit, 100))
    response = _confluence().get_page_comments(
        page_id,
        expand="body.view,version",
        depth="all",
        limit=safe_limit,
    )
    rows = response.get("results", []) if isinstance(response, dict) else []
    return {
        "status": "success",
        "page_id": page_id,
        "count": len(rows),
        "comments": [_comment_summary(row) for row in rows],
    }


def get_confluence_labels(page_url_or_id: str) -> dict:
    """Read the labels on a page — what most `label = ...` CQL searches rely on."""
    page_id = _resolve_page_id(page_url_or_id)
    response = _confluence().get_page_labels(page_id)
    rows = response.get("results", []) if isinstance(response, dict) else []
    return {
        "status": "success",
        "page_id": page_id,
        "labels": [str(row.get("name", "")) for row in rows],
    }


def get_confluence_attachments(page_url_or_id: str, limit: int = 50) -> dict:
    """List the files attached to a page.

    Metadata only. Downloading the bytes is deliberately absent — see AGENTS.md
    §A6 for why.
    """
    page_id = _resolve_page_id(page_url_or_id)
    safe_limit = max(1, min(limit, 100))
    response = _confluence().get_attachments_from_content(page_id, limit=safe_limit)
    rows = response.get("results", []) if isinstance(response, dict) else []
    return {
        "status": "success",
        "page_id": page_id,
        "count": len(rows),
        "attachments": [_attachment_summary(row) for row in rows],
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


def create_confluence_page(
    space_key: str | None = None,
    title: str = "",
    body: str = "",
    parent_url_or_id: str | None = None,
    *,
    apply: bool = False,
) -> dict:
    """Create a Confluence page, guarded by apply mode.

    The body is Confluence storage format, the same representation
    ``confluence_get_page`` returns. Pass ``space_key``, ``parent_url_or_id``,
    or both; the space is derived from the parent when it is omitted.
    """
    if not title.strip():
        return {"status": "error", "message": "A page title is required."}

    parent_id = _resolve_page_id(parent_url_or_id) if parent_url_or_id else None
    parent_title = get_confluence_page(parent_id)["title"] if parent_id else None

    space = (space_key or "").strip() or (
        _page_space_key(parent_id) if parent_id else ""
    )
    if not space:
        return {
            "status": "error",
            "message": "A space_key or a parent_url_or_id is required.",
        }
    _require_space(space)

    existing = _page_in_space(space, title)
    if existing is not None:
        return {
            "status": "error",
            "message": (
                f"A page titled {title!r} already exists in space {space}. "
                "Confluence titles are unique per space; update it instead."
            ),
            "space_key": space,
            "title": title,
            "existing_page_id": existing,
        }

    diff = render_diff("", body)
    target = {
        "space_key": space,
        "title": title,
        "parent_id": parent_id,
        "parent_title": parent_title,
    }

    if not (apply or apply_enabled()):
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with apply=true to create the page.",
            **target,
            "diff": diff,
        }

    created = _confluence().create_page(
        space=space,
        title=title,
        body=body,
        parent_id=parent_id,
        representation="storage",
    )
    return {
        "status": "success",
        "message": "Page created.",
        "page_id": str(created["id"]),
        **target,
        "url": _page_url(created),
        "diff": diff,
    }


def add_confluence_comment(
    page_url_or_id: str,
    comment: str,
    *,
    apply: bool = False,
) -> dict:
    """Add a comment to a Confluence page, guarded by apply mode.

    Commenting is additive and reversible where a body edit is neither, so this
    is often the right tool where an agent reaches for ``confluence_update_page``.
    """
    if not comment.strip():
        return {"status": "error", "message": "A comment body is required."}

    page = get_confluence_page(page_url_or_id)
    diff = render_diff("", comment)
    if not (apply or apply_enabled()):
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with apply=true to comment.",
            "page_id": page["id"],
            "title": page["title"],
            "diff": diff,
        }

    result = _confluence().add_comment(page["id"], comment)
    return {
        "status": "success",
        "message": "Comment added.",
        "page_id": page["id"],
        "title": page["title"],
        "comment_id": str(result.get("id", "")) if isinstance(result, dict) else "",
        "diff": diff,
    }


def add_confluence_labels(
    page_url_or_id: str,
    labels: list[str],
    *,
    apply: bool = False,
) -> dict:
    """Add labels to a Confluence page, guarded by apply mode.

    Existing labels are left alone and never removed; labels already on the page
    are reported as ``unchanged`` rather than re-sent.
    """
    wanted = [label.strip() for label in labels if label.strip()]
    if not wanted:
        return {"status": "error", "message": "At least one label is required."}

    page_id = _resolve_page_id(page_url_or_id)
    current = get_confluence_labels(page_id)["labels"]
    missing = [label for label in wanted if label not in current]

    if not missing:
        return {
            "status": "unchanged",
            "message": "Every label is already on the page.",
            "page_id": page_id,
            "labels": current,
            "added": [],
        }

    if not (apply or apply_enabled()):
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with apply=true to label the page.",
            "page_id": page_id,
            "labels": current,
            "would_add": missing,
        }

    confluence = _confluence()
    for label in missing:
        confluence.set_page_label(page_id, label)
    return {
        "status": "success",
        "message": "Labels added.",
        "page_id": page_id,
        "labels": sorted({*current, *missing}),
        "added": missing,
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

    if not (apply or apply_enabled()):
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


def _require_space(space_key: str) -> None:
    """Fail at the read when the target space does not exist."""
    message = f"Could not resolve Confluence space: {space_key}"
    try:
        space = _confluence().get_space(space_key)
    except requests.RequestException as exc:
        raise RuntimeError(message) from exc
    if not space:
        raise RuntimeError(message)


def _page_space_key(page_id: str) -> str:
    page = _confluence().get_page_by_id(page_id, expand="space")
    return str(page.get("space", {}).get("key", ""))


@contextmanager
def _quiet(logger_name: str) -> Iterator[None]:
    """Silence a third-party logger for the duration of one call."""
    logger = logging.getLogger(logger_name)
    previous = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(previous)


def _page_in_space(space_key: str, title: str) -> str | None:
    """Return the ID of a page with this title in the space, if one exists.

    A free title is the outcome we want here, but the library logs it at ERROR
    as "Can't find '<title>' page on <host>". That line lands in the transcript
    immediately before a perfectly good dry run and reads like a failure — it
    has already sent one agent off diagnosing a bug that was not there. The
    absent title is reported through the return value instead.
    """
    with _quiet("atlassian.confluence"):
        # Positional on purpose. atlassian-python-api renamed this parameter
        # `space` -> `space_key` in v5, so either keyword breaks on one side of
        # that line while the position is the same on both.
        response = _confluence().get_page_by_title(space_key, title)
    return _first_page_id(response)


def _first_page_id(response: dict | None) -> str | None:
    """Read a page ID out of whatever `get_page_by_title` returned.

    Up to v4 it hands back the first result, or None when the title is free.
    In v5 it hands back the raw `{"results": [...]}` envelope, which is truthy
    even when empty — so the obvious `if response` test reports a duplicate
    that is not there. Both shapes are normalised here.
    """
    if not response:
        return None
    if "results" in response:
        results = response.get("results") or []
        return str(results[0]["id"]) if results else None
    return str(response["id"]) if "id" in response else None


def _page_url(page: dict) -> str:
    links = page.get("_links", {})
    base = str(links.get("base", "")).rstrip("/")
    webui = str(links.get("webui", ""))
    return f"{base}{webui}" if base and webui else ""


def _person_name(person: dict) -> str:
    return str(person.get("displayName", "") or person.get("username", ""))


def _comment_summary(row: dict) -> dict:
    body = row.get("body", {}).get("view", {}).get("value", "")
    version = row.get("version", {})
    return {
        "id": str(row.get("id", "")),
        "by": _person_name(version.get("by", {})),
        "when": str(version.get("when", "")),
        "text": _plain_text(str(body)),
    }


def _attachment_summary(row: dict) -> dict:
    return {
        "id": str(row.get("id", "")),
        "filename": str(row.get("title", "")),
        "media_type": str(row.get("metadata", {}).get("mediaType", "")),
        "bytes": int(row.get("extensions", {}).get("fileSize", 0) or 0),
    }


def _search_result(row: dict, base_url: str) -> dict:
    content = row.get("content", {}) if isinstance(row.get("content"), dict) else {}
    webui = str(row.get("url", "") or content.get("_links", {}).get("webui", ""))
    return {
        "id": str(content.get("id", "")),
        "title": str(content.get("title", "") or row.get("title", "")),
        "type": str(content.get("type", "")),
        "space_key": str(content.get("space", {}).get("key", "")),
        "url": f"{base_url}{webui}" if base_url and webui else webui,
        "excerpt": _plain_text_preview(str(row.get("excerpt", "")), 300),
    }


def _plain_text(storage_body: str) -> str:
    """Strip markup and macros from a storage or view body.

    CDATA is unwrapped before tags are stripped. A code macro holds its content
    in `<![CDATA[...]]>`, which has no `>` until the very end, so a plain tag
    strip eats the code with the markup — and on a runbook the code is the part
    that mattered.
    """
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", storage_body, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _plain_text_preview(storage_body: str, max_characters: int = 1200) -> str:
    text = _plain_text(storage_body)
    if len(text) <= max_characters:
        return text
    return f"{text[:max_characters].rstrip()}..."


CONFLUENCE_MCP_TOOLS = (
    search_confluence,
    get_confluence_page,
    get_confluence_page_family,
    get_confluence_page_history,
    get_confluence_comments,
    get_confluence_labels,
    get_confluence_attachments,
    add_confluence_comment,
    add_confluence_labels,
    create_confluence_page,
    update_confluence_page,
    append_confluence_sentence,
)
