"""CandidateService: two-hop Swiggy dish discovery.

Flow: search_restaurants(dish) → filter ≤max_distance_km + open
      → get_restaurant_menu per candidate → match dish tokens in item name
      → choose lowest-price match per restaurant → DishHit.

Biryani/briyani: the spec-confirmed spelling variant handled by token normalisation
(both "biryani" and "briyani" canonicalize to "biryani" before matching).
"""

from swiggy_deal_finder.models import DishHit, MenuItem
from swiggy_deal_finder.swiggy_client import SwiggyClient

# Known alternate spellings that should be treated as the same token.
_SPELLING_VARIANTS: dict[str, str] = {
    "briyani": "biryani",
}


def _normalise(text: str) -> str:
    """Lowercase, strip, and canonicalise known spelling variants."""
    tokens = text.lower().strip().split()
    return " ".join(_SPELLING_VARIANTS.get(t, t) for t in tokens)


def _matches(query_tokens: list[str], item: MenuItem) -> bool:
    """Return True if every query token appears in the normalised item name."""
    item_tokens = _normalise(item.name).split()
    return all(qt in item_tokens for qt in query_tokens)


def _best_match(query_tokens: list[str], items: list[MenuItem]) -> MenuItem | None:
    """Pick the lowest-price in-stock item whose name matches all query tokens.

    Out-of-stock items are skipped. Returns None if no item matches.
    """
    candidates = [
        it for it in items if it.in_stock and _matches(query_tokens, it)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda it: it.price)


class CandidateService:
    def __init__(self, client: SwiggyClient, max_distance_km: float = 7.0) -> None:
        self._client = client
        self._max_distance_km = max_distance_km

    async def find(self, dish: str, address_id: str) -> list[DishHit]:
        """Return one DishHit per restaurant that has a matching, in-stock item.

        Restaurants beyond max_distance_km or that are closed are excluded before
        any menu fetch, minimising unnecessary MCP calls.
        """
        restaurants = await self._client.search_restaurants(dish, address_id)

        # Filter: distance and open status
        reachable = [
            r for r in restaurants
            if r.distance_km <= self._max_distance_km and r.is_open
        ]

        query_tokens = _normalise(dish).split()
        hits: list[DishHit] = []

        for restaurant in reachable:
            items = await self._client.get_restaurant_menu(restaurant.id, address_id)
            best = _best_match(query_tokens, items)
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
