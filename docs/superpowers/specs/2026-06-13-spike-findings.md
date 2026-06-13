# Phase 2a Spike — Findings & Phase 2b Contract

Validated **live** against the consumer Swiggy MCP (`mcp.swiggy.com/food`, via the
Claude Code session) + Vertex AI, 2026-06-13. Verdict: **technical feasibility = GO.**

## Validated mechanics

### U1 — cross-restaurant dish discovery  ✅
- `search_restaurants(query, addressId)` returns ~10 restaurants, each with
  `id`, `name`, `distanceKm` (float, km), `avgRating` (float), availability.
- **No radius param** → enforce ≤7 km **client-side** on `distanceKm`.
- `search_menu` (dish-level cross-restaurant) returns `0` consistently at both
  saved addresses → **do not rely on it.**
- Real flow is **two-hop**: `search_restaurants(dish)` → filter ≤7 km + open →
  `get_restaurant_menu(restaurantId)` per candidate → match the dish item.
- `get_restaurant_menu` items: `id` (str), `name`, `price` (int rupees),
  `inStock` (0/1), `rating`, `hasVariants` (bool), `hasAddons` (bool).

### U2 — final price after best Swiggy coupon  ✅
- Cycle: `update_food_cart` (add item) → Swiggy auto-suggests the best regular
  coupon in `offers.coupon_applied` → `apply_food_coupon(code, addressId)` →
  **re-read** `get_food_cart` → `flush_food_cart`.
- Final price fields: `pricing.to_pay` (int), `offers.coupon_discount` (int off),
  item `final_price`. Rank on **`to_pay`** (taxes recompute on the discounted base).
- **Gotcha:** `apply_food_coupon` and `fetch_food_coupons` return empty `{}`
  bodies — the side effect still lands. **Always re-read `get_food_cart` for truth.**
- Measured: 1× Chicken Briyani ₹350 → SAVEBITE −₹80 → `to_pay` ₹403 → ₹321.

### Coupon enumeration / bank-vs-Swiggy
- `fetch_food_coupons` returns `{}` for all probed restaurants (18 chains without
  a cart; Ambur Star *with* a populated cart) → **no reliable enumeration via MCP.**
- **Decision:** CouponPricer uses the cart's auto-suggested `coupon_applied` as the
  best regular coupon. Bank/card offers are payment-time (separate `paymentOffers`
  per tool docs) and never hit `to_pay`, so ranking on post-auto-coupon `to_pay`
  uses regular Swiggy coupons by construction. `fetch_food_coupons` is consumed
  opportunistically (if it ever returns `bestCoupons`/`moreOffers`, pick the max
  regular coupon and ignore `paymentOffers`) with graceful `{}` fallback.
- Open (integration-time, low risk): confirm the auto-suggested coupon is never a
  card-linked offer.

### Cart shape
- Empty cart: top-level `data` is `null`.
- Populated: `data.cart_id`, `data.items[]` (`menu_item_id`, `name`, `quantity`,
  `subtotal`, `total`, `final_price`), `data.pricing` (`item_total`,
  `delivery_charge`, `taxes_and_charges`, `to_pay`), `data.offers`
  (`coupon_applied`, `coupon_discount`, `free_delivery_applied`).
- `update_food_cart(restaurantId, addressId, cartItems:[{menu_item_id, quantity}])`.
- **CartGuard:** check empty before mutating; snapshot/restore if non-empty;
  always flush after a pricing probe. Never call `place_food_order`.

### Addresses
- `get_addresses` → `[{id, addressLine, addressCategory, addressTag}]`, no coords.
- Test addresses: Work `cug4elpnnp0lq52vuk10` (Ramapuram), Other `50918630` (Adambakkam).

### Vertex / Gemini  ✅
- `gemini-3.5-flash`, location `global`, project `criteo-e6e97` + service-account
  JSON → responds. Config uses google-genai env-var names (see `config.py`).

## Phase 2b contract (build TDD against fixtures; live MCP/OAuth deferred to Phase 4)

- `models.py` — `Address`, `Restaurant(id, name, distance_km, avg_rating, is_open)`,
  `MenuItem(id, name, price, in_stock)`, `DishHit(restaurant, item_id, item_name,
  base_price)`, `PricedOption(hit, coupon_code, coupon_discount, final_to_pay)`.
- `swiggy_client.py` — `SwiggyClient` Protocol (async): `get_addresses`,
  `search_restaurants(query, address_id)`, `get_restaurant_menu(restaurant_id)`,
  `get_food_cart(address_id)`, `update_food_cart(...)`, `apply_coupon(code, address_id)`,
  `flush_cart()`. Parsing helpers map raw MCP JSON → typed models, tolerant of the
  `{}`/`data:null` cases. Live impl deferred; tests use a fake seeded from fixtures.
- `candidates.py` — `CandidateService.find(dish, address_id) -> list[DishHit]`:
  search → filter ≤7 km + open → per-restaurant menu → dish-name match
  (normalize case + biryani/briyani spelling).
- `ranking.py` — `rank(options) -> list[PricedOption]` sorted by `final_to_pay` asc,
  then `avg_rating` desc.
- `pricing.py` (next unit, Opus-reviewed) — `CartGuard` + `CouponPricer` implementing
  the U2 cycle with snapshot/restore.
