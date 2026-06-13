"""Tests for ranking.py — sort by final_to_pay asc, then avg_rating desc."""

from swiggy_deal_finder.models import DishHit, PricedOption, Restaurant
from swiggy_deal_finder.ranking import rank


def _make_option(
    restaurant_id: str,
    avg_rating: float | None,
    final_to_pay: int,
    coupon_discount: int = 0,
) -> PricedOption:
    restaurant = Restaurant(
        id=restaurant_id,
        name=f"Restaurant {restaurant_id}",
        distance_km=1.0,
        avg_rating=avg_rating,
        is_open=True,
    )
    hit = DishHit(
        restaurant=restaurant,
        item_id="item1",
        item_name="Biryani",
        base_price=350,
    )
    return PricedOption(
        hit=hit,
        coupon_code=None,
        coupon_discount=coupon_discount,
        final_to_pay=final_to_pay,
    )


class TestRank:
    def test_sorts_by_final_to_pay_ascending(self):
        options = [
            _make_option("A", 4.0, 500),
            _make_option("B", 4.0, 300),
            _make_option("C", 4.0, 450),
        ]
        result = rank(options)
        assert [o.final_to_pay for o in result] == [300, 450, 500]

    def test_tiebreak_by_avg_rating_descending(self):
        """Equal price: higher rating wins."""
        options = [
            _make_option("A", 3.5, 300),
            _make_option("B", 4.4, 300),
            _make_option("C", 4.0, 300),
        ]
        result = rank(options)
        assert [o.hit.restaurant.id for o in result] == ["B", "C", "A"]

    def test_none_rating_sorts_last_in_tiebreak(self):
        """None avg_rating is considered worse than any numeric rating in a tiebreak."""
        options = [
            _make_option("A", None, 300),
            _make_option("B", 4.4, 300),
            _make_option("C", 3.0, 300),
        ]
        result = rank(options)
        # B (4.4) first, then C (3.0), then A (None)
        assert result[-1].hit.restaurant.id == "A"
        assert result[0].hit.restaurant.id == "B"

    def test_price_beats_rating(self):
        """Cheaper beats higher-rated when prices differ."""
        options = [
            _make_option("A", 4.9, 400),  # high rating but expensive
            _make_option("B", 3.0, 250),  # low rating but cheap
        ]
        result = rank(options)
        assert result[0].hit.restaurant.id == "B"

    def test_empty_list(self):
        assert rank([]) == []

    def test_single_option(self):
        options = [_make_option("A", 4.0, 350)]
        result = rank(options)
        assert len(result) == 1

    def test_returns_new_list(self):
        """rank must not mutate the input list."""
        options = [
            _make_option("A", 4.0, 500),
            _make_option("B", 4.0, 300),
        ]
        original_order = [o.hit.restaurant.id for o in options]
        rank(options)
        assert [o.hit.restaurant.id for o in options] == original_order
