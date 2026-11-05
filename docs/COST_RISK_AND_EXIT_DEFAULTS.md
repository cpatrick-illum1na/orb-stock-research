# Cost, Risk and Execution Defaults — Milestone 1

This file freezes the design choices agreed or recommended before any strategy code is written. They are research assumptions, not financial advice or a prediction of profitability.

## Capital and risk

- Starting equity: **$100,000**.
- Proposed primary sizing: **1.0% of current account equity at risk per position** (initially $1,000 at $100,000), where risk = absolute entry-to-stop distance × whole-share quantity.
- Proposed max aggregate open stop-risk: **4.0% of current equity**.
- Max gross notional exposure: **4.0× current equity**.
- Position quantity: floor(`risk_budget_dollars / stop_distance_dollars`), then cap by remaining gross exposure. Do not round up.
- The top-20 ranking is a screener/ranking list, not a guarantee that all 20 names can be held simultaneously. New positions are accepted in deterministic rank order only while portfolio risk and leverage caps allow.
- Report a sizing sensitivity at 0.5% risk per position and 2.0% maximum aggregate open risk.
- These are proposed implementation limits. They do not exactly reproduce the paper's ambiguous “1% of capital deployed for that position” denominator. User confirmation remains outstanding.

## ATR and end-of-day exit

- ATR: Wilder's ATR(14), computed from 14 prior completed regular-session daily bars using true range = max(high-low, abs(high-prev_close), abs(low-prev_close)). No current-day high/low/close may enter the pre-open ATR.
- Stop: 0.10 × ATR14 in dollars from the actual executed entry price, matching the paper's 10%-of-ATR rule.
- EOD proxy: the final regular-session 15:55–16:00 ET bar close. Label it as a bar-close proxy, not an official closing-auction fill. If the data source supplies explicitly identified auction prints, compare them separately.

## Cost model views

Both views include the same measured/modelled bid-ask spread and slippage; only broker/commission components differ.

### Paper reference

- Commission: $0.0035 per share per executed side, entry and exit recorded independently. The source paper reports the per-share rate but does not spell out per-side accounting; per-side charging is the implementation convention.
- No historical quote/spread or slippage deduction is claimed to be present in the paper's cost model.

### Alpaca fee schedule view

- Commission: $0.00 for eligible self-directed individual commission-free accounts; verify applicability to the user's account agreement.
- FINRA TAF: use an effective-dated schedule. As published in Alpaca's fee schedule revised Apr. 1, 2026, `$0.000195/share` on sell executions, capped at `$9.79` per trade, rounded up to cents.
- SEC Section 31: use effective dates from SEC advisories. FY2026 rate is `$0.00 per $1,000,000` through Apr. 3, 2026 and `$20.60 per $1,000,000` for charge dates from Apr. 4, 2026, per the Feb. 27, 2026 advisory.
- CAT fee: Alpaca's Apr. 1, 2026 schedule lists `$0.00` for NMS equities.
- Regulatory rates may change. Code must load an effective-dated rate table and store the rate-table version in every report; do not apply today's rates to earlier historical dates.

## Spread and slippage

- Use consolidated NBBO quotes at/around entry and exit whenever the acquired SIP dataset supplies adequate quote timestamps.
- For marketable buys, model ask-side execution; for sells, bid-side execution. Do not add a second half-spread if the fill price already uses ask/bid.
- The opening window gets its own measured spread distribution; do not use a midday average spread for 9:30–9:35 ET.
- Additional slippage default when event-level estimation is unavailable: 5 bps adverse per executed side; stress: 10 bps per side. Mark this as an explicit modeled assumption, not observed slippage.
- Stress scenario doubles base commission, spread and slippage. Paper commission view therefore becomes $0.007/share/side in the doubled-cost scenario; Alpaca regulatory rates themselves are not arbitrarily doubled for an official-fee display, but an additional conservative fee-stress line may be shown separately.

## Long / short / portfolio reports

- Report long-only, short-only, and combined books separately.
- For short positions, model only point-in-time borrow/locate-eligible securities where reliable historical availability exists. If not available, label short results assumption-dependent and show the long-only result as the primary executable baseline.
- If both an entry and stop are touched in one source bar, assume the adverse ordering: entry first, then stop. If the bar opens through the stop, use the worse opening price plus slippage. A missing bar is not proof of a halt.

## SPY benchmark

- Buy-and-hold SPY over exactly the same evaluation dates, using a split/dividend-adjusted total-return series; report SPY return, ORB return, excess return, and both maximum drawdowns on matching daily NAV dates.
