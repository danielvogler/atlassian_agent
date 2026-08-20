"""Runtime flags shared by CLI tools."""

from __future__ import annotations

_APPLY_CHANGES = False


def set_apply_mode(enabled: bool) -> None:
    """Allow write tools to publish changes when explicitly enabled by the CLI."""
    global _APPLY_CHANGES
    _APPLY_CHANGES = enabled


def apply_enabled() -> bool:
    """Return whether mutating tools may publish changes."""
    return _APPLY_CHANGES
