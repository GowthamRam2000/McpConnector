"""SwiggyClient Protocol and pure parser helpers.

The Protocol defines the async interface that both the live MCP adapter and the
FakeSwiggyClient (tests) must satisfy. Parser functions are pure and independently
testable — no network, no state.

Cart shape (CartSummary) is a TypedDict because the cart is a transient probe artifact,
not a persisted domain object; typed dict is lighter than a full Pydantic model here.
"""

from typing import Any, Protocol, TypedDict

from swiggy_deal_finder.models import Address, MenuItem, Restaurant


class CartItem(TypedDict):
    menu_item_id: str
    name: str
    quantity: int


class CartSummary(TypedDict):
    is_empty: bool
    to_pay: int | None
    coupon_applied: str | None
    coupon_discount: int
    items: list[CartItem]




def parse_cart(raw: dict[str, Any]) -> CartSummary:
    """Map a raw get_food_cart (or empty {}) response to CartSummary.

    Tolerates:
      - {}          → from apply_food_coupon / fetch_food_coupons (always re-read after)
      - {data: null} → empty cart from get_food_cart
      - full cart   → populated data dict
    """
    data = raw.get("data")
    if data is None:
        return CartSummary(
            is_empty=True,
            to_pay=None,
            coupon_applied=None,
            coupon_discount=0,
            items=[],
        )

    pricing = data.get("pricing", {})
    offers = data.get("offers", {})
    raw_items: list[dict[str, Any]] = data.get("items", [])

    items: list[CartItem] = [
        CartItem(
            menu_item_id=str(it.get("menu_item_id", "")),
            name=str(it.get("name", "")),
            quantity=int(it.get("quantity", 1)),
        )
        for it in raw_items
    ]

    return CartSummary(
        is_empty=False,
        to_pay=int(pricing["to_pay"]) if "to_pay" in pricing else None,
        coupon_applied=offers.get("coupon_applied") or None,
        coupon_discount=int(offers.get("coupon_discount", 0)),
        items=items,
    )


def parse_restaurants(raw: list[dict[str, Any]]) -> list[Restaurant]:
    """Map raw search_restaurants results to Restaurant models.

    Open-flag inference (two shapes observed from live Swiggy MCP):
      - New shape: `availabilityStatus` string — "OPEN" means open, anything else closed.
      - Legacy shape: `availability.opened` bool — used when availabilityStatus absent.
      - Default True when neither key is present (optimistic; documented assumption).
    """
    results: list[Restaurant] = []
    for entry in raw:
        try:
            availability_status = entry.get("availabilityStatus")
            if availability_status is not None:
                # Live MCP returns a string: "OPEN", "CLOSED", or "UNAVAILABLE".
                is_open: bool = availability_status == "OPEN"
            else:
                availability = entry.get("availability", {}) or {}
                # opened key may be missing; default True per documented assumption.
                is_open = bool(availability.get("opened", True))
            avg_rating_raw = entry.get("avgRating")
            avg_rating: float | None = (
                float(avg_rating_raw) if avg_rating_raw is not None else None
            )
            results.append(
                Restaurant(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    distance_km=float(entry["distanceKm"]),
                    avg_rating=avg_rating,
                    is_open=is_open,
                )
            )
        except (KeyError, TypeError, ValueError):
            # Skip an entry missing the fields we rank on (e.g. no distanceKm)
            # rather than failing the whole search.
            continue
    return results


def parse_menu_items(raw: list[dict[str, Any]]) -> list[MenuItem]:
    """Map raw get_restaurant_menu items to MenuItem models.

    inStock is 0/1 in the MCP response; absent key defaults to 1 (in-stock).
    hasVariants / hasAddons default to False when absent (tolerant — new fields).
    """
    results: list[MenuItem] = []
    for entry in raw:
        try:
            in_stock = bool(int(entry.get("inStock", 1)))
            results.append(
                MenuItem(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    price=int(entry["price"]),
                    in_stock=in_stock,
                    has_variants=bool(entry.get("hasVariants", False)),
                    has_addons=bool(entry.get("hasAddons", False)),
                )
            )
        except (KeyError, TypeError, ValueError):
            # Skip an item without a usable id/name/price (e.g. variant-only items
            # with no top-level price) rather than failing the whole menu parse.
            continue
    return results


class SwiggyClient(Protocol):
    """Async interface for all Swiggy MCP interactions.

    Implementations: live MCP adapter (Phase 4), FakeSwiggyClient (tests).
    Never call place_food_order through this interface.
    """

    async def get_addresses(self) -> list[Address]: ...

    async def search_restaurants(
        self, query: str, address_id: str, offset: int = 0
    ) -> list[Restaurant]: ...

    async def get_restaurant_menu(
        self, restaurant_id: str, address_id: str
    ) -> list[MenuItem]: ...

    async def get_food_cart(self, address_id: str) -> CartSummary: ...

    async def update_food_cart(
        self,
        restaurant_id: str,
        address_id: str,
        menu_item_id: str,
        quantity: int,
    ) -> dict: ...

    async def apply_coupon(self, coupon_code: str, address_id: str) -> None: ...

    async def flush_cart(self) -> None: ...
