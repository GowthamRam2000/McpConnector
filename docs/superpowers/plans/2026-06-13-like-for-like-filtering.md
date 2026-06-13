# Like-For-Like Dish Filtering & Disambiguation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ingredient/portion filtering to the dish matcher so junk items are excluded, and add a `list_dish_variants` MCP tool so Claude can ask users to pick a specific dish variant before pricing.

**Architecture:** Feature 1 adds an ingredient DENYLIST and a portion filter inside `candidates.py` (`_best_match` and a new helper), threaded through `CandidateService.find(dish, address_id, portion="regular")`. Feature 2 adds `CandidateService.list_variants(dish, address_id)` + a new `list_dish_variants` MCP tool in `server.py`, and threads `portion` through `DealFinder.find_deals` and the `find_deals` MCP tool.

**Tech Stack:** Python 3.14, uv, ruff, pytest (asyncio_mode=auto), pydantic, FastMCP.

---

## File Map

| File | Change |
|---|---|
| `src/swiggy_deal_finder/models.py` | Add `Portion = Literal["regular", "mini", "any"]` |
| `src/swiggy_deal_finder/candidates.py` | Add `INGREDIENT_DENYLIST`, `SHRINK_TOKENS`, `_is_ingredient`, `_is_shrink`, filter in `_best_match`; add `portion` param to `CandidateService.find`; add `list_variants` method |
| `src/swiggy_deal_finder/service.py` | Thread `portion` param through `DealFinder.find_deals` |
| `src/swiggy_deal_finder/server.py` | Thread `portion` param through `find_deals` MCP tool; add `list_dish_variants` MCP tool |
| `tests/fakes.py` | Add dosa restaurant + menu; extend `search_restaurants` to handle "dosa" |
| `tests/test_candidates.py` | Add ingredient filter tests, portion filter tests, `list_variants` tests |
| `tests/test_service.py` | Add test that `portion` is threaded through to `find_deals` |

---

## Task 1: Add `Portion` type alias to models.py

**Files:**
- Modify: `src/swiggy_deal_finder/models.py`

- [ ] **Step 1.1: Write the failing test (import check)**

In `tests/test_candidates.py`, add at the top (do NOT run yet — just write):

```python
from swiggy_deal_finder.models import Portion
```

Run: `uv run pytest tests/test_candidates.py -q 2>&1 | head -5`
Expected: `ImportError: cannot import name 'Portion'`

- [ ] **Step 1.2: Add `Portion` to models.py**

Open `src/swiggy_deal_finder/models.py`. Add these two lines after the existing imports:

```python
from typing import Literal

Portion = Literal["regular", "mini", "any"]
```

The full file top becomes:

```python
"""Domain models for Swiggy Deal Finder.

All models are immutable (frozen) so they can be safely passed across async boundaries.
"""

from typing import Literal

from pydantic import BaseModel

Portion = Literal["regular", "mini", "any"]
```

- [ ] **Step 1.3: Verify import resolves**

Run: `uv run python -c "from swiggy_deal_finder.models import Portion; print(Portion)"`
Expected output: `typing.Literal['regular', 'mini', 'any']`

- [ ] **Step 1.4: Run full suite to confirm nothing broken**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: `67 passed`

- [ ] **Step 1.5: Commit**

```bash
git add src/swiggy_deal_finder/models.py
git commit -m "feat(models): add Portion Literal type alias"
```

---

## Task 2: Extend fakes.py with dosa restaurant and menu

**Files:**
- Modify: `tests/fakes.py`

Dosa items needed:
- `"Plain Dosa"` price=50, in_stock=True
- `"Masala Dosa"` price=90, in_stock=True
- `"Ghee Dosa"` price=95, in_stock=True
- `"Mini Masala Dosa"` price=70, in_stock=True
- `"Idli Dosa Batter"` price=65, in_stock=True  ← ingredient denylist hit
- `"Adai Dosa Mix"` price=76, in_stock=True     ← ingredient denylist hit

- [ ] **Step 2.1: Add dosa restaurant constant and menu to fakes.py**

In `tests/fakes.py`, after the `BIRYANI_RESTAURANTS` block, add:

```python
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
```

- [ ] **Step 2.2: Add dosa entries to the MENUS dict**

Change the existing `MENUS` definition from:

```python
MENUS: dict[str, list[MenuItem]] = {
    "62683": MENU_62683,
}
```

to:

```python
MENUS: dict[str, list[MenuItem]] = {
    "62683": MENU_62683,
    "11111": MENU_11111,
}
```

- [ ] **Step 2.3: Extend `search_restaurants` to return dosa fixtures**

In the `FakeSwiggyClient.search_restaurants` method, change:

```python
    async def search_restaurants(self, query: str, address_id: str) -> list[Restaurant]:
        self._record("search_restaurants", query, address_id)
        # Return biryani fixtures for any query containing "biryani" or "briyani"
        q = query.lower()
        if "biryani" in q or "briyani" in q:
            return list(BIRYANI_RESTAURANTS)
        return []
```

to:

```python
    async def search_restaurants(self, query: str, address_id: str) -> list[Restaurant]:
        self._record("search_restaurants", query, address_id)
        q = query.lower()
        if "biryani" in q or "briyani" in q:
            return list(BIRYANI_RESTAURANTS)
        if "dosa" in q:
            return list(DOSA_RESTAURANTS)
        return []
```

- [ ] **Step 2.4: Run tests to confirm no regressions**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: `67 passed`

- [ ] **Step 2.5: Commit**

```bash
git add tests/fakes.py
git commit -m "test(fakes): add dosa restaurant and menu fixture with ingredient/portion items"
```

---

## Task 3: Add ingredient denylist + portion filter to candidates.py

**Files:**
- Modify: `src/swiggy_deal_finder/candidates.py`

The ingredient denylist tokens (any of these in the normalised item name → skip):
`batter`, `mix`, `premix`, `powder`, `paste`, `combo`, `pack`, `packet`, `kit`, `frozen`, `ready to cook`, `refill`, `masala powder`

The shrink/portion tokens (for portion="regular" → exclude; for portion="mini" → require):
`mini`, `half`, `qtr`, `quarter`, `small`, `single`, `1 pc`, `2 pc`, `2 pcs`, `4 pcs`

Note: multi-word tokens (`ready to cook`, `masala powder`, `1 pc`, `2 pc`, `2 pcs`, `4 pcs`) require substring matching on the full normalised item name string, not just token-by-token. The implementation must handle both single-token and multi-word deny-list entries.

- [ ] **Step 3.1: Write the failing tests first**

Add this class to `tests/test_candidates.py`:

```python
class TestIngredientFilter:
    async def test_ingredient_item_excluded_by_default(self):
        """Idli Dosa Batter must never appear in results (ingredient denylist)."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("dosa", WORK_ADDRESS_ID)
        item_names = [h.item_name for h in hits]
        assert "Idli Dosa Batter" not in item_names
        assert "Adai Dosa Mix" not in item_names

    async def test_ingredient_item_excluded_with_portion_any(self):
        """portion='any' disables portion filter but ingredient denylist still applies."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("dosa", WORK_ADDRESS_ID, portion="any")
        item_names = [h.item_name for h in hits]
        assert "Idli Dosa Batter" not in item_names
        assert "Adai Dosa Mix" not in item_names

    async def test_portion_regular_excludes_mini(self):
        """portion='regular' (default) must exclude 'Mini Masala Dosa'."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("dosa", WORK_ADDRESS_ID, portion="regular")
        item_names = [h.item_name for h in hits]
        assert "Mini Masala Dosa" not in item_names

    async def test_portion_any_includes_mini(self):
        """portion='any' keeps mini items (ingredient denylist still applies)."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("dosa", WORK_ADDRESS_ID, portion="any")
        item_names = [h.item_name for h in hits]
        assert "Mini Masala Dosa" in item_names

    async def test_portion_mini_returns_only_mini_items(self):
        """portion='mini' keeps ONLY items containing a shrink token."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("dosa", WORK_ADDRESS_ID, portion="mini")
        item_names = [h.item_name for h in hits]
        assert item_names == ["Mini Masala Dosa"]

    async def test_specific_query_masala_dosa_matches_only_masala_dosa(self):
        """Query 'masala dosa' must match Masala Dosa (and Mini Masala Dosa is excluded
        by portion=regular). Plain Dosa and Ghee Dosa do not contain 'masala' token."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("masala dosa", WORK_ADDRESS_ID, portion="regular")
        item_names = [h.item_name for h in hits]
        assert item_names == ["Masala Dosa"]

    async def test_query_with_mini_token_keeps_mini_items_under_regular(self):
        """If user query contains 'mini', portion=regular still shows mini items
        because the user explicitly asked for mini."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("mini dosa", WORK_ADDRESS_ID, portion="regular")
        item_names = [h.item_name for h in hits]
        assert "Mini Masala Dosa" in item_names
```

Run: `uv run pytest tests/test_candidates.py -q 2>&1 | tail -10`
Expected: multiple failures — `find()` doesn't accept `portion` yet, and no filtering logic exists.

- [ ] **Step 3.2: Add denylist constants and filter helpers to candidates.py**

Replace the entire `candidates.py` content with:

```python
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
            best = _best_match(query_tokens, items, portion=portion, query_normalised=query_normalised)
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
```

- [ ] **Step 3.3: Run new tests**

Run: `uv run pytest tests/test_candidates.py -q 2>&1 | tail -15`
Expected: all tests pass including the new `TestIngredientFilter` class.

- [ ] **Step 3.4: Run full suite + ruff**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: `74 passed` (67 + 7 new)

Run: `uv run ruff check . 2>&1`
Expected: no output (clean).

- [ ] **Step 3.5: Commit**

```bash
git add src/swiggy_deal_finder/candidates.py tests/test_candidates.py
git commit -m "feat(candidates): ingredient denylist, portion filter, list_variants"
```

---

## Task 4: Add `list_variants` tests

**Files:**
- Modify: `tests/test_candidates.py`

- [ ] **Step 4.1: Write `list_variants` tests**

Add this class to `tests/test_candidates.py`:

```python
class TestListVariants:
    async def test_returns_distinct_names_sorted(self):
        """list_variants returns each distinct item name once, sorted alphabetically."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("dosa", WORK_ADDRESS_ID)
        names = [v[0] for v in variants]
        assert names == sorted(names)
        # No duplicates
        assert len(names) == len(set(names))

    async def test_excludes_ingredient_items(self):
        """Ingredient denylist items (Batter, Mix) must not appear in variants."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("dosa", WORK_ADDRESS_ID)
        names = [v[0] for v in variants]
        assert "Idli Dosa Batter" not in names
        assert "Adai Dosa Mix" not in names

    async def test_includes_mini_items(self):
        """Mini items are NOT filtered out in list_variants (no portion filter)."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("dosa", WORK_ADDRESS_ID)
        names = [v[0] for v in variants]
        assert "Mini Masala Dosa" in names

    async def test_price_range_correct(self):
        """Each variant carries the correct (min_price, max_price)."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("dosa", WORK_ADDRESS_ID)
        by_name = {v[0]: (v[1], v[2]) for v in variants}
        # All items appear at a single restaurant so min==max
        assert by_name["Plain Dosa"] == (50, 50)
        assert by_name["Masala Dosa"] == (90, 90)
        assert by_name["Ghee Dosa"] == (95, 95)
        assert by_name["Mini Masala Dosa"] == (70, 70)

    async def test_returns_empty_for_unknown_dish(self):
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("sushi", WORK_ADDRESS_ID)
        assert variants == []
```

- [ ] **Step 4.2: Run new tests**

Run: `uv run pytest tests/test_candidates.py::TestListVariants -v 2>&1 | tail -15`
Expected: all 5 pass.

- [ ] **Step 4.3: Run full suite**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: `79 passed`

- [ ] **Step 4.4: Commit**

```bash
git add tests/test_candidates.py
git commit -m "test(candidates): list_variants test coverage"
```

---

## Task 5: Thread `portion` through DealFinder.find_deals

**Files:**
- Modify: `src/swiggy_deal_finder/service.py`

- [ ] **Step 5.1: Write the failing test**

Add to `tests/test_service.py`:

```python
    async def test_portion_threaded_to_candidates(self):
        """find_deals with portion='mini' should filter to mini items only.
        With the dosa menu, portion='mini' returns only Mini Masala Dosa."""
        client = FakeSwiggyClient()
        finder = DealFinder(client)
        # With portion='mini', only Mini Masala Dosa survives
        results = await finder.find_deals("dosa", WORK_ADDRESS_ID, portion="mini")
        # Mini Masala Dosa at price 70 should be the only hit
        assert len(results) == 1
        assert results[0].hit.item_name == "Mini Masala Dosa"
```

Run: `uv run pytest tests/test_service.py::TestDealFinder::test_portion_threaded_to_candidates -v 2>&1 | tail -10`
Expected: `FAILED` — `find_deals` doesn't accept `portion` yet.

- [ ] **Step 5.2: Update DealFinder.find_deals signature**

In `src/swiggy_deal_finder/service.py`, change the method signature and docstring and the call to `self._candidates.find`:

```python
    async def find_deals(
        self,
        dish: str,
        address_id: str,
        top_n: int = 5,
        portion: str = "regular",
    ) -> list[PricedOption]:
        """Return up to top_n PricedOptions for dish, ranked cheapest-first.

        Raises CartNotEmptyError up front if the user's cart is non-empty (pricing
        needs an empty cart) so the caller can tell the user to clear it — rather than
        returning a confusing empty result. Individual candidates that fail pricing
        with a transient error are skipped with a warning — a partial result beats a
        full abort.

        portion: "regular" (default) excludes shrink-sized items unless the query asks
        for them; "mini" keeps only shrink-sized items; "any" disables the portion filter.
        """
        # Fail fast on a busy cart before doing any search/pricing work.
        cart = await self._client.get_food_cart(address_id)
        if not cart["is_empty"]:
            raise CartNotEmptyError(
                "Swiggy cart is not empty. Clear your cart before running Deal Finder."
            )

        hits = await self._candidates.find(dish, address_id, portion=portion)  # type: ignore[arg-type]
```

(Leave the rest of the method body unchanged.)

- [ ] **Step 5.3: Run the new test**

Run: `uv run pytest tests/test_service.py -v 2>&1 | tail -15`
Expected: all pass including `test_portion_threaded_to_candidates`.

Note: the new test relies on the dosa fake menu added in Task 2. Since `FakeSwiggyClient.apply_coupon` only handles `"SAVEBITE"` and there is no coupon auto-suggested for the dosa menu, pricing will produce a `PricedOption` with `coupon_code=None`, `coupon_discount=0`, and `final_to_pay` equal to the base price + delivery. Check that `_CART_AFTER_ADD` has `to_pay=403` — this will be the `final_to_pay`. The assertion `results[0].hit.item_name == "Mini Masala Dosa"` is sufficient.

- [ ] **Step 5.4: Run full suite + ruff**

Run: `uv run pytest -q 2>&1 | tail -5`
Run: `uv run ruff check . 2>&1`
Expected: all pass, no ruff errors.

- [ ] **Step 5.5: Commit**

```bash
git add src/swiggy_deal_finder/service.py tests/test_service.py
git commit -m "feat(service): thread portion param through DealFinder.find_deals"
```

---

## Task 6: Thread `portion` through the `find_deals` MCP tool and add `list_dish_variants`

**Files:**
- Modify: `src/swiggy_deal_finder/server.py`

The exact `find_deals` docstring guidance text to add (per spec):

```
IMPORTANT: compare like-for-like. If the user's dish is generic/ambiguous (a family
such as 'dosa', 'biryani', 'pizza', 'noodles') rather than a specific item, FIRST
call list_dish_variants(dish, address_id), show the user the variants, and ask which
specific one they want (including toppings, e.g. plain vs masala vs ghee dosa). Only
then call find_deals with that specific dish. Use portion='regular' (default) for a
normal portion, 'mini' only if the user explicitly wants a small/mini size.
```

The `list_dish_variants` output format:

```
Variants of 'dosa' available nearby:
  - Ghee Dosa    (₹95–95)
  - Masala Dosa  (₹90–90)
  - Mini Masala Dosa  (₹70–70)
  - Plain Dosa   (₹50–50)
Ask the user which specific one to compare, then call find_deals with that name.
```

- [ ] **Step 6.1: Write server-level tests**

Create `tests/test_server_tools.py`:

```python
"""Tests for server.py MCP tool logic (without a live Swiggy session).

We test the formatting helpers by calling the inner logic directly through
a thin wrapper that substitutes FakeSwiggyClient for the lifespan client.
"""

import pytest

from swiggy_deal_finder.candidates import CandidateService
from tests.fakes import WORK_ADDRESS_ID, FakeSwiggyClient


class TestListDishVariantsFormatting:
    async def test_output_contains_header_and_items(self):
        """list_variants output must include the header line and item bullet points."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("dosa", WORK_ADDRESS_ID)

        # Simulate what the MCP tool does:
        dish = "dosa"
        if not variants:
            output = f"No variants found for '{dish}' within 7 km."
        else:
            lines = [f"Variants of '{dish}' available nearby:"]
            for name, lo, hi in variants:
                lines.append(f"  - {name}  (₹{lo}–{hi})")
            lines.append(
                "Ask the user which specific one to compare, then call find_deals with that name."
            )
            output = "\n".join(lines)

        assert f"Variants of '{dish}' available nearby:" in output
        assert "  - Plain Dosa  (₹50–50)" in output
        assert "  - Masala Dosa  (₹90–90)" in output
        assert "Idli Dosa Batter" not in output
        assert "Adai Dosa Mix" not in output
        assert "Ask the user which specific one" in output

    async def test_empty_dish_returns_no_variants_message(self):
        """Unknown dish returns a clear no-variants message."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("sushi", WORK_ADDRESS_ID)
        dish = "sushi"
        output = (
            f"No variants found for '{dish}' within 7 km."
            if not variants
            else "should not reach"
        )
        assert output == f"No variants found for '{dish}' within 7 km."
```

Run: `uv run pytest tests/test_server_tools.py -v 2>&1 | tail -10`
Expected: both tests pass (they test `CandidateService.list_variants` directly, not the MCP tool, so they pass now).

- [ ] **Step 6.2: Update `find_deals` MCP tool in server.py**

In `src/swiggy_deal_finder/server.py`, replace the existing `find_deals` tool function:

```python
@mcp.tool()
async def find_deals(dish: str, address_id: str, top_n: int = 5, portion: str = "regular") -> str:
    """Find the cheapest nearby Swiggy options for a dish after coupons are applied.

    Parameters
    ----------
    dish:
        The dish to search for, e.g. "masala dosa", "chicken biryani".
    address_id:
        The delivery address id from get_locations(). Must be a valid saved address.
    top_n:
        How many candidates to price and return (default 5). Fewer → faster.
    portion:
        "regular" (default) for a normal portion; "mini" only if the user explicitly
        wants a small/mini size; "any" to disable the portion filter.

    Returns a ranked table (cheapest first) with restaurant name, rating, distance,
    base price, best Swiggy coupon applied, and final amount to pay.

    Requires an EMPTY Swiggy cart. If your cart is not empty, this tool will tell
    you so — clear your cart in the Swiggy app and try again.

    IMPORTANT: compare like-for-like. If the user's dish is generic/ambiguous (a family
    such as 'dosa', 'biryani', 'pizza', 'noodles') rather than a specific item, FIRST
    call list_dish_variants(dish, address_id), show the user the variants, and ask which
    specific one they want (including toppings, e.g. plain vs masala vs ghee dosa). Only
    then call find_deals with that specific dish. Use portion='regular' (default) for a
    normal portion, 'mini' only if the user explicitly wants a small/mini size.

    Typical flow: call get_locations() first → pick address_id → for ambiguous dishes
    call list_dish_variants() first → then call find_deals() with the specific variant.
    """
    client = _require_client()
    finder = DealFinder(client)
    try:
        options = await finder.find_deals(dish=dish, address_id=address_id, top_n=top_n, portion=portion)
    except CartNotEmptyError:
        return (
            "Your Swiggy cart is not empty. "
            "Please clear your cart in the Swiggy app and try again. "
            "Deal Finder needs an empty cart to probe prices accurately."
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean message, never a raw trace
        return (
            f"The deal search hit an unexpected error ({type(exc).__name__}). "
            "Please try again in a moment."
        )
    if not options:
        return (
            f"No results found for '{dish}' within 7 km of the selected address. "
            "Try a broader search term or a different address."
        )

    header = f"Top {len(options)} deals for '{dish}' (cheapest first after coupons):\n"
    rows = [header]
    for i, opt in enumerate(options, 1):
        rest = opt.hit.restaurant
        rating_str = f"{rest.avg_rating:.1f}" if rest.avg_rating is not None else "N/A"
        coupon_str = f"{opt.coupon_code} (−₹{opt.coupon_discount})" if opt.coupon_code else "none"
        rows.append(
            f"{i}. {rest.name}"
            f"\n   Rating: {rating_str}  Distance: {rest.distance_km:.1f} km"
            f"\n   Item: {opt.hit.item_name}  Base: ₹{opt.hit.base_price}"
            f"\n   Coupon: {coupon_str}"
            f"\n   Final to pay: ₹{opt.final_to_pay}\n"
        )
    return "\n".join(rows)
```

- [ ] **Step 6.3: Add the `list_dish_variants` MCP tool to server.py**

Also add `CandidateService` to the imports at the top of server.py:

```python
from swiggy_deal_finder.candidates import CandidateService
```

Then add after the `find_deals` function, before `if __name__ == "__main__":`:

```python
@mcp.tool()
async def list_dish_variants(dish: str, address_id: str) -> str:
    """List the distinct variants of a dish available at nearby open restaurants.

    Use this BEFORE find_deals when the user's dish is generic or a family name
    (e.g. 'dosa', 'biryani', 'pizza', 'noodles'). Shows all variants with their
    price range so the user can pick the specific one they want before pricing.

    Parameters
    ----------
    dish:
        Generic dish name to search for variants, e.g. "dosa", "biryani".
    address_id:
        The delivery address id from get_locations().

    Returns a list of distinct dish variants with price ranges, ready to show the user.
    After showing this list, ask the user which specific variant to compare, then call
    find_deals with that specific dish name.
    """
    client = _require_client()
    service = CandidateService(client)
    variants = await service.list_variants(dish, address_id)
    if not variants:
        return (
            f"No variants found for '{dish}' within 7 km of the selected address. "
            "Try a broader search term or a different address."
        )
    lines = [f"Variants of '{dish}' available nearby:"]
    for name, lo, hi in variants:
        lines.append(f"  - {name}  (₹{lo}–{hi})")
    lines.append(
        "Ask the user which specific one to compare, then call find_deals with that name."
    )
    return "\n".join(lines)
```

- [ ] **Step 6.4: Run all tests + ruff**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: all pass (count depends on Task 5 test; expect 82+ passed)

Run: `uv run ruff check . 2>&1`
Expected: clean (no output).

- [ ] **Step 6.5: Commit**

```bash
git add src/swiggy_deal_finder/server.py tests/test_server_tools.py
git commit -m "feat(server): thread portion through find_deals, add list_dish_variants MCP tool"
```

---

## Task 7: Final verification

- [ ] **Step 7.1: Run full test suite**

Run: `uv run pytest -q 2>&1`
Expected last line: something like `82 passed, 1 warning` (or higher). Zero failures.

- [ ] **Step 7.2: Run ruff**

Run: `uv run ruff check . 2>&1`
Expected: no output.

- [ ] **Step 7.3: Spot-check key behaviors manually**

Run:
```bash
uv run python -c "
import asyncio
from swiggy_deal_finder.candidates import CandidateService
from tests.fakes import FakeSwiggyClient, WORK_ADDRESS_ID

async def main():
    svc = CandidateService(FakeSwiggyClient())
    # Ingredient exclusion
    hits = await svc.find('dosa', WORK_ADDRESS_ID)
    names = [h.item_name for h in hits]
    print('Regular dosa hits:', names)
    assert 'Idli Dosa Batter' not in names
    assert 'Adai Dosa Mix' not in names
    assert 'Mini Masala Dosa' not in names  # shrink excluded

    # list_variants
    variants = await svc.list_variants('dosa', WORK_ADDRESS_ID)
    vnames = [v[0] for v in variants]
    print('Variants:', vnames)
    assert 'Mini Masala Dosa' in vnames  # no portion filter in list_variants
    assert 'Idli Dosa Batter' not in vnames
    print('All checks passed.')

asyncio.run(main())
"
```

Expected output:
```
Regular dosa hits: ['Plain Dosa']   # cheapest non-ingredient, non-shrink dosa
Variants: ['Ghee Dosa', 'Masala Dosa', 'Mini Masala Dosa', 'Plain Dosa']
All checks passed.
```

---

## Spec Coverage Self-Review

| Spec requirement | Covered by |
|---|---|
| Ingredient denylist (batter, mix, premix, powder, paste, combo, pack, packet, kit, frozen, ready to cook, refill, masala powder) | Task 3 — `INGREDIENT_DENYLIST` in candidates.py |
| Portion = Literal["regular","mini","any"] in models.py | Task 1 |
| portion arg threaded through CandidateService.find | Task 3 |
| portion="regular" excludes shrink tokens unless query contains them | Task 3 — `_passes_portion_filter` |
| portion="mini" keeps ONLY shrink items | Task 3 |
| portion="any" no portion filter, ingredient denylist still applies | Task 3 |
| Keep existing match + biryani/briyani normalisation | Task 3 — unchanged |
| Pick lowest-price in-stock match (after filters) | Task 3 — `_best_match` unchanged in intent |
| CandidateService.list_variants(dish, address_id) → list[tuple[str,int,int]] | Task 3 |
| list_variants: denylist applied, no portion filter, ≤20, sorted by name | Task 3 |
| list_dish_variants MCP tool | Task 6 |
| list_dish_variants output format | Task 6 |
| portion threaded through DealFinder.find_deals | Task 5 |
| portion threaded through find_deals MCP tool | Task 6 |
| find_deals docstring guidance text | Task 6 |
| Fakes extended with dosa menu | Task 2 |
| Tests: ingredient excluded | Task 3/4 |
| Tests: portion=regular excludes mini | Task 3 |
| Tests: portion=mini returns only mini | Task 3 |
| Tests: portion=any allows mini, excludes ingredient | Task 3 |
| Tests: specific query masala dosa | Task 3 |
| Tests: list_variants distinct names + price ranges | Task 4 |
| Tests: find_deals threads portion | Task 5 |
| All existing tests still pass | Every task runs full suite |
| ruff clean | Every commit step |

---

## Ambiguities Resolved

1. **`"masala powder"` as multi-word denylist entry**: uses substring match on the full normalised item name string (not token-by-token), which also catches `"1 pc"`, `"2 pc"`, etc. correctly.

2. **`portion` type in service.py and server.py**: uses `str` rather than the `Portion` Literal in the function signatures of `DealFinder.find_deals` and the MCP tool, to avoid import coupling and keep MCP tool parameter passing simple. `CandidateService.find` accepts `Portion` (the Literal) which type-checks fine with `str` defaults at call sites.

3. **list_variants return type**: `list[tuple[str, int, int]]` — name, min_price, max_price. Distinct by item name (first-seen when same name appears across restaurants; prices merged).

4. **test_portion_threaded_to_candidates**: the dosa `_CART_AFTER_ADD` has `to_pay=403`; the fake `apply_coupon` only handles `"SAVEBITE"` and the dosa restaurant doesn't serve that coupon, so `final_to_pay=403`. The test only asserts `item_name`, not price, so this is fine.

## Residual Risks for Opus Reviewer

- The fake `search_restaurants` returns `DOSA_RESTAURANTS` for any query containing `"dosa"`. If a new test queries `"masala dosa"` it returns the same set, which is correct for all current tests.
- `_CART_AFTER_ADD` has `to_pay=403` regardless of the item — the fake cart state machine is not per-item. Tests that assert `final_to_pay` for dosa items will get 403 (delivery platform fee), not the item price. This is a known fake simplification.
- The `"masala powder"` multi-word denylist entry is a substring of `"masala"` + `"powder"` but NOT of `"masala dosa"` alone. Items like `"Masala Dosa"` normalise to `"masala dosa"` — the substring `"masala powder"` does not appear in `"masala dosa"`, so Masala Dosa is correctly NOT blocked by this entry.
- Line length: the `_best_match` call in `CandidateService.find` with all four arguments may exceed the 100-char ruff limit. If ruff complains, break it as:
  ```python
  best = _best_match(
      query_tokens, items, portion=portion, query_normalised=query_normalised
  )
  ```
