# ORB Stock Research — Milestone 2: Read-only data layer

This is a separate project. Milestone 2 retrieves only Alpaca stock bars through the official Alpaca MCP server. The local MCP subprocess is started with `ALPACA_TOOLSETS=stock-data` and `ALPACA_PAPER_TRADE=true`. The Python client allowlists only `get_stock_bars`; it fails closed if an execution-capable tool appears in the discovered inventory. No order, position, replace, cancel, or close tool is imported or called.

## Data scope

- Supported timeframes: `1Day` and `5Min`.
- Supported feeds: `sip` and `iex`; every CSV row and quality report records the feed.
- Historical research default: SIP. Alpaca documents historical SIP access without the paid real-time SIP entitlement when the requested end is at least 15 minutes old. Recent real-time SIP still requires the paid subscription; this project does not request it.
- Free real-time mode: IEX. Use the same feed for future RV numerator and historical denominator; do not combine IEX and SIP data in one RV calculation.
- Default price adjustment: `raw`, matching the paper's description of unadjusted intraday bars. Adjustment is configurable and recorded.
- Optional `--asof YYYY-MM-DD` is passed to Alpaca when its MCP schema supports historical symbol mapping. For a historical universe on a given date, pass the same date. A multi-year request spanning ticker changes should later be segmented at ticker-effective dates rather than rely on one as-of date.
- No interpolation, forward fill, or synthetic bars. Zero-volume bars are retained and counted. Missing intervals are reported as unreturned intervals, not assumed to be feed outages.
- Historical membership can be exported from the downloaded S&P 500 PIT dataset. It is a point-in-time S&P 500 subset, not the paper's full U.S.-listed universe. Alpaca availability for delisted/inactive names is not guaranteed; the quality report discloses this and a later per-symbol coverage audit is mandatory.

## Setup (PowerShell)

From `G:\orb-stock-research`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
notepad .env
```

Enter paper credentials in `.env` locally. Never print it, paste it into chat, or include it in a ZIP. Install Astral uv so `uvx` is on PATH: https://docs.astral.sh/uv/getting-started/installation/ . The MCP client starts the official Alpaca MCP package as a subprocess, with only the `stock-data` toolset enabled.

## Verify the downloaded S&P 500 data

The clone must be located at `reference_data\sp500-pit`:

```powershell
python scripts\verify_sp500_dataset.py
python -m orb_data.cli universe --date 2025-01-02
python -m orb_data.cli universe --all
```

## Verify the read-only MCP connection

```powershell
python -m orb_data.cli doctor
```

Expected output identifies `get_stock_bars` as the only allowed call, and confirms `stock-data` only. If the server exposes any order/position action tool, the client stops before requesting bars.

## Retrieve a small test sample

Use an older date so the SIP request is historical and comfortably more than 15 minutes old:

```powershell
python -m orb_data.cli fetch --symbols AAPL,MSFT --timeframe 1Day --start 2025-01-02T00:00:00Z --end 2025-01-31T23:59:59Z --feed sip --adjustment raw --asof 2025-01-31
python -m orb_data.cli fetch --symbols AAPL,MSFT --timeframe 5Min --start 2025-01-02T14:30:00Z --end 2025-01-02T21:00:00Z --feed sip --adjustment raw --asof 2025-01-02
```

Outputs are written to `data\raw\<timeframe>\<feed>\` as CSV plus JSON quality reports. The UTC timestamp range is inclusive at the API; timestamps are normalized to UTC. Use the XNYS regular-session clock in later screener logic; this data layer does not remove pre/post-market bars.

## Tests

```powershell
python -m pytest -v
```

Tests use mocked MCP responses and never require real credentials or network calls.

## Pagination and reliability

The client discovers `get_stock_bars` input schema at runtime. If `page_token` is present, it follows `next_page_token` with repeated-token protection. If the server schema lacks a page-token input, it recursively splits oversized date ranges. If a full page is returned without a token, it fails rather than treating the response as complete. Requests are sequential to avoid bursting Alpaca rate limits.

## Milestone 2 acceptance criteria

1. S&P 500 repository validates and exports a dated ticker list.
2. MCP doctor finds `get_stock_bars` in the stock-data-only server.
3. Daily and 5-minute SIP sample requests return normalized bars for AAPL/MSFT.
4. IEX sample can be fetched separately and feed provenance is retained.
5. Duplicate/invalid bars are counted; gaps/zero volume are reported; no bars are fabricated.
6. Pagination is exercised or an oversized-range split test passes.
7. Full pytest suite passes.
8. No trading or position-management tools are imported, enabled, or called.

**This milestone is a data retrieval layer only. It does not implement the screener, signals, backtest, or orders.**

## File layout added in this milestone

```text
orb-stock-research/
├── orb_data/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── mcp_bars.py
│   ├── normalization.py
│   ├── service.py
│   ├── storage.py
│   └── universe.py
├── scripts/
│   └── verify_sp500_dataset.py
├── tests/
│   ├── test_config_safety.py
│   ├── test_mcp_bars.py
│   ├── test_normalization.py
│   └── test_service.py
├── docs/
│   ├── MILESTONE_1_DESIGN.md
│   ├── HISTORICAL_UNIVERSE_ACQUISITION.md
│   ├── COST_RISK_AND_EXIT_DEFAULTS.md
│   └── MILESTONE_2_DATA_LAYER.md
├── .env.example
├── .gitignore
├── pytest.ini
├── requirements.txt
└── requirements-dev.txt
```
