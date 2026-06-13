"""Unit tests for swiggy_client.py parser functions.

We test parse_cart, parse_restaurants, parse_menu_items directly — no network needed.
"""

from swiggy_deal_finder.swiggy_client import parse_cart, parse_menu_items, parse_restaurants


class TestParseCart:
    def test_empty_body_returns_empty_cart(self):
        """apply_food_coupon/fetch_food_coupons return {}; parse_cart must not crash."""
        result = parse_cart({})
        assert result["is_empty"] is True
        assert result["to_pay"] is None
        assert result["coupon_applied"] is None
        assert result["coupon_discount"] == 0
        assert result["items"] == []

    def test_data_null_returns_empty_cart(self):
        """get_food_cart returns {data: null} when cart is empty."""
        result = parse_cart({"data": None})
        assert result["is_empty"] is True
        assert result["to_pay"] is None
        assert result["coupon_applied"] is None
        assert result["coupon_discount"] == 0
        assert result["items"] == []

    def test_populated_cart_parsed_correctly(self):
        """Full cart shape from the spec: pricing.to_pay, offers, items."""
        raw = {
            "data": {
                "cart_id": "abc123",
                "items": [
                    {
                        "menu_item_id": "81574197",
                        "name": "Chicken Briyani",
                        "quantity": 1,
                        "subtotal": 350,
                        "total": 350,
                        "final_price": 350,
                    }
                ],
                "pricing": {
                    "item_total": 350,
                    "delivery_charge": 53,
                    "taxes_and_charges": 0,
                    "to_pay": 403,
                },
                "offers": {
                    "coupon_applied": "SAVEBITE",
                    "coupon_discount": 0,
                    "free_delivery_applied": False,
                },
            }
        }
        result = parse_cart(raw)
        assert result["is_empty"] is False
        assert result["to_pay"] == 403
        assert result["coupon_applied"] == "SAVEBITE"
        assert result["coupon_discount"] == 0
        assert len(result["items"]) == 1
        assert result["items"][0]["menu_item_id"] == "81574197"

    def test_populated_cart_after_coupon_applied(self):
        """After apply_food_coupon + re-read: to_pay drops, coupon_discount filled."""
        raw = {
            "data": {
                "cart_id": "abc123",
                "items": [
                    {
                        "menu_item_id": "81574197",
                        "name": "Chicken Briyani",
                        "quantity": 1,
                        "subtotal": 350,
                        "total": 270,
                        "final_price": 270,
                    }
                ],
                "pricing": {
                    "item_total": 350,
                    "delivery_charge": 51,
                    "taxes_and_charges": 0,
                    "to_pay": 321,
                },
                "offers": {
                    "coupon_applied": "SAVEBITE",
                    "coupon_discount": 80,
                    "free_delivery_applied": False,
                },
            }
        }
        result = parse_cart(raw)
        assert result["to_pay"] == 321
        assert result["coupon_discount"] == 80
        assert result["coupon_applied"] == "SAVEBITE"


class TestParseRestaurants:
    def test_basic_mapping(self):
        raw = [
            {
                "id": "62683",
                "name": "Ambur Star Biryani",
                "distanceKm": 0.4,
                "avgRating": 4.4,
                "availability": {"opened": True},
            }
        ]
        results = parse_restaurants(raw)
        assert len(results) == 1
        r = results[0]
        assert r.id == "62683"
        assert r.name == "Ambur Star Biryani"
        assert r.distance_km == 0.4
        assert r.avg_rating == 4.4
        assert r.is_open is True

    def test_missing_avg_rating_defaults_none(self):
        raw = [{"id": "1", "name": "X", "distanceKm": 1.0}]
        results = parse_restaurants(raw)
        assert results[0].avg_rating is None

    def test_missing_availability_defaults_open(self):
        """No availability flag → assume open (optimistic; documented assumption)."""
        raw = [{"id": "1", "name": "X", "distanceKm": 1.0, "avgRating": 4.0}]
        results = parse_restaurants(raw)
        assert results[0].is_open is True

    def test_closed_restaurant(self):
        raw = [
            {
                "id": "2",
                "name": "Closed Place",
                "distanceKm": 2.0,
                "avgRating": 3.5,
                "availability": {"opened": False},
            }
        ]
        results = parse_restaurants(raw)
        assert results[0].is_open is False

    def test_empty_list(self):
        assert parse_restaurants([]) == []

    def test_skips_entry_missing_distance(self):
        """A restaurant without distanceKm must be skipped, not crash the whole search."""
        raw = [
            {"id": "1", "name": "No Distance", "avgRating": 4.0},  # missing distanceKm
            {"id": "2", "name": "Good", "distanceKm": 1.5, "avgRating": 4.2},
        ]
        results = parse_restaurants(raw)
        assert [r.id for r in results] == ["2"]

    def test_skips_entry_with_null_distance(self):
        raw = [
            {"id": "1", "name": "Null Dist", "distanceKm": None, "avgRating": 4.0},
            {"id": "2", "name": "Good", "distanceKm": 2.0},
        ]
        assert [r.id for r in parse_restaurants(raw)] == ["2"]


class TestParseMenuItems:
    def test_basic_mapping(self):
        raw = [{"id": "81574197", "name": "Chicken Briyani", "price": 350, "inStock": 1}]
        items = parse_menu_items(raw)
        assert len(items) == 1
        m = items[0]
        assert m.id == "81574197"
        assert m.name == "Chicken Briyani"
        assert m.price == 350
        assert m.in_stock is True

    def test_out_of_stock(self):
        raw = [{"id": "1", "name": "X", "price": 100, "inStock": 0}]
        items = parse_menu_items(raw)
        assert items[0].in_stock is False

    def test_missing_in_stock_defaults_true(self):
        """Absent inStock → treat as in-stock (spec says 0/1; tolerate missing key)."""
        raw = [{"id": "1", "name": "Y", "price": 200}]
        items = parse_menu_items(raw)
        assert items[0].in_stock is True

    def test_empty_list(self):
        assert parse_menu_items([]) == []

    def test_skips_item_missing_price(self):
        """A variant-only item without a top-level price must be skipped, not crash."""
        raw = [
            {"id": "1", "name": "Dosa (variants only)"},  # missing price
            {"id": "2", "name": "Masala Dosa", "price": 90, "inStock": 1},
        ]
        items = parse_menu_items(raw)
        assert [m.id for m in items] == ["2"]
