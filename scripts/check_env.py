"""Post-env-fill connectivity check. Reports OK/FAIL per dependency; exits non-zero on failure."""
import asyncio
import sys

from swiggy_deal_finder.config import Settings


def _line(ok: bool, label: str, detail: str = "") -> str:
    mark = "OK  " if ok else "FAIL"
    return f"[{mark}] {label}{(' — ' + detail) if detail else ''}"


async def check_mcp(settings: Settings) -> tuple[bool, str]:
    if not settings.swiggy_mcp_url:
        return True, "skipped — no SWIGGY_MCP_URL (MCP runs via the Claude session for the spike)"
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
        import os

        from google import genai

        # google.auth reads the credentials path from the process env, but
        # pydantic only loads .env into Settings — bridge it across.
        if settings.google_application_credentials:
            os.environ.setdefault(
                "GOOGLE_APPLICATION_CREDENTIALS",
                settings.google_application_credentials,
            )
        client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
        resp = client.models.generate_content(
            model=settings.gemini_model, contents="ping"
        )
        return True, f"{settings.gemini_model} responded ({len(resp.text or '')} chars)"
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
