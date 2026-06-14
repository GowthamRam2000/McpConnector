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

from swiggy_deal_finder.models import DishHit, FillerResult, MenuItem, PricedOption
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
    """Prices a single DishHit by running the coupon probe cycle.

    Cycle:
      1. update_food_cart (add `quantity`×) inside a CartGuard (requires empty cart)
      2. get_food_cart → base to_pay (no coupon yet) + Swiggy's auto-suggested coupon
      3. For each candidate coupon (the auto-suggested one + any user-supplied codes):
         apply_coupon(code) → get_food_cart AGAIN for the real to_pay/discount
         (apply returns {} — never trust it; always re-read). Keep the code that
         yields the LOWEST to_pay, counting a coupon ONLY when its discount > 0.
      4. Build PricedOption from the best result
      5. CartGuard.__aexit__ flushes regardless

    Why discount > 0 gates the coupon: Swiggy auto-suggests a coupon in the cart even
    when it is NOT actually applied (e.g. a min-cart threshold is not met), reporting
    coupon_discount=0.  Surfacing that as "coupon applied" would be wrong, so we only
    credit a coupon that genuinely reduced the bill.

    Swiggy's MCP exposes no browsable coupon list (fetch_food_coupons returns {}), so
    the only codes we can evaluate are the single auto-suggested best plus codes the
    user supplies.  Never calls place_food_order.
    """

    def __init__(self, client: SwiggyClient) -> None:
        self._client = client

    async def price(
        self,
        hit: DishHit,
        address_id: str,
        quantity: int = 1,
        coupon_codes: list[str] | None = None,
    ) -> PricedOption:
        async with CartGuard(self._client, address_id):
            # Add `quantity` of the item. A larger quantity can meet a coupon's
            # min-cart threshold — the caller decides quantity (we never assume it).
            await self._client.update_food_cart(
                hit.restaurant.id, address_id, hit.item_id, quantity
            )

            # First read: base price (no coupon applied yet) + Swiggy's auto-suggestion.
            cart = await self._client.get_food_cart(address_id)
            base_to_pay = cart["to_pay"]
            if base_to_pay is None:
                # A populated cart always has to_pay; None means the add/read failed.
                # Raise so DealFinder skips this candidate instead of surfacing a
                # ₹0 option that would wrongly rank as the cheapest.
                raise PricingError(
                    f"no to_pay for {hit.restaurant.id}/{hit.item_id} after add"
                )

            best_code, best_discount, best_to_pay = await self._best_coupon(
                address_id, base_to_pay, cart["coupon_applied"], coupon_codes
            )
            return PricedOption(
                hit=hit,
                coupon_code=best_code,
                coupon_discount=best_discount,
                final_to_pay=best_to_pay,
                quantity=quantity,
            )

    async def _best_coupon(
        self,
        address_id: str,
        base_to_pay: int,
        auto_suggested: str | None,
        coupon_codes: list[str] | None,
    ) -> tuple[str | None, int, int]:
        """Try the auto-suggested coupon plus any user codes; return the best
        (code, discount, to_pay), counting a coupon only when its discount > 0.

        Returns (None, 0, base_to_pay) when nothing beats the no-coupon price.
        """
        candidates: list[str] = []
        if auto_suggested:
            candidates.append(auto_suggested)
        if coupon_codes:
            candidates.extend(coupon_codes)

        best_code: str | None = None
        best_discount = 0
        best_to_pay = base_to_pay

        seen: set[str] = set()
        for code in candidates:
            if not code or code in seen:
                continue
            seen.add(code)
            # apply_coupon returns {} — re-read the cart for the truth.
            await self._client.apply_coupon(code, address_id)
            applied = await self._client.get_food_cart(address_id)
            to_pay = applied["to_pay"]
            discount = applied["coupon_discount"]
            if to_pay is not None and discount > 0 and to_pay < best_to_pay:
                best_to_pay = to_pay
                best_discount = discount
                # Attribute the code the cart reports as applied (robust if an
                # invalid code silently left a prior coupon in place).
                best_code = applied["coupon_applied"] or code

        return best_code, best_discount, best_to_pay

    async def price_with_filler(
        self,
        hit: DishHit,
        address_id: str,
        filler: list[tuple[MenuItem, int]],
        coupon_codes: list[str] | None = None,
    ) -> FillerResult:
        """Price the dish plus `filler` (item, quantity) lines as one cart, then apply
        the best coupon — used to clear a coupon's minimum-cart threshold.

        The caller (the tool) computes the filler plan from the menu and offer; this
        method only mutates the cart and reads the realised post-coupon bill. The cart
        is built in ONE update so it is defined deterministically from the empty start.
        """
        async with CartGuard(self._client, address_id):
            lines: list[tuple[str, int]] = [(hit.item_id, 1)]
            lines.extend((item.id, qty) for item, qty in filler)
            await self._client.update_food_cart_items(hit.restaurant.id, address_id, lines)

            cart = await self._client.get_food_cart(address_id)
            base_to_pay = cart["to_pay"]
            if base_to_pay is None:
                raise PricingError(
                    f"no to_pay for {hit.restaurant.id}/{hit.item_id} with filler"
                )

            best_code, best_discount, best_to_pay = await self._best_coupon(
                address_id, base_to_pay, cart["coupon_applied"], coupon_codes
            )

            subtotal = hit.base_price + sum(item.price * qty for item, qty in filler)
            return FillerResult(
                dish_name=hit.item_name,
                dish_price=hit.base_price,
                filler=[(item.name, item.price, qty) for item, qty in filler],
                subtotal=subtotal,
                coupon_code=best_code,
                coupon_discount=best_discount,
                final_to_pay=best_to_pay,
            )
