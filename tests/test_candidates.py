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
