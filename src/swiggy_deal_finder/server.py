"""Swiggy Deal Finder — MCP connector server.

Exposes two tools for Claude (or any MCP client):

  get_locations()
    Lists the user's saved Swiggy delivery addresses so they can pick one.
    Call this first; give the user the id + label + line so they can select.

  find_deals(dish, address_id, top_n=5)
    Finds the cheapest options for `dish` at the chosen address after Swiggy
    coupons are applied, ranked cheapest-first.  Requires an empty Swiggy cart.

Typical flow for the orchestrating agent:
  1. get_locations() → present list to user → user picks address_id.
  2. find_deals(dish, address_id) → present ranked table to user.

Session management
------------------
One persistent LiveSwiggyClient (one mcp-remote subprocess + MCP session) is owned by
the server lifespan, so connect()/aclose() run in the server's root anyio task. Doing
this in a tool handler instead (lazy init) hangs under an MCP client, because the
stdio_client task group must be entered and exited in the same task. OAuth therefore
runs once at startup — the natural moment right after the user connects the connector.

Run as a stdio MCP server::

    uv run python -m swiggy_deal_finder.server
    # or directly:
    python src/swiggy_deal_finder/server.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from swiggy_deal_finder.live_client import LiveSwiggyClient
from swiggy_deal_finder.pricing import CartNotEmptyError
from swiggy_deal_finder.service import DealFinder

# The single Swiggy session, owned by the lifespan (one anyio task context).
_client: LiveSwiggyClient | None = None


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    """Connect one persistent Swiggy session at startup; close it at shutdown.

    Entering/exiting the mcp-remote stdio session in the server's root task (here)
    rather than in a tool handler avoids the anyio cross-task cancel-scope hang.
    """
    global _client
    _client = LiveSwiggyClient()
    await _client.connect()
    try:
        yield
    finally:
        await _client.aclose()
        _client = None


mcp = FastMCP("swiggy-deal-finder", lifespan=_lifespan)


def _require_client() -> LiveSwiggyClient:
    """Return the lifespan-owned client, or error if the server isn't started."""
    if _client is None:
        raise RuntimeError("Swiggy session not initialised (lifespan not started).")
    return _client


@mcp.tool()
async def get_locations() -> str:
    """List the user's saved Swiggy delivery addresses.

    Call this first so the user can choose which address to use for delivery.
    Each address has an `id` (pass to find_deals), a `label` (Work/Home/etc.),
    and a `line` (human-readable address string).

    After showing this list, ask the user to confirm their preferred address_id
    before calling find_deals.
    """
    client = _require_client()
    addresses = await client.get_addresses()
    if not addresses:
        return "No saved addresses found. Please add a delivery address in the Swiggy app first."
    lines = ["Your saved Swiggy delivery addresses:\n"]
    for addr in addresses:
        lines.append(f"  id={addr.id!r}  label={addr.label!r}")
        lines.append(f"    {addr.line}")
    return "\n".join(lines)


@mcp.tool()
async def find_deals(dish: str, address_id: str, top_n: int = 5) -> str:
    """Find the cheapest nearby Swiggy options for a dish after coupons are applied.

    Parameters
    ----------
    dish:
        The dish to search for, e.g. "biryani", "chicken biryani", "masala dosa".
    address_id:
        The delivery address id from get_locations(). Must be a valid saved address.
    top_n:
        How many candidates to price and return (default 5). Fewer → faster.

    Returns a ranked table (cheapest first) with restaurant name, rating, distance,
    base price, best Swiggy coupon applied, and final amount to pay.

    Requires an EMPTY Swiggy cart. If your cart is not empty, this tool will tell
    you so — clear your cart in the Swiggy app and try again.

    Typical flow: call get_locations() first → pick address_id → call find_deals().
    """
    client = _require_client()
    finder = DealFinder(client)
    try:
        options = await finder.find_deals(dish=dish, address_id=address_id, top_n=top_n)
    except CartNotEmptyError:
        return (
            "Your Swiggy cart is not empty. "
            "Please clear your cart in the Swiggy app and try again. "
            "Deal Finder needs an empty cart to probe prices accurately."
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean message, never a raw trace
        return (
            f"The deal search hit an unexpected error ({type(exc).__name__}). "
            "Please try again in a moment."
        )
    if not options:
        return (
            f"No results found for '{dish}' within 7 km of the selected address. "
            "Try a broader search term or a different address."
        )

    header = f"Top {len(options)} deals for '{dish}' (cheapest first after coupons):\n"
    rows = [header]
    for i, opt in enumerate(options, 1):
        rest = opt.hit.restaurant
        rating_str = f"{rest.avg_rating:.1f}" if rest.avg_rating is not None else "N/A"
        coupon_str = f"{opt.coupon_code} (−₹{opt.coupon_discount})" if opt.coupon_code else "none"
        rows.append(
            f"{i}. {rest.name}"
            f"\n   Rating: {rating_str}  Distance: {rest.distance_km:.1f} km"
            f"\n   Item: {opt.hit.item_name}  Base: ₹{opt.hit.base_price}"
            f"\n   Coupon: {coupon_str}"
            f"\n   Final to pay: ₹{opt.final_to_pay}\n"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    mcp.run()
