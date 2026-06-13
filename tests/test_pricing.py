"""Tests for pricing.py — CartGuard and CouponPricer.

These are the highest-risk tests because they exercise the cart mutation cycle.
Key invariants:
  1. flush_cart ALWAYS called (even on error)
  2. CartGuard raises CartNotEmptyError for non-empty start carts
  3. CouponPricer returns final_to_pay 321 (post-coupon), not 403 (pre-coupon)
  4. {} from apply_coupon does not trick the pricer — it re-reads the cart
"""

import pytest

from swiggy_deal_finder.models import DishHit
from swiggy_deal_finder.pricing import CartGuard, CartNotEmptyError, CouponPricer
from tests.fakes import (
    _CART_AFTER_ADD,
    _CART_EMPTY,
    RESTAURANT_AMBUR,
    WORK_ADDRESS_ID,
    FakeSwiggyClient,
)

_HIT = DishHit(
    restaurant=RESTAURANT_AMBUR,
    item_id="81574197",
    item_name="Chicken Briyani",
    base_price=350,
)


class TestCartGuard:
    async def test_raises_cart_not_empty_when_cart_has_items(self):
        """Non-empty cart at entry must raise CartNotEmptyError — we must not clobber it."""
        client = FakeSwiggyClient(start_cart=_CART_AFTER_ADD)
        with pytest.raises(CartNotEmptyError):
            async with CartGuard(client, WORK_ADDRESS_ID):
                pass  # should never reach here

    async def test_does_not_flush_non_empty_cart(self):
        """A non-empty cart must be left untouched — we never clobber a real cart."""
        client = FakeSwiggyClient(start_cart=_CART_AFTER_ADD)
        with pytest.raises(CartNotEmptyError):
            async with CartGuard(client, WORK_ADDRESS_ID):
                pass
        assert "flush_cart" not in client.call_names()

    async def test_flush_called_on_clean_entry_and_exit(self):
        client = FakeSwiggyClient()
        async with CartGuard(client, WORK_ADDRESS_ID):
            pass  # body does nothing; cart should still be flushed on exit
        assert "flush_cart" in client.call_names()

    async def test_flush_called_even_on_exception_inside_body(self):
        """flush_cart must run even when the body raises an arbitrary exception."""
        client = FakeSwiggyClient()
        with pytest.raises(RuntimeError):
            async with CartGuard(client, WORK_ADDRESS_ID):
                raise RuntimeError("oops")
        assert "flush_cart" in client.call_names()


class TestCouponPricer:
    async def test_returns_post_coupon_price(self):
        """Spike confirmed: 350 + SAVEBITE → to_pay 321 (not 403 pre-coupon)."""
        client = FakeSwiggyClient()
        pricer = CouponPricer(client)
        option = await pricer.price(_HIT, WORK_ADDRESS_ID)
        assert option.final_to_pay == 321
        assert option.coupon_code == "SAVEBITE"
        assert option.coupon_discount == 80

    async def test_flush_always_called(self):
        client = FakeSwiggyClient()
        pricer = CouponPricer(client)
        await pricer.price(_HIT, WORK_ADDRESS_ID)
        assert "flush_cart" in client.call_names()

    async def test_coupon_applied_via_auto_suggestion(self):
        """apply_coupon called with the code the cart auto-suggested (SAVEBITE)."""
        client = FakeSwiggyClient()
        pricer = CouponPricer(client)
        await pricer.price(_HIT, WORK_ADDRESS_ID)
        apply_calls = [c for c in client.calls if c[0] == "apply_coupon"]
        assert len(apply_calls) == 1
        assert apply_calls[0][1][0] == "SAVEBITE"

    async def test_no_coupon_when_none_suggested(self):
        """When cart suggests no coupon, return to_pay with coupon_code=None."""
        # Build a fake that returns a cart with no coupon_applied after add
        from swiggy_deal_finder.swiggy_client import CartSummary

        no_coupon_cart: CartSummary = {
            "is_empty": False,
            "to_pay": 403,
            "coupon_applied": None,
            "coupon_discount": 0,
            "items": [{"menu_item_id": "81574197", "name": "Chicken Briyani", "quantity": 1}],
        }

        class NoCouponClient(FakeSwiggyClient):
            _call_count: int = 0

            async def get_food_cart(self, address_id: str) -> CartSummary:
                self._record("get_food_cart", address_id)
                # First call: CartGuard entry check — must be empty
                # Subsequent calls: after add, return no-coupon populated cart
                if self._call_count == 0:
                    self._call_count += 1
                    return dict(_CART_EMPTY)  # type: ignore[return-value]
                self._call_count += 1
                return dict(no_coupon_cart)  # type: ignore[return-value]

        client = NoCouponClient()
        pricer = CouponPricer(client)
        option = await pricer.price(_HIT, WORK_ADDRESS_ID)
        assert option.coupon_code is None
        assert option.final_to_pay == 403
        assert option.coupon_discount == 0
        # apply_coupon must NOT have been called
        assert "apply_coupon" not in client.call_names()

    async def test_flush_called_even_when_pricing_raises(self):
        """If update_food_cart raises, flush must still run."""
        import contextlib

        class ErrorClient(FakeSwiggyClient):
            async def update_food_cart(self, *args, **kwargs) -> dict:
                self._record("update_food_cart", *args, **kwargs)
                raise OSError("MCP timeout")

        client = ErrorClient()
        pricer = CouponPricer(client)
        with contextlib.suppress(OSError):
            await pricer.price(_HIT, WORK_ADDRESS_ID)
        assert "flush_cart" in client.call_names()

    async def test_raises_pricing_error_when_no_to_pay(self):
        """A malformed cart (to_pay None after add) must raise, not return ₹0."""
        from swiggy_deal_finder.pricing import PricingError
        from swiggy_deal_finder.swiggy_client import CartSummary

        broken_cart: CartSummary = {
            "is_empty": False,
            "to_pay": None,
            "coupon_applied": None,
            "coupon_discount": 0,
            "items": [],
        }

        class BrokenClient(FakeSwiggyClient):
            _n: int = 0

            async def get_food_cart(self, address_id: str) -> CartSummary:
                self._record("get_food_cart", address_id)
                if self._n == 0:  # CartGuard entry check must see an empty cart
                    self._n += 1
                    return dict(_CART_EMPTY)  # type: ignore[return-value]
                self._n += 1
                return dict(broken_cart)  # type: ignore[return-value]

        client = BrokenClient()
        pricer = CouponPricer(client)
        with pytest.raises(PricingError):
            await pricer.price(_HIT, WORK_ADDRESS_ID)
        assert "flush_cart" in client.call_names()  # cart still cleaned up
