"""Minimal MCP smoke client.

This is optional. Claude Code itself is the real MCP client for this project.
Use this only to verify the local server can list tools/resources.

Run:
    python scripts/mcp_smoke_client.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "claude_ops.mcp.server"],
        env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            resources = await session.list_resources()
            prompts = await session.list_prompts()

            print("TOOLS:")
            for tool in tools.tools:
                print(f"- {tool.name}")

            print("\nRESOURCES:")
            for resource in resources.resources:
                print(f"- {resource.uri}")

            print("\nPROMPTS:")
            for prompt in prompts.prompts:
                print(f"- {prompt.name}")


if __name__ == "__main__":
    asyncio.run(main())
