"""MCP-first Jira and Confluence tools for coding agents."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

_DISTRIBUTION = "atlassian-agent-mcp"

try:
    # The version is declared once, in pyproject.toml, and read back from the
    # installed metadata here. It used to be written out in both places, which
    # is a pair that drifts silently: the release workflow checks the tag
    # against pyproject, so a stale literal in this file would ship under a
    # version number that disagrees with the one it reports at runtime.
    __version__ = version(_DISTRIBUTION)
except PackageNotFoundError:  # pragma: no cover - a source tree with no install
    __version__ = "0+unknown"
