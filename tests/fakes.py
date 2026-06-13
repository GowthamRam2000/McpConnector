"""Fake SwiggyClient seeded from real spike data for deterministic testing.

Addresses, restaurant list, menu, and cart progression are all drawn from the
2026-06-13 spike findings so tests exercise the exact shapes the production MCP returns.
"""

from swiggy_deal_finder.models import Address, MenuItem, Restaurant
from swiggy_deal_finder.swiggy_client import CartSummary

# ── Spike fixture data ──────────────────────────────────────────────────────────────────

WORK_ADDRESS_ID = "cug4elpnnp0lq52vuk10"
OTHER_ADDRESS_ID = "50918630"

ADDRESSES = [
    Address(id=WORK_ADDRESS_ID, label="Work", line="Ramapuram, Chennai"),
    Address(id=OTHER_ADDRESS_ID, label="Other", line="Adambakkam, Chennai"),
]

# Restaurants returned by search_restaurants("biryani")
RESTAURANT_AMBUR = Restaurant(
    id="62683",
    name="Ambur Star Biryani",
    distance_km=0.4,
    avg_rating=4.4,
    is_open=True,
)
RESTAURANT_FAR = Restaurant(
    id="99999",
    name="Far Away Biryani",
    distance_km=12.5,  # exceeds 7 km cap
    avg_rating=4.0,
    is_open=True,
)
RESTAURANT_CLOSED = Restaurant(
    id="77777",
    name="Closed Biryani House",
    distance_km=1.2,
    avg_rating=4.2,
    is_open=False,
)

BIRYANI_RESTAURANTS = [RESTAURANT_AMBUR, RESTAURANT_FAR, RESTAURANT_CLOSED]

RESTAURANT_DOSA = Restaurant(
    id="11111",
    name="Saravana Bhavan",
    distance_km=1.5,
    avg_rating=4.6,
    is_open=True,
)

DOSA_RESTAURANTS = [RESTAURANT_DOSA]

MENU_11111 = [
    MenuItem(id="d001", name="Plain Dosa", price=50, in_stock=True),
    MenuItem(id="d002", name="Masala Dosa", price=90, in_stock=True),
    MenuItem(id="d003", name="Ghee Dosa", price=95, in_stock=True),
    MenuItem(id="d004", name="Mini Masala Dosa", price=70, in_stock=True),
    MenuItem(id="d005", name="Idli Dosa Batter", price=65, in_stock=True),
    MenuItem(id="d006", name="Adai Dosa Mix", price=76, in_stock=True),
]

# Menus keyed by restaurant_id
MENU_62683 = [
    MenuItem(id="81574197", name="Chicken Briyani", price=350, in_stock=True),
    MenuItem(id="81574198", name="Mutton Biryani", price=450, in_stock=True),
]
MENUS: dict[str, list[MenuItem]] = {
    "62683": MENU_62683,
    "11111": MENU_11111,
}

# ── Cart state machine ──────────────────────────────────────────────────────────────────
# Tracks cart state inside FakeSwiggyClient:
#   EMPTY → after add → ADDED (coupon auto-suggested, not yet applied)
#   ADDED → after apply("SAVEBITE") → COUPON_APPLIED
#   * → after flush → EMPTY

_CART_EMPTY: CartSummary = {
    "is_empty": True,
    "to_pay": None,
    "coupon_applied": None,
    "coupon_discount": 0,
    "items": [],
}

_CART_AFTER_ADD: CartSummary = {
    "is_empty": False,
    "to_pay": 403,
    "coupon_applied": "SAVEBITE",
    "coupon_discount": 0,
    "items": [{"menu_item_id": "81574197", "name": "Chicken Briyani", "quantity": 1}],
}

_CART_AFTER_COUPON: CartSummary = {
    "is_empty": False,
    "to_pay": 321,
    "coupon_applied": "SAVEBITE",
    "coupon_discount": 80,
    "items": [{"menu_item_id": "81574197", "name": "Chicken Briyani", "quantity": 1}],
}


class FakeSwiggyClient:
    """In-memory SwiggyClient that implements the same Protocol as the real one.

    Call tracking: check `calls` list to assert what methods were called.
    State machine: `_cart_state` advances through EMPTY → ADDED → COUPON_APPLIED → EMPTY.
    """

    def __init__(self, *, start_cart: CartSummary | None = None) -> None:
        # Default: empty cart. Pass a non-empty cart to test CartGuard rejection.
        self._cart_state: CartSummary = start_cart if start_cart is not None else _CART_EMPTY
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def call_names(self) -> list[str]:
        return [c[0] for c in self.calls]

    async def get_addresses(self) -> list[Address]:
        self._record("get_addresses")
        return list(ADDRESSES)

    async def search_restaurants(self, query: str, address_id: str) -> list[Restaurant]:
        self._record("search_restaurants", query, address_id)
        q = query.lower()
        if "biryani" in q or "briyani" in q:
            return list(BIRYANI_RESTAURANTS)
        if "dosa" in q:
            return list(DOSA_RESTAURANTS)
        return []

    async def get_restaurant_menu(
        self, restaurant_id: str, address_id: str
    ) -> list[MenuItem]:
        self._record("get_restaurant_menu", restaurant_id, address_id)
        return list(MENUS.get(restaurant_id, []))

    async def get_food_cart(self, address_id: str) -> CartSummary:
        self._record("get_food_cart", address_id)
        return dict(self._cart_state)  # type: ignore[return-value]

    async def update_food_cart(
        self,
        restaurant_id: str,
        address_id: str,
        menu_item_id: str,
        quantity: int,
    ) -> dict:
        self._record("update_food_cart", restaurant_id, address_id, menu_item_id, quantity)
        if quantity > 0:
            self._cart_state = _CART_AFTER_ADD
        else:
            self._cart_state = _CART_EMPTY
        return {}

    async def apply_coupon(self, coupon_code: str, address_id: str) -> None:
        self._record("apply_coupon", coupon_code, address_id)
        # Returns {} in real MCP; side-effect: mark coupon applied
        if coupon_code == "SAVEBITE":
            self._cart_state = _CART_AFTER_COUPON

    async def flush_cart(self) -> None:
        self._record("flush_cart")
        self._cart_state = _CART_EMPTY
