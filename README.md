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
