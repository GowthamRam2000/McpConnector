"""Cart-filler utilities for Swiggy Deal Finder.

When a user's dish total is below a coupon's minimum-cart threshold, these
helpers figure out the cheapest extra menu items to add to bridge the gap.
"""

import math
import re
from dataclasses import dataclass

from swiggy_deal_finder.models import MenuItem


@dataclass(frozen=True)
class OfferTerms:
    min_cart: int | None  # e.g. 249 parsed from "ABOVE ₹249"
    flat_discount: int | None  # e.g. 125 parsed from "₹125 OFF"


# Matches an integer (optionally preceded by ₹, possibly containing commas)
# that is immediately followed by the word OFF, with no leading "%" — so we
# capture only fixed-rupee discounts, not "60% OFF".
_FLAT_DISCOUNT_RE = re.compile(
    r"(?<!\d)(?<!%)\s*(?:flat\s+)?₹?\s*([\d,]+)\s+off\b",
    re.IGNORECASE,
)

# Percentage offer detector — if "%  OFF" appears, this is NOT a flat discount.
_PERCENT_RE = re.compile(r"\d+\s*%\s*off", re.IGNORECASE)

# "ABOVE ₹N" phrase — the only source of min_cart.
_ABOVE_RE = re.compile(r"above\s+₹?\s*([\d,]+)", re.IGNORECASE)


def _parse_int(s: str) -> int:
    """Strip commas/spaces and return int."""
    return int(s.replace(",", "").replace(" ", ""))


def parse_offer(offer: str | None) -> OfferTerms:
    """Parse a Swiggy headline offer string into structured terms.

    Returns OfferTerms(None, None) for anything that doesn't match the
    supported patterns (percentage offers, free-item offers, None/empty).
    """
    if not offer:
        return OfferTerms(min_cart=None, flat_discount=None)

    # Extract min_cart from "ABOVE ₹N"
    above_match = _ABOVE_RE.search(offer)
    min_cart = _parse_int(above_match.group(1)) if above_match else None

    # Reject percentage-based discounts ("60% OFF") — they are not flat rupee
    # discounts even if there's a cap amount after UPTO.
    if _PERCENT_RE.search(offer):
        flat_discount = None
    else:
        flat_match = _FLAT_DISCOUNT_RE.search(offer)
        flat_discount = _parse_int(flat_match.group(1)) if flat_match else None

    return OfferTerms(min_cart=min_cart, flat_discount=flat_discount)


def choose_filler(
    items: list[MenuItem],
    gap: int,
    exclude_ids: set[str],
) -> list[tuple[MenuItem, int]]:
    """Return the cheapest add-on plan whose total price reaches >= gap.

    Args:
        items:       Full menu item list (may include ineligible items).
        gap:         Rupees still needed to reach the coupon's min_cart.
        exclude_ids: IDs to skip (typically the user's already-chosen dish).

    Returns:
        A list of (MenuItem, quantity) lines in ascending price order, or []
        if gap <= 0 or the gap cannot be reached with available eligible items.

    Two strategies are compared and the cheaper TOTAL wins:
      (1) one item repeated as many times as needed — qty = ceil(gap / price).
          This is what makes "2× chutney @ ₹30 = ₹60" beat adding a single ₹95
          dish when the gap is small; it also covers a single item >= gap (qty 1).
      (2) a greedy mix of distinct cheapest items, one each.
    """
    if gap <= 0:
        return []

    # Eligible items: in stock, not the user's dish, and have no variants or
    # add-ons (items needing customisation can't be added to the cart
    # deterministically by an automated agent).
    eligible = [
        it
        for it in items
        if it.in_stock
        and it.id not in exclude_ids
        and not it.has_variants
        and not it.has_addons
        and it.price > 0
    ]
    if not eligible:
        return []

    # Stable sort: cheapest first, then name, then id for full determinism.
    eligible.sort(key=lambda it: (it.price, it.name, it.id))

    # --- Strategy 1: a single item repeated to reach the gap ----------------
    single: list[tuple[MenuItem, int]] | None = None
    single_total: int | None = None
    for it in eligible:
        qty = math.ceil(gap / it.price)
        total = qty * it.price
        if single_total is None or total < single_total:
            single = [(it, qty)]
            single_total = total

    # --- Strategy 2: greedy mix of distinct cheapest items ------------------
    combo: list[tuple[MenuItem, int]] = []
    running = 0
    for it in eligible:
        if running >= gap:
            break
        combo.append((it, 1))
        running += it.price
    combo_total: int | None = running if running >= gap else None

    candidates: list[tuple[int, int, list[tuple[MenuItem, int]]]] = []
    if single_total is not None:
        candidates.append((single_total, len(single or []), single or []))
    if combo_total is not None:
        candidates.append((combo_total, len(combo), combo))
    if not candidates:
        return []

    # Lowest total wins; tie-break to fewer line items (simpler for the user).
    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[0][2]
