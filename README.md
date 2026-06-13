# Swiggy Deal Finder

Swiggy Deal Finder is an MCP connector that finds the cheapest nearby option for a dish on **your own Swiggy account**, ranked by final post-coupon payable amount.

It is designed for **discovery only**:
- no order placement
- read + controlled cart mutation for pricing probes
- automatic cart cleanup after each pricing probe

---

## What this project does

Given a dish and a selected saved delivery address, the connector:
1. Finds open restaurants within 7 km.
2. Matches menu items for the requested dish.
3. Prices top candidates by probing Swiggy cart + coupon application.
4. Returns ranked options by:
   - lowest final payable amount (`to_pay`)
   - highest rating as tie-breaker

The MCP server exposes these tools:
- `get_locations()`
- `list_dish_variants(dish, address_id, category?)`
- `find_deals(dish, address_id, top_n=5, portion="regular", category?)`

---

## Architecture overview

Core modules (`src/swiggy_deal_finder`):
- `server.py`  
  MCP entrypoint (FastMCP), tool definitions, lifespan-managed persistent Swiggy client.
- `live_client.py`  
  Real Swiggy adapter over `mcp-remote` (spawned via `npx`), wraps MCP tool calls.
- `service.py`  
  Orchestration pipeline (`DealFinder`) for candidate discovery → pricing → ranking.
- `candidates.py`  
  Restaurant/menu discovery, dish-token matching, distance/open filters, portion filtering.
- `pricing.py`  
  Cart safety guard + coupon probe cycle. Handles empty-cart precondition and cart flush.
- `ranking.py`  
  Final ordering logic (`final_to_pay` asc, rating desc).
- `swiggy_client.py`  
  Protocol and pure parsers for Swiggy responses.
- `models.py`  
  Pydantic domain models.
- `main.py`  
  Minimal FastAPI app (`/health`) for service health checks.

---

## Safety invariants

The project enforces a few critical rules:
- Never places an order.
- Refuses pricing if the cart is already non-empty.
- Never flushes a cart it did not create.
- Always flushes probe cart items after pricing completes (or on failure path after entry).
- Uses fresh cart reads for truth after coupon application.

---

## Prerequisites

- Python `>= 3.14` (as defined in `pyproject.toml`)
- [`uv`](https://docs.astral.sh/uv/) for environment management
- Node.js / `npx` (used to spawn `mcp-remote`)
- A Swiggy account with saved delivery addresses

---

## Setup

1. Install dependencies:

   ```bash
   uv sync --dev
   ```

2. Create environment file:

   ```bash
   cp .env.example .env
   ```

3. Fill required values in `.env`:
   - Swiggy MCP/OAuth values (as available in your environment)
   - Vertex/Gemini values for model access
   - `APP_SECRET_KEY` (required)

4. Validate connectivity:

   ```bash
   uv run python scripts/check_env.py
   ```

---

## Running locally

### 1) FastAPI health app

```bash
uv run uvicorn swiggy_deal_finder.main:app --reload
```

Health endpoint:
- `GET /health` → `{"status":"ok"}`

### 2) MCP server (for Claude Desktop / MCP clients)

```bash
uv run python -m swiggy_deal_finder.server
```

This starts the MCP connector and keeps one persistent authenticated Swiggy session for tool calls.

---

## Typical MCP usage flow

1. Call `get_locations()` and choose an `address_id`.
2. If dish is generic (e.g. `dosa`, `pizza`, `biryani`), call `list_dish_variants(...)` first.
3. Call `find_deals(...)` with the selected specific dish.
4. Present ranked results to the user.

Notes:
- `category` should be a broad cuisine/category term when dish search is too specific.
- `portion="regular"` excludes mini/half-style servings unless user explicitly asks.

---

## Scripts

Repository scripts:
- `scripts/check_env.py`  
  Validates env load + Swiggy MCP reachability + Vertex/Gemini reachability.
- `scripts/smoke_live.py`  
  Read-only live smoke test against Swiggy MCP bridge.
- `scripts/demo.py`  
  Runs the full deal-finder engine from CLI.
- `scripts/check_server.py`  
  End-to-end MCP server check by spawning connector and invoking tools.

See also: [`DEMO.md`](./DEMO.md) for local Claude Desktop demo wiring details.

---

## Development commands

Lint:

```bash
uv run ruff check .
```

Tests:

```bash
uv run pytest
```

---

## Testing scope

Tests in `tests/` cover:
- parser and config behavior
- candidate filtering and matching
- pricing/cart guard behavior
- ranking logic
- server tool behavior
- live-client adapter contract assumptions

---

## Troubleshooting

- **`uv: command not found`**  
  Install `uv` and ensure it is in your PATH.

- **No results for a specific dish phrase**  
  Retry with a broader `category` (e.g. `dosa`, `biryani`, `roll`) and then match exact dish via menu scan.

- **Cart-not-empty response from `find_deals`**  
  Clear your cart in Swiggy app before pricing; this is a deliberate safety check.

- **No saved addresses**  
  Add delivery addresses in your Swiggy app first.
