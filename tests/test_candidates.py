"""Tests for CandidateService — the two-hop search + filter + match logic."""

from swiggy_deal_finder.candidates import CandidateService
from tests.fakes import (
    DOSA_SUPERSET_PAGE0,
    WORK_ADDRESS_ID,
    FakeSwiggyClient,
)


class TestRatingFilter:
    async def test_low_rated_restaurant_excluded(self):
        from swiggy_deal_finder.models import MenuItem, Restaurant

        class _Client(FakeSwiggyClient):
            async def search_restaurants(self, query, address_id, offset=0):
                self._record("search_restaurants", query, address_id, offset=offset)
                if query == "dosa" and offset == 0:
                    return [
                        Restaurant(
                            id="hi", name="Good Dosa", distance_km=1.0,
                            avg_rating=4.5, is_open=True,
                        ),
                        Restaurant(
                            id="lo", name="Bad Dosa", distance_km=0.5,
                            avg_rating=3.2, is_open=True,
                        ),
                    ]
                return []

            async def get_restaurant_menu(self, restaurant_id, address_id):
                self._record("get_restaurant_menu", restaurant_id, address_id)
                return [MenuItem(id="g", name="Ghee Roast", price=100, in_stock=True)]

        svc = CandidateService(_Client())
        hits = await svc.find("ghee roast", WORK_ADDRESS_ID, category="dosa", min_rating=4.0)
        # "lo" (3.2 stars) is excluded despite being closer and having the dish.
        assert {h.restaurant.id for h in hits} == {"hi"}


class TestCandidateServiceFind:
    async def test_returns_dish_hit_for_matching_restaurant(self):
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("biryani", WORK_ADDRESS_ID)
        assert len(hits) == 1
        hit = hits[0]
        assert hit.restaurant.id == "62683"
        assert hit.item_id == "81574197"
        assert hit.item_name == "Chicken Briyani"
        # lowest-price match: 350 (Chicken Briyani) < 450 (Mutton Biryani)
        assert hit.base_price == 350

    async def test_filters_restaurants_beyond_max_distance(self):
        client = FakeSwiggyClient()
        service = CandidateService(client, max_distance_km=7.0)
        hits = await service.find("biryani", WORK_ADDRESS_ID)
        restaurant_ids = [h.restaurant.id for h in hits]
        # 99999 is 12.5 km — must be excluded
        assert "99999" not in restaurant_ids

    async def test_filters_closed_restaurants(self):
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("biryani", WORK_ADDRESS_ID)
        restaurant_ids = [h.restaurant.id for h in hits]
        # 77777 is closed — must be excluded
        assert "77777" not in restaurant_ids

    async def test_biryani_briyani_spelling_variant_matches(self):
        """Query 'chicken biryani' must match 'Chicken Briyani' in the menu."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("chicken biryani", WORK_ADDRESS_ID)
        assert len(hits) == 1
        assert hits[0].item_name == "Chicken Briyani"

    async def test_two_hop_behavior_calls_menu_per_restaurant(self):
        """Each candidate restaurant after filter must have its menu fetched."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        await service.find("biryani", WORK_ADDRESS_ID)
        menu_calls = [c for c in client.call_names() if c == "get_restaurant_menu"]
        # Only RESTAURANT_AMBUR (62683) passes filter; FAR and CLOSED are excluded
        assert len(menu_calls) == 1

    async def test_no_results_for_unknown_query(self):
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("sushi", WORK_ADDRESS_ID)
        assert hits == []

    async def test_restaurant_with_no_matching_item_is_skipped(self):
        """If menu has no matching item, the restaurant produces no DishHit."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        # "mutton" tokens: query token "mutton" appears in "Mutton Biryani" only
        hits = await service.find("mutton biryani", WORK_ADDRESS_ID)
        assert len(hits) == 1
        assert hits[0].item_name == "Mutton Biryani"
        assert hits[0].base_price == 450


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
        """portion='any' does not exclude mini items — querying 'mini dosa' returns
        Mini Masala Dosa even without portion='mini'."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        # Query "mini dosa" with portion="any": Mini Masala Dosa is the only item
        # matching both "mini" and "dosa" tokens, so it must be returned.
        hits = await service.find("mini dosa", WORK_ADDRESS_ID, portion="any")
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


class TestCategoryRouting:
    """Tests for the search-term resolver and category-based restaurant routing."""

    async def test_category_routes_ghee_roast_to_dosa_restaurants(self):
        """find("ghee roast", category="dosa") must search "dosa", find Saravana Bhavan
        on page 0 and Murugan Idli Shop on page 1 (via pagination), and return
        Ghee Roast hits from both restaurants."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("ghee roast", WORK_ADDRESS_ID, category="dosa")
        restaurant_ids = {h.restaurant.id for h in hits}
        assert "11111" in restaurant_ids
        assert hits[0].item_name == "Ghee Roast"

    async def test_ghee_roast_without_category_returns_empty(self):
        """find("ghee roast") with no category: "ghee roast"→[] and last token
        "roast"→[] so no restaurants are found → []."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("ghee roast", WORK_ADDRESS_ID)
        assert hits == []

    async def test_auto_broaden_masala_dosa_via_last_token(self):
        """find("masala dosa") with no category: "masala dosa"→[] then last token
        "dosa"→DOSA_RESTAURANTS → menu match finds "Masala Dosa"."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("masala dosa", WORK_ADDRESS_ID)
        item_names = [h.item_name for h in hits]
        assert "Masala Dosa" in item_names

    async def test_list_variants_with_category_surfaces_ghee_roast(self):
        """list_variants("ghee roast", category="dosa") must surface "Ghee Roast"."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("ghee roast", WORK_ADDRESS_ID, category="dosa")
        names = [v[0] for v in variants]
        assert "Ghee Roast" in names

    async def test_category_used_first_in_search_terms(self):
        """When category is given it must be the FIRST search term tried.
        Verify by checking search_restaurants call order in client.calls."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        await service.find("ghee roast", WORK_ADDRESS_ID, category="dosa")
        search_calls = [
            c for c in client.calls if c[0] == "search_restaurants"
        ]
        # First search must be with the category "dosa", not the full dish
        assert search_calls[0][1][0] == "dosa"

    async def test_no_category_full_dish_tried_before_last_token(self):
        """Without category, the full dish is tried first. "masala dosa" is a specific
        phrase that yields [] so we fall back to "dosa" (last token)."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        await service.find("masala dosa", WORK_ADDRESS_ID)
        search_calls = [
            c for c in client.calls if c[0] == "search_restaurants"
        ]
        # First tried: "masala dosa" (specific phrase → [])
        # Second tried: "dosa" offset 0 (last token → restaurants, becomes winning term)
        assert search_calls[0][1][0] == "masala dosa"
        assert search_calls[1][1][0] == "dosa"


class TestPagination:
    """Tests for multi-page restaurant discovery in _find_reachable."""

    async def test_pagination_widens_ghee_roast_to_two_restaurants(self):
        """find("ghee roast", category="dosa") returns Ghee Roast from BOTH restaurants:
        RESTAURANT_DOSA (page 0) and RESTAURANT_DOSA2 (page 1 offset=10)."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("ghee roast", WORK_ADDRESS_ID, category="dosa")
        restaurant_ids = {h.restaurant.id for h in hits}
        assert len(hits) == 2
        assert "11111" in restaurant_ids
        assert "22222" in restaurant_ids

    async def test_pagination_stops_when_page_returns_empty(self):
        """Pagination halts as soon as a page returns []; no further calls made.

        "dosa" page 0 → [DOSA], page 1 → [DOSA2], page 2 → [].
        With MAX_SEARCH_PAGES=3, offset 20 is requested but returns [] so we stop.
        Assert search_restaurants is not called beyond offset 20."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        await service.find("ghee roast", WORK_ADDRESS_ID, category="dosa")
        search_calls = [c for c in client.calls if c[0] == "search_restaurants"]
        offsets = [c[2].get("offset", 0) for c in search_calls]
        # Offsets tried: 0, 10, 20 (page 2 returns [] → loop breaks after fetching it)
        assert 0 in offsets
        assert 10 in offsets
        # No offset beyond 20 (MAX_SEARCH_PAGES=3 → pages 0,1,2 → offsets 0,10,20)
        assert all(o <= 20 for o in offsets)

    async def test_pagination_respects_max_search_pages_cap(self):
        """Even if a page returns restaurants, we stop after MAX_SEARCH_PAGES pages.

        Biryani page 0 returns results; page 1 returns []. We verify offset never
        exceeds (MAX_SEARCH_PAGES - 1) * 10."""
        from swiggy_deal_finder.candidates import MAX_SEARCH_PAGES
        client = FakeSwiggyClient()
        service = CandidateService(client)
        await service.find("biryani", WORK_ADDRESS_ID)
        search_calls = [c for c in client.calls if c[0] == "search_restaurants"]
        offsets = [c[2].get("offset", 0) for c in search_calls]
        max_expected_offset = (MAX_SEARCH_PAGES - 1) * 10
        assert all(o <= max_expected_offset for o in offsets)

    async def test_dedup_same_restaurant_on_multiple_pages(self):
        """A restaurant appearing on two pages is counted only once."""
        from tests.fakes import RESTAURANT_DOSA

        class DuplicatingClient(FakeSwiggyClient):
            """Returns RESTAURANT_DOSA on both page 0 and page 1."""
            async def search_restaurants(self, query, address_id, offset=0):
                self._record("search_restaurants", query, address_id, offset=offset)
                if query == "dosa":
                    if offset <= 10:
                        return [RESTAURANT_DOSA]
                    return []
                return []

        client = DuplicatingClient()
        service = CandidateService(client)
        hits = await service.find("ghee roast", WORK_ADDRESS_ID, category="dosa")
        restaurant_ids = [h.restaurant.id for h in hits]
        # id "11111" should appear only once despite being on two pages
        assert restaurant_ids.count("11111") == 1

    async def test_biryani_still_single_page_result(self):
        """Biryani search: page 0 has results, page 1 returns [] → stops. Single hit."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("biryani", WORK_ADDRESS_ID)
        # Only RESTAURANT_AMBUR passes the distance/open filter
        assert len(hits) == 1
        assert hits[0].restaurant.id == "62683"


# ── Helpers shared across new test classes ───────────────────────────────────────────────

class _SupersetDosaClient(FakeSwiggyClient):
    """Serves DOSA_SUPERSET_PAGE0 (restaurants 33333, 44444) for query "dosa".

    Restaurant 33333 (A2B) has both an exact "Ghee Dosa" and superset items.
    Restaurant 44444 only has superset items (no exact "Ghee Dosa").
    """

    async def search_restaurants(self, query, address_id, offset=0):
        self._record("search_restaurants", query, address_id, offset=offset)
        q = query.lower().strip()
        if q in self._SPECIFIC_PHRASES:
            return []
        if q == "dosa":
            if offset == 0:
                return list(DOSA_SUPERSET_PAGE0)
            return []
        return []


# ── Change 1: Tight like-for-like matching in find() ────────────────────────────────────

class TestTightMatching:
    """find() uses tight matching; list_variants() stays loose."""

    async def test_tight_match_excludes_superset_items(self):
        """find("ghee dosa") with tight matching must NOT return "Ghee Podi Dosa"
        or "Millet Ghee Karam Dosa" or "Nice Ghee Dosa" — these have extra meaningful
        tokens beyond the query ("podi", "millet", "karam", "nice")."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        hits = await service.find("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        item_names = [h.item_name for h in hits]
        assert "Ghee Podi Dosa" not in item_names
        assert "Millet Ghee Karam Dosa" not in item_names
        assert "Nice Ghee Dosa" not in item_names

    async def test_tight_match_includes_exact_item(self):
        """find("ghee dosa") must include the exact "Ghee Dosa" item from restaurant 33333."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        hits = await service.find("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        item_names = [h.item_name for h in hits]
        # "Ghee Dosa" (exact) and "Ghee Dosa - Plain" ("plain" is a FILLER token) both qualify
        assert any(n in ("Ghee Dosa", "Ghee Dosa - Plain") for n in item_names)

    async def test_tight_match_allows_filler_tokens(self):
        """Items with only FILLER tokens beyond the query are still tight matches.
        "Ghee Dosa - Plain" → extra token "plain" is a FILLER → should be included."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        hits = await service.find("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        item_names = [h.item_name for h in hits]
        # The cheapest tight match wins per restaurant; "Ghee Dosa - Plain" (90) < "Ghee Dosa" (95)
        assert "Ghee Dosa - Plain" in item_names

    async def test_tight_match_only_restaurant_with_exact_returns_hit(self):
        """Restaurant 44444 has NO exact ghee dosa (only supersets); tight matching means
        it yields no hit when at least one other restaurant does have a tight match."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        hits = await service.find("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        restaurant_ids = {h.restaurant.id for h in hits}
        # 33333 has tight matches; 44444 does not → 44444 excluded when tight tier active
        assert "44444" not in restaurant_ids
        assert "33333" in restaurant_ids

    async def test_loose_fallback_when_no_tight_match_anywhere(self):
        """When NO restaurant has a tight match, fall back to loose matching so the user
        still gets results rather than an empty list."""
        from swiggy_deal_finder.models import MenuItem, Restaurant

        class _NoExactClient(FakeSwiggyClient):
            """Only superset items; no item whose tokens match exactly."""
            async def search_restaurants(self, query, address_id, offset=0):
                self._record("search_restaurants", query, address_id, offset=offset)
                if query.lower().strip() == "dosa":
                    if offset == 0:
                        return [Restaurant(id="x1", name="Superset Only", distance_km=1.0,
                                           avg_rating=4.0, is_open=True)]
                    return []
                return []

            async def get_restaurant_menu(self, restaurant_id, address_id):
                self._record("get_restaurant_menu", restaurant_id, address_id)
                return [
                    MenuItem(id="z1", name="Ghee Podi Dosa", price=110, in_stock=True),
                    MenuItem(id="z2", name="Nice Ghee Dosa", price=98, in_stock=True),
                ]

        client = _NoExactClient()
        service = CandidateService(client)
        hits = await service.find("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        # Loose fallback: should return results (not empty), even though nothing is tight
        assert len(hits) > 0

    async def test_list_variants_still_returns_superset_names(self):
        """list_variants() must stay LOOSE — it should include 'Ghee Podi Dosa',
        'Nice Ghee Dosa', etc. alongside 'Ghee Dosa', so the user can see all variants."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        variants = await service.list_variants("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        names = [v[0] for v in variants]
        # Loose matching: all items whose name contains BOTH "ghee" and "dosa"
        assert "Ghee Podi Dosa" in names
        assert "Nice Ghee Dosa" in names
        assert "Ghee Dosa" in names


# ── Change 2: restaurant_name filter ────────────────────────────────────────────────────

class TestRestaurantNameFilter:
    """find() supports restaurant_name= to narrow results to one chain."""

    async def test_restaurant_name_filters_to_matching_restaurant(self):
        """find("ghee dosa", restaurant_name="a2b") returns only A2B's hit."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        hits = await service.find(
            "ghee dosa", WORK_ADDRESS_ID, category="dosa", restaurant_name="a2b"
        )
        # Only restaurant 33333 (A2B - Adyar Ananda Bhavan) passes the name filter
        assert all(h.restaurant.id == "33333" for h in hits)
        assert len(hits) > 0

    async def test_restaurant_name_filter_is_case_insensitive(self):
        """Matching is case-insensitive substring: "A2B" and "a2b" both match."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        hits_upper = await service.find(
            "ghee dosa", WORK_ADDRESS_ID, category="dosa", restaurant_name="A2B"
        )
        hits_lower = await service.find(
            "ghee dosa", WORK_ADDRESS_ID, category="dosa", restaurant_name="a2b"
        )
        assert {h.restaurant.id for h in hits_upper} == {h.restaurant.id for h in hits_lower}

    async def test_restaurant_name_filter_skips_menu_for_non_matching(self):
        """Menus for restaurants that don't match restaurant_name must NOT be fetched."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        await service.find(
            "ghee dosa", WORK_ADDRESS_ID, category="dosa", restaurant_name="a2b"
        )
        menu_calls = [c for c in client.calls if c[0] == "get_restaurant_menu"]
        fetched_ids = {c[1][0] for c in menu_calls}
        # Restaurant 44444 (Sri Murugan) must NOT have been fetched
        assert "44444" not in fetched_ids
        assert "33333" in fetched_ids

    async def test_restaurant_name_filter_no_match_returns_empty(self):
        """If no restaurant matches restaurant_name, return []."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        hits = await service.find(
            "ghee dosa", WORK_ADDRESS_ID, category="dosa", restaurant_name="zzznomatch"
        )
        assert hits == []

    async def test_restaurant_name_none_returns_all(self):
        """restaurant_name=None (default) does not filter any restaurant."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        hits_default = await service.find("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        hits_none = await service.find(
            "ghee dosa", WORK_ADDRESS_ID, category="dosa", restaurant_name=None
        )
        assert {h.restaurant.id for h in hits_default} == {h.restaurant.id for h in hits_none}


# ── Change 3: Parallel menu fetches ─────────────────────────────────────────────────────

class TestParallelMenuFetch:
    """Menu fetches run concurrently; correctness must be preserved."""

    async def test_concurrent_fetch_returns_same_hits_as_sequential(self):
        """gather-based fetch must produce the same hits as the old sequential approach.
        Use the default dosa fixture (two pages → two restaurants with ghee roast)."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        hits = await service.find("ghee roast", WORK_ADDRESS_ID, category="dosa")
        restaurant_ids = {h.restaurant.id for h in hits}
        # Both restaurants (11111, 22222) have "Ghee Roast" and must both return a hit
        assert "11111" in restaurant_ids
        assert "22222" in restaurant_ids

    async def test_concurrent_fetch_list_variants_correct(self):
        """list_variants() concurrently fetches menus and still returns all variants."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("ghee roast", WORK_ADDRESS_ID, category="dosa")
        names = [v[0] for v in variants]
        assert "Ghee Roast" in names

    async def test_all_reachable_menus_fetched(self):
        """Every restaurant that passes the reachable filter gets a menu fetch."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        # No restaurant_name filter → both 33333 and 44444 are reachable
        await service.find("dosa", WORK_ADDRESS_ID, category="dosa")
        menu_calls = [c for c in client.calls if c[0] == "get_restaurant_menu"]
        fetched_ids = {c[1][0] for c in menu_calls}
        assert "33333" in fetched_ids
        assert "44444" in fetched_ids


# ── Change 4: has_variants / has_addons flags ────────────────────────────────────────────

class TestMenuItemFlags:
    """MenuItem.has_variants / has_addons are parsed from raw menu entries."""

    def test_parse_menu_items_reads_has_variants_flag(self):
        from swiggy_deal_finder.swiggy_client import parse_menu_items
        raw = [
            {"id": "1", "name": "Ghee Dosa", "price": 95, "inStock": 1, "hasVariants": True},
            {"id": "2", "name": "Plain Dosa", "price": 50, "inStock": 1},
        ]
        items = parse_menu_items(raw)
        by_id = {it.id: it for it in items}
        assert by_id["1"].has_variants is True
        assert by_id["2"].has_variants is False  # missing → default False

    def test_parse_menu_items_reads_has_addons_flag(self):
        from swiggy_deal_finder.swiggy_client import parse_menu_items
        raw = [
            {"id": "1", "name": "Masala Dosa", "price": 90, "inStock": 1, "hasAddons": True},
            {"id": "2", "name": "Plain Dosa", "price": 50, "inStock": 1},
        ]
        items = parse_menu_items(raw)
        by_id = {it.id: it for it in items}
        assert by_id["1"].has_addons is True
        assert by_id["2"].has_addons is False

    def test_parse_menu_items_tolerates_missing_flags(self):
        """Neither hasVariants nor hasAddons is required; both default to False."""
        from swiggy_deal_finder.swiggy_client import parse_menu_items
        raw = [{"id": "1", "name": "A", "price": 50, "inStock": 1}]
        items = parse_menu_items(raw)
        assert items[0].has_variants is False
        assert items[0].has_addons is False


class TestListVariantsHasOptions:
    """list_variants() returns 4-tuples; 4th element signals size/add-on options."""

    async def test_list_variants_returns_four_tuples(self):
        """Each element of list_variants result must be a 4-tuple."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("dosa", WORK_ADDRESS_ID)
        for v in variants:
            assert len(v) == 4, f"Expected 4-tuple, got {len(v)}-tuple: {v}"

    async def test_has_options_true_for_flagged_item(self):
        """When a matched item has has_variants=True or has_addons=True,
        the 4th tuple element must be True for that variant name."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        # MENU_33333 has "Ghee Dosa Special" with has_variants=True
        variants = await service.list_variants("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        by_name = {v[0]: v[3] for v in variants}
        assert by_name.get("Ghee Dosa Special") is True

    async def test_has_options_false_for_plain_item(self):
        """Items without variant/add-on flags produce has_options=False."""
        client = _SupersetDosaClient()
        service = CandidateService(client)
        variants = await service.list_variants("ghee dosa", WORK_ADDRESS_ID, category="dosa")
        by_name = {v[0]: v[3] for v in variants}
        # "Ghee Podi Dosa" has no special flags → has_options=False
        assert by_name.get("Ghee Podi Dosa") is False

    async def test_existing_price_range_still_correct_in_four_tuple(self):
        """Extending to 4-tuple must not disturb the (name, min, max) values."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("dosa", WORK_ADDRESS_ID)
        by_name = {v[0]: (v[1], v[2]) for v in variants}
        assert by_name["Plain Dosa"] == (50, 50)
        assert by_name["Ghee Dosa"] == (95, 95)
