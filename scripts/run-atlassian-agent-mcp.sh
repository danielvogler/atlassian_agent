#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$repo_root/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  echo "Missing $python_bin. Run 'make setup' in $repo_root first." >&2
  exit 1
fi

export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$repo_root"
exec "$python_bin" -m atlassian_agent.mcp_server
