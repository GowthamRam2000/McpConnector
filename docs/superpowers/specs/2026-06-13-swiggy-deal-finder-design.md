# Swiggy Deal Finder — Design Spec

- **Date:** 2026-06-13
- **Status:** Approved design, pre-implementation
- **Owner:** gowthamram

## 1. Problem & Goal

A user picks a specific dish and gives a location. The app finds that dish at nearby
restaurants (≤ 7 km), and surfaces the **cheapest + highest-rated** options where
"cheapest" means the **final price after applying the best-value regular Swiggy coupon**
(item/cart coupons — **not** bank/card offers). Output is a ranked shortlist the user taps
through to order on Swiggy.

## 2. Scope

**In scope**
- Discovery only: surface the best option(s). The user places the order in Swiggy themselves.
- Public product: each end-user connects their **own** Swiggy account (Login-with-Swiggy).
- Single dish per search, single delivery location, optional rating floor.

**Out of scope (v1)**
- Placing orders / checkout / payment (Swiggy MCP is COD-only at launch anyway).
- Instamart and Dineout MCP servers (Food server only).
- Cross-platform comparison (e.g. vs Zomato) — explicitly prohibited by Swiggy ToS.
- Centralized/anonymous price-comparison engine (impossible under per-user OAuth).

## 3. Feasibility Summary & Key Risks

The Swiggy MCP **Food server** (14 tools) exposes the primitives needed. Buildable and
publicly shippable **as a per-user agent**, with three caveats:

1. **"Public" = Login-with-Swiggy, not a central engine.** Auth is OAuth 2.1 + PKCE,
   per end-user (phone + OTP, their own account). Viable for a public product; each user
   authenticates individually.
2. **Final post-coupon price requires touching the user's cart.** No endpoint returns an
   abstract "coupon X saves ₹Y". To get a real number: add item to cart → fetch coupons →
   apply coupon → read bill breakdown → flush. This mutates the user's single real Swiggy
   cart and consumes the write-rate budget. Mitigated by `CartGuard` + lazy top-3 pricing.
3. **ToS gray zone → needs Builders Club sign-off.** Guidelines prohibit "competitive
   intelligence/benchmarking" and "aggregation layers that obscure Swiggy's brand."
   Ranking *within* Swiggy and driving orders *to* Swiggy is defensible, but a public
   price-ranking app sits near the line. **Go/no-go for public launch** — confirmed via the
   local-demo → application flow (Phase 5), not assumed.

**Two technical unknowns to validate on staging before building (Phase 2a):**
- (U1) Does `search_restaurants`/`search_menu` accept a radius, or do we filter ≤ 7 km
  client-side from returned distances?
- (U2) Do coupons expose a Swiggy-vs-bank flag, and does the cart bill breakdown return the
  post-coupon total? If not, the "final price" promise needs heuristics or a rethink.

Docs state local/staging works **without full OAuth**, so both are cheap to verify.

## 4. Architecture Overview

```
PWA (installable web app)
  │  HTTPS / JSON
  ▼
FastAPI backend ───────────────────────────────────────────────┐
  • AuthService        (Swiggy OAuth 2.1 + PKCE, token storage)  │
  • DiscoveryAgent     (Gemini 3.5 Flash Lite via Vertex AI)     │
  • GeminiToolBridge   (MCP tools ⇄ Gemini function declarations)│
  • CandidateService   (search + normalize + ≤7km filter)        │
  • CouponPricer       (cart-simulation, the sensitive subsystem)│
  • CartGuard          (per-user cart mutex + snapshot/restore)  │
  • Ranker             (lowest final price, rating tiebreak)     │
  • SwiggyMCPClient    (typed wrapper + rate limiter + retries)  │
        │                                  │
        ▼                                  ▼
   Vertex AI (Gemini)              Swiggy MCP Food server
```

A backend is mandatory: the MCP client, Vertex credentials, and per-user OAuth tokens
cannot live in the browser. The agent loop runs server-side.

## 5. Agent Design (Approach B — agent-planner + deterministic executors)

**Model:** Gemini 3.5 Flash Lite via **Vertex AI** (GCP project, ADC/service-account auth,
configurable region + model id). Gemini **function-calling** drives a *tiny* tool surface;
the MCP tools are never exposed to it directly.

**The 3 tools Gemini sees** (via `GeminiToolBridge`, which dispatches to `SwiggyMCPClient`):
- `find_dish_candidates(dish, location, rating_floor?)` → `CandidateService`
- `price_with_best_coupon(restaurant_id, item_id)` → `CouponPricer`
- `rank_and_explain(candidates)` → `Ranker` + NL summary

**Agent responsibilities:** interpret intent, disambiguate the dish (structured output, not
free-form looping), decide shortlist size, decide which/how many candidates to deep-price,
explain the result.

**Reliability guardrails (Flash Lite is a lite model):**
- Tool surface capped at 3; raw MCP tools hidden behind deterministic services.
- Structured output for disambiguation.
- **Graceful degradation:** if function-calling misbehaves, deterministic control flow
  drives the pipeline and Gemini is used only for NL intent + explanation (falls back toward
  a thin-LLM shape). Cart safety and rate-limit safety **never** depend on model behavior.

## 6. Data Flow (one search)

1. User submits dish + location (+ optional rating floor).
2. Agent interprets; if dish ambiguous → one clarify round-trip (structured options).
3. Agent → `find_dish_candidates` → `search_menu`/`search_restaurants` → candidates with
   rating, distance, base price; filter ≤ 7 km.
4. Agent shortlists top N by a **cheap score** (base price + rating), default N = 5.
5. Agent → `price_with_best_coupon` on the **top 3 only** (cart simulation, serial, under
   `CartGuard`). Remaining cards exact-price **lazily** when the user expands them.
6. `Ranker` sorts: **lowest final price, rating as tiebreak** (+ optional rating gate).
7. Agent explains; backend returns cards (restaurant · rating · distance · base price ·
   best coupon · final price · "Order on Swiggy" deep link).

## 7. CouponPricer & CartGuard (sensitive subsystem)

Per candidate, `CouponPricer` runs **serially under a per-user `CartGuard` lock**:

1. `CartGuard` snapshots `get_food_cart`. If non-empty → **warn the user, never silent-flush.**
2. Ensure clean cart for the candidate's restaurant (`flush_food_cart` only after consent).
3. `update_food_cart` (add the item).
4. `fetch_food_coupons` → filter to **Swiggy coupons** (heuristic until U2 confirms a flag;
   conservatively apply only coupons positively identified as cart/promo, skip ambiguous,
   note skipped).
5. `apply_food_coupon` (best candidate coupon; respects min-order-value — if it doesn't
   apply or doesn't reduce, skip).
6. Read the cart **bill breakdown** → record final price.
7. `flush_food_cart` / restore snapshot. Release lock.

Mutation is minimized by lazy top-3 pricing. Concurrency across candidates is forbidden —
one real cart per user.

## 8. Ranking

- Primary: lowest **final post-coupon price**.
- Tiebreak: higher rating.
- Optional rating gate (configurable; off by default in v1).

## 9. Rate Limiting

`SwiggyMCPClient` owns a per-user token bucket aligned to planned v1.x limits: **120
read/min, 30 write/min, 2× burst (10s)**. Cart simulation (writes) is the constrained path —
hence serial pricing of a small shortlist. Under pressure: degrade to fewer deep-priced
candidates rather than failing the search.

## 10. Auth & Infra

- **Swiggy:** OAuth 2.1 + PKCE per user; tokens encrypted at rest; refresh on expiry; prompt
  reconnect on refresh failure. Dev/staging usable without full OAuth.
- **Vertex AI:** GCP project, service account / Workload Identity, region + model id config.
- **Token storage:** SQLite (dev) → managed Postgres (prod) for encrypted per-user tokens.

## 11. Error Handling

- MCP tool failure → backoff retry; on repeated failure drop the candidate, return partial
  results with "couldn't price N options".
- Non-empty cart → `CartGuard` warns before any mutation; never silent-flush.
- Rate-limit pressure → serialize/degrade, don't fail.
- Coupon not positively identifiable as Swiggy → skip conservatively, note it.
- OAuth expiry → refresh; on failure → reconnect prompt.
- Vertex/function-calling failure → degrade to deterministic pipeline + NL-only model use.

## 12. Tech Stack & Platform

- **Frontend:** installable **PWA** (one codebase → Android + iOS + desktop; OAuth redirect
  is native to web; wrap as Trusted Web Activity for Play Store later if wanted).
- **Backend:** **Python 3.14 + FastAPI**; `uv` for envs/deps; `ruff` for lint/format.
- **MCP:** Swiggy Food server via an MCP client in Python.
- **LLM:** Gemini 3.5 Flash Lite via Vertex AI (`google-genai` / Vertex SDK).
- **Deploy (post-approval):** Cloud Run on GCP (Vertex in same project; secrets in Secret
  Manager; Cloud SQL Postgres for tokens).

## 13. Build Phases

Mapped to the agreed sequence (env-fill is the first handoff back to the user):

- **Phase 1 — Setup / scaffolding.** Repo structure, `pyproject` deps (`uv`), `ruff` config,
  FastAPI skeleton, config loader, **`.env.example`** listing every required value, README.
  → **User fills env values** → **I verify connectivity** (reach MCP staging, call Vertex).
- **Phase 2 — Backend.**
  - 2a **Staging spike (go/no-go):** hand-run the food tools; resolve U1, U2, and cart
    snapshot/restore fidelity. Short findings note.
  - 2b `SwiggyMCPClient` + rate limiter + `CandidateService` + `Ranker` (CLI-driven, ranked
    by base price). Proves the read path end-to-end.
  - 2c `CouponPricer` + `CartGuard` against staging with a test account.
  - 2d `DiscoveryAgent` (Gemini) + `GeminiToolBridge` over the deterministic tools.
- **Phase 3 — Frontend (PWA).** Search, results, Connect-Swiggy, installability.
- **Phase 4 — Local demos.** End-to-end working locally; recorded demos as the application
  artifact.
- **Phase 5 — Apply to Swiggy Builders Club** for production MCP access (non-code; demos as
  evidence; resolves ToS go/no-go).
- **Phase 6 — If approved: deploy to Cloud Run / GCP.** Containerize, prod OAuth, Secret
  Manager, Cloud SQL, Vertex in-project.

## 14. Testing Strategy

- **Staging spike gates everything** (Phase 2a).
- **Unit:** candidate normalization/filter, ranker ordering, coupon heuristic, `CartGuard`
  locking, rate-limit token bucket.
- **Integration:** `SwiggyMCPClient` + full `CouponPricer` loop against staging.
- **Agent eval:** scripted scenarios — clear dish, ambiguous dish, nothing in radius,
  all-coupons-bank-only — plus **Flash Lite function-calling reliability** on the 3-tool
  schema.

## 15. Open Questions / Verify-on-Staging

- U1: radius parameter vs client-side distance filter.
- U2: Swiggy-vs-bank coupon flag; post-coupon total in bill breakdown.
- Cart snapshot/restore fidelity (can we faithfully restore a user's pre-existing cart?).
- Exact `google-genai` ⇄ MCP integration path (verify against current docs in Phase 2d).
- Exact Vertex model id/endpoint for "Gemini 3.5 Flash Lite" (confirm at build time).

## 16. Efficient-Frontier Execution Plan (model assignment)

Judgment/synthesis/review stays on **Opus**; token-heavy and bounded work is delegated and
verified in proportion to risk.

| Work | Tier | Why |
|---|---|---|
| Architecture, spec, plan, conflict resolution, diff review, go/no-go calls | **Opus** | Judgment |
| Backend services (MCP client, CandidateService, Ranker, CouponPricer, rate limiter, Gemini bridge) | **Sonnet** | Bounded multi-file implementation against a fixed interface |
| PWA frontend implementation | **Sonnet** | Bounded UI build |
| Staging spike interpretation (U1/U2) | **Opus-reviewed** | Real API behavior gates the design |
| Scaffolding boilerplate, dependency inventory, log/test-output reduction, repetitive edits | **Haiku** | Mechanical/high-volume |

`CouponPricer` + `CartGuard` are the highest-risk diffs (real cart mutation) → reviewed
closely on Opus regardless of who writes them.
