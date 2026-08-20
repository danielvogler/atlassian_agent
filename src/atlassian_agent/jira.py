"""Jira and Jira Structure read tools."""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import requests
from atlassian import Jira
from dotenv import load_dotenv

from atlassian_agent.common import tool_error
from atlassian_agent.runtime import apply_enabled


def jira_search(jql: str, limit: int = 10) -> dict:
    """Search Jira with JQL when Jira token configuration is available."""
    try:
        jira = _jira()
        result = jira.jql(jql, limit=limit)
        if result is None:
            raise RuntimeError("Jira returned an empty response for the JQL search")
        return result
    except RuntimeError as exc:
        return tool_error(exc)


def jira_get_issue(key: str) -> dict:
    """Fetch a Jira issue by key when Jira token configuration is available."""
    try:
        jira = _jira()
        return jira.issue(key)
    except RuntimeError as exc:
        return tool_error(exc)


def jira_create_issue(
    fields: dict,
    update: dict | None = None,
    *,
    apply: bool = False,
) -> dict:
    """Create a Jira issue, guarded by apply mode."""
    if not (apply or apply_enabled()):
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with apply=true or --apply to create.",
            "fields": fields,
            "update": update or {},
        }
    try:
        jira = _jira()
        result = jira.create_issue(fields=fields, update=update)
        return {"status": "success", "result": result}
    except RuntimeError as exc:
        return tool_error(exc)


def jira_update_issue_fields(
    issue_key: str,
    fields: dict,
    *,
    notify_users: bool = True,
    apply: bool = False,
) -> dict:
    """Update Jira issue fields, guarded by apply mode."""
    if not (apply or apply_enabled()):
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with apply=true or --apply to update.",
            "issue_key": issue_key,
            "fields": fields,
            "notify_users": notify_users,
        }
    try:
        jira = _jira()
        result = jira.issue_update(
            issue_key=issue_key,
            fields=fields,
            notify_users=notify_users,
        )
        return {"status": "success", "issue_key": issue_key, "result": result}
    except RuntimeError as exc:
        return tool_error(exc)


def jira_add_comment(
    issue_key: str,
    comment: str,
    visibility: dict | None = None,
    *,
    apply: bool = False,
) -> dict:
    """Add a Jira issue comment, guarded by apply mode."""
    if not (apply or apply_enabled()):
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with apply=true or --apply to comment.",
            "issue_key": issue_key,
            "comment": comment,
            "visibility": visibility,
        }
    try:
        jira = _jira()
        result = jira.issue_add_comment(issue_key, comment, visibility=visibility)
        return {"status": "success", "issue_key": issue_key, "result": result}
    except RuntimeError as exc:
        return tool_error(exc)


def jira_transition_issue(issue_key: str, status: str, *, apply: bool = False) -> dict:
    """Transition a Jira issue to a target status, guarded by apply mode."""
    if not (apply or apply_enabled()):
        return {
            "status": "dry_run",
            "message": "Dry-run only. Re-run with apply=true or --apply to transition.",
            "issue_key": issue_key,
            "target_status": status,
        }
    try:
        jira = _jira()
        result = jira.issue_transition(issue_key, status)
        return {"status": "success", "issue_key": issue_key, "result": result}
    except RuntimeError as exc:
        return tool_error(exc)


def jira_get_structure(structure_url_or_id: str) -> dict:
    """Fetch read-only Jira Structure metadata by URL or numeric structure ID."""
    try:
        structure_id = _resolve_structure_id(structure_url_or_id)
        base_url = _resolve_jira_base_url(structure_url_or_id)
        metadata = _jira_structure_request(
            "GET",
            f"/structure/{structure_id}",
            base_url=base_url,
            params={"withPermissions": "true", "withOwner": "true"},
        )
        return {
            "status": "success",
            "structure_id": structure_id,
            "metadata": metadata,
        }
    except (RuntimeError, ValueError) as exc:
        return tool_error(exc)


def jira_get_structure_forest(
    structure_url_or_id: str,
    limit: int = 200,
) -> dict:
    """Read Jira Structure forest rows without mutating the structure."""
    try:
        structure_id = _resolve_structure_id(structure_url_or_id)
        base_url = _resolve_jira_base_url(structure_url_or_id)
        safe_limit = max(1, min(limit, 1000))
        forest = _jira_structure_request(
            "POST",
            "/forest/latest",
            base_url=base_url,
            json={"structureId": int(structure_id)},
        )
        entries = _structure_formula_entries(forest)
        item_types = forest.get("itemTypes", {}) if isinstance(forest, dict) else {}
        rows = [
            _parse_structure_row(entry, item_types) for entry in entries[:safe_limit]
        ]
        return {
            "status": "success",
            "structure_id": structure_id,
            "version": forest.get("version") if isinstance(forest, dict) else None,
            "rows": rows,
            "count": len(rows),
            "truncated": len(entries) > safe_limit,
        }
    except (RuntimeError, ValueError) as exc:
        return tool_error(exc)


def jira_get_structure_values(
    structure_url_or_id: str,
    row_ids: list[int],
    attributes: list[str] | None = None,
) -> dict:
    """Read text-formatted Jira Structure row values for selected rows."""
    try:
        structure_id = _resolve_structure_id(structure_url_or_id)
        base_url = _resolve_jira_base_url(structure_url_or_id)
        safe_row_ids = [int(row_id) for row_id in row_ids[:500]]
        safe_attributes = attributes or ["key", "summary", "status", "issuetype"]
        payload = {
            "requests": [
                {
                    "forestSpec": {"structureId": int(structure_id)},
                    "rows": safe_row_ids,
                    "attributes": [
                        {"id": attribute, "format": "text"}
                        for attribute in safe_attributes[:50]
                    ],
                }
            ]
        }
        values = _jira_structure_request(
            "POST",
            "/value",
            base_url=base_url,
            json=payload,
        )
        return {
            "status": "success",
            "structure_id": structure_id,
            "row_ids": safe_row_ids,
            "attributes": safe_attributes[:50],
            "values": values,
        }
    except (RuntimeError, ValueError) as exc:
        return tool_error(exc)


def _jira() -> Jira:
    load_dotenv()
    url = _required_env("JIRA_URL")
    token = _required_env("JIRA_TOKEN")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    return Jira(url=url, session=session)


def _jira_structure_request(
    method: str,
    path: str,
    *,
    base_url: str | None = None,
    params: dict | None = None,
    json: dict | None = None,
) -> dict:
    load_dotenv()
    url = base_url or _required_env("JIRA_URL").rstrip("/")
    token = _required_env("JIRA_TOKEN")
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
    )
    if json is not None:
        session.headers["Content-Type"] = "application/json"

    response = session.request(
        method,
        f"{url}/rest/structure/2.0{path}",
        params=params,
        json=json,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(
            f"Jira Structure {method} {path} failed with HTTP {response.status_code}"
        ) from exc
    return response.json()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _resolve_structure_id(structure_url_or_id: str) -> str:
    value = structure_url_or_id.strip()
    if value.isdigit():
        return value

    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    structure_ids = query.get("s", [])
    if structure_ids and structure_ids[0].isdigit():
        return structure_ids[0]

    raise RuntimeError(f"Could not resolve Jira Structure ID from: {value}")


def _resolve_jira_base_url(structure_url_or_id: str) -> str | None:
    value = structure_url_or_id.strip()
    parsed = urlparse(value)
    if not parsed.scheme and not parsed.netloc:
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Jira Structure URLs must use https")
    return f"{parsed.scheme}://{parsed.netloc}"


def _structure_formula_entries(forest: dict) -> list[str]:
    formula = forest.get("formula", [])
    if isinstance(formula, list):
        return [str(entry) for entry in formula if str(entry)]
    if isinstance(formula, str):
        return [entry for entry in formula.split(",") if entry]
    return []


def _parse_structure_row(entry: str, item_types: dict) -> dict:
    parts = entry.split(":", 3)
    if len(parts) < 3:
        raise RuntimeError(f"Could not parse Jira Structure forest row: {entry}")

    row = {
        "row_id": int(parts[0]),
        "depth": int(parts[1]),
        "item_identity": parts[2],
    }
    if len(parts) == 4 and parts[3]:
        row["semantic"] = parts[3]

    prefix = parts[2].split("/", 1)[0]
    if prefix != "issue" and prefix in item_types:
        row["item_type"] = item_types[prefix]
    return row


JIRA_MCP_TOOLS = (
    jira_search,
    jira_get_issue,
    jira_create_issue,
    jira_update_issue_fields,
    jira_add_comment,
    jira_transition_issue,
    jira_get_structure,
    jira_get_structure_forest,
    jira_get_structure_values,
)
