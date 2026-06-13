"""End-to-end check of our MCP connector.

An MCP client spawns our server (which bridges to Swiggy via mcp-remote) and calls
its tools — exactly what Claude Desktop does. Validates the server's lifespan session
across multiple tool calls. Mutates the cart transiently (find_deals). Self-terminates
after 90s so a hang can't run forever.

    uv run python scripts/check_server.py
"""

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

WORK = "cug4elpnnp0lq52vuk10"

SERVER = StdioServerParameters(
    command="uv",
    args=[
        "run",
        "--directory",
        "/Users/gowthamram/PycharmProjects/Swiggy",
        "python",
        "-m",
        "swiggy_deal_finder.server",
    ],
)


def _text(result: object) -> str:
    for block in getattr(result, "content", []):
        if hasattr(block, "text"):
            return block.text
    return str(getattr(result, "structuredContent", ""))


async def _run() -> None:
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(">> initialized", flush=True)
            tools = await session.list_tools()
            print(">> tools:", [t.name for t in tools.tools], flush=True)

            loc = await session.call_tool("get_locations", {})
            print("\n[get_locations]\n" + _text(loc)[:300], flush=True)

            deals = await session.call_tool(
                "find_deals", {"dish": "biryani", "address_id": WORK, "top_n": 2}
            )
            print("\n[find_deals]\n" + _text(deals), flush=True)


async def main() -> None:
    try:
        await asyncio.wait_for(_run(), timeout=90)
    except TimeoutError:
        print(">> TIMEOUT after 90s — server still hung", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
