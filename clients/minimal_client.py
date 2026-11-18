"""A minimal MCP client in Python (slide 58), against our tracker server.

Run:  .venv/bin/python clients/minimal_client.py
No LLM involved: this is the raw client library. Inside an agent, the LLM
chooses which call_tool to make. That is the whole trick.
"""

import os
import sys
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

LAB = Path(__file__).resolve().parent.parent
# LOG_LEVEL=WARNING keeps the server's own stderr log lines out of this demo's output.
server = StdioServerParameters(
    command=sys.executable,
    args=[str(LAB / "tracker" / "server.py")],
    env={**os.environ, "LOG_LEVEL": "WARNING"},
)


async def main() -> None:
    async with Client(server) as client:
        print("Connected to:", client.server_info)
        tools = await client.list_tools()
        print("\n# tools/list")
        for tool in tools.tools:
            ro = tool.annotations.read_only_hint if tool.annotations else None
            print(f"  {tool.name:<17} read-only={ro!s:<5} - {(tool.description or '').splitlines()[0]}")

        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()
        print("\n# resources/list:", [str(r.uri) for r in resources.resources], [t.uri_template for t in templates.resource_templates])
        print("# prompts/list:  ", [p.name for p in prompts.prompts])

        print("\n# tools/call client_status")
        result = await client.call_tool("client_status", {})
        for c in result.structured_content["clients"]:
            print(f"  {c['client']:<10} {c['status']:<6} {c['reasons'][0]}")

        print("\n# tools/call add_action with priority 'urgent' (deliberately invalid)")
        print("  Expected: the server rejects it with a clear message, not a crash.")
        bad = await client.call_tool("add_action", {"client": "Northwind", "title": "x", "owner": "Priya", "due": "2026-11-20", "priority": "urgent"})
        message = next((ln.strip() for ln in bad.content[0].text.splitlines() if "Input should be" in ln), bad.content[0].text)
        message = message.split(" [type=")[0]
        print(f"  isError = {bad.is_error}  ->  {message}")
        print("  OK: bad input rejected, nothing saved." if bad.is_error else "  UNEXPECTED: the server accepted bad input.")


anyio.run(main)
