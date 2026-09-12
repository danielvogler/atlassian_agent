#!/usr/bin/env bash
# Verify a built wheel by installing it somewhere clean and using it.
#
# Every test in this repository imports from the working tree, where a module
# left out of the wheel, or an entry point that does not resolve, is invisible.
# This is the one check that looks at what somebody actually installs — and it
# is the last cheap moment to look, because a version on PyPI can be yanked but
# never replaced.
#
# Used by `make dist-check` before tagging and by release.yml after building,
# so the release pipeline and the local rehearsal cannot drift apart.
#
# Usage: scripts/verify-wheel.sh path/to/wheel.whl
set -euo pipefail

wheel="${1:-}"
if [ -z "$wheel" ] || [ ! -f "$wheel" ]; then
  echo "usage: scripts/verify-wheel.sh path/to/wheel.whl" >&2
  exit 2
fi

venv="$(mktemp -d)/verify"
trap 'rm -rf "$(dirname "$venv")"' EXIT

uv venv "$venv" --quiet
VIRTUAL_ENV="$venv" uv pip install --quiet "$wheel"

# The diagnostic CLI resolves as a console script.
"$venv/bin/atlassian-agent" --help >/dev/null

# The MCP server is a stdio process and would block, so register its tools
# in-process instead. No credentials are needed, which is the same property
# `make mcp-tools` relies on.
"$venv/bin/python" - <<'PY'
import asyncio

from atlassian_agent import __version__
from atlassian_agent.mcp_server import main, mcp

assert callable(main), "the atlassian-agent-mcp entry point does not resolve"

tools = asyncio.run(mcp.list_tools())
assert tools, "the installed wheel registers no MCP tools"

# A write tool an agent's client believes is read-only is the single worst bug
# this repository can ship, and it is a packaging concern too: the annotations
# are derived from function names at import time.
by_name = {tool.name: tool for tool in tools}
assert by_name["confluence_get_page"].annotations.read_only_hint is True
assert by_name["confluence_update_page"].annotations.read_only_hint is False

print(f"installed wheel {__version__} registers {len(tools)} tools")
PY
