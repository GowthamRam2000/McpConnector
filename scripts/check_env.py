"""Post-env-fill connectivity check. Reports OK/FAIL per dependency; exits non-zero on failure."""
import asyncio
import sys

from swiggy_deal_finder.config import Settings


def _line(ok: bool, label: str, detail: str = "") -> str:
    mark = "OK  " if ok else "FAIL"
    return f"[{mark}] {label}{(' — ' + detail) if detail else ''}"


async def check_mcp(settings: Settings) -> tuple[bool, str]:
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(settings.swiggy_mcp_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = ", ".join(t.name for t in tools.tools[:5])
                return True, f"{len(tools.tools)} tools (e.g. {names})"
    except Exception as exc:  # noqa: BLE001 - report any failure verbatim
        return False, f"{type(exc).__name__}: {exc}"


def check_vertex(settings: Settings) -> tuple[bool, str]:
    try:
        from google import genai

        client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
        )
        resp = client.models.generate_content(
            model=settings.vertex_model_id, contents="ping"
        )
        return True, f"model responded ({len(resp.text or '')} chars)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    settings = Settings()
    results = []
    results.append((True, "env loaded", f"environment={settings.environment}"))
    mcp_ok, mcp_detail = asyncio.run(check_mcp(settings))
    results.append((mcp_ok, "Swiggy MCP reachable", mcp_detail))
    vx_ok, vx_detail = check_vertex(settings)
    results.append((vx_ok, "Vertex / Gemini reachable", vx_detail))

    print("\n".join(_line(ok, label, detail) for ok, label, detail in results))
    return 0 if all(ok for ok, _, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
