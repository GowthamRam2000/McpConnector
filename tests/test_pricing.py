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


class TestCouponDiscountGate:
    """A coupon auto-suggested but not actually applied (discount 0, e.g. min-cart not
    met) must NOT be reported as applied — final price is the no-coupon amount."""

    async def test_zero_discount_coupon_not_reported(self):
        from swiggy_deal_finder.swiggy_client import CartSummary

        # After add: coupon "TRYNEW" is suggested but applying it yields discount 0.
        suggested_no_discount: CartSummary = {
            "is_empty": False,
            "to_pay": 200,
            "coupon_applied": "TRYNEW",
            "coupon_discount": 0,
            "items": [{"menu_item_id": "81574197", "name": "Chicken Briyani", "quantity": 1}],
        }

        class ZeroDiscountClient(FakeSwiggyClient):
            _n: int = 0

            async def get_food_cart(self, address_id: str) -> CartSummary:
                self._record("get_food_cart", address_id)
                if self._n == 0:  # CartGuard entry — must be empty
                    self._n += 1
                    return dict(_CART_EMPTY)  # type: ignore[return-value]
                self._n += 1
                return dict(suggested_no_discount)  # type: ignore[return-value]

            async def apply_coupon(self, coupon_code: str, address_id: str) -> None:
                # Real MCP returns {}; here the coupon never actually applies (no discount).
                self._record("apply_coupon", coupon_code, address_id)

        client = ZeroDiscountClient()
        pricer = CouponPricer(client)
        option = await pricer.price(_HIT, WORK_ADDRESS_ID)
        # We DID try the suggested coupon, but it earned nothing → report no coupon.
        assert "apply_coupon" in client.call_names()
        assert option.coupon_code is None
        assert option.coupon_discount == 0
        assert option.final_to_pay == 200


class TestUserSuppliedCoupons:
    """find_deals can pass user-known coupon codes; the lowest realised price wins."""

    async def test_user_code_beats_auto_suggested(self):
        from swiggy_deal_finder.swiggy_client import CartSummary

        base: CartSummary = {
            "is_empty": False, "to_pay": 400, "coupon_applied": "AUTO",
            "coupon_discount": 0, "items": [],
        }
        after_auto: CartSummary = {
            "is_empty": False, "to_pay": 350, "coupon_applied": "AUTO",
            "coupon_discount": 50, "items": [],
        }
        after_big: CartSummary = {
            "is_empty": False, "to_pay": 280, "coupon_applied": "BIG",
            "coupon_discount": 120, "items": [],
        }

        class MultiCouponClient(FakeSwiggyClient):
            _n: int = 0
            _applied: str | None = None

            async def get_food_cart(self, address_id: str) -> CartSummary:
                self._record("get_food_cart", address_id)
                if self._n == 0:
                    self._n += 1
                    return dict(_CART_EMPTY)  # type: ignore[return-value]
                self._n += 1
                if self._applied == "AUTO":
                    return dict(after_auto)  # type: ignore[return-value]
                if self._applied == "BIG":
                    return dict(after_big)  # type: ignore[return-value]
                return dict(base)  # type: ignore[return-value]

            async def apply_coupon(self, coupon_code: str, address_id: str) -> None:
                self._record("apply_coupon", coupon_code, address_id)
                self._applied = coupon_code

        client = MultiCouponClient()
        pricer = CouponPricer(client)
        option = await pricer.price(_HIT, WORK_ADDRESS_ID, coupon_codes=["BIG"])
        applied = [c[1][0] for c in client.calls if c[0] == "apply_coupon"]
        assert applied == ["AUTO", "BIG"]  # auto-suggested first, then user code
        assert option.coupon_code == "BIG"
        assert option.coupon_discount == 120
        assert option.final_to_pay == 280


class TestCartFiller:
    """Tests for CouponPricer.price_with_filler — the multi-line cart-filler probe."""

    # ------------------------------------------------------------------
    # Fake client that models a min-cart coupon "FILL" (threshold ₹249):
    # discount only when subtotal >= 249.
    # ------------------------------------------------------------------

    # Price map used by the fake to compute to_pay from the items list.
    _PRICES: dict[str, int] = {
        "81574197": 200,   # dish  (Chicken Briyani)
        "c001": 30,        # Chutney (qty >1 filler)
        "s001": 49,        # Side (qty 1 filler)
    }
    _COUPON = "FILL"
    _THRESHOLD = 249
    _COUPON_DISCOUNT = 40

    def _make_client(self):
        from swiggy_deal_finder.swiggy_client import CartSummary

        prices = self._PRICES
        threshold = self._THRESHOLD
        coupon = self._COUPON
        discount = self._COUPON_DISCOUNT

        class FillerClient(FakeSwiggyClient):
            """Stateful fake that tracks items added via update_food_cart_items
            and applies a min-cart coupon when subtotal >= threshold."""

            def __init__(self) -> None:
                super().__init__()
                self._lines: list[tuple[str, int]] = []
                self._coupon_applied: str | None = None
                self._guard_read_done: bool = False

            def _subtotal(self) -> int:
                return sum(prices.get(mid, 0) * qty for mid, qty in self._lines)

            async def get_food_cart(self, address_id: str) -> CartSummary:
                self._record("get_food_cart", address_id)
                # First call is CartGuard entry — must return empty.
                if not self._guard_read_done:
                    self._guard_read_done = True
                    return dict(_CART_EMPTY)  # type: ignore[return-value]
                sub = self._subtotal()
                if sub == 0:
                    return dict(_CART_EMPTY)  # type: ignore[return-value]
                if self._coupon_applied == coupon and sub >= threshold:
                    to_pay = sub - discount
                    return {  # type: ignore[return-value]
                        "is_empty": False,
                        "to_pay": to_pay,
                        "coupon_applied": coupon,
                        "coupon_discount": discount,
                        "items": [],
                    }
                return {  # type: ignore[return-value]
                    "is_empty": False,
                    "to_pay": sub,
                    "coupon_applied": coupon,  # auto-suggested even before apply
                    "coupon_discount": 0,
                    "items": [],
                }

            async def update_food_cart_items(
                self,
                restaurant_id: str,
                address_id: str,
                items: list[tuple[str, int]],
            ) -> dict:
                self._record("update_food_cart_items", restaurant_id, address_id, items)
                self._lines = list(items)
                return {}

            async def apply_coupon(self, coupon_code: str, address_id: str) -> None:
                self._record("apply_coupon", coupon_code, address_id)
                self._coupon_applied = coupon_code

            async def flush_cart(self) -> None:
                self._record("flush_cart")
                self._lines = []
                self._coupon_applied = None
                self._guard_read_done = False

        return FillerClient()

    # dish: 200, filler: chutney×2=60 + side×1=49 → subtotal 309 >= 249 → coupon applies
    def _hit(self) -> DishHit:
        return DishHit(
            restaurant=RESTAURANT_AMBUR,
            item_id="81574197",
            item_name="Chicken Briyani",
            base_price=200,
        )

    def _filler_above_threshold(self):
        """Returns filler that brings subtotal to 309 (dish 200 + chutney×2 + side×1)."""
        from swiggy_deal_finder.models import MenuItem
        chutney = MenuItem(id="c001", name="Chutney", price=30, in_stock=True)
        side = MenuItem(id="s001", name="Side Salad", price=49, in_stock=True)
        # (item, quantity): chutney×2 (qty>1 line), side×1
        return [(chutney, 2), (side, 1)]

    def _filler_below_threshold(self):
        """Returns filler that keeps subtotal at 230 (dish 200 + chutney×1=30) < 249."""
        from swiggy_deal_finder.models import MenuItem
        chutney = MenuItem(id="c001", name="Chutney", price=30, in_stock=True)
        return [(chutney, 1)]

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    async def test_update_food_cart_items_called_once_with_dish_first(self):
        """update_food_cart_items is called exactly once; dish line comes first,
        filler lines follow (including a qty>1 line)."""
        client = self._make_client()
        filler = self._filler_above_threshold()
        await CouponPricer(client).price_with_filler(
            self._hit(), WORK_ADDRESS_ID, filler, coupon_codes=[self._COUPON]
        )
        calls = [c for c in client.calls if c[0] == "update_food_cart_items"]
        assert len(calls) == 1
        _, args, _ = calls[0]
        _restaurant_id, _address_id, lines = args
        # Dish line must be first: (item_id, 1)
        assert lines[0] == ("81574197", 1)
        # Filler lines: chutney×2 then side×1
        assert lines[1] == ("c001", 2)
        assert lines[2] == ("s001", 1)

    async def test_filler_result_filler_field(self):
        """FillerResult.filler is [(name, unit_price, quantity)] matching the inputs."""
        client = self._make_client()
        filler = self._filler_above_threshold()
        result = await CouponPricer(client).price_with_filler(
            self._hit(), WORK_ADDRESS_ID, filler, coupon_codes=[self._COUPON]
        )
        assert result.filler == [
            ("Chutney", 30, 2),
            ("Side Salad", 49, 1),
        ]

    async def test_filler_result_subtotal(self):
        """FillerResult.subtotal == dish_price + Σ(unit_price × qty)."""
        client = self._make_client()
        filler = self._filler_above_threshold()
        result = await CouponPricer(client).price_with_filler(
            self._hit(), WORK_ADDRESS_ID, filler, coupon_codes=[self._COUPON]
        )
        # 200 + 30×2 + 49×1 = 200 + 60 + 49 = 309
        assert result.subtotal == 309

    async def test_coupon_applied_when_subtotal_above_threshold(self):
        """When subtotal >= threshold, coupon is credited and final_to_pay is discounted."""
        client = self._make_client()
        filler = self._filler_above_threshold()
        result = await CouponPricer(client).price_with_filler(
            self._hit(), WORK_ADDRESS_ID, filler, coupon_codes=[self._COUPON]
        )
        assert result.coupon_code == self._COUPON
        assert result.coupon_discount == self._COUPON_DISCOUNT
        # 309 - 40 = 269
        assert result.final_to_pay == 309 - self._COUPON_DISCOUNT

    async def test_no_coupon_when_subtotal_below_threshold(self):
        """When subtotal < threshold, coupon_code is None and discount is 0."""
        client = self._make_client()
        filler = self._filler_below_threshold()
        result = await CouponPricer(client).price_with_filler(
            self._hit(), WORK_ADDRESS_ID, filler, coupon_codes=[self._COUPON]
        )
        # 200 + 30×1 = 230 < 249
        assert result.subtotal == 230
        assert result.coupon_code is None
        assert result.coupon_discount == 0
        assert result.final_to_pay == 230

    async def test_flush_always_called_on_success(self):
        """flush_cart must appear in call_names after a successful price_with_filler."""
        client = self._make_client()
        filler = self._filler_above_threshold()
        await CouponPricer(client).price_with_filler(
            self._hit(), WORK_ADDRESS_ID, filler, coupon_codes=[self._COUPON]
        )
        assert "flush_cart" in client.call_names()

    async def test_flush_called_even_when_cart_read_returns_no_to_pay(self):
        """When get_food_cart returns to_pay=None after add, PricingError is raised
        but flush_cart must still be called."""
        from swiggy_deal_finder.pricing import PricingError
        from swiggy_deal_finder.swiggy_client import CartSummary

        class NullPayClient(FakeSwiggyClient):
            _guard_read_done: bool = False

            async def get_food_cart(self, address_id: str) -> CartSummary:
                self._record("get_food_cart", address_id)
                if not self._guard_read_done:
                    self._guard_read_done = True
                    return dict(_CART_EMPTY)  # type: ignore[return-value]
                # All subsequent reads: to_pay is None (broken cart)
                return {  # type: ignore[return-value]
                    "is_empty": False,
                    "to_pay": None,
                    "coupon_applied": None,
                    "coupon_discount": 0,
                    "items": [],
                }

            async def update_food_cart_items(self, *args, **kwargs) -> dict:
                self._record("update_food_cart_items", *args, **kwargs)
                return {}

        client = NullPayClient()
        from swiggy_deal_finder.models import MenuItem
        filler = [(MenuItem(id="c001", name="Chutney", price=30, in_stock=True), 2)]
        with pytest.raises(PricingError):
            await CouponPricer(client).price_with_filler(
                self._hit(), WORK_ADDRESS_ID, filler
            )
        assert "flush_cart" in client.call_names()


class TestQuantityPricing:
    """Quantity is threaded to the cart; a larger quantity can unlock a min-cart coupon."""

    def _client(self):
        from swiggy_deal_finder.swiggy_client import CartSummary

        class QuantityClient(FakeSwiggyClient):
            def __init__(self) -> None:
                super().__init__()
                self._qty = 0
                self._coupon = False

            async def get_food_cart(self, address_id: str) -> CartSummary:
                self._record("get_food_cart", address_id)
                if self._qty == 0:  # CartGuard entry — empty
                    return dict(_CART_EMPTY)  # type: ignore[return-value]
                item_total = 150 * self._qty
                # Coupon SAVE needs a 2+ cart (min-cart); applies only then.
                if self._coupon and self._qty >= 2:
                    return {  # type: ignore[return-value]
                        "is_empty": False, "to_pay": item_total - 100,
                        "coupon_applied": "SAVE", "coupon_discount": 100, "items": [],
                    }
                return {  # type: ignore[return-value]
                    "is_empty": False, "to_pay": item_total,
                    "coupon_applied": "SAVE", "coupon_discount": 0, "items": [],
                }

            async def update_food_cart(self, restaurant_id, address_id, menu_item_id, quantity):
                self._record("update_food_cart", restaurant_id, address_id, menu_item_id, quantity)
                self._qty = quantity
                return {}

            async def apply_coupon(self, coupon_code: str, address_id: str) -> None:
                self._record("apply_coupon", coupon_code, address_id)
                if coupon_code == "SAVE":
                    self._coupon = True

            async def flush_cart(self) -> None:
                self._record("flush_cart")
                self._qty = 0
                self._coupon = False

        return QuantityClient()

    async def test_single_item_below_min_cart_gets_no_coupon(self):
        client = self._client()
        option = await CouponPricer(client).price(_HIT, WORK_ADDRESS_ID, quantity=1)
        assert option.quantity == 1
        assert option.coupon_code is None
        assert option.final_to_pay == 150

    async def test_quantity_two_unlocks_coupon(self):
        client = self._client()
        option = await CouponPricer(client).price(_HIT, WORK_ADDRESS_ID, quantity=2)
        add_calls = [c for c in client.calls if c[0] == "update_food_cart"]
        assert add_calls[0][1][3] == 2  # quantity passed through to the cart
        assert option.quantity == 2
        assert option.coupon_code == "SAVE"
        assert option.coupon_discount == 100
        assert option.final_to_pay == 200  # 300 − 100, i.e. ₹100 each for two
