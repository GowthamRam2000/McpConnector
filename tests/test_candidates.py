"""Tests for CandidateService — the two-hop search + filter + match logic."""

from swiggy_deal_finder.candidates import CandidateService
from tests.fakes import WORK_ADDRESS_ID, FakeSwiggyClient


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
