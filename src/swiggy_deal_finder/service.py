"""DealFinder — top-level orchestrator.

Pipeline:
  1. CandidateService.find → all DishHits matching dish + ≤7km + open
  2. Sort hits by base_price asc (cheapest items probed first; avoids wasting
     cart cycles on obviously expensive options)
  3. Take top_n hits
  4. CouponPricer.price per hit, sequentially (Swiggy cart is a singleton — we
     cannot price in parallel); if a candidate raises, skip it (log-level warning
     in a real app; here we silently continue so tests can assert the skip)
  5. rank(results) → cheapest final price first, then highest rating
"""

import logging

from swiggy_deal_finder.candidates import CandidateService
from swiggy_deal_finder.models import PricedOption
from swiggy_deal_finder.pricing import CartNotEmptyError, CouponPricer
from swiggy_deal_finder.ranking import rank
from swiggy_deal_finder.swiggy_client import SwiggyClient

logger = logging.getLogger(__name__)


class DealFinder:
    def __init__(self, client: SwiggyClient) -> None:
        self._client = client
        self._candidates = CandidateService(client)
        self._pricer = CouponPricer(client)

    async def find_deals(
        self,
        dish: str,
        address_id: str,
        top_n: int = 5,
        portion: str = "regular",
        category: str | None = None,
        min_rating: float | None = None,
        restaurant_name: str | None = None,
        quantity: int = 1,
        coupon_codes: list[str] | None = None,
    ) -> list[PricedOption]:
        """Return up to top_n PricedOptions for dish, ranked cheapest-first.

        Raises CartNotEmptyError up front if the user's cart is non-empty (pricing
        needs an empty cart) so the caller can tell the user to clear it — rather than
        returning a confusing empty result. Individual candidates that fail pricing
        with a transient error are skipped with a warning — a partial result beats a
        full abort.

        portion: "regular" (default) excludes shrink-sized items unless the query asks
        for them; "mini" keeps only shrink-sized items; "any" disables the portion filter.
        category: broad food category for restaurant search (e.g. "dosa" for "ghee roast").
        min_rating: optional USER-supplied rating floor; restaurants below it are excluded
        (unrated kept). None = no filter.
        restaurant_name: optional chain/restaurant name substring to narrow results to one
        restaurant (case-insensitive). None = no filter.
        quantity: how many of the item to price (default 1). A larger quantity can meet a
        coupon's min-cart threshold — the USER decides this; we never assume more than 1.
        coupon_codes: optional user-supplied coupon codes to try in addition to Swiggy's
        single auto-suggested best; the lowest realised price wins.
        See CandidateService.find for details.
        """
        # Fail fast on a busy cart before doing any search/pricing work.
        cart = await self._client.get_food_cart(address_id)
        if not cart["is_empty"]:
            raise CartNotEmptyError(
                "Swiggy cart is not empty. Clear your cart before running Deal Finder."
            )

        hits = await self._candidates.find(  # type: ignore[arg-type]
            dish, address_id, portion=portion, category=category, min_rating=min_rating,
            restaurant_name=restaurant_name,
        )
        # Sort by base_price asc before slicing so we probe cheapest options first.
        hits_sorted = sorted(hits, key=lambda h: h.base_price)
        top_hits = hits_sorted[:top_n]

        results: list[PricedOption] = []
        for hit in top_hits:
            try:
                option = await self._pricer.price(
                    hit, address_id, quantity=quantity, coupon_codes=coupon_codes
                )
                results.append(option)
            except Exception:
                # A failed candidate should not abort the whole run.
                # In production this would emit a structured warning.
                logger.warning(
                    "Skipping candidate %s/%s due to pricing error",
                    hit.restaurant.id,
                    hit.item_id,
                    exc_info=True,
                )

        return rank(results)
