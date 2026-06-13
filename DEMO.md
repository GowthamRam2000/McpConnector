# Swiggy Deal Finder — local demo (Claude Desktop)

Our MCP connector runs locally and bridges to the Swiggy MCP on **your own** Swiggy
account. Claude is the conversational driver; all deal-finding logic is deterministic
code in the connector. No order is ever placed (discovery only).

## What it does

1. `get_locations` → your saved Swiggy delivery addresses.
2. `find_deals(dish, address_id)` → searches the dish, keeps open restaurants ≤7 km,
   ranks by base price, then carts the top N to read the **post-coupon `to_pay`**
   (flushing after each), and returns them cheapest-final-price first.

## Prerequisites

- `uv` installed, repo at `/Users/gowthamram/PycharmProjects/Swiggy` (`uv sync --dev`).
- `npx` (Node) available — the connector spawns `mcp-remote` to reach Swiggy.
- A saved delivery address on your Swiggy account.

## Wire it into Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "swiggy-deal-finder": {
      "command": "uv",
      "args": [
        "run", "--directory", "/Users/gowthamram/PycharmProjects/Swiggy",
        "python", "-m", "swiggy_deal_finder.server"
      ]
    }
  }
}
```

Restart Claude Desktop. On first use the connector opens a browser to log into your
Swiggy account (OAuth, once). Then chat:

> "Use Swiggy Deal Finder — what are my saved locations?"
> "Find the cheapest biryani near my Work address."

Claude calls `get_locations`, you pick an address, then `find_deals` returns the ranked
options. You place the order yourself in the Swiggy app.

## Proven results (live, 2026-06-13)

`scripts/check_server.py` (an MCP client driving the connector, exactly as Claude
Desktop does) returned, for "biryani" near the Work address:

```
[get_locations] → Work (cug4elpnnp0lq52vuk10), V (50918630)
[find_deals "biryani", top 2]
  1. Arcot Biriyani   [4.1, 2.8 km]  Chicken Mini Biryani  base ₹190 → FINAL ₹181
  2. Bikkgane Biryani [4.2, 4.0 km]  Basmati Biryani Rice  base ₹199 → FINAL ₹204
```

## Run the engine headless (alternative evidence)

```bash
uv run python scripts/demo.py biryani 3       # full pipeline, ranked deals
uv run python scripts/smoke_live.py           # read-only bridge check
uv run python scripts/check_server.py         # client → our connector → Swiggy
```

## Notes / next

- Pricing transiently mutates your real (single) cart and flushes after; it refuses to
  run if your cart is non-empty (`CartGuard`) so it never clobbers an existing order.
- "Cheapest" currently surfaces the lowest-priced matching item (often "mini" portions)
  — a future tuning option (portion/type filters).
- Production path (hosted connector + chained OAuth) requires Swiggy Builders Club
  approval; this local demo is the application artifact.
