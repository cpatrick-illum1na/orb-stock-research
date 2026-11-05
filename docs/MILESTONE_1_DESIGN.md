# ORB Stock Research — Milestone 1 Design

**Project:** `orb-stock-research`  
**Milestone:** 1 — verified research specification and architecture  
**Status:** Design only. No application code, no data retrieval, and no broker actions have been created or run.

## 1. Scope and safety boundary

This is a new, independent project. It must not import, copy, or modify modules from the prior BTC/USD project.

Phase 1 is research-only and read-only. The project will use Alpaca MCP only for market-data and permitted reference-data retrieval. It will not import order-placement, order-replacement, order-cancellation, position-liquidation, or other execution tools. It will not submit or manage orders. A later paper-trading stage requires a separate design and explicit approval.

All strategy decisions will be deterministic Python rules. An LLM will not select symbols, rank candidates, generate signals, size trades, or decide exits. `.env` will be read only by the local process; credentials must never be printed, copied into reports, committed, or included in archives.

## 2. Primary source: Zarattini, Barbon & Aziz (2024)

**Paper:** Carlo Zarattini, Andrea Barbon, and Andrew Aziz, *A Profitable Day Trading Strategy for the U.S. Equity Market*, Swiss Finance Institute Research Paper No. 24-98. First version dated February 16, 2024; SSRN record lists a revision on April 29, 2025. The paper studies U.S. equities from January 1, 2016 through December 31, 2023.

Primary sources:
- SSRN paper record and PDF: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284
- Author/research PDF: https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf
- University of St. Gallen research page: https://www.hsg-summit.com/papers/a-profitable-day-trading-strategy-for-the-u-s-equity-market

### 2.1 Exact paper rules verified

| Component | Paper definition | Implementation implication |
|---|---|---|
| Universe | Approximately 7,000 U.S. equities over 2016–2023. The paper says its universe is survivorship-bias-free and includes delisted issuers. | A present-day active ticker list is not a faithful substitute. Point-in-time membership is a hard data requirement for a valid historical replication. |
| Data sources | CRSP for the equity universe; IQFeed for intraday data. Intraday data were left unadjusted for splits and dividends. | Alpaca data are a different source and will be labeled as a replication/port, not an exact reproduction. Corporate-action handling requires an explicit point-in-time rule. |
| Price screen | **Opening price strictly above $5**. | This is not a premarket price rule and it is `> $5`, not `>= $5`. Use prior close only as a pre-open proxy; confirm the opening price after the regular session opens. |
| Average daily volume | Average volume over the **previous 14 days** must be **at least 1,000,000 shares per day**. | Use 14 completed prior regular sessions. Do not include the current session's full-day volume. |
| ATR | **14-day ATR must be more than $0.50**. The paper defines true range using the current high/low and prior close. It does not clearly specify the ATR smoothing convention (e.g. Wilder versus simple average) in the strategy definition. | ATR period and dollar threshold are clear; smoothing is an implementation ambiguity to resolve and document before coding. |
| Opening range | First **5 minutes**, 9:30:00–9:35:00 a.m. Eastern Time. | Use the first regular-session 5-minute bar, excluding premarket trades. Respect U.S. daylight-saving changes via `America/New_York`. |
| Relative volume | After the first 5 minutes, `RV(t,j) = ORVolume(t,j) / mean(ORVolume(t-1..t-14,j))`. The denominator is the average volume in the same first-five-minute opening window across the prior 14 trading days. | Numerator and all 14 denominator observations must use the same feed, session definition, and bar construction. A missing/zero denominator is invalid, not an infinite score. |
| Stocks-in-Play filter | Relative volume must be **at least 100% (RV >= 1.0)**. Rank qualifying stocks and trade the **top 20** by relative volume. | Top 20 is the total ranked universe per day, not 20 long plus 20 short. Ranking happens only after the first 5-minute bar has completed. |
| Direction | First 5-minute candle bullish (`close > open`) permits a long only; bearish (`close < open`) permits a short only; doji (`close == open`) produces no trade. | Do not reverse direction if price later breaks the opposite side. |
| Entry | Place a stop-entry order at the first 5-minute bar's high for a bullish bar, or at its low for a bearish bar. | The paper describes a stop order at the exact high/low; it does not specify an extra one-tick buffer. Fill mechanics must be modeled conservatively and separately documented. |
| Stop loss | Once entry triggers, place the stop **10% of the 14-day ATR away from the executed entry price**. Example in the paper: ATR = $5, stop distance = $0.50. | `stop_distance = 0.10 * ATR14_dollars`; it is not 10% of the share price and is not 10% of the opening range. |
| Profit-taking / exit | No intraday profit target is specified. If not stopped, close at the end of the regular session, **4:00 p.m. ET**. | EOD is a time exit, not a 10R target. Unfilled entry orders expire at the session end. Exact close-auction versus last-bar-close proxy is an implementation choice to confirm. |
| Position sizing | The paper says the loss if stopped should be 1% of capital deployed for that position and imposes a **4x maximum leverage** constraint. A footnote says the leverage cap can cause some trades' modeled loss to be less than 1%. | The paper's prose does not fully specify the risk denominator/portfolio allocation convention. Do not silently reinterpret this as 1% of total account equity; confirm the sizing convention before coding. |
| Commission | **$0.0035 per share**, referenced to Interactive Brokers Pro–Tiered pricing as of December 31, 2023. | This is a paper assumption, not an Alpaca fee quote. The paper does not model bid/ask spread or slippage in its stated cost model. Per-side versus round-trip commission accounting should be explicitly recorded. |
| Portfolio | Long and short trades are combined in the paper's reported portfolio. | This project will report long, short, combined long/short, and long-only results separately, as requested. The paper does not provide a complete separate long-versus-short performance table. |

### 2.2 Paper results and caveat

For the 2016–2023 sample, the paper reports the 5-minute ORB plus relative-volume / top-20 strategy at 1,637% total return, 41.6% IRR, 2.81 Sharpe, 48.4% hit ratio, and 12% maximum drawdown. The S&P 500 benchmark in the paper is reported at 198% total return, 14.2% IRR, 0.78 Sharpe, and 34% maximum drawdown. Its base 5-minute ORB without the Stocks-in-Play filter materially underperformed: 29% total return, 3.2% IRR, 0.48 Sharpe, and 13% maximum drawdown.

These are the authors' historical results, not an independently verified result for Alpaca data or this implementation. The reported net-of-costs result deducts the stated commission assumption; the paper does not provide a realistic opening bid/ask spread and slippage model. The much stronger Stocks-in-Play result should therefore not be treated as evidence that the strategy will remain profitable after more complete execution costs, data-feed differences, or a new out-of-sample period.

## 3. Differences between the user's initial description and the paper

1. **Price screen timing:** the paper says the regular-session opening price is strictly above $5. It is not a price known before the open. Proposed design: use prior close > $5 only as a pre-open candidate proxy, then confirm the actual opening price > $5 using the 9:30–9:35 bar.
2. **Comparison operator:** paper says `> $5`; user described `>= about $5`.
3. **ATR stop:** user's description is directionally correct, but specify `0.10 * ATR14` in dollars from the executed entry price. It is not 10% of price.
4. **Relative volume:** user's formula is correct. The paper explicitly averages the previous 14 **opening-range** volumes, not the 14-day average daily volume.
5. **Ranking:** paper's top 20 are ranked across all qualifying stocks after RV >= 1.0; not top 20 per direction.
6. **Risk:** paper's 1% wording is ambiguous about the portfolio capital denominator and must not be silently converted to a 1% account-equity risk budget.
7. **Additional exclusions:** ETF exclusions, halt filters, and a fixed recent-IPO age cutoff are not explicit filters in the paper's strategy definition. They would be documented adaptations.
8. **Costs:** the user's requested spread and slippage modeling is stricter than the paper's stated commission-only cost model. This is intentional and necessary for a conservative replication.
9. **Universe:** the paper used CRSP plus IQFeed and states it included delisted names. Alpaca's current asset list and corporate-action API do not, by themselves, provide a complete historical point-in-time universe.

## 4. Alpaca feed and data plan

### 4.1 IEX versus SIP: important subscription nuance

Alpaca's current documentation distinguishes real-time access from historical access:

- Basic/free real-time equities data is IEX-only.
- Real-time SIP requires Algo Trader Plus.
- On historical endpoints, the `feed` parameter selects `iex` or `sip`. Alpaca's FAQ states that historical SIP can be queried without the paid subscription when the requested `end` is at least 15 minutes old. Thus, SIP is not necessarily paid for all historical backtesting; the restriction is on recent real-time SIP data.
- The Alpaca FAQ's AAPL example shows the scale of the feed difference for one date: 923,134 shares / 12,630 eligible IEX trades versus 51,861,083 shares / 535,134 consolidated trades. IEX is one venue, not consolidated market volume.

**Proposed feed modes:**

- `historical_research`: use `sip` for historical daily and 5-minute data when the requested end time is at least 15 minutes old, if confirmed available through the existing Alpaca MCP server and credentials.
- `realtime_basic`: use `iex` for the first 5-minute bar and the corresponding prior-14-day opening-bar denominator when running the 9:35 ET screen without a real-time SIP subscription.
- `realtime_sip`: optional paid mode for a truly consolidated 9:35 ET screen.

Each run must be feed-homogeneous for its calculations: same feed for the current first-5-minute volume and the historical first-5-minute denominator; the daily ADV and ATR will also use the configured feed in that run. Reports will store the actual feed, not just the requested feed.

Do **not** multiply IEX volume by a fixed factor (for example, 40x) to estimate consolidated volume. IEX's share of a security's volume varies by symbol and time. The paper's absolute 1,000,000-share ADV filter was applied to broad-market historical data; on IEX it becomes a different, much more restrictive screen. ATR computed from IEX-only highs/lows can also differ from consolidated ATR. IEX mode must therefore be labeled as a separate feed-specific strategy variant. A sensible initial comparison is historical SIP as the reference research feed and IEX as a distinct real-time screener feed, never mixed within one RV calculation.

### 4.2 Retrieval plan for Milestone 2

Read-only retrieval through the existing Alpaca MCP server, after inspecting its exact tool schemas:

- Daily bars (`1Day`) for 14 completed prior sessions plus warm-up history for ATR and volume filters.
- 5-minute bars (`5Min`) for the regular-session opening window on the current day and the prior 14 trading days, plus subsequent regular-session bars needed to model entry, stop, and end-of-day exits.
- Historical quotes around entries and exits if the MCP server exposes them; otherwise use a clearly parameterized conservative spread schedule. Do not claim observed spread if only OHLCV bars are available.
- Asset metadata (`status`, exchange, asset class, current `shortable` / `easy_to_borrow` flags) as a current reference only, not a historical borrow or universe record.
- Corporate actions as supplemental split/name-change context, not as proof of point-in-time membership.

The Alpaca historical stock-bars endpoint accepts `1Day` and `5Min` timeframes, feed selection, adjustments and `asof` symbol mapping. It allows up to 10,000 data points per page, but can return fewer; results are sorted by symbol first and then timestamp. Every response's `next_page_token` must be followed until exhausted. A multi-symbol request's limit is total across symbols, not per symbol.

Normalization and validation will preserve symbol, UTC timestamp, timeframe, feed, adjustment mode, `asof`, OHLCV, trade count, VWAP, retrieval time, and pagination metadata. Checks include duplicate timestamps, invalid OHLC relationships, missing intervals, regular-session boundary alignment, zero-volume / zero-trade bars, incomplete bars, out-of-order data, and page completeness. A missing bar is not automatically treated as a halt.

### 4.3 Point-in-time universe and survivorship bias

This is a material limitation. Alpaca's Assets endpoint can return current `active`, `inactive`, or `all` assets. That is a current status inventory, not a daily historical record of which securities were listed and eligible on each past date. Alpaca's corporate-actions endpoint provides events (including mergers, delistings/worthless removals, and name changes), but Alpaca explicitly warns that corporate-action creation/processing can be delayed and is not guaranteed immediately after announcement. Alpaca's historical `asof` parameter can map renamed tickers across a name change; it does not reconstruct the full set of listed/delisted securities each day.

The paper used CRSP for the active/inactive security universe and IQFeed for intraday observations. Alpaca's current asset inventory and symbol-based historical bars are not, by themselves, a verified daily point-in-time universe including delisted names.

**Zero-cost retrospective baseline: a public dated S&P 500 membership dataset combined with Alpaca historical bars where available.** This provides a point-in-time S&P 500 subset, not the paper's full approximately 7,000-stock universe. Alpaca's free historical SIP access is available for requests with an `end` at least 15 minutes old, but every historically removed/delisted symbol must be tested individually; Alpaca does not provide a complete point-in-time asset master. See `docs/HISTORICAL_UNIVERSE_ACQUISITION.md`.

The free S&P 500 membership dataset is community-maintained and narrower than the original paper. Alpaca historical bars are symbol-based and do not guarantee complete inactive/delisted intraday history. The first data milestone must produce an explicit historical ticker coverage audit. From the first complete snapshot date forward, the project will archive Nasdaq Trader symbol directories and SEC public EDGAR evidence to build a broader point-in-time universe. The project will support a provider-neutral `UniverseProvider` interface and stable identifiers; raw symbols alone will never be treated as security identity.

Reports must state prominently: **“Retrospective universe is limited to point-in-time S&P 500 constituents, not all U.S.-listed stocks. Historical intraday coverage for delisted/inactive securities is not guaranteed and is audited symbol by symbol.”** Such results are a restricted-universe research experiment, not a full replication. For prospective broad-market use, the screener will archive a daily universe snapshot from the date collection begins; that does not repair earlier history.

### 4.4 ETF, IPO, and halt handling

- **Verified halts are excluded**, using a historical halt/status reference source with timestamps and reason/status codes. A missing bar alone is not proof of a halt.
- No ETF exclusion is added to the paper baseline because the paper does not specify that filter and the user has not requested it. Security type will be tagged, ETF results can be reported separately, and an ETF-excluded sensitivity can be added only as a declared variant.
- No arbitrary 60-/90-day IPO-age cutoff is added. A newly listed security naturally fails until it has the required 14 completed sessions of history. IPO status will be retained as a tag for diagnostics.

## 5. Proposed deterministic implementation specification

### 5.1 Daily and 9:35 screener sequence

1. Load the date-effective security universe for that session and record the universe source and point-in-time status.
2. From the prior 14 completed regular sessions, calculate ADV14 and ATR14. Apply `ADV14 >= 1,000,000` and `ATR14 > $0.50`.
3. Use prior close > $5 as a pre-open proxy only if a pre-open candidate list is required. After 9:35 ET, confirm the first regular-session bar's opening price is strictly > $5.
4. Validate the complete 9:30–9:35 ET bar; calculate RV from its volume divided by the arithmetic mean of the same bar's volume on the previous 14 trading sessions, all from the selected feed.
5. Remove symbols with insufficient history, missing/zero denominator, incomplete opening bars, invalid data, or RV < 1.0.
6. Rank remaining symbols by RV descending; break exact ties deterministically by symbol ascending; retain at most 20 names overall.
7. For each selected symbol, bullish first bar => long-only entry stop at opening-bar high; bearish => short-only entry stop at opening-bar low; doji => no entry.

### 5.2 Backtest and sizing policy

- Simulate entries using subsequent regular-session 5-minute bars; do not assume a fill before the opening bar has closed and ranking is complete.
- Stop distance is `0.10 * ATR14` from actual entry fill. **ATR smoothing default: Wilder's 14-period smoothing**, because the paper's ATR footnote cites Wilder; the paper does not explicitly spell out the smoothing recurrence, so this is a documented replication convention, not a claim that the authors confirmed the exact implementation.
- If a stop is crossed by a gap, fill at the worse available bar open/stop price plus adverse slippage. If an entry and stop can both be touched in one 1-minute source bar, assume entry then stop (unfavorable ordering). If any future target extension and stop are both touched in one bar, stop is assumed first. One-minute source data is preferred; aggregate to 5-minute bars for the official ORB signal so the opening window matches the paper.
- If not stopped, exit using the **final regular-session 15:55–16:00 ET bar close** as the base proxy for the paper's 4:00 p.m. exit. This is a bar-close proxy, not a guaranteed official closing-auction fill; if the purchased data include a separately identified closing-auction print, report it as a separate sensitivity.
- One opening-range setup per symbol per day; unfilled entry orders expire at session end. This is a proposed operational rule because the paper does not explicitly describe re-entry after a stopped trade.
- Starting research equity is **$100,000**.
- Proposed sizing for the primary conservative portfolio implementation: **1.0% of current account equity at risk per position**, with **4.0% maximum aggregate open stop-risk** and **4.0x maximum gross exposure**. This is a clearly labeled implementation choice, not an exact interpretation of the paper's ambiguous phrase “1% of capital deployed for that position.” The selector ranks the top 20, but new positions are opened only while both the aggregate stop-risk and gross-exposure caps permit. A 0.5%-per-position / 2%-aggregate sensitivity will also be reported. User confirmation of these portfolio-level risk caps is still required before coding.
- Produce independent `long`, `short`, `combined_long_short`, and `long_only` books/reports. Short backtests require historical borrow/locate assumptions and an explicit borrow-fee model; current Alpaca `shortable` / `easy_to_borrow` flags cannot be treated as historical availability.

### 5.3 Cost model

Two independently labeled commission/regulatory-fee views will be shown for every backtest; spread and slippage are charged in both views:

1. **Paper reference-cost view:** `$0.0035 per share per executed side`, applied independently to entry and exit fills. The paper reports $0.0035/share and identifies it as Interactive Brokers Pro–Tiered pricing as of Dec. 31, 2023; it does not include an explicit bid/ask or slippage model. Applying the rate per executed side is our explicit accounting convention for this comparison.
2. **Alpaca schedule view:** commission set to `$0.00` for a self-directed individual account eligible for Alpaca commission-free trading, plus effective-dated regulatory charges. As currently published (fee schedule revised Apr. 1, 2026), FINRA TAF is `$0.000195/share` on sells, capped at `$9.79` per trade and rounded up to the nearest cent; the SEC Section 31 rate is `$20.60 per $1,000,000` of covered sell principal from Apr. 4, 2026; the CAT rate is currently shown as `$0.00`. These rates can change and the model will use dated fee tables rather than assume today's rates applied historically. Account eligibility and the fee schedule must be verified against the user's actual Alpaca account agreement. SEC rate source: FY2026 advisory; Alpaca fee schedule and regulatory-fee support pages.

In both views:
- Bid/ask spread is measured from historical SIP NBBO/quote data where available. The opening 9:30–9:35 window uses its own observed spread distribution and conservative fill-side treatment; a marketable buy pays the ask and a marketable sell receives the bid. If the purchased data lack quotes, use a predeclared conservative time-of-day spread schedule and label it modeled, not observed. Do not count half-spread twice if fills already use bid/ask prices.
- Slippage is a separate adverse per-fill parameter, initially 5 bps per side when no higher-quality event-level estimate is available; stress it at 10 bps per side. Run additional predeclared sensitivity values rather than tuning to performance.
- Short borrow/locate availability and borrow fees remain separate. Without historical locate/borrow data, short results are assumption-dependent; a long-only result is mandatory.
- Run a stress scenario that doubles the commission, spread, and slippage assumptions. Reports show gross P&L, each cost component, net P&L, and reconciliation error. Paper commission-only results are not directly comparable to all-in-cost Alpaca results.

### 5.4 Benchmarks and validation

- Benchmark: SPY buy-and-hold over exactly the same dates. Use a dividend/split-adjusted total-return series for SPY; report strategy return, SPY return, excess return, and each maximum drawdown on matching daily portfolio-NAV dates.
- Separate long, short, combined, and long-only performance and risk statistics.
- Use chronological walk-forward folds only. Parameter selection can use training data only; validation/test periods remain unseen until evaluation. A terminal holdout must be sealed in the run configuration and omitted from all tuning/report summaries until the final evaluation.
- A period inspected during design/debugging or used for parameter changes must not be called an untouched holdout.
- Proposed minimum evidence: at least 100 closed trades per side in aggregate out-of-sample results and at least 30 closed trades per side in a validation fold. Anything below the relevant threshold is labeled **“insufficient trades”**; the thresholds are proposed and require confirmation.

### 5.5 Predeclared, small comparison grid

Primary model is the paper specification. The following are one-factor robustness checks, not an optimizer search; no best-looking parameter is selected after seeing the final holdout:

| Variant | Opening range | RV floor | Top N | Stop distance |
|---|---:|---:|---:|---:|
| Paper baseline | 5 min | 1.0 | 20 | 0.10 × ATR14 |
| RV sensitivity | 5 min | 2.0 | 20 | 0.10 × ATR14 |
| Concentration sensitivity | 5 min | 1.0 | 10 | 0.10 × ATR14 |
| Stop sensitivity | 5 min | 1.0 | 20 | 0.20 × ATR14 |

The last three are explicitly adaptations, not parameters claimed to be from the paper. All four are reported, not used for unconstrained in-sample optimization. If desired, a separately declared 15-minute ORB replication can be added later; the paper tested 15-, 30-, and 60-minute windows but found the 5-minute variant strongest in its sample.

## 6. Proposed project layout (new root: `G:\orb-stock-research`)

```text
orb-stock-research/
├── .env.example                 # variable names only; never real credentials
├── .gitignore                   # excludes .env, venv, cache, raw private data
├── README.md
├── pyproject.toml               # package/dependencies/test configuration
├── config/
│   ├── research.yaml            # feed, universe, filters, sizing, costs, splits
│   └── parameter_grid.yaml      # frozen baseline + one-factor checks
├── docs/
│   ├── MILESTONE_1_DESIGN.md
│   ├── DATA_DICTIONARY.md
│   └── METHODOLOGY.md
├── data/
│   ├── raw/alpaca/              # optional local raw responses, not committed
│   ├── normalized/              # normalized daily and 5m bars
│   ├── universe_snapshots/      # date-effective security master inputs
│   ├── cache/
│   └── reports/YYYY-MM-DD/
├── src/orb_research/
│   ├── __init__.py
│   ├── config.py
│   ├── mcp/
│   │   ├── client.py            # read-only MCP connection
│   │   └── market_data.py       # approved market-data tools only
│   ├── data/
│   │   ├── schemas.py
│   │   ├── normalize.py
│   │   ├── validate.py
│   │   └── pagination.py
│   ├── universe/
│   │   ├── provider.py
│   │   ├── alpaca_assets.py
│   │   └── point_in_time.py
│   ├── screener/
│   │   ├── daily_filters.py
│   │   ├── relative_volume.py
│   │   ├── ranking.py
│   │   └── reports.py
│   ├── strategy/
│   │   ├── orb.py
│   │   ├── sizing.py
│   │   └── costs.py
│   ├── backtest/
│   │   ├── fills.py
│   │   ├── engine.py
│   │   └── portfolio.py
│   ├── validation/
│   │   ├── walk_forward.py
│   │   ├── holdout.py
│   │   └── benchmarks.py
│   └── reporting/
│       ├── audit.py
│       └── export.py
├── scripts/
│   ├── inspect_mcp_tools.py
│   ├── fetch_daily_bars.py
│   ├── fetch_5m_bars.py
│   └── run_screener.py
└── tests/
    ├── test_normalize.py
    ├── test_validation.py
    ├── test_pagination.py
    ├── test_universe.py
    ├── test_screener.py
    ├── test_orb_signals.py
    ├── test_sizing.py
    ├── test_costs.py
    ├── test_backtest_fills.py
    └── test_read_only_safety.py
```

## 7. Report audit fields (required in every output)

Every CSV and readable report will record at least:

- Run ID, UTC creation time, requested and actual data feed, feed mode (historical/realtime), bar timeframe, adjustment mode, market-data source, and data coverage range.
- Universe source, universe snapshot date, point-in-time coverage status, delisted coverage status, and an explicit survivorship-bias warning where applicable.
- Strategy version/hash, frozen parameter set, risk sizing convention, leverage cap, and long/short mode.
- Commission, spread, slippage, borrow-fee and regulatory-fee assumptions; base/stress scenario marker.
- Data-quality counts: missing bars, invalid bars, zero-volume/trade bars, incomplete bars, symbols excluded and reason codes.
- Gross P&L, each cost component, net P&L, reconciliation error, SPY return, excess return, maximum drawdown, trade count, and evidence label.

## 8. Decisions recorded and remaining confirmation

### 8.1 User-confirmed decisions

| Area | Confirmed decision |
|---|---|
| Project isolation | New root `G:\orb-stock-research`; no imports from or edits to the earlier BTC project. |
| Phase 1 safety | Read-only; no order placement/replacement/cancellation/closing imports or calls; deterministic Python rules; no LLM trading decisions. No live trading at any stage. A separate paper stage requires explicit approval. |
| Historical/realtime feeds | Use SIP for historical research and IEX for a free real-time screener as separate, clearly labeled profiles. Never mix feeds in RV numerator/denominator. |
| Starting capital | `$100,000`. |
| Halts | Exclude only securities with a verified halt record; a missing bar alone is not proof of a halt. |
| Costs | Show both the paper's `$0.0035/share/executed-side` reference cost and the applicable Alpaca fee schedule, with explicit spread/slippage and doubled-cost stress. |
| ATR convention | Wilder ATR(14), documented as an implementation convention because the paper cites Wilder but does not fully specify smoothing. |
| EOD fill proxy | Final regular-session 15:55–16:00 ET bar close; separately compare official closing-auction price if the data provider supplies a reliable auction print. |
| ETFs / IPOs | No new ETF exclusion or arbitrary IPO-age cutoff in the baseline. Tag and report security types separately; insufficient 14-session history naturally excludes very new listings. |

### 8.2 Proposed historical data acquisition

Zero-cost baseline: use a public point-in-time S&P 500 membership history plus Alpaca historical SIP bars for completed historical periods (when the requested `end` is at least 15 minutes old). The S&P 500 subset is not the paper's full U.S. equity universe; Alpaca historical bars for inactive/delisted symbols must be verified individually. For the free 9:35 live screener, use Alpaca IEX and calculate both the opening-bar RV numerator and denominator from IEX. Starting now, archive Nasdaq Trader symbol directories and SEC EDGAR evidence daily to build broad-market point-in-time coverage prospectively. See `docs/HISTORICAL_UNIVERSE_ACQUISITION.md`.

### 8.3 One unresolved portfolio-risk setting

The paper's phrase “1% of capital deployed for that position” does not unambiguously specify whether the denominator is total account equity or the position's capital allocation. For a reproducible and risk-capped implementation, the proposed primary setting is 1.0% of current account equity at risk per position, 4.0% maximum aggregate open stop-risk, and 4.0x maximum gross exposure. A 0.5%-per-position / 2.0%-aggregate-risk sensitivity will also be reported. **Please confirm these proposed limits or specify alternatives before Milestone 2 / strategy implementation.**

### 8.4 Milestone 1 exit condition

Milestone 1's research specification is updated. Before Milestone 2, the user should:

1. Acquire and validate the free point-in-time S&P 500 membership files, then run the Alpaca symbol-by-symbol historical SIP coverage audit before building any historical screener/backtest.
2. Confirm the proposed 1.0% per-position / 4.0% aggregate stop-risk limits, or change them.

No Milestone 2 code or data retrieval begins until the user confirms the vendor/data path and risk limits. No broker execution functionality will be included.
