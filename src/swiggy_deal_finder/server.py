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

from swiggy_deal_finder.candidates import CandidateService
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
async def find_deals(
    dish: str,
    address_id: str,
    top_n: int = 5,
    portion: str = "regular",
    category: str | None = None,
) -> str:
    """Find the cheapest nearby Swiggy options for a dish after coupons are applied.

    Parameters
    ----------
    dish:
        The dish to search for, e.g. "masala dosa", "chicken biryani".
    address_id:
        The delivery address id from get_locations(). Must be a valid saved address.
    top_n:
        How many candidates to price and return (default 5). Fewer → faster.
    portion:
        "regular" (default) for a normal portion; "mini" only if the user explicitly
        wants a small/mini size; "any" to disable the portion filter.
    category:
        The BROAD food category the dish belongs to — INFER it from the dish using your
        own food knowledge (this works for ANY cuisine, not a fixed list). Examples:
        "ghee roast"/"masala dosa" → "dosa"; "chicken biryani" → "biryani";
        "margherita" → "pizza"; "butter chicken" → "north indian"; "california roll" → "sushi".
        The Swiggy restaurant search only returns restaurants for such broad categories, so
        ALWAYS set category to the dish's family when the dish itself is specific. If the
        search returns nothing, infer a DIFFERENT plausible category or a synonym for the
        dish (e.g. "frankie" → "roll"/"wrap", "curd rice" → "south indian") and call again
        before telling the user it's unavailable.

    Returns a ranked table (cheapest first) with restaurant name, rating, distance,
    base price, best Swiggy coupon applied, and final amount to pay.

    Requires an EMPTY Swiggy cart. If your cart is not empty, this tool will tell
    you so — clear your cart in the Swiggy app and try again.

    IMPORTANT: compare like-for-like. If the user's dish is generic/ambiguous (a family
    such as 'dosa', 'biryani', 'pizza', 'noodles') rather than a specific item, FIRST
    call list_dish_variants(dish, address_id), show the user the variants, and ask which
    specific one they want (including toppings, e.g. plain vs masala vs ghee dosa). Only
    then call find_deals with that specific dish. Use portion='regular' (default) for a
    normal portion, 'mini' only if the user explicitly wants a small/mini size.

    Typical flow: call get_locations() first → pick address_id → for ambiguous dishes
    call list_dish_variants() first → then call find_deals() with the specific variant.
    """
    client = _require_client()
    finder = DealFinder(client)
    try:
        options = await finder.find_deals(
            dish=dish, address_id=address_id, top_n=top_n, portion=portion, category=category
        )
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
            f"No results found for '{dish}' near this address. This usually means the "
            "category was too narrow or off — try calling find_deals again with a "
            "different `category` (the dish's broad food family) or a synonym for the "
            "dish, or a different saved address, before concluding it's unavailable."
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


@mcp.tool()
async def list_dish_variants(
    dish: str,
    address_id: str,
    category: str | None = None,
) -> str:
    """List the distinct variants of a dish available at nearby open restaurants.

    Use this BEFORE find_deals when the user's dish is generic or a family name
    (e.g. 'dosa', 'biryani', 'pizza', 'noodles'). Shows all variants with their
    price range so the user can pick the specific one they want before pricing.

    Parameters
    ----------
    dish:
        Generic dish name to search for variants, e.g. "dosa", "biryani".
    address_id:
        The delivery address id from get_locations().
    category:
        The BROAD food category the dish belongs to — INFER it from the dish using your
        own food knowledge (this works for ANY cuisine, not a fixed list). Examples:
        "ghee roast"/"masala dosa" → "dosa"; "chicken biryani" → "biryani";
        "margherita" → "pizza"; "butter chicken" → "north indian"; "california roll" → "sushi".
        The Swiggy restaurant search only returns restaurants for such broad categories, so
        ALWAYS set category to the dish's family when the dish itself is specific. If the
        search returns nothing, infer a DIFFERENT plausible category or a synonym for the
        dish (e.g. "frankie" → "roll"/"wrap", "curd rice" → "south indian") and call again
        before telling the user it's unavailable.

    Returns a list of distinct dish variants with price ranges, ready to show the user.
    After showing this list, ask the user which specific variant to compare, then call
    find_deals with that specific dish name.
    """
    client = _require_client()
    service = CandidateService(client)
    variants = await service.list_variants(dish, address_id, category=category)
    if not variants:
        return (
            f"No variants found for '{dish}' within 7 km of the selected address. "
            "Try a broader search term or a different address."
        )
    lines = [f"Variants of '{dish}' available nearby:"]
    for name, lo, hi in variants:
        lines.append(f"  - {name}  (₹{lo}–{hi})")
    lines.append(
        "Ask the user which specific one to compare, then call find_deals with that name."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
