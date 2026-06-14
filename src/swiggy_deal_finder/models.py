"""Domain models for Swiggy Deal Finder.

All models are immutable (frozen) so they can be safely passed across async boundaries.
"""

from typing import Literal

from pydantic import BaseModel

Portion = Literal["regular", "mini", "any"]


class Address(BaseModel, frozen=True):
    id: str
    label: str
    line: str


class Restaurant(BaseModel, frozen=True):
    id: str
    name: str
    distance_km: float
    # avgRating absent in some search results — default None
    avg_rating: float | None
    # is_open inferred from availability flags; default True when flag absent (optimistic;
    # logged at call site so callers can warn if they want stricter behaviour)
    is_open: bool
    # Headline offer string from search (e.g. "₹125 OFF ABOVE ₹249"). Surfaced so the
    # agent can tell the user a min-cart threshold exists and ask about buying more.
    offer: str | None = None


class MenuItem(BaseModel, frozen=True):
    id: str
    name: str
    # price in integer rupees (Swiggy always sends int; reject float to surface bad data)
    price: int
    # inStock comes as 0/1 from MCP; validator coerces via pydantic int→bool
    in_stock: bool
    # hasVariants / hasAddons from Swiggy menu; default False (tolerant — missing → False)
    has_variants: bool = False
    has_addons: bool = False


class DishHit(BaseModel, frozen=True):
    restaurant: Restaurant
    item_id: str
    item_name: str
    base_price: int


class PricedOption(BaseModel, frozen=True):
    hit: DishHit
    coupon_code: str | None
    coupon_discount: int
    # final_to_pay is the cart total for `quantity` items after the coupon (not per-unit).
    final_to_pay: int
    quantity: int = 1
