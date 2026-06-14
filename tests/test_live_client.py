"""Unit tests for LiveSwiggyClient — all network calls are stubbed.

A FakeClientSession replaces mcp.ClientSession so no subprocess is spawned and
no network is touched. Each test seeds call_tool to return canned JSON text that
matches the envelopes observed from the live Swiggy MCP (2026-06-13).

Coverage:
  - get_addresses    → correct Address mapping (addressTag label, addressLine)
  - search_restaurants → envelope unwrapping (restaurants[]) + availabilityStatus parsing
  - get_restaurant_menu → category flattening + dedup + parse_menu_items
  - get_food_cart    → parse_cart on data:null (empty) and populated
  - update_food_cart → correct tool name + cartItems arg shape
  - apply_coupon     → correct tool name + couponCode arg
  - flush_cart       → correct tool name, no args required
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from swiggy_deal_finder.live_client import LiveSwiggyClient

# ── Helpers ─────────────────────────────────────────────────────────────────────────────


def _text_block(payload: Any) -> MagicMock:
    """Return a fake content block whose .text is the JSON-encoded payload."""
    block = MagicMock()
    block.text = json.dumps(payload)
    return block


def _result(payload: Any) -> MagicMock:
    """Wrap payload as a fake call_tool result with one text content block."""
    r = MagicMock()
    r.content = [_text_block(payload)]
    return r


def _empty_result() -> MagicMock:
    """Fake result with no content (e.g. apply_food_coupon → {})."""
    r = MagicMock()
    r.content = [_text_block({})]
    return r


async def _make_client(call_tool_mock: AsyncMock) -> LiveSwiggyClient:
    """Build a LiveSwiggyClient whose internal session uses call_tool_mock.

    Bypasses the real stdio_client / subprocess entirely.
    """
    client = LiveSwiggyClient()
    fake_session = MagicMock()
    fake_session.call_tool = call_tool_mock
    fake_session.initialize = AsyncMock()
    # Inject the session directly so connect() is not needed
    client._session = fake_session
    return client


# ── get_addresses ────────────────────────────────────────────────────────────────────────


class TestGetAddresses:
    async def test_maps_address_fields_correctly(self):
        payload = {
            "addresses": [
                {
                    "id": "cug4elpnnp0lq52vuk10",
                    "addressLine": "LTIM innovation campus, Ramapuram, Chennai",
                    "addressCategory": "Work",
                    "addressTag": "Work",
                },
                {
                    "id": "50918630",
                    "addressLine": "Om muruga flats, Adambakkam, Chennai",
                    "addressCategory": "Other",
                    "addressTag": "V",
                },
            ],
            "total": 2,
        }
        mock = AsyncMock(return_value=_result(payload))
        client = await _make_client(mock)

        addresses = await client.get_addresses()

        assert len(addresses) == 2
        mock.assert_called_once_with("get_addresses", {})

        work = addresses[0]
        assert work.id == "cug4elpnnp0lq52vuk10"
        assert work.label == "Work"  # addressTag preferred
        assert "Ramapuram" in work.line

        other = addresses[1]
        assert other.id == "50918630"
        assert other.label == "V"  # addressTag preferred over addressCategory

    async def test_empty_addresses_returns_empty_list(self):
        mock = AsyncMock(return_value=_result({"addresses": [], "total": 0}))
        client = await _make_client(mock)
        assert await client.get_addresses() == []


# ── search_restaurants ───────────────────────────────────────────────────────────────────


class TestSearchRestaurants:
    _LIVE_PAYLOAD = {
        "restaurants": [
            {
                "id": "62683",
                "name": "Ambur Star Briyani",
                "avgRating": 4.4,
                "distanceKm": 0.4,
                "availabilityStatus": "OPEN",
            },
            {
                "id": "999",
                "name": "Closed Biryani House",
                "avgRating": 3.5,
                "distanceKm": 2.0,
                "availabilityStatus": "CLOSED",
            },
        ],
        "total": 2,
        "query": "biryani",
    }

    async def test_unwraps_restaurants_key(self):
        mock = AsyncMock(return_value=_result(self._LIVE_PAYLOAD))
        client = await _make_client(mock)

        results = await client.search_restaurants("biryani", "cug4elpnnp0lq52vuk10")

        mock.assert_called_once_with(
            "search_restaurants",
            {"query": "biryani", "addressId": "cug4elpnnp0lq52vuk10", "offset": 0},
        )
        assert len(results) == 2

    async def test_availability_status_open_parsed_correctly(self):
        mock = AsyncMock(return_value=_result(self._LIVE_PAYLOAD))
        client = await _make_client(mock)
        results = await client.search_restaurants("biryani", "cug4elpnnp0lq52vuk10")

        open_r = next(r for r in results if r.id == "62683")
        closed_r = next(r for r in results if r.id == "999")
        assert open_r.is_open is True
        assert closed_r.is_open is False

    async def test_restaurant_fields_mapped(self):
        mock = AsyncMock(return_value=_result(self._LIVE_PAYLOAD))
        client = await _make_client(mock)
        results = await client.search_restaurants("biryani", "addr")

        r = results[0]
        assert r.id == "62683"
        assert r.name == "Ambur Star Briyani"
        assert r.avg_rating == pytest.approx(4.4)
        assert r.distance_km == pytest.approx(0.4)

    async def test_unavailable_status_is_closed(self):
        payload = {
            "restaurants": [
                {
                    "id": "1",
                    "name": "X",
                    "avgRating": 4.0,
                    "distanceKm": 1.0,
                    "availabilityStatus": "UNAVAILABLE",
                }
            ],
            "total": 1,
            "query": "x",
        }
        mock = AsyncMock(return_value=_result(payload))
        client = await _make_client(mock)
        results = await client.search_restaurants("x", "addr")
        assert results[0].is_open is False


# ── get_restaurant_menu ──────────────────────────────────────────────────────────────────


class TestGetRestaurantMenu:
    _LIVE_PAYLOAD = {
        "restaurant": {"id": "62683", "name": "Ambur Star Briyani"},
        "categories": [
            {
                "title": "Briyani",
                "categoryId": "27470116",
                "items": [
                    {"id": "18776699", "name": "Mutton Briyani", "price": 450, "inStock": 1},
                    {"id": "81574197", "name": "Chicken Briyani", "price": 350, "inStock": 1},
                    {"id": "81574199", "name": "Egg Briyani", "price": 300, "inStock": 1},
                ],
            },
            {
                "title": "Recommended",
                "items": [
                    # Duplicate item — must be deduped
                    {"id": "81574197", "name": "Chicken Briyani", "price": 350, "inStock": 1},
                    {"id": "16299236", "name": "Paneer 65", "price": 320, "inStock": 1},
                ],
            },
        ],
        "totalCategories": 2,
    }

    async def test_flattens_categories_and_dedupes(self):
        mock = AsyncMock(return_value=_result(self._LIVE_PAYLOAD))
        client = await _make_client(mock)

        items = await client.get_restaurant_menu("62683", "addr")

        # 4 unique items: Mutton Briyani, Chicken Briyani, Egg Briyani, Paneer 65
        assert len(items) == 4
        ids = {it.id for it in items}
        assert ids == {"18776699", "81574197", "81574199", "16299236"}

    async def test_correct_tool_and_args(self):
        mock = AsyncMock(return_value=_result(self._LIVE_PAYLOAD))
        client = await _make_client(mock)

        await client.get_restaurant_menu("62683", "addr")

        mock.assert_called_once_with(
            "get_restaurant_menu",
            {"restaurantId": "62683", "addressId": "addr", "page": 1, "pageSize": 8},
        )

    async def test_in_stock_flag_mapped(self):
        payload = {
            "restaurant": {"id": "1"},
            "categories": [
                {
                    "title": "A",
                    "items": [
                        {"id": "10", "name": "Item A", "price": 100, "inStock": 0},
                        {"id": "11", "name": "Item B", "price": 200, "inStock": 1},
                    ],
                }
            ],
        }
        mock = AsyncMock(return_value=_result(payload))
        client = await _make_client(mock)
        items = await client.get_restaurant_menu("1", "addr")
        by_id = {it.id: it for it in items}
        assert by_id["10"].in_stock is False
        assert by_id["11"].in_stock is True

    async def test_empty_categories_returns_empty_list(self):
        payload = {"restaurant": {"id": "1"}, "categories": []}
        mock = AsyncMock(return_value=_result(payload))
        client = await _make_client(mock)
        assert await client.get_restaurant_menu("1", "addr") == []


# ── get_food_cart ────────────────────────────────────────────────────────────────────────


class TestGetFoodCart:
    async def test_empty_cart_data_null(self):
        """Live Swiggy returns {data: null} for an empty cart."""
        payload = {
            "statusCode": 0,
            "statusMessage": "CART",
            "data": None,
            "successful": True,
        }
        mock = AsyncMock(return_value=_result(payload))
        client = await _make_client(mock)

        cart = await client.get_food_cart("cug4elpnnp0lq52vuk10")

        mock.assert_called_once_with("get_food_cart", {"addressId": "cug4elpnnp0lq52vuk10"})
        assert cart["is_empty"] is True
        assert cart["to_pay"] is None
        assert cart["coupon_applied"] is None
        assert cart["coupon_discount"] == 0
        assert cart["items"] == []

    async def test_populated_cart_parsed(self):
        payload = {
            "data": {
                "cart_id": "abc123",
                "items": [
                    {
                        "menu_item_id": "81574197",
                        "name": "Chicken Briyani",
                        "quantity": 1,
                    }
                ],
                "pricing": {"to_pay": 403},
                "offers": {
                    "coupon_applied": "SAVEBITE",
                    "coupon_discount": 0,
                },
            }
        }
        mock = AsyncMock(return_value=_result(payload))
        client = await _make_client(mock)
        cart = await client.get_food_cart("addr")

        assert cart["is_empty"] is False
        assert cart["to_pay"] == 403
        assert cart["coupon_applied"] == "SAVEBITE"
        assert len(cart["items"]) == 1
        assert cart["items"][0]["menu_item_id"] == "81574197"


# ── update_food_cart ─────────────────────────────────────────────────────────────────────


class TestUpdateFoodCart:
    async def test_correct_tool_name_and_cart_items_shape(self):
        """update_food_cart must pass cartItems:[{menu_item_id, quantity}] to the tool."""
        mock = AsyncMock(return_value=_result({"success": True}))
        client = await _make_client(mock)

        result = await client.update_food_cart(
            restaurant_id="62683",
            address_id="cug4elpnnp0lq52vuk10",
            menu_item_id="81574197",
            quantity=1,
        )

        mock.assert_called_once_with(
            "update_food_cart",
            {
                "restaurantId": "62683",
                "addressId": "cug4elpnnp0lq52vuk10",
                "cartItems": [{"menu_item_id": "81574197", "quantity": 1}],
            },
        )
        assert isinstance(result, dict)

    async def test_quantity_zero_remove_item(self):
        """Passing quantity=0 should translate to cartItems with quantity 0."""
        mock = AsyncMock(return_value=_result({}))
        client = await _make_client(mock)
        await client.update_food_cart("r", "a", "item1", 0)
        called_args = mock.call_args[0]
        assert called_args[1]["cartItems"][0]["quantity"] == 0


# ── apply_coupon ─────────────────────────────────────────────────────────────────────────


class TestApplyCoupon:
    async def test_calls_correct_tool_with_coupon_code(self):
        mock = AsyncMock(return_value=_empty_result())
        client = await _make_client(mock)

        await client.apply_coupon("SAVEBITE", "cug4elpnnp0lq52vuk10")

        mock.assert_called_once_with(
            "apply_food_coupon",
            {"couponCode": "SAVEBITE", "addressId": "cug4elpnnp0lq52vuk10"},
        )

    async def test_returns_none(self):
        mock = AsyncMock(return_value=_empty_result())
        client = await _make_client(mock)
        result = await client.apply_coupon("SAVEBITE", "addr")
        assert result is None


# ── flush_cart ───────────────────────────────────────────────────────────────────────────


class TestFlushCart:
    async def test_calls_flush_food_cart(self):
        mock = AsyncMock(return_value=_empty_result())
        client = await _make_client(mock)

        await client.flush_cart()

        mock.assert_called_once_with("flush_food_cart", {})

    async def test_returns_none(self):
        mock = AsyncMock(return_value=_empty_result())
        client = await _make_client(mock)
        result = await client.flush_cart()
        assert result is None
