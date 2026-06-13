"""Read-only smoke test for LiveSwiggyClient against the real Swiggy MCP.

Validates the spawned mcp-remote session, OAuth, and the live response envelopes
WITHOUT mutating the cart (no add/apply/flush/order). Run:

    uv run python scripts/smoke_live.py

Uses the Work address from the spike. Prints compact counts + a sample so we can
confirm the bridge before wiring Claude Desktop.
"""

import asyncio

from swiggy_deal_finder.live_client import LiveSwiggyClient

WORK_ADDRESS_ID = "cug4elpnnp0lq52vuk10"
DISH = "biryani"
SAMPLE_RESTAURANT = "62683"  # Ambur Star Briyani


async def main() -> None:
    async with LiveSwiggyClient() as client:
        addresses = await client.get_addresses()
        print(f"[get_addresses] {len(addresses)} addresses")
        for a in addresses:
            print(f"  - id={a.id!r} label={a.label!r} :: {a.line[:60]}")

        restaurants = await client.search_restaurants(DISH, WORK_ADDRESS_ID)
        print(f"\n[search_restaurants {DISH!r}] {len(restaurants)} restaurants")
        for r in restaurants[:6]:
            print(
                f"  - {r.name[:32]:32}  {r.distance_km:>4} km  "
                f"rating={r.avg_rating}  open={r.is_open}  id={r.id}"
            )
        within7 = [r for r in restaurants if r.distance_km <= 7 and r.is_open]
        print(f"  -> {len(within7)} open within 7km")

        menu = await client.get_restaurant_menu(SAMPLE_RESTAURANT, WORK_ADDRESS_ID)
        biryani_items = [
            m for m in menu if "biryani" in m.name.lower() or "briyani" in m.name.lower()
        ]
        print(f"\n[get_restaurant_menu {SAMPLE_RESTAURANT}] {len(menu)} items, "
              f"{len(biryani_items)} biryani-matching")
        for m in biryani_items[:5]:
            print(f"  - {m.name[:40]:40}  ₹{m.price}  in_stock={m.in_stock}  id={m.id}")

        cart = await client.get_food_cart(WORK_ADDRESS_ID)
        print(f"\n[get_food_cart] is_empty={cart['is_empty']}  to_pay={cart['to_pay']}")


if __name__ == "__main__":
    asyncio.run(main())
