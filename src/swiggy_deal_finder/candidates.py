"""CandidateService: two-hop Swiggy dish discovery.

Flow: _search_terms(dish, category) → try each term with search_restaurants
      → filter ≤max_distance_km + open (stop at first term yielding ≥1 restaurant)
      → get_restaurant_menu per candidate (concurrent, bounded) → match dish tokens
      → choose lowest-price match per restaurant → DishHit.

Why _search_terms exists: Swiggy's search_restaurants is DUAL-BEHAVIOR.
Broad category/cuisine words ("dosa", "biryani", "pizza") return real Restaurant
objects with distanceKm/avgRating/availabilityStatus.  Specific dish phrases
("ghee roast", "masala dosa") return dataless dish-suggestions that parse_restaurants
drops, yielding 0 restaurants.  The fix: let the caller supply the broad `category`
(e.g. "dosa" for "ghee roast") and use it as the search term, then match the full
specific dish inside each restaurant's menu.  Last-token broadening is the automatic
fallback when no category is supplied.

Biryani/briyani: the spec-confirmed spelling variant handled by token normalisation
(both "biryani" and "briyani" canonicalize to "biryani" before matching).
"""

import asyncio

from swiggy_deal_finder.models import DishHit, MenuItem, Portion
from swiggy_deal_finder.swiggy_client import SwiggyClient

# Cap concurrent menu fetches to stay well under Swiggy's read-rate limit (~120/min).
# Read-only menu fetches are independent and idempotent, safe to run concurrently.
MENU_FETCH_CONCURRENCY = 8

MAX_SEARCH_PAGES = 3

_SPELLING_VARIANTS: dict[str, str] = {
    "briyani": "biryani",
}


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

# Generic filler tokens: digits/numerals, punctuation separators, and quantity/style
# words that add no semantic meaning.  Used by tight matching to allow items like
# "Ghee Dosa - Plain (2 Nos)" to still be an exact match for "ghee dosa".
# NO dish-specific or size words here.
_FILLER_TOKENS: frozenset[str] = frozenset({
    "nos", "no", "pc", "pcs", "piece", "pieces", "plain", "regular",
    # Punctuation that str.split() breaks out as standalone tokens
    "-", "/", "|", "&", "+", "(",  ")",
    # Single digit tokens that appear as quantity suffixes (e.g. "1", "2")
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
})


def _normalise(text: str) -> str:
    """Lowercase, strip, and canonicalise known spelling variants."""
    tokens = text.lower().strip().split()
    return " ".join(_SPELLING_VARIANTS.get(t, t) for t in tokens)


def _search_terms(dish: str, category: str | None) -> list[str]:
    """Return ordered, de-duplicated search terms to try with search_restaurants.

    Priority:
      1. category — if given; broad words are the only reliable way to get restaurants
         back from Swiggy when the dish is a specific phrase (e.g. "ghee roast").
      2. full dish — verbatim; works when the dish itself is a broad category word.
      3. last token of dish — automatic broadening fallback; e.g. "masala dosa" → "dosa".
         Omitted when it equals the full dish (single-word dish).

    Duplicates are removed while preserving order.
    """
    seen: set[str] = set()
    terms: list[str] = []

    def _add(term: str) -> None:
        t = term.strip()
        if t and t not in seen:
            seen.add(t)
            terms.append(t)

    if category:
        _add(category)
    _add(dish)
    last_token = dish.strip().split()[-1] if dish.strip() else ""
    if last_token != dish.strip():
        _add(last_token)

    return terms


def _matches(query_tokens: list[str], item: MenuItem) -> bool:
    """Return True if every query token appears in the normalised item name."""
    item_tokens = _normalise(item.name).split()
    return all(qt in item_tokens for qt in query_tokens)


def _is_tight_match(query_tokens: list[str], item: MenuItem) -> bool:
    """Return True if the item is a tight like-for-like match for the query.

    Tight = matches all query tokens AND has no extra meaningful tokens beyond the
    query (ignoring generic FILLER tokens like "plain", "nos", "pcs", digits).
    This prevents "Ghee Podi Dosa" from matching a search for "Ghee Dosa" because
    "podi" is a meaningful extra token.
    """
    if not _matches(query_tokens, item):
        return False
    item_tokens = set(_normalise(item.name).split())
    query_set = set(query_tokens)
    # Extra tokens = item tokens not in query and not in filler set
    extra = item_tokens - query_set - _FILLER_TOKENS
    return len(extra) == 0


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
    if not _has_shrink_token(normalised_name):
        return True
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


MAX_MENU_FETCH = 20


def _prune(restaurants: list, min_rating: float | None) -> list:
    """Apply the caller-supplied rating floor (if any), then cap the menu-fetch
    fan-out to the nearest MAX_MENU_FETCH restaurants (a bandwidth/latency bound).

    The rating floor is a USER preference — we never assume a default here. A
    restaurant with no rating is kept even when a floor is given (rare; avoids
    over-pruning new places).
    """
    if min_rating is not None:
        restaurants = [
            r for r in restaurants if r.avg_rating is None or r.avg_rating >= min_rating
        ]
    return sorted(restaurants, key=lambda r: r.distance_km)[:MAX_MENU_FETCH]


class CandidateService:
    def __init__(self, client: SwiggyClient, max_distance_km: float = 7.0) -> None:
        self._client = client
        self._max_distance_km = max_distance_km

    async def find(
        self,
        dish: str,
        address_id: str,
        portion: Portion = "regular",
        category: str | None = None,
        min_rating: float | None = None,
        restaurant_name: str | None = None,
    ) -> list[DishHit]:
        """Return one DishHit per restaurant that has a matching, in-stock item.

        Restaurants beyond max_distance_km or that are closed are excluded before
        any menu fetch, minimising unnecessary MCP calls.

        Applies ingredient denylist (always) and portion filter (controlled by
        `portion`): "regular" excludes shrink-sized items unless the query asks for
        them, "mini" keeps only shrink-sized items, "any" disables the portion filter.

        Tight matching: per-restaurant best matches are selected via tight like-for-like
        matching (no extra meaningful tokens beyond the query).  If at least one tight
        match exists across all restaurants, only tight matches are returned.  When zero
        tight matches are found anywhere, falls back to loose (superset) matching so the
        user still gets results.

        category: broad food category for the restaurant search (e.g. "dosa" when
        dish is "ghee roast").  When omitted, the full dish is tried first, then the
        last token as a fallback.  See _search_terms for the full priority order.
        restaurant_name: when given, only restaurants whose name contains this string
        (case-insensitive substring) are probed; menus for others are never fetched.
        None = no filter (default).
        """
        reachable = _prune(
            await self._find_reachable(dish, address_id, category), min_rating
        )

        # Filter to a named restaurant before fetching menus (saves MCP calls).
        if restaurant_name is not None:
            needle = restaurant_name.lower()
            reachable = [r for r in reachable if needle in r.name.lower()]

        query_normalised = _normalise(dish)
        query_tokens = query_normalised.split()

        # Fetch all menus concurrently; gather preserves input order for determinism.
        sem = asyncio.Semaphore(MENU_FETCH_CONCURRENCY)

        async def _fetch(restaurant_id: str) -> list[MenuItem]:
            async with sem:
                return await self._client.get_restaurant_menu(restaurant_id, address_id)

        menus = await asyncio.gather(*(_fetch(r.id) for r in reachable))

        # Collect best matches under TIGHT matching first; fall back to loose if none.
        tight_hits: list[DishHit] = []
        loose_hits: list[DishHit] = []

        for restaurant, items in zip(reachable, menus, strict=True):
            # Tight best match: same filters as _best_match but requires _is_tight_match.
            tight_candidates = [
                it for it in items
                if it.in_stock
                and _is_tight_match(query_tokens, it)
                and not _is_ingredient(_normalise(it.name))
                and _passes_portion_filter(_normalise(it.name), portion, query_normalised)
            ]
            if tight_candidates:
                best_tight = min(tight_candidates, key=lambda it: it.price)
                tight_hits.append(DishHit(
                    restaurant=restaurant,
                    item_id=best_tight.id,
                    item_name=best_tight.name,
                    base_price=best_tight.price,
                ))

            loose_best = _best_match(
                query_tokens, items, portion=portion, query_normalised=query_normalised
            )
            if loose_best is not None:
                loose_hits.append(DishHit(
                    restaurant=restaurant,
                    item_id=loose_best.id,
                    item_name=loose_best.name,
                    base_price=loose_best.price,
                ))

        # Global fallback: use tight results when any tight match exists; else loose.
        return tight_hits if tight_hits else loose_hits

    async def list_variants(
        self,
        dish: str,
        address_id: str,
        category: str | None = None,
    ) -> list[tuple[str, int, int, bool]]:
        """Return distinct dish variant names with min/max prices and options flag.

        Searches ≤max_distance_km open restaurants, fetches menus (concurrently),
        collects items matching the dish tokens with the INGREDIENT DENYLIST applied
        (no portion filter — all sizes shown, loose/superset matching — all variants
        shown so the user can pick).  Returns up to 20 distinct item names sorted
        alphabetically, each as (name, min_price, max_price, has_options) where
        has_options=True signals the item has size variants or add-on choices.

        category: broad food category for the restaurant search.  See find() for details.
        """
        reachable = await self._find_reachable(dish, address_id, category)
        # Scan only the nearest MAX_MENU_FETCH restaurants to bound menu-fetch reads.
        reachable = sorted(reachable, key=lambda r: r.distance_km)[:MAX_MENU_FETCH]

        query_normalised = _normalise(dish)
        query_tokens = query_normalised.split()

        # name → [prices]; name → has_options (True if any matching item flags variants/addons)
        variant_prices: dict[str, list[int]] = {}
        variant_has_options: dict[str, bool] = {}

        # Read-only menu fetches are independent; run them concurrently.
        sem = asyncio.Semaphore(MENU_FETCH_CONCURRENCY)

        async def _fetch(restaurant_id: str) -> list[MenuItem]:
            async with sem:
                return await self._client.get_restaurant_menu(restaurant_id, address_id)

        all_menus = await asyncio.gather(*(_fetch(r.id) for r in reachable))

        for items in all_menus:
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
                    variant_has_options[it.name] = False
                variant_prices[it.name].append(it.price)
                # Accumulate: once True, always True (any matching item triggers flag)
                if it.has_variants or it.has_addons:
                    variant_has_options[it.name] = True

        results = [
            (name, min(prices), max(prices), variant_has_options[name])
            for name, prices in variant_prices.items()
        ]
        results.sort(key=lambda t: t[0])
        return results[:20]

    async def _find_reachable(
        self,
        dish: str,
        address_id: str,
        category: str | None,
    ) -> list:
        """Try each search term in priority order; paginate the first term that
        yields ≥1 reachable (open + within distance) result on page 0.

        Pagination: for the winning term, fetch offsets 0, 10, 20, … up to
        MAX_SEARCH_PAGES pages total, or until a page returns 0 restaurants.
        Results are deduplicated by restaurant id across pages.

        Returns [] if no term yields any reachable restaurants.
        """
        _page_size = 10

        for term in _search_terms(dish, category):
            # --- page 0 probe (also determines if this term is the winner) ---
            page0 = await self._client.search_restaurants(term, address_id, offset=0)
            reachable_page0 = [
                r for r in page0
                if r.distance_km <= self._max_distance_km and r.is_open
            ]
            if not reachable_page0:
                # This term yielded nothing reachable on page 0; try the next term.
                continue

            # This term is the winner — paginate it.
            seen_ids: set[str] = {r.id for r in reachable_page0}
            accumulated = list(reachable_page0)

            for page_num in range(1, MAX_SEARCH_PAGES):
                offset = page_num * _page_size
                page = await self._client.search_restaurants(term, address_id, offset=offset)
                if not page:
                    # Empty page signals end of results.
                    break
                for r in page:
                    if r.distance_km <= self._max_distance_km and r.is_open:
                        if r.id not in seen_ids:
                            seen_ids.add(r.id)
                            accumulated.append(r)

            return accumulated

        return []
