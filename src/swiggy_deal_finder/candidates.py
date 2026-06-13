"""CandidateService: two-hop Swiggy dish discovery.

Flow: search_restaurants(dish) → filter ≤max_distance_km + open
      → get_restaurant_menu per candidate → match dish tokens in item name
      → choose lowest-price match per restaurant → DishHit.

Biryani/briyani: the spec-confirmed spelling variant handled by token normalisation
(both "biryani" and "briyani" canonicalize to "biryani" before matching).
"""

from swiggy_deal_finder.models import DishHit, MenuItem, Portion
from swiggy_deal_finder.swiggy_client import SwiggyClient

# Known alternate spellings that should be treated as the same token.
_SPELLING_VARIANTS: dict[str, str] = {
    "briyani": "biryani",
}

# Items whose normalised name contains any of these substrings are groceries/ingredients,
# not prepared dishes.  Excluded in all portion modes.
INGREDIENT_DENYLIST: tuple[str, ...] = (
    "batter",
    "mix",
    "premix",
    "powder",
    "paste",
    "combo",
    "pack",
    "packet",
    "kit",
    "frozen",
    "ready to cook",
    "refill",
    "masala powder",
)

# Shrink/portion tokens that indicate a smaller-than-regular serving.
SHRINK_TOKENS: tuple[str, ...] = (
    "mini",
    "half",
    "qtr",
    "quarter",
    "small",
    "single",
    "1 pc",
    "2 pc",
    "2 pcs",
    "4 pcs",
)


def _normalise(text: str) -> str:
    """Lowercase, strip, and canonicalise known spelling variants."""
    tokens = text.lower().strip().split()
    return " ".join(_SPELLING_VARIANTS.get(t, t) for t in tokens)


def _matches(query_tokens: list[str], item: MenuItem) -> bool:
    """Return True if every query token appears in the normalised item name."""
    item_tokens = _normalise(item.name).split()
    return all(qt in item_tokens for qt in query_tokens)


def _is_ingredient(normalised_name: str) -> bool:
    """Return True if the normalised item name contains any ingredient denylist entry."""
    return any(entry in normalised_name for entry in INGREDIENT_DENYLIST)


def _has_shrink_token(normalised_name: str) -> bool:
    """Return True if the normalised item name contains any shrink/portion token."""
    return any(token in normalised_name for token in SHRINK_TOKENS)


def _passes_portion_filter(
    normalised_name: str,
    portion: Portion,
    query_normalised: str,
) -> bool:
    """Return True if this item should be kept given the portion mode.

    - "any": no portion filter (ingredient denylist applied separately).
    - "mini": keep ONLY items with a shrink token.
    - "regular": exclude items with shrink tokens, UNLESS the user query itself
      contains one of those tokens (the user explicitly asked for a small portion).
    """
    if portion == "any":
        return True
    if portion == "mini":
        return _has_shrink_token(normalised_name)
    # portion == "regular"
    if not _has_shrink_token(normalised_name):
        return True
    # Item has a shrink token — keep it only if the query also contains that token.
    query_has_shrink = any(token in query_normalised for token in SHRINK_TOKENS)
    return query_has_shrink


def _best_match(
    query_tokens: list[str],
    items: list[MenuItem],
    portion: Portion = "regular",
    query_normalised: str = "",
) -> MenuItem | None:
    """Pick the lowest-price in-stock item whose name matches all query tokens.

    Applies ingredient denylist and portion filter before selecting lowest price.
    Out-of-stock items are skipped. Returns None if no item matches.
    """
    candidates = []
    for it in items:
        if not it.in_stock:
            continue
        if not _matches(query_tokens, it):
            continue
        norm = _normalise(it.name)
        if _is_ingredient(norm):
            continue
        if not _passes_portion_filter(norm, portion, query_normalised):
            continue
        candidates.append(it)

    if not candidates:
        return None
    return min(candidates, key=lambda it: it.price)


class CandidateService:
    def __init__(self, client: SwiggyClient, max_distance_km: float = 7.0) -> None:
        self._client = client
        self._max_distance_km = max_distance_km

    async def find(
        self,
        dish: str,
        address_id: str,
        portion: Portion = "regular",
    ) -> list[DishHit]:
        """Return one DishHit per restaurant that has a matching, in-stock item.

        Restaurants beyond max_distance_km or that are closed are excluded before
        any menu fetch, minimising unnecessary MCP calls.

        Applies ingredient denylist (always) and portion filter (controlled by
        `portion`): "regular" excludes shrink-sized items unless the query asks for
        them, "mini" keeps only shrink-sized items, "any" disables the portion filter.
        """
        restaurants = await self._client.search_restaurants(dish, address_id)

        # Filter: distance and open status
        reachable = [
            r for r in restaurants
            if r.distance_km <= self._max_distance_km and r.is_open
        ]

        query_normalised = _normalise(dish)
        query_tokens = query_normalised.split()
        hits: list[DishHit] = []

        for restaurant in reachable:
            items = await self._client.get_restaurant_menu(restaurant.id, address_id)
            best = _best_match(
                query_tokens, items, portion=portion, query_normalised=query_normalised
            )
            if best is None:
                continue
            hits.append(
                DishHit(
                    restaurant=restaurant,
                    item_id=best.id,
                    item_name=best.name,
                    base_price=best.price,
                )
            )

        return hits

    async def list_variants(
        self,
        dish: str,
        address_id: str,
    ) -> list[tuple[str, int, int]]:
        """Return distinct dish variant names with min/max prices across restaurants.

        Searches ≤max_distance_km open restaurants, fetches menus, collects items
        matching the dish tokens with the INGREDIENT DENYLIST applied (no portion filter
        — all sizes shown). Returns up to 20 distinct item names sorted alphabetically,
        each with (name, min_price, max_price).
        """
        restaurants = await self._client.search_restaurants(dish, address_id)
        reachable = [
            r for r in restaurants
            if r.distance_km <= self._max_distance_km and r.is_open
        ]

        query_normalised = _normalise(dish)
        query_tokens = query_normalised.split()

        # name → [prices]
        variant_prices: dict[str, list[int]] = {}

        for restaurant in reachable:
            items = await self._client.get_restaurant_menu(restaurant.id, address_id)
            for it in items:
                if not it.in_stock:
                    continue
                if not _matches(query_tokens, it):
                    continue
                norm = _normalise(it.name)
                if _is_ingredient(norm):
                    continue
                # Canonical key: use the normalised name so near-duplicates collapse,
                # but store the original name for display (first seen wins).
                if it.name not in variant_prices:
                    variant_prices[it.name] = []
                variant_prices[it.name].append(it.price)

        results = [
            (name, min(prices), max(prices))
            for name, prices in variant_prices.items()
        ]
        results.sort(key=lambda t: t[0])
        return results[:20]
