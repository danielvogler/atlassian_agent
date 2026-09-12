"""Packaging contract tests.

The wheel is what somebody installs from PyPI, and nothing else in this suite
looks at it: every other test imports from the working tree, where a broken
entry point or a version that disagrees with the tag is invisible. These are
the cheap checks that would otherwise only fail after a release, and a version
on PyPI can be yanked but never replaced.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import entry_points, metadata
from pathlib import Path

import atlassian_agent

DISTRIBUTION = "atlassian-agent-mcp"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def test_the_declared_version_is_the_one_the_package_reports() -> None:
    # Arrange
    declared = _project()["version"]

    # Act
    reported = atlassian_agent.__version__

    # Assert
    assert reported == declared, (
        f"pyproject declares {declared} but the installed package reports "
        f"{reported}; reinstall with `uv sync` or the release tag check will "
        f"publish under a number the code disagrees with"
    )


def test_the_distribution_name_is_the_one_published_to_pypi() -> None:
    # `atlassian-agent` is taken on PyPI by an unrelated project. This test is
    # the reminder: renaming the distribution back would upload nowhere, and
    # the trusted-publishing configuration on PyPI names this string.
    assert _project()["name"] == DISTRIBUTION
    assert metadata(DISTRIBUTION)["Name"] == DISTRIBUTION


def test_both_console_scripts_resolve_to_something_callable() -> None:
    # Arrange
    scripts = {ep.name: ep for ep in entry_points(group="console_scripts")}

    # Act / Assert
    for name in ("atlassian-agent", "atlassian-agent-mcp"):
        assert name in scripts, f"{name} is not installed as a console script"
        assert callable(scripts[name].load()), f"{name} does not resolve"


def test_python_support_is_declared_without_an_upper_bound() -> None:
    # An upper cap on requires-python does not protect this package from a
    # future Python; it stops anyone on that Python from installing it at all,
    # and only a new release can lift it.
    requires = _project()["requires-python"]
    assert "<" not in requires, f"requires-python caps the interpreter: {requires}"
