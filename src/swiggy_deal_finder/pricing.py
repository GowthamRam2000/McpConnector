"""CartGuard and CouponPricer — the highest-risk module.

Design constraints (from spike findings):
  - The cart is a global singleton on the Swiggy account. We MUST not clobber
    an existing cart, so CartGuard refuses entry if the cart is non-empty.
  - apply_food_coupon returns {} (empty body); the truth is always in a fresh
    get_food_cart call — CouponPricer never trusts the apply response.
  - On exit (only reached when the cart was empty at entry), flush_cart always runs
    — on success and on any unhandled exception from the body — to remove probe items.
  - A non-empty cart at entry is NEVER flushed: we must not destroy a cart we did not
    create. CartGuard raises CartNotEmptyError and leaves the cart untouched.
  - NEVER call place_food_order anywhere in this module.
"""

from types import TracebackType

from swiggy_deal_finder.models import DishHit, PricedOption
from swiggy_deal_finder.swiggy_client import SwiggyClient


class CartNotEmptyError(Exception):
    """Raised when pricing is requested but the user's Swiggy cart is not empty.

    The caller should surface this to the user rather than attempting to clear
    an existing cart automatically (to avoid silently losing real orders).
    """


class PricingError(Exception):
    """Raised when a candidate cannot be priced (e.g. cart has no to_pay after add).

    DealFinder skips the candidate rather than surfacing a broken ₹0 option that
    would wrongly rank as the cheapest.
    """


class CartGuard:
    """Async context manager that enforces a clean-cart invariant.

    Entry: reads the cart. If non-empty, raises CartNotEmptyError WITHOUT touching it
           (we must not clobber a cart we did not create).
    Exit:  always calls flush_cart (only reached when entry found an empty cart),
           removing probe items regardless of how the body completes.

    Usage::

        async with CartGuard(client, address_id):
            # only reached when cart was empty at entry
            ...  # pricing mutations go here

    Do not use CartGuard if you need to preserve an existing cart —
    there is no restore path (we cannot safely re-add arbitrary prior items).
    """

    def __init__(self, client: SwiggyClient, address_id: str) -> None:
        self._client = client
        self._address_id = address_id

    async def __aenter__(self) -> CartGuard:
        cart = await self._client.get_food_cart(self._address_id)
        if not cart["is_empty"]:
            # Never flush a cart we did not create — preserve the user's items and
            # let the caller surface this.
            raise CartNotEmptyError(
                "Swiggy cart is not empty. Clear your cart before running Deal Finder."
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        # Always flush — we must not leave probe items in the live cart.
        await self._client.flush_cart()
        # Propagate all exceptions (return False / None)
        return False


class CouponPricer:
    """Prices a single DishHit by running the U2 coupon probe cycle.

    Cycle (from spec):
      1. update_food_cart (add 1×) inside a CartGuard (requires empty cart)
      2. get_food_cart → read auto-suggested coupon_applied
      3. If coupon suggested: apply_coupon(code) → get_food_cart AGAIN for real to_pay
         (apply returns {} — never trust it; always re-read)
      4. Build PricedOption from final cart state
      5. CartGuard.__aexit__ flushes regardless

    Never calls place_food_order.
    """

    def __init__(self, client: SwiggyClient) -> None:
        self._client = client

    async def price(self, hit: DishHit, address_id: str) -> PricedOption:
        async with CartGuard(self._client, address_id):
            # Add item to cart
            await self._client.update_food_cart(
                hit.restaurant.id, address_id, hit.item_id, 1
            )

            # First cart read: Swiggy auto-suggests the best regular coupon here
            cart = await self._client.get_food_cart(address_id)

            coupon_code = cart["coupon_applied"]
            to_pay = cart["to_pay"]
            coupon_discount = cart["coupon_discount"]

            if coupon_code:
                # apply_coupon returns {} — its only purpose is to trigger the side-effect.
                # We re-read immediately; the re-read is the source of truth.
                await self._client.apply_coupon(coupon_code, address_id)
                cart = await self._client.get_food_cart(address_id)
                to_pay = cart["to_pay"]
                coupon_discount = cart["coupon_discount"]

            if to_pay is None:
                # A populated cart always has to_pay; None means the add/read failed.
                # Raise so DealFinder skips this candidate instead of surfacing a
                # ₹0 option that would wrongly rank as the cheapest.
                raise PricingError(
                    f"no to_pay for {hit.restaurant.id}/{hit.item_id} after add"
                )

            return PricedOption(
                hit=hit,
                coupon_code=coupon_code,
                coupon_discount=coupon_discount,
                final_to_pay=to_pay,
            )
