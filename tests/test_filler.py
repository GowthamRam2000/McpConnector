"""Tests for filler.py — cart-filler utilities.

TDD: written before implementation to define the contract.
"""


from swiggy_deal_finder.filler import OfferTerms, choose_filler, parse_offer
from swiggy_deal_finder.models import MenuItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    id: str,
    name: str,
    price: int,
    in_stock: bool = True,
    has_variants: bool = False,
    has_addons: bool = False,
) -> MenuItem:
    return MenuItem(
        id=id,
        name=name,
        price=price,
        in_stock=in_stock,
        has_variants=has_variants,
        has_addons=has_addons,
    )


# ---------------------------------------------------------------------------
# parse_offer tests  (API unchanged — keep as-is)
# ---------------------------------------------------------------------------


def test_parse_offer_flat_with_min_cart():
    result = parse_offer("₹125 OFF ABOVE ₹249")
    assert result == OfferTerms(min_cart=249, flat_discount=125)


def test_parse_offer_smaller_flat_with_min_cart():
    result = parse_offer("₹40 OFF ABOVE ₹199")
    assert result == OfferTerms(min_cart=199, flat_discount=40)


def test_parse_offer_flat_no_min_cart():
    result = parse_offer("FLAT ₹75 OFF")
    assert result == OfferTerms(min_cart=None, flat_discount=75)


def test_parse_offer_percentage_cap_returns_none():
    # Percentage-based offer — flat_discount must stay None (not the cap amount)
    result = parse_offer("60% OFF UPTO ₹120")
    assert result == OfferTerms(min_cart=None, flat_discount=None)


def test_parse_offer_items_at_price_returns_none():
    result = parse_offer("ITEMS AT ₹99")
    assert result == OfferTerms(min_cart=None, flat_discount=None)


def test_parse_offer_none_input():
    assert parse_offer(None) == OfferTerms(min_cart=None, flat_discount=None)


def test_parse_offer_empty_string():
    assert parse_offer("") == OfferTerms(min_cart=None, flat_discount=None)


def test_parse_offer_case_insensitive():
    result = parse_offer("flat ₹50 off above ₹300")
    assert result == OfferTerms(min_cart=300, flat_discount=50)


def test_parse_offer_rupee_symbol_optional():
    # No ₹ symbol, still parseable
    result = parse_offer("50 OFF ABOVE 200")
    assert result == OfferTerms(min_cart=200, flat_discount=50)


def test_parse_offer_numbers_with_commas():
    result = parse_offer("₹1,000 OFF ABOVE ₹2,499")
    assert result == OfferTerms(min_cart=2499, flat_discount=1000)


# ---------------------------------------------------------------------------
# choose_filler tests — new shape: list[tuple[MenuItem, int]]
# ---------------------------------------------------------------------------


def test_choose_filler_gap_zero_returns_empty():
    items = [_item("a", "Alpha", 100)]
    assert choose_filler(items, 0, set()) == []


def test_choose_filler_gap_negative_returns_empty():
    items = [_item("a", "Alpha", 100)]
    assert choose_filler(items, -50, set()) == []


def test_choose_filler_single_item_bridges_gap():
    # One item at 150 bridges a gap of 100; it's the only option.
    items = [_item("a", "Alpha", 150)]
    result = choose_filler(items, 100, set())
    assert len(result) == 1
    item, qty = result[0]
    assert item.id == "a"
    assert qty >= 1
    assert item.price * qty >= 100


def test_choose_filler_single_item_cheaper_than_greedy_combo():
    # Gap: 200.
    # Strategy 1 (single repeated): BigItem ₹210 × 1 = 210.
    # Strategy 2 (greedy combo): Small1+Small2+Small3 = 80+80+80 = 240.
    # Single item wins (210 < 240).
    items = [
        _item("big", "BigItem", 210),
        _item("s1", "Small1", 80),
        _item("s2", "Small2", 80),
        _item("s3", "Small3", 80),
    ]
    result = choose_filler(items, 200, set())
    assert len(result) == 1
    item, qty = result[0]
    assert item.id == "big"
    assert qty == 1


def test_choose_filler_greedy_combo_cheaper_than_single_bridge():
    # Gap: 200.
    # Strategy 1 (single repeated):
    #   s1 ₹90: ceil(200/90)=3 × 90 = 270
    #   s2 ₹130: ceil(200/130)=2 × 130 = 260
    #   big ₹350: 1 × 350 = 350
    #   Best single = 260 (s2).
    # Strategy 2 (greedy combo): s1(90) + s2(130) = 220 < 260 → combo wins.
    items = [
        _item("big", "BigItem", 350),
        _item("s1", "Small1", 90),
        _item("s2", "Small2", 130),
    ]
    result = choose_filler(items, 200, set())
    assert len(result) == 2
    ids = {item.id for item, qty in result}
    assert ids == {"s1", "s2"}
    for _item_line, qty in result:
        assert qty == 1


def test_choose_filler_excludes_by_id():
    # "dish" is the user's original dish; it should be excluded.
    items = [
        _item("dish", "TargetDish", 50),
        _item("extra", "Extra", 120),
    ]
    result = choose_filler(items, 100, exclude_ids={"dish"})
    assert all(item.id != "dish" for item, qty in result)
    assert len(result) == 1
    item, qty = result[0]
    assert item.id == "extra"


def test_choose_filler_excludes_out_of_stock():
    items = [
        _item("oos", "OutOfStock", 50, in_stock=False),
        _item("ok", "InStock", 120),
    ]
    result = choose_filler(items, 100, set())
    assert all(item.in_stock for item, qty in result)


def test_choose_filler_excludes_has_variants():
    items = [
        _item("v", "WithVariants", 50, has_variants=True),
        _item("ok", "Plain", 120),
    ]
    result = choose_filler(items, 100, set())
    assert all(not item.has_variants for item, qty in result)


def test_choose_filler_excludes_has_addons():
    items = [
        _item("a", "WithAddons", 50, has_addons=True),
        _item("ok", "Plain", 120),
    ]
    result = choose_filler(items, 100, set())
    assert all(not item.has_addons for item, qty in result)


def test_choose_filler_unreachable_gap_returns_empty():
    # Only one item at 50; qty × 50 is always a multiple of 50, but gap=200 needs 4×50.
    # Actually 4×50=200 IS reachable — use a gap that's truly unreachable: no eligible items.
    # Reframe: all items have variants so none are eligible.
    items = [_item("a", "Alpha", 50, has_variants=True)]
    result = choose_filler(items, 200, set())
    assert result == []


def test_choose_filler_no_eligible_items_returns_empty():
    items = [
        _item("v", "WithVariants", 300, has_variants=True),
        _item("oos", "OutOfStock", 300, in_stock=False),
    ]
    result = choose_filler(items, 100, set())
    assert result == []


def test_choose_filler_returned_list_ascending_price_order():
    # When the greedy combo wins, items must be in ascending unit-price order.
    items = [
        _item("c", "Cheap", 60),
        _item("m", "Mid", 90),
        _item("e", "Expensive", 110),
    ]
    # gap=200; greedy: 60+90+110=260 total; single: ceil(200/60)=4×60=240 — greedy wins.
    result = choose_filler(items, 200, set())
    prices = [item.price for item, qty in result]
    assert prices == sorted(prices)


def test_choose_filler_tie_returns_valid_result():
    # Single bridge item costs exactly the same as the greedy combo total.
    # Both are equally valid; we just verify the result is valid (sum >= gap).
    items = [
        _item("big", "BigItem", 200),
        _item("s1", "Small1", 100),
        _item("s2", "Small2", 100),
    ]
    result = choose_filler(items, 200, set())
    total = sum(item.price * qty for item, qty in result)
    assert total >= 200


# ---------------------------------------------------------------------------
# New: quantity-multiple tests (headline cart-filler feature)
# ---------------------------------------------------------------------------


def test_choose_filler_quantity_multiple_beats_single_expensive_item():
    """gap=60, chutney ₹30 × 2 = 60 is cheaper than any single ₹95 item."""
    items = [
        _item("chutney", "Chutney", 30),
        _item("big", "BigDish", 95),
    ]
    result = choose_filler(items, 60, set())
    # Expect one line: chutney × 2, total 60 — cheaper than BigDish × 1 (95).
    assert len(result) == 1
    item, qty = result[0]
    assert item.id == "chutney"
    assert qty == 2
    assert item.price * qty == 60


def test_choose_filler_single_item_with_qty_beats_single_item_gte_gap():
    """gap=60, chutney ₹30 ×2 = 60, BigDish ₹95 ×1 = 95 → chutney wins."""
    items = [
        _item("chutney", "Chutney", 30),
        _item("big", "BigDish", 95),
    ]
    result = choose_filler(items, 60, set())
    assert len(result) == 1
    item, qty = result[0]
    assert item.id == "chutney"
    assert qty == 2
    total = item.price * qty
    assert total == 60


def test_choose_filler_single_item_gte_gap_chosen_when_cheapest():
    """gap=79, chutney ₹30 × ceil(79/30)=3 = 90, item79 ₹79 × 1 = 79 → item79 wins."""
    items = [
        _item("chutney", "Chutney", 30),
        _item("item79", "Item79", 79),
    ]
    result = choose_filler(items, 79, set())
    assert len(result) == 1
    item, qty = result[0]
    assert item.id == "item79"
    assert qty == 1
    assert item.price * qty == 79


def test_choose_filler_greedy_distinct_combo_beats_single_with_quantity():
    """Construct prices so combo of two distinct cheapest items is cheaper than any
    repeated-single strategy.

    gap=100.
    Items: A ₹40, B ₹70.
    Strategy 1 (single repeated):
      - A: ceil(100/40)=3 × 40=120.
      - B: ceil(100/70)=2 × 70=140.
      Best single = 120 (A×3).
    Strategy 2 (greedy combo):
      - A(40) + B(70) = 110 < 120 → combo wins.
    """
    items = [
        _item("a", "ItemA", 40),
        _item("b", "ItemB", 70),
    ]
    result = choose_filler(items, 100, set())
    assert len(result) == 2
    ids = {item.id for item, qty in result}
    assert ids == {"a", "b"}
    total = sum(item.price * qty for item, qty in result)
    assert total >= 100
    assert total == 110  # exactly 40+70


def test_choose_filler_result_ascending_by_unit_price():
    """The returned list must be ascending by unit price, regardless of strategy chosen."""
    items = [
        _item("chutney", "Chutney", 30),
        _item("item79", "Item79", 79),
    ]
    # chutney × 2 wins (total 60) — single line; ascending trivially holds.
    result = choose_filler(items, 60, set())
    prices = [item.price for item, qty in result]
    assert prices == sorted(prices)


def test_choose_filler_combo_ascending_by_unit_price():
    """Greedy combo result must be ascending by unit price."""
    items = [
        _item("a", "ItemA", 40),
        _item("b", "ItemB", 70),
    ]
    result = choose_filler(items, 100, set())
    prices = [item.price for item, qty in result]
    assert prices == sorted(prices)
