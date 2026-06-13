"""Ranking of PricedOptions.

Sort key: final_to_pay ascending, then avg_rating descending.
None avg_rating sorts after any numeric rating (represented as -inf so it loses all
comparisons against real ratings in a descending sort).
"""

from swiggy_deal_finder.models import PricedOption

_NO_RATING_SENTINEL = -1.0  # sorts last when ordering avg_rating descending


def rank(options: list[PricedOption]) -> list[PricedOption]:
    """Return a new list of PricedOptions sorted cheapest-first, then highest-rated."""
    return sorted(
        options,
        key=lambda o: (
            o.final_to_pay,
            # Negate rating for descending order; None → sentinel (last)
            -(o.hit.restaurant.avg_rating
              if o.hit.restaurant.avg_rating is not None
              else _NO_RATING_SENTINEL),
        ),
    )
