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
from swiggy_deal_finder.filler import choose_filler, parse_offer
from swiggy_deal_finder.live_client import LiveSwiggyClient
from swiggy_deal_finder.pricing import CartNotEmptyError, CouponPricer
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
    min_rating: float | None = None,
    restaurant_name: str | None = None,
    quantity: int = 1,
    coupon_codes: list[str] | None = None,
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
    min_rating:
        Minimum acceptable restaurant rating (e.g. 4.0). This is a USER preference —
        ASK the user what minimum rating they want and pass their answer here. NEVER
        assume or hardcode a default. Leave as None only if the user has no preference;
        a floor also reduces how many restaurants are probed (faster results).
    restaurant_name:
        Optional restaurant or chain name to narrow results to one outlet, e.g. "A2B"
        or "Saravana". Case-insensitive substring match against restaurant names.
        If no result is returned, retry with the chain's FULL brand name or an alternate
        spelling (e.g. "A2B" → "Adyar Ananda Bhavan") — chains may be listed under their
        full name. Only restaurants the category search surfaces (within ~7 km, paginated)
        can be matched; a specific branch beyond that range will not appear.
    quantity:
        How many of the item to price (default 1). Swiggy coupons often need a minimum
        cart value (e.g. "₹125 OFF ABOVE ₹249"), so a single item may show NO coupon.
        Do NOT silently increase this — the user may want only one. See the IMPORTANT note.
    coupon_codes:
        Optional list of specific coupon codes the USER already knows about (from the
        Swiggy app, an SMS, etc.). The connector applies each plus Swiggy's auto-best and
        keeps the lowest price. NOTE: Swiggy's MCP exposes no browsable coupon list, so
        the connector cannot discover codes on its own — it already auto-applies Swiggy's
        single best coupon; pass codes here only when the user names them.

    Returns a ranked table (cheapest first) with restaurant name, rating, distance,
    base price, the restaurant's headline offer, best Swiggy coupon applied, and final
    amount to pay (for `quantity` items).

    Requires an EMPTY Swiggy cart. If your cart is not empty, this tool will tell
    you so — clear your cart in the Swiggy app and try again.

    IMPORTANT — ask clarifying questions BEFORE / AFTER calling find_deals:
    1. If the dish is a generic family (e.g. 'dosa', 'biryani', 'pizza', 'noodles'),
       FIRST call list_dish_variants(dish, address_id), show the variants, and ask the
       user which specific one they want. Only then call find_deals with that specific dish.
    2. If the chosen variant is flagged "has size/add-on options" in the list_dish_variants
       result, ASK AS MANY CLARIFYING QUESTIONS AS NEEDED about the exact size, toppings,
       and add-ons BEFORE calling find_deals — never guess toppings or portion sizes.
    3. If a result shows a headline offer with a minimum (e.g. "₹125 OFF ABOVE ₹249") but
       NO coupon was applied because the single item is below that minimum, ASK the user
       whether they'd like to buy a larger quantity (or add items) to unlock the discount —
       NEVER assume they want more than one. If they agree, call find_deals again with the
       chosen `quantity`.

    Typical flow: call get_locations() first → pick address_id → for ambiguous dishes
    call list_dish_variants() first → clarify size/add-ons if flagged → find_deals →
    if a min-cart offer went unused, ask about quantity and re-run.
    """
    client = _require_client()
    finder = DealFinder(client)
    try:
        options = await finder.find_deals(
            dish=dish, address_id=address_id, top_n=top_n, portion=portion,
            category=category, min_rating=min_rating, restaurant_name=restaurant_name,
            quantity=quantity, coupon_codes=coupon_codes,
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

    qty_note = f" (×{quantity})" if quantity != 1 else ""
    header = (
        f"Top {len(options)} deals for '{dish}'{qty_note} "
        "(cheapest first after coupons):\n"
    )
    rows = [header]
    for i, opt in enumerate(options, 1):
        rest = opt.hit.restaurant
        rating_str = f"{rest.avg_rating:.1f}" if rest.avg_rating is not None else "N/A"
        coupon_str = (
            f"{opt.coupon_code} (−₹{opt.coupon_discount})" if opt.coupon_code else "none"
        )
        item_line = f"\n   Item: {opt.hit.item_name}  Base: ₹{opt.hit.base_price}"
        if opt.quantity != 1:
            item_line += f"  ×{opt.quantity} = ₹{opt.hit.base_price * opt.quantity}"
        offer_line = f"\n   Offer: {rest.offer}" if rest.offer else ""
        # When a min-cart offer exists but no coupon applied, hint that buying more may
        # unlock it — the agent should ASK the user, never silently bump the quantity.
        hint_line = ""
        if rest.offer and not opt.coupon_code:
            hint_line = (
                "\n   ⓘ No coupon applied at this quantity — this restaurant has the "
                "offer above; ask the user if they want a larger quantity to unlock it."
            )
        final_line = f"\n   Final to pay: ₹{opt.final_to_pay}"
        if opt.quantity != 1:
            per_unit = opt.final_to_pay / opt.quantity
            final_line += f" for {opt.quantity}  (≈₹{per_unit:.0f} each)"
        rows.append(
            f"{i}. {rest.name}"
            f"\n   Rating: {rating_str}  Distance: {rest.distance_km:.1f} km"
            f"{item_line}"
            f"{offer_line}"
            f"\n   Coupon: {coupon_str}"
            f"{hint_line}"
            f"{final_line}\n"
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
    IMPORTANT — ASK AS MANY CLARIFYING QUESTIONS AS NEEDED: show this list to the user
    and ask which specific variant they want. If any variant is flagged
    "has size/add-on options", also ask the user to specify their exact size, toppings,
    and add-ons BEFORE calling find_deals — never guess these details.
    Only then call find_deals with the fully-specified dish name.
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
    for name, lo, hi, has_options in variants:
        suffix = "  — has size/add-on options" if has_options else ""
        lines.append(f"  - {name}  (₹{lo}–{hi}){suffix}")
    lines.append(
        "Ask the user which specific one to compare, then call find_deals with that name."
    )
    return "\n".join(lines)


@mcp.tool()
async def suggest_coupon_filler(
    dish: str,
    address_id: str,
    restaurant_name: str,
    category: str | None = None,
    coupon_codes: list[str] | None = None,
) -> str:
    """Reach a coupon's minimum-cart by adding the cheapest extra items, so a discount
    that the single dish is too small to unlock gets applied.

    Use this ONLY when the user explicitly asks to "fill the cart" / "add cheap items to
    get the discount" for a specific restaurant whose offer needs a minimum cart (e.g.
    "₹125 OFF ABOVE ₹249") that their dish alone does not meet. This adds extra food to
    the order — ALWAYS confirm with the user first that they're happy to buy more, and
    show them the plan below before they order.

    Parameters
    ----------
    dish:
        The specific dish the user wants (already disambiguated), e.g. "ghee dosa".
    address_id:
        The delivery address id from get_locations().
    restaurant_name:
        The restaurant to fill the cart at (substring match, e.g. "A2B"). Required —
        this tool works on ONE restaurant.
    category:
        The BROAD food family for the restaurant search (e.g. "dosa" for "ghee dosa") —
        infer it as you do for find_deals.
    coupon_codes:
        Optional user-known coupon codes to also try (Swiggy's MCP exposes no coupon list;
        the connector auto-applies Swiggy's single best coupon regardless).

    Returns a plan: the dish, the cheapest filler item(s) to add (with quantities), the
    new subtotal, the coupon applied, and the final amount to pay. The filler may repeat
    one cheap item (e.g. 2× chutney) when that is the cheapest way to clear the threshold.
    It only adds plain items (no size/add-on customisation needed). Requires an EMPTY cart.
    """
    client = _require_client()
    hits = await CandidateService(client).find(
        dish, address_id, category=category, restaurant_name=restaurant_name
    )
    if not hits:
        return (
            f"Couldn't find '{dish}' at a restaurant matching '{restaurant_name}' nearby. "
            "Try the restaurant's full brand name, a different category, or confirm the "
            "dish is available there."
        )
    hit = hits[0]
    rest = hit.restaurant
    terms = parse_offer(rest.offer)
    if terms.min_cart is None:
        offer_txt = f" (offer: {rest.offer})" if rest.offer else ""
        return (
            f"{rest.name} has no minimum-cart coupon I can read{offer_txt}, so there's no "
            f"threshold to fill toward. Just order '{hit.item_name}' (₹{hit.base_price}) "
            "directly, or use find_deals to see its post-coupon price."
        )

    gap = terms.min_cart - hit.base_price
    menu = await client.get_restaurant_menu(rest.id, address_id)
    filler = choose_filler(menu, gap, {hit.item_id}) if gap > 0 else []
    if gap > 0 and not filler:
        return (
            f"{rest.name}'s coupon needs a ₹{terms.min_cart} cart, but I couldn't find "
            f"cheap plain add-ons to bridge the ₹{gap} gap from '{hit.item_name}' "
            f"(₹{hit.base_price}). The user could pick add-ons manually in the app."
        )

    try:
        result = await CouponPricer(client).price_with_filler(
            hit, address_id, filler, coupon_codes=coupon_codes
        )
    except CartNotEmptyError:
        return (
            "Your Swiggy cart is not empty. Please clear it in the Swiggy app and try "
            "again — the cart-filler needs an empty cart to build the order accurately."
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean message, never a raw trace
        return (
            f"The cart-filler hit an unexpected error ({type(exc).__name__}). "
            "Please try again in a moment."
        )

    lines = [
        f"Cart-filler plan at {rest.name} (offer: {rest.offer}):",
        f"  • {result.dish_name} — ₹{result.dish_price}",
    ]
    for name, unit_price, qty in result.filler:
        if qty != 1:
            lines.append(f"  • {qty}× {name} — ₹{unit_price} each = ₹{unit_price * qty}")
        else:
            lines.append(f"  • {name} — ₹{unit_price}")
    lines.append(f"  Subtotal: ₹{result.subtotal}  (clears the ₹{terms.min_cart} minimum)")
    if result.coupon_code and result.coupon_discount > 0:
        lines.append(
            f"  Coupon {result.coupon_code}: −₹{result.coupon_discount}  →  "
            f"Final to pay: ₹{result.final_to_pay}"
        )
        lines.append(
            "Confirm with the user they want these extra items before they order — they "
            "get more food AND the discount, but it costs more than the dish alone."
        )
    else:
        lines.append(
            f"  Final to pay: ₹{result.final_to_pay} — note: even at ₹{result.subtotal} no "
            "coupon applied, so filling the cart did NOT unlock a discount here. Tell the "
            "user it isn't worth adding the extra items."
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
