"""Integration-level tests for DealFinder service.

These tests wire FakeSwiggyClient through the full pipeline:
  CandidateService → sort → top_n → CouponPricer per hit → rank.
"""

import pytest

from swiggy_deal_finder.pricing import CartNotEmptyError
from swiggy_deal_finder.service import DealFinder
from tests.fakes import _CART_AFTER_ADD, WORK_ADDRESS_ID, FakeSwiggyClient


class TestDealFinder:
    async def test_find_deals_returns_priced_results(self):
        client = FakeSwiggyClient()
        finder = DealFinder(client)
        results = await finder.find_deals("biryani", WORK_ADDRESS_ID)
        assert len(results) == 1
        r = results[0]
        assert r.final_to_pay == 321
        assert r.coupon_code == "SAVEBITE"
        assert r.hit.restaurant.id == "62683"

    async def test_top_n_limits_results(self):
        """top_n=0 → empty list (edge case)."""
        client = FakeSwiggyClient()
        finder = DealFinder(client)
        results = await finder.find_deals("biryani", WORK_ADDRESS_ID, top_n=0)
        assert results == []

    async def test_skips_candidate_on_pricing_error(self):
        """A candidate whose pricing raises a transient error is skipped, not aborted
        (the cart starts empty, so this is a per-candidate failure, not a busy cart)."""

        class FlakyPricingClient(FakeSwiggyClient):
            async def update_food_cart(self, *args, **kwargs) -> dict:
                self._record("update_food_cart", *args, **kwargs)
                raise OSError("MCP timeout")

        client = FlakyPricingClient()
        finder = DealFinder(client)
        # The single matching candidate fails pricing → empty result, no exception.
        results = await finder.find_deals("biryani", WORK_ADDRESS_ID)
        assert results == []

    async def test_raises_when_cart_not_empty(self):
        """A busy cart is surfaced up front, not silently swallowed as 'no deals'."""
        client = FakeSwiggyClient(start_cart=_CART_AFTER_ADD)
        finder = DealFinder(client)
        with pytest.raises(CartNotEmptyError):
            await finder.find_deals("biryani", WORK_ADDRESS_ID)

    async def test_results_are_ranked(self):
        """Results must come out sorted by final_to_pay (via rank())."""
        client = FakeSwiggyClient()
        finder = DealFinder(client)
        results = await finder.find_deals("biryani", WORK_ADDRESS_ID)
        to_pays = [r.final_to_pay for r in results]
        assert to_pays == sorted(to_pays)

    async def test_no_results_for_unknown_dish(self):
        client = FakeSwiggyClient()
        finder = DealFinder(client)
        results = await finder.find_deals("sushi", WORK_ADDRESS_ID)
        assert results == []

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
