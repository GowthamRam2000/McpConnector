"""Live end-to-end demo of the Swiggy Deal Finder engine.

Runs the full DealFinder against the REAL Swiggy MCP: searches the dish, filters
to ≤7km open restaurants, ranks by base price, then transiently carts each of the
top-N to read the post-coupon `to_pay` (flushing after each — CartGuard). Prints
the options sorted cheapest-first.

Usage:
    uv run python scripts/demo.py [dish] [top_n]
"""

import asyncio
import sys

from swiggy_deal_finder.live_client import LiveSwiggyClient
from swiggy_deal_finder.service import DealFinder

WORK_ADDRESS_ID = "cug4elpnnp0lq52vuk10"


async def main() -> None:
    dish = sys.argv[1] if len(sys.argv) > 1 else "biryani"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    address_id = sys.argv[3] if len(sys.argv) > 3 else WORK_ADDRESS_ID
    category = sys.argv[4] if len(sys.argv) > 4 else None

    async with LiveSwiggyClient() as client:
        finder = DealFinder(client)
        print(f"Finding cheapest '{dish}' (category={category}) at {address_id}...\n")
        options = await finder.find_deals(
            dish, address_id, top_n=top_n, category=category
        )
        if not options:
            print("No deals found.")
            return
        print(f"=== {len(options)} deals, cheapest final price first ===\n")
        for i, o in enumerate(options, 1):
            r = o.hit.restaurant
            rating = f"{r.avg_rating:.1f}" if r.avg_rating is not None else "N/A"
            coupon = f"{o.coupon_code} (-Rs{o.coupon_discount})" if o.coupon_code else "none"
            print(f"{i}. {r.name}  [{rating} stars, {r.distance_km:.1f} km]")
            print(
                f"   {o.hit.item_name}: base Rs{o.hit.base_price} | "
                f"coupon {coupon} | FINAL Rs{o.final_to_pay}\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
