# Swiggy Deal Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-user PWA + FastAPI agent that finds a chosen dish at nearby Swiggy restaurants (≤ 7 km) and ranks them by final post-coupon price (Swiggy coupons only), highest rating as tiebreak.

**Architecture:** Installable PWA → FastAPI backend. A Gemini 3.5 Flash Lite agent (Vertex AI) plans over 3 deterministic tools; Python services do the rate-limited MCP work and the cart-simulation pricing. Discovery only — user orders in Swiggy.

**Tech Stack:** Python 3.14, FastAPI, `uv`, `ruff`, pytest; `mcp` SDK (Swiggy Food MCP); `google-genai` (Vertex); PWA frontend; Cloud Run (post-approval).

**Spec:** `docs/superpowers/specs/2026-06-13-swiggy-deal-finder-design.md`

---

## Scope of THIS plan

**Phase 1 (Setup)** is fully specified below and is executable immediately. It ends at the
**env-fill handoff**: you populate `.env`, then the connectivity check (Task 6) verifies
reachability before any real backend work.

**Phases 2–6 are a roadmap** (bottom of this doc), intentionally not yet broken into
code-level tasks because their implementations depend on real Swiggy MCP response schemas
resolved by the Phase 2a staging spike (unknowns U1/U2 in the spec). They get their own
detailed plan after env-fill + spike.

## File Structure (Phase 1)

```
Swiggy/
├── pyproject.toml                     # deps + ruff/pytest config (modify)
├── .gitignore                         # exclude .env, .venv, db, caches (create)
├── .env.example                       # every required env var, documented (create)
├── README.md                          # setup + run instructions (create)
├── src/swiggy_deal_finder/
│   ├── __init__.py                    # (create)
│   ├── config.py                      # pydantic-settings Settings (create)
│   └── main.py                        # FastAPI app + /health (create)
├── scripts/
│   └── check_env.py                   # post-env-fill connectivity check (create)
└── tests/
    ├── __init__.py                    # (create)
    ├── test_config.py                 # config loader behavior (create)
    └── test_health.py                 # /health endpoint (create)
```

---

## Task 0: Initialize git + ignore secrets

**Files:**
- Create: `.gitignore`

- [ ] **Step 1: Initialize the repository**

Run: `git init`
Expected: `Initialized empty Git repository in .../Swiggy/.git/`

- [ ] **Step 2: Create `.gitignore` (secrets-safe before any `.env` exists)**

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/

# Secrets & local state — NEVER commit
.env
.env.*
!.env.example
*.db
*.sqlite3

# Cloud / creds
*-service-account*.json
gha-creds-*.json

# IDE
.idea/
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: init repo with secrets-safe gitignore"
```

---

## Task 1: Dependencies & tooling

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Replace `pyproject.toml` with project + dependency + tool config**

```toml
[project]
name = "swiggy-deal-finder"
version = "0.1.0"
description = "Per-user agent that finds the cheapest, highest-rated nearby Swiggy dish after coupons."
requires-python = ">=3.14"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "pydantic-settings>=2.6",
    "httpx>=0.28",
    "mcp>=1.2",
    "google-genai>=1.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "anyio>=4.6",
    "ruff>=0.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/swiggy_deal_finder"]

[tool.ruff]
target-version = "py314"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Sync the environment**

Run: `uv sync --dev`
Expected: resolves and installs the deps into `.venv`; ends with a summary like `Installed N packages`. If a package lacks a 3.14 wheel, note it and pin a compatible version — do not silently drop a dependency.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: project deps and ruff/pytest config"
```

---

## Task 2: Config loader (pydantic-settings)

**Files:**
- Create: `src/swiggy_deal_finder/__init__.py`, `src/swiggy_deal_finder/config.py`
- Test: `tests/__init__.py`, `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
from swiggy_deal_finder.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("SWIGGY_MCP_URL", "https://staging.example/mcp")
    monkeypatch.setenv("GCP_PROJECT_ID", "demo-project")
    monkeypatch.setenv("GCP_LOCATION", "asia-south1")
    monkeypatch.setenv("VERTEX_MODEL_ID", "gemini-flash-lite")
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 32)

    settings = Settings()

    assert settings.swiggy_mcp_url == "https://staging.example/mcp"
    assert settings.gcp_project_id == "demo-project"
    assert settings.environment == "dev"  # default


def test_missing_required_raises(monkeypatch):
    for var in ("SWIGGY_MCP_URL", "GCP_PROJECT_ID", "GCP_LOCATION",
                "VERTEX_MODEL_ID", "APP_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'swiggy_deal_finder.config'`

- [ ] **Step 3: Create the package init**

`src/swiggy_deal_finder/__init__.py`:
```python
```
(empty file)

`tests/__init__.py`:
```python
```
(empty file)

- [ ] **Step 4: Implement `config.py`**

`src/swiggy_deal_finder/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Swiggy MCP
    swiggy_mcp_url: str
    swiggy_oauth_client_id: str = ""
    swiggy_oauth_client_secret: str = ""
    swiggy_oauth_redirect_uri: str = ""

    # Vertex AI / Gemini
    gcp_project_id: str
    gcp_location: str
    vertex_model_id: str
    google_application_credentials: str = ""

    # App
    app_secret_key: str
    database_url: str = "sqlite:///./dev.db"
    environment: str = "dev"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/swiggy_deal_finder/__init__.py src/swiggy_deal_finder/config.py tests/__init__.py tests/test_config.py
git commit -m "feat: typed settings loader"
```

---

## Task 3: FastAPI app + health endpoint

**Files:**
- Create: `src/swiggy_deal_finder/main.py`
- Test: `tests/test_health.py`

- [ ] **Step 1: Write the failing test**

`tests/test_health.py`:
```python
from fastapi.testclient import TestClient

from swiggy_deal_finder.main import app


def test_health_ok():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'swiggy_deal_finder.main'`

- [ ] **Step 3: Implement `main.py`**

`src/swiggy_deal_finder/main.py`:
```python
from fastapi import FastAPI

app = FastAPI(title="Swiggy Deal Finder")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_health.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Verify the server boots**

Run: `uv run uvicorn swiggy_deal_finder.main:app --port 8000 &` then `curl -s localhost:8000/health` then `kill %1`
Expected: `{"status":"ok"}`

- [ ] **Step 6: Commit**

```bash
git add src/swiggy_deal_finder/main.py tests/test_health.py
git commit -m "feat: FastAPI app with health endpoint"
```

---

## Task 4: `.env.example` (the env-fill contract)

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Create `.env.example` documenting every value you must fill**

```dotenv
# ─── Swiggy MCP (Builders Club) ───────────────────────────────
# Staging MCP endpoint (docs say staging works without full OAuth).
SWIGGY_MCP_URL=
# OAuth app credentials (needed from Phase 4 on; leave blank for the spike).
SWIGGY_OAUTH_CLIENT_ID=
SWIGGY_OAUTH_CLIENT_SECRET=
SWIGGY_OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback

# ─── Vertex AI / Gemini 3.5 Flash Lite ────────────────────────
GCP_PROJECT_ID=
GCP_LOCATION=asia-south1
# Confirm the exact published model id for Flash Lite on Vertex.
VERTEX_MODEL_ID=
# Absolute path to a service-account JSON, OR leave blank to use gcloud ADC.
GOOGLE_APPLICATION_CREDENTIALS=

# ─── App ──────────────────────────────────────────────────────
# 32+ random chars; used later for encrypting stored user tokens.
APP_SECRET_KEY=
DATABASE_URL=sqlite:///./dev.db
ENVIRONMENT=dev
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: env var contract"
```

> **HANDOFF:** Copy `.env.example` to `.env` and fill the values. At minimum for the
> staging spike: `SWIGGY_MCP_URL`, `GCP_PROJECT_ID`, `GCP_LOCATION`, `VERTEX_MODEL_ID`,
> `APP_SECRET_KEY` (and either `GOOGLE_APPLICATION_CREDENTIALS` or run `gcloud auth
> application-default login`). Then run Task 6.

---

## Task 5: README setup section

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Swiggy Deal Finder

Per-user agent that finds a chosen dish at nearby Swiggy restaurants (≤ 7 km) and ranks
them by final post-coupon price (Swiggy coupons only), rating as tiebreak. Discovery only.

## Setup
1. `uv sync --dev`
2. `cp .env.example .env` and fill values (see comments in the file).
3. `uv run python scripts/check_env.py` — verifies MCP + Vertex reachability.
4. `uv run uvicorn swiggy_deal_finder.main:app --reload`

## Test
`uv run pytest`

## Docs
- Design: `docs/superpowers/specs/2026-06-13-swiggy-deal-finder-design.md`
- Plan:   `docs/superpowers/plans/2026-06-13-swiggy-deal-finder.md`
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: readme setup instructions"
```

---

## Task 6: Connectivity check (run AFTER env-fill)

**Files:**
- Create: `scripts/check_env.py`

This is the "then you check" gate: deterministic env validation, then best-effort live
checks of the Swiggy MCP endpoint and Vertex. It reports a checklist; it never assumes
success.

- [ ] **Step 1: Write `scripts/check_env.py`**

```python
"""Post-env-fill connectivity check. Reports OK/FAIL per dependency; exits non-zero on failure."""
import asyncio
import sys

from swiggy_deal_finder.config import Settings


def _line(ok: bool, label: str, detail: str = "") -> str:
    mark = "OK  " if ok else "FAIL"
    return f"[{mark}] {label}{(' — ' + detail) if detail else ''}"


async def check_mcp(settings: Settings) -> tuple[bool, str]:
    try:
        from mcp.client.streamable_http import streamablehttp_client
        from mcp import ClientSession

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
```

- [ ] **Step 2: Run it**

Run: `uv run python scripts/check_env.py`
Expected (after env-fill): three `[OK ]` lines. Any `[FAIL]` line prints the exact
exception so we fix the specific credential/endpoint — this is where the exact MCP client
transport and the exact Vertex model id get confirmed against reality.

- [ ] **Step 3: Commit**

```bash
git add scripts/check_env.py
git commit -m "feat: connectivity check for MCP + Vertex"
```

---

## Phase 1 Definition of Done

- `uv run pytest` → all green.
- `uv run python scripts/check_env.py` → all OK (after you fill `.env`).
- Server boots and `/health` returns `{"status":"ok"}`.
- No secret committed (`.env` is gitignored; only `.env.example` is tracked).

---

## Phases 2–6 Roadmap (detailed after env-fill + staging spike)

Each becomes its own task-level plan once the spike resolves U1/U2 and we know the real
MCP response schemas. Model tier per the spec's efficient-frontier table.

- **Phase 2a — Staging spike (Opus-reviewed).** Hand-run the Food tools; resolve: radius vs
  client-side ≤7 km filter (U1); Swiggy-vs-bank coupon flag + post-coupon total in bill
  breakdown (U2); cart snapshot/restore fidelity. Output: a findings note that unblocks 2b–2d.
- **Phase 2b — Read path (Sonnet).** `SwiggyMCPClient` + rate limiter, `CandidateService`,
  `Ranker`. CLI returns ranked-by-base-price results.
- **Phase 2c — Pricing (Sonnet, Opus-reviewed — highest risk).** `CouponPricer` +
  `CartGuard` cart-simulation against staging with a test account.
- **Phase 2d — Agent (Sonnet).** `DiscoveryAgent` (Gemini Flash Lite) + `GeminiToolBridge`
  over the 3-tool surface; structured-output disambiguation; graceful degradation.
- **Phase 3 — PWA frontend (Sonnet).** Search, results cards, Connect-Swiggy, installability.
- **Phase 4 — OAuth + multi-user (Sonnet, Opus-reviewed).** Swiggy OAuth 2.1 + PKCE,
  encrypted token storage, refresh.
- **Phase 5 — Local demos + Builders Club application (non-code).** Record end-to-end demos;
  apply for production MCP access; resolves the ToS go/no-go.
- **Phase 6 — Cloud Run deploy (Sonnet, Opus-reviewed).** Containerize, Secret Manager,
  Cloud SQL Postgres, Vertex in-project, prod OAuth redirect.
