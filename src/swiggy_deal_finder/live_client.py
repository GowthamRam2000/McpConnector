"""LiveSwiggyClient — production MCP adapter.

Bridges the SwiggyClient Protocol to the real Swiggy MCP at mcp.swiggy.com/food
via a persistent `mcp-remote` subprocess (spawned with npx).

Session lifecycle
-----------------
One subprocess + one MCP ClientSession is shared for the entire server lifetime.
connect() must be called once before any Protocol method (or use as an async
context manager). aclose() tears down the session and the subprocess cleanly.

Why persistent?
  Each new mcp-remote process triggers a fresh OAuth browser flow.  Reconnecting
  per-call would force the user to re-authenticate on every tool invocation.

Envelope notes (verified against live Swiggy MCP, 2026-06-13):
  - search_restaurants  → {"restaurants":[...], "total":N, "query":"..."}
  - get_restaurant_menu → {"restaurant":{...}, "categories":[{"title":..., "items":[...]}...], ...}
    items are nested under categories; we flatten them before passing to parse_menu_items.
  - get_food_cart (empty) → top-level "data" is null.
  - apply_food_coupon    → {} (empty body; side-effect only).
"""

from __future__ import annotations

import json
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from swiggy_deal_finder.models import Address, MenuItem, Restaurant
from swiggy_deal_finder.swiggy_client import (
    CartSummary,
    parse_cart,
    parse_menu_items,
    parse_restaurants,
)

_MCP_SERVER = StdioServerParameters(
    command="npx",
    args=["-y", "mcp-remote", "https://mcp.swiggy.com/food"],
)


def _extract_text(result: Any) -> str | None:
    """Return the text payload from a call_tool result, or None if absent/empty."""
    if not result.content:
        return None
    for block in result.content:
        if hasattr(block, "text"):
            return block.text
    return None


def _parse_json(text: str | None) -> dict[str, Any]:
    """JSON-decode text; return {} if text is None or not valid JSON."""
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {}
    except json.JSONDecodeError:
        return {}


class LiveSwiggyClient:
    """Concrete SwiggyClient backed by the real Swiggy MCP over a spawned mcp-remote process.

    Usage (async context manager, recommended)::

        async with LiveSwiggyClient() as client:
            addresses = await client.get_addresses()

    Or manual lifecycle::

        client = LiveSwiggyClient()
        await client.connect()
        ...
        await client.aclose()

    NEVER call place_food_order through this client — it is intentionally absent.
    """

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._stdio_cm: Any = None
        self._session_cm: Any = None

    async def connect(self) -> None:
        """Spawn mcp-remote and initialise the MCP ClientSession.

        Idempotent: a second call is a no-op if already connected.
        """
        if self._session is not None:
            return
        self._stdio_cm = stdio_client(_MCP_SERVER)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()

    async def aclose(self) -> None:
        """Tear down the MCP session and the subprocess."""
        if self._session_cm is not None:
            await self._session_cm.__aexit__(None, None, None)
            self._session_cm = None
            self._session = None
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(None, None, None)
            self._stdio_cm = None

    async def __aenter__(self) -> LiveSwiggyClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def _call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a Swiggy MCP tool and return the parsed JSON body (or {} on empty)."""
        if self._session is None:
            raise RuntimeError("LiveSwiggyClient is not connected. Call connect() first.")
        result = await self._session.call_tool(tool, args or {})
        # Recent MCP servers (protocol 2025-11-25) return the payload as a dict in
        # `structuredContent`; older ones embed JSON in a text content block.
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and structured:
            return structured
        text = _extract_text(result)
        return _parse_json(text)


    async def get_addresses(self) -> list[Address]:
        """Return the user's saved Swiggy delivery addresses."""
        raw = await self._call("get_addresses")
        entries: list[dict[str, Any]] = raw.get("addresses", [])
        return [
            Address(
                id=str(e["id"]),
                label=str(e.get("addressTag") or e.get("addressCategory") or ""),
                line=str(e.get("addressLine") or ""),
            )
            for e in entries
        ]

    async def search_restaurants(
        self, query: str, address_id: str, offset: int = 0
    ) -> list[Restaurant]:
        """Search for restaurants matching query at address_id.

        Live envelope: {"restaurants":[...], "total":N, "query":"..."}
        offset: page start index (0, 10, 20, …); page size is 10 on the Swiggy side.
        """
        raw = await self._call(
            "search_restaurants",
            {"query": query, "addressId": address_id, "offset": offset},
        )
        entries: list[dict[str, Any]] = raw.get("restaurants", [])
        return parse_restaurants(entries)

    async def get_restaurant_menu(
        self, restaurant_id: str, address_id: str, max_pages: int = 4
    ) -> list[MenuItem]:
        """Return the menu for restaurant_id as a flat, deduplicated list of MenuItems.

        Live envelope: {"restaurant":{...}, "categories":[{"title":..., "items":[...]}...],
        "hasMore": bool}. The menu is paginated by category (pageSize max 8); we follow
        `hasMore` up to max_pages so a dish in a later category is still found.

        addressId is REQUIRED — passing an empty string returns an empty menu, and prices
        are location-specific.
        """
        flat_items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        page = 1
        while page <= max_pages:
            raw = await self._call(
                "get_restaurant_menu",
                {
                    "restaurantId": restaurant_id,
                    "addressId": address_id,
                    "page": page,
                    "pageSize": 8,
                },
            )
            categories: list[dict[str, Any]] = raw.get("categories", [])
            for cat in categories:
                for item in cat.get("items", []):
                    item_id = str(item.get("id", ""))
                    if item_id and item_id not in seen_ids:
                        flat_items.append(item)
                        seen_ids.add(item_id)
            if not raw.get("hasMore"):
                break
            page += 1
        return parse_menu_items(flat_items)

    async def get_food_cart(self, address_id: str) -> CartSummary:
        """Return the current cart state for address_id."""
        raw = await self._call("get_food_cart", {"addressId": address_id})
        return parse_cart(raw)


    async def update_food_cart(
        self,
        restaurant_id: str,
        address_id: str,
        menu_item_id: str,
        quantity: int,
    ) -> dict:
        """Add/update a single item in the Swiggy cart.

        Maps to tool args: {restaurantId, addressId, cartItems:[{menu_item_id, quantity}]}
        Returns the raw response dict (not parsed; truth comes from a fresh get_food_cart).
        """
        raw = await self._call(
            "update_food_cart",
            {
                "restaurantId": restaurant_id,
                "addressId": address_id,
                "cartItems": [{"menu_item_id": menu_item_id, "quantity": quantity}],
            },
        )
        return raw

    async def apply_coupon(self, coupon_code: str, address_id: str) -> None:
        """Apply a coupon code to the current cart.

        The tool returns {} (empty body); truth is always in a subsequent get_food_cart.
        """
        await self._call(
            "apply_food_coupon",
            {"couponCode": coupon_code, "addressId": address_id},
        )

    async def flush_cart(self) -> None:
        """Remove all items from the Swiggy cart."""
        await self._call("flush_food_cart")
