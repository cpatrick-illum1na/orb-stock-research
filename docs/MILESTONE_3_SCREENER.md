# Milestone 3 — Deterministic ORB screener

## Scope

Milestone 3 adds an **offline, deterministic, read-only** screening stage. It consumes normalized `1Day` and `5Min` CSV files produced by Milestone 2. The `screen` command does not start the Alpaca MCP process, read `.env`, make network requests, place orders, model fills, size positions, or manage positions.

The screener is a deterministic rule engine. An LLM is not involved in eligibility, calculations, ranking, direction, or output generation.

## Paper-baseline filters

For a target date `t`, the input data must contain the 09:30 New York 5-minute bar for `t`, that same opening bar for each of the prior 14 trading sessions, and at least **100 contiguous completed daily sessions before `t` for each symbol** to establish the ATR warm-up.

| Metric | Rule | Implementation |
|---|---|---|
| Opening price | Strictly greater than $5 | `open` of the 09:30–09:35 ET bar; exactly $5 is rejected |
| ADV14 | At least 1,000,000 shares | Arithmetic mean of daily volume over the 14 completed sessions before `t`; current-day volume is excluded |
| ATR14 | Strictly greater than $0.50 | True range is `max(high-low, abs(high-prior_close), abs(low-prior_close))`; 14-period Wilder seed and recurrence run over the contiguous daily-history suffix |
| ATR warm-up | At least 100 contiguous completed daily sessions | The first 14 true ranges seed ATR; later observations recursively smooth it. Insufficient per-symbol history is rejected, never filled |
| Opening range | First 5 minutes | Exactly the `09:30:00 America/New_York` 5-minute bar; high and low are retained as reference levels |
| Relative volume | At least 1.0 | Current 09:30 bar volume divided by the arithmetic mean of the previous 14 sessions' 09:30 bar volumes |
| Ranking | Top 20 by default | One combined long/short ranking, relative volume descending, ticker ascending as deterministic tie-break |
| Direction | Candle direction | `close > open` → `LONG`; `close < open` → `SHORT`; equal → `DOJI_SKIP` |

A selected doji remains in the ranked top-N output but is explicitly non-actionable and has no entry reference price. For bullish bars, `entry_reference_price` is the opening-bar high; for bearish bars it is the opening-bar low. These are research reference prices only, not orders or fill assumptions.

## Data integrity and fail-closed behavior

- Both inputs must be normalized project CSVs with expected timeframes (`1Day` and `5Min`).
- Daily and intraday inputs must use the same single feed (`sip` or `iex`) and same adjustment. The baseline requires `raw` bars. SIP and IEX are separate strategy variants; they are never mixed within a screen.
- When quality sidecars exist, feed, adjustment, timeframe, and as-of mapping metadata are checked. When as-of dates are present, daily and intraday dates must match each other and the screen date.
- The report records SHA-256 hashes for the universe and both bar inputs.
- Duplicate daily symbol/session keys or intraday symbol/timestamp keys are fatal input errors.
- Missing daily sessions, prior opening bars, the current 09:30 bar, or a valid prior opening-volume denominator cause rejection. No bars or volumes are imputed.
- Global session dates are derived from the union of available daily dates and observed 09:30 intraday dates. A date absent from every input cannot be inferred from these files; a separate exchange-calendar/coverage control remains necessary.
- `America/New_York` is used to identify the opening bar, including daylight-saving changes.

## Security-type classification

The optional security reference is produced from Nasdaq Trader's consolidated `nasdaqtraded.txt` symbol directory. It contains an explicit `ETF` Y/N field and listing venue metadata. The importer:

- retains all directory rows and preserves the `Nasdaq Traded` flag as metadata rather than using it as a filter;
- sets `security_type=ETF` only for explicit `ETF=Y` and `security_type=NON_ETF` only for explicit `ETF=N`;
- leaves missing or conflicting classifications as `UNKNOWN`;
- preserves the listing venue, source name, test-issue flag, source creation timestamp, file hash, and output hash;
- rejects a test issue (`Test Issue=Y`) in the screener;
- requires a snapshot dated for the screen date, created no later than 09:35 ET on that date, and no older than the configured age limit (default 7 calendar days).

`NON_ETF` does **not** mean `COMMON_STOCK`; it can include other non-ETF security types such as preferreds, depositary shares, warrants, rights, units, or debt. This reference is an ETF/non-ETF classification only. `UNKNOWN` is retained and reported separately rather than inferred from a security name.

ETFs remain in the combined ranking, matching the M1 decision. The screener additionally writes separate candidate files for `ETF`, `NON_ETF`, and `UNKNOWN` classifications. The consolidated file is available at:

`https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt`

Nasdaq's directory files carry a `File Creation Time` footer and Nasdaq says the symbol directories are updated periodically during the day. The importer parses that timestamp and refuses a source snapshot created after the 09:35 ET cutoff. A current directory file must **not** be backdated to older screen dates. For historical research, archive dated snapshots as they become available; this public directory is not a full historical point-in-time classification archive.

## Halt-event reference and clearance controls

The optional halt pipeline normalizes source CSVs while preserving source name, exact source URL, input hash, row number, and event identifiers. Supported input formats are `cboe`, `nasdaq`, `quant-lodge`, and `canonical` (or `auto` detection).

Halt evaluation is limited to the opening bar interval **[09:30, 09:35) America/New_York**:

- A halt beginning inside that interval rejects the symbol.
- A pre-market halt with an actual trade resumption after 09:30 ET rejects the symbol.
- A pre-market halt with actual trade resumption no later than 09:30 ET does not overlap the opening bar.
- Events beginning at or after 09:35 ET do not affect that screen's opening-range decision; this prevents a later halt from being used as a reason to reject an earlier screen.
- A relevant event with missing/unparseable time or unknown resumption is `HALT_REVIEW_REQUIRED` and is rejected rather than assumed clear.
- The parser uses the actual **trade-resumption** time when available; a quote-only resumption is not treated as tradable resumption.

**Absence of a halt row is not proof that a symbol was not halted.** A candidate is marked `SELECTED_HALT_CLEAR_VERIFIED` only when an exact-universe coverage attestation is supplied and passes every hash/date/source/window check. Otherwise it remains `SELECTED_PENDING_HALT_CHECK` (with `HALT_DATA_UNAVAILABLE` or `HALT_COVERAGE_UNVERIFIED`). A positive source-attributed halt that overlaps the opening bar is rejected even when the overall file's coverage is not complete.

The coverage template is intentionally incomplete. To verify clearance, the attestation must establish all of the following:

1. exact screen date and full 09:30–09:35 ET interval;
2. complete coverage of the exact universe CSV and its normalized symbol list;
3. the exact normalized halt-event CSV hash;
4. source name and URL matching the halt-event provenance manifest;
5. the source data was available by 09:35 ET on the screen date, with an explicit timestamp and evidence description;
6. a named attester and explanatory notes.

The code checks the recorded source-availability timestamp against the 09:35 ET cutoff. It cannot independently prove the human attestation or the upstream source's completeness. Do not set the attestation fields to true unless the source methodology, universe/venue coverage, event-time semantics, and availability timestamp are evidenced. A retrospective end-of-day archive usually cannot prove that it was available by 09:35 ET. Its event timestamp can establish when a halt occurred, but not when the archive record became available to the screener.

### Available public halt references and limitations

- Nasdaq Trading Halt Search exposes a recent historical search window (the public search is described as covering approximately the last year); Nasdaq halt messages provide halt date/time and resumption quote/trade fields.
- Cboe provides a 2026 downloadable halt CSV and lists both Cboe and non-Cboe listed halt notices. Its published table uses Eastern-time halt/resumption fields. This is useful for cross-checking but the importer does not automatically certify that it is complete for a particular S&P 500 universe and 09:30–09:35 window.
- Quant-Lodge's public `ticker-reference-data` repository describes one CSV per year of reconciled U.S. equity halt events from 2019 onward, with immutable date-tagged releases and `resume_trade_time` distinct from `resume_quote_time`. It is a third-party project, not an exchange authority; its own documentation disclaims guarantees and asks users to verify primary sources. It also documents a history floor of 2019-02-22 and notes ticker-name/symbol changes.

No complete, free, point-in-time halt archive back to 2004 has been established in this milestone. Do not claim full historical halt exclusion for pre-2019 history. Do not use missing bars as a halt signal.

## Outputs

`orb-data screen` writes the following under `data/screeners/<date>/<feed>/` unless `--output-dir` is supplied:

1. `*_candidates.csv` — selected top-N candidates ranked by relative volume (may remain pending halt verification).
2. `*_audit.csv` — every input-universe symbol, metrics, security type, halt status, status, and reason codes.
3. `*_report.json` — configuration, session dates, feed/adjustment, input hashes, counts, rejection reasons, and limitations.
4. `*_candidates_etf.csv`, `*_candidates_non_etf.csv`, `*_candidates_unknown.csv` — candidate subsets by security type.

Important status values include `SELECTED_HALT_CLEAR_VERIFIED`, `SELECTED_PENDING_HALT_CHECK`, `QUALIFIED_OUTSIDE_TOP_N`, and `REJECTED`. A row in `*_candidates.csv` that is pending halt verification is **not cleared for downstream use**.

## CLI examples

```powershell
# View command options
python -m orb_data.cli screen --help
python -m orb_data.cli reference --help

# Run offline using already-fetched data
python -m orb_data.cli screen `
  --date 2026-11-03 `
  --universe-file .\data\universe\sp500_asof_2026-11-03.csv `
  --daily-bars .\data\raw\1Day\sip\<daily-bars-file>.csv `
  --intraday-bars .\data\raw\5Min\sip\<intraday-bars-file>.csv `
  --feed sip --top-n 20
```

The screen command is offline. Only the preceding Milestone 2 `fetch` commands call Alpaca's read-only `get_stock_bars` endpoint.

## Explicit exclusions

This milestone does not add breakout triggering, order placement, stop execution, position sizing, aggregate risk enforcement, paper-trade simulation, commissions/slippage, or exits. No account/order/position tools are imported or called. No profitability claim is made.
