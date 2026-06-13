"""Tests for CandidateService — the two-hop search + filter + match logic."""

from swiggy_deal_finder.candidates import CandidateService
from tests.fakes import WORK_ADDRESS_ID, FakeSwiggyClient


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
