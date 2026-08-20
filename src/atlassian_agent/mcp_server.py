"""MCP server exposing guarded Atlassian tools for coding agents."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

from atlassian_agent.confluence import CONFLUENCE_MCP_TOOLS
from atlassian_agent.jira import JIRA_MCP_TOOLS

mcp = FastMCP(
    name="Atlassian Agent",
    instructions=(
        "Use these tools for guarded Jira and Confluence work. Confluence writes "
        "and Jira writes are dry-run by default. To publish, pass apply=true "
        "only after the operator has explicitly approved the exact target and "
        "change. Never print token values."
    ),
)

MCP_TOOLS = (*CONFLUENCE_MCP_TOOLS, *JIRA_MCP_TOOLS)


def _tool_name(fn: Callable[..., Any]) -> str:
    name = fn.__name__
    if name.startswith("get_confluence_"):
        return name.replace("get_confluence_", "confluence_get_", 1)
    if name.startswith("update_confluence_"):
        return name.replace("update_confluence_", "confluence_update_", 1)
    if name.startswith("append_confluence_"):
        return name.replace("append_confluence_", "confluence_append_", 1)
    return name


def _tool_tags(fn: Callable[..., Any]) -> set[str]:
    name = fn.__name__
    tags = {"confluence"} if "confluence" in name else {"jira"}
    tags.add("read" if _is_read_tool(fn) else "write")
    if "structure" in name:
        tags.add("structure")
    return tags


def _tool_annotations(fn: Callable[..., Any]) -> dict[str, Any]:
    read_only = _is_read_tool(fn)
    return {
        "readOnlyHint": read_only,
        "destructiveHint": not read_only and _is_destructive_tool(fn),
    }


def _is_read_tool(fn: Callable[..., Any]) -> bool:
    return fn.__name__.startswith(("get_", "jira_get_", "jira_search"))


def _is_destructive_tool(fn: Callable[..., Any]) -> bool:
    return fn.__name__.startswith(("update_", "jira_update_", "jira_transition_"))


def _description(fn: Callable[..., Any]) -> str:
    return inspect.getdoc(fn) or f"Run {_tool_name(fn)}."


def _safe_call(fn: Callable[..., dict[str, Any]], *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _mcp_adapter(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @wraps(fn)
    def wrapped(*args, **kwargs) -> dict[str, Any]:
        load_dotenv()
        return _safe_call(fn, *args, **kwargs)

    wrapped.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return wrapped


def _register_tools(tools: Iterable[Callable[..., dict[str, Any]]]) -> None:
    for fn in tools:
        mcp.tool(
            _mcp_adapter(fn),
            name=_tool_name(fn),
            description=_description(fn),
            tags=_tool_tags(fn),
            annotations=_tool_annotations(fn),
        )


_register_tools(MCP_TOOLS)


def main() -> None:
    """Run the MCP server over stdio."""
    load_dotenv()
    mcp.run()


if __name__ == "__main__":
    main()
