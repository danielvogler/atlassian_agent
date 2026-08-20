#!/usr/bin/env python
"""Print the registered MCP tools without calling Atlassian.

Useful as a smoke test — it proves every tool registers cleanly, and needs no
credentials to do it.
"""

from __future__ import annotations

import asyncio

from atlassian_agent.mcp_server import mcp


def main() -> None:
    for tool in asyncio.run(mcp.list_tools()):
        access = "read" if "read" in tool.tags else "write"
        print(f"{tool.name:<28} {access}")


if __name__ == "__main__":
    main()
