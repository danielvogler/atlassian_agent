"""Shared helpers for local Jira/Confluence tools."""

from __future__ import annotations

import difflib


def render_diff(original: str, proposed: str) -> str:
    """Render a unified diff between two text bodies."""
    return "\n".join(
        difflib.unified_diff(
            original.splitlines(),
            proposed.splitlines(),
            fromfile="current",
            tofile="proposed",
            lineterm="",
        )
    )


def tool_error(exc: Exception) -> dict:
    """Return a tool-shaped error without leaking implementation details."""
    return {"status": "error", "message": str(exc)}
