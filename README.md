# ORB Stock Research — new project

This is a standalone project at `G:\orb-stock-research`; it is not related to the previous BTC/USD project.

## Milestone status

- **Milestone 1:** completed — design, verified paper rules, cost/risk assumptions, and $0 historical universe plan in `docs/`.
- **Milestone 2:** implemented — read-only Alpaca MCP daily/5-minute stock bar data layer and S&P 500 point-in-time universe adapter. The local MCP smoke test remains to be run by the user.
- **Milestones 3–6:** not started.

## Safety

The Python data client starts Alpaca MCP with `ALPACA_TOOLSETS=stock-data` and `ALPACA_PAPER_TRADE=true`. It calls only `get_stock_bars`, fails closed if tools outside the recognized stock-data toolset are exposed, and has no order/position code. Phase 1 is read-only. No live trading will be implemented. A later paper stage requires separate design and explicit approval.

## Setup

```powershell
cd G:\orb-stock-research
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
notepad .env
```

Put paper credentials in `.env` locally. Never print, upload, or include `.env` in an archive. Install Astral uv (`uvx` must be on PATH) for the official Alpaca MCP v2 server.

## Verify the downloaded universe

Expected clone location: `reference_data\sp500-pit`.

```powershell
python scripts\verify_sp500_dataset.py
python -m orb_data.cli universe --date 2025-01-02
```

## Check the read-only Alpaca MCP connection

```powershell
python -m orb_data.cli doctor
```

## Small historical data sample

```powershell
python -m orb_data.cli fetch --symbols AAPL,MSFT --timeframe 1Day --start 2025-01-02T00:00:00Z --end 2025-01-31T23:59:59Z --feed sip --adjustment raw --asof 2025-01-31
python -m orb_data.cli fetch --symbols AAPL,MSFT --timeframe 5Min --start 2025-01-02T14:30:00Z --end 2025-01-02T21:00:00Z --feed sip --adjustment raw --asof 2025-01-02
```

Use `--feed iex` for a separate IEX dataset. Never mix the SIP and IEX volumes in a later relative-volume numerator/denominator.

Outputs go to `data\raw\<timeframe>\<feed>\` as CSV plus JSON quality reports.

## Test

```powershell
python -m pytest -v
```

See `docs/MILESTONE_2_DATA_LAYER.md` for details, limitations, and acceptance criteria.
