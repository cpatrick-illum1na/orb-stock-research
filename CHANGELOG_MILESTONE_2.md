# Milestone 2 change log

- Added a read-only Python MCP client for Alpaca's official v2 server.
- Restricted the MCP child process to `ALPACA_TOOLSETS=stock-data` and paper mode.
- Added a strict recognized-tool inventory; the client stops if any non-stock-data tool appears.
- Implemented schema discovery for `get_stock_bars`, single-symbol retrieval, `next_page_token` pagination, repeated-token protection, and recursive time-range subdivision when the MCP schema has no page-token input.
- Added `1Day` and `5Min` support with `sip` and `iex` feed selection, optional `asof` point-in-time symbol mapping, and configurable adjustment.
- Added canonical UTC OHLCV normalization, validation, duplicate/invalid row accounting, zero-volume reporting, and potential intraday gap diagnostics without interpolation.
- Added feed-labeled CSV storage and JSON provenance/quality reports.
- Added adapter for the downloaded S&P 500 point-in-time YAML universe and dataset validation/audit script.
- Added mocked unit tests. No real Alpaca calls were run during build validation.
- No order placement, cancellation, replacement, liquidation, or position management code was added.
