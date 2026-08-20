"""Workspace-scoped local file tools."""

from __future__ import annotations

from pathlib import Path

from atlassian_agent.common import render_diff, tool_error
from atlassian_agent.runtime import apply_enabled

_WORKSPACE_ROOT = Path.cwd().resolve()
_SENSITIVE_FILE_NAMES = {".env", ".env.local", ".envrc"}


def read_source_file(path: str) -> dict:
    """Read source text from a local file."""
    return read_local_file(path)


def read_local_file(path: str) -> dict:
    """Read a UTF-8 file below the current workspace."""
    try:
        source_path = _resolve_workspace_path(path)
        text = source_path.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        return tool_error(exc)

    return {
        "status": "success",
        "path": _workspace_relative_path(source_path),
        "text": text,
        "characters": len(text),
    }


def write_local_file(path: str, content: str) -> dict:
    """Write a UTF-8 file below the workspace, guarded by apply mode."""
    try:
        destination = _resolve_workspace_path(path, must_exist=False)
        previous = (
            destination.read_text(encoding="utf-8") if destination.exists() else ""
        )
        diff = render_diff(previous, content)
        if not apply_enabled():
            return {
                "status": "dry_run",
                "message": "Dry-run only. Re-run with --apply to write local files.",
                "path": _workspace_relative_path(destination),
                "diff": diff,
            }

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        return tool_error(exc)

    return {
        "status": "success",
        "message": "File written.",
        "path": _workspace_relative_path(destination),
        "characters": len(content),
        "diff": diff,
    }


def _resolve_workspace_path(path: str, *, must_exist: bool = True) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        raise RuntimeError("Local file paths must be relative to the workspace")

    resolved = (_WORKSPACE_ROOT / candidate).resolve()
    if _WORKSPACE_ROOT not in {resolved, *resolved.parents}:
        raise RuntimeError("Local file path must stay inside the workspace")
    if any(
        part in _SENSITIVE_FILE_NAMES
        for part in resolved.relative_to(_WORKSPACE_ROOT).parts
    ):
        raise RuntimeError("Refusing to read or write sensitive local file")
    if must_exist and not resolved.is_file():
        raise RuntimeError(f"Local file does not exist: {path}")
    if resolved.exists() and not resolved.is_file():
        raise RuntimeError(f"Local path is not a file: {path}")
    return resolved


def _workspace_relative_path(path: Path) -> str:
    return str(path.relative_to(_WORKSPACE_ROOT))
