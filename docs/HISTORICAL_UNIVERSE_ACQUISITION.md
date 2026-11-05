# Free Historical Universe and Intraday Data Plan

**Project:** `G:\orb-stock-research`  
**Budget:** $0. No paid vendors, subscriptions, datasets, or APIs.  
**Status:** Design only; this document does not retrieve data or connect to Alpaca.

## Recommendation: use a point-in-time S&P 500 subset for retrospective research, and build a broad-market archive prospectively

There is no free source I can currently verify that provides a complete, point-in-time universe of every U.S.-listed stock (including delisted names) together with complete SIP 1-minute/5-minute history and historical quotes/halts for the full market. We should not describe a free approximation as a complete survivorship-bias-free replication of the paper.

The best defensible $0 plan has two parts:

1. **Retrospective universe:** use a public, dated S&P 500 constituent history as a restricted point-in-time universe. This includes securities later removed from the index, but it is not the paper's approximately 7,000-stock universe. The results must be labeled `S&P 500 point-in-time subset`, not `full U.S. market`.
2. **Prospective broad universe:** beginning on the first collection date, save daily snapshots of current U.S. exchange-listed securities from public exchange symbol files and the SEC's public EDGAR data. Preserve each day's file and effective date. This creates genuine point-in-time coverage going forward; it cannot repair earlier history.

Use Alpaca's free market-data access for bars where the symbol's history is actually available. Run a coverage audit for inactive/delisted symbols before assuming they are present.

## A. Free point-in-time S&P 500 constituent history

A public repository provides dated S&P 500 membership changes, including additions and removals, for 2004 through June 2026. It documents that post-2019 changes are reconciled against official S&P announcements, and says its source membership data comes from an MIT-licensed upstream repository. Treat it as a community-maintained research source, validate it, preserve the commit hash used, and do not assume it is an official S&P feed.

Repository: [SP500-Survivorship-bias-data-2004-2026](https://github.com/shardul0701/SP500-Survivorship-bias-data-2004-2026)

### Windows / PowerShell acquisition steps

Run these commands in a separate data-source folder, not inside the Alpaca application source:

```powershell
cd G:\orb-stock-research
New-Item -ItemType Directory -Force .\reference_data | Out-Null
Set-Location .\reference_data

git clone https://github.com/shardul0701/SP500-Survivorship-bias-data-2004-2026.git .\sp500-pit
Set-Location .\sp500-pit

python -m pip install -r requirements.txt
python scripts/validate_yaml.py

# Reconstruct a point-in-time universe for a specific historical date:
python scripts/build_universe.py 2015-06-01

# List every ticker that appears in the repository's historical membership:
python scripts/build_universe.py --all
```

The repository states that membership changes are verified through June 2, 2026, with a later announced rebalance stored as pending. For dates after the verified cutoff, use only changes confirmed from official S&P announcements, and preserve their source and effective date. Do not infer index membership from today's constituents.

### What this does and does not solve

- It includes past S&P 500 constituents that were subsequently removed; it avoids the simple error of using today's S&P 500 list for all historical dates.
- It represents only the S&P 500. The ORB paper ranked a much broader U.S. equity universe. A top-20 result among S&P constituents is a different strategy experiment and may miss small/mid-cap stocks in play.
- It is a membership list, not a complete intraday tape. It does not prove that Alpaca has 5-minute history for every removed/delisted constituent.
- Ticker-only joins are unsafe. Keep the repository's dated membership, preserve ticker history, and maintain our own canonical security mapping. Ticker reuse and corporate actions must be explicitly audited.

## B. Free historical bars through Alpaca

Alpaca's current Market Data FAQ states that a historical SIP query can be made without the paid subscription when the request's `end` parameter is at least 15 minutes old. The same FAQ says the latest endpoints / real-time SIP require the paid subscription. Alpaca's free real-time equities feed is IEX. Its historical bars API supports `1Min`, `5Min`, and `1Day` bars and pagination.

Sources:
- [Alpaca Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca Historical Bars API](https://docs.alpaca.markets/us/reference/stockbars)
- [Alpaca Market Data plans](https://docs.alpaca.markets/us/docs/about-market-data-api)

Free feed design:

| Use | Feed | Access rule | Important limitation |
|---|---|---|---|
| Retrospective reference backtest | SIP | Historical `end` at least 15 minutes old | Must verify each symbol, particularly inactive/delisted tickers |
| Retrospective IEX sensitivity run | IEX | Historical bars | Single-exchange volume; not consolidated volume |
| Free 9:35 live screener | IEX | Basic plan real-time access | Does not see the consolidated market |

Never mix IEX numerator with SIP denominator for relative volume. The 9:35 live scanner on the free plan will rank IEX-observed first-five-minute volume against the same symbol's IEX first-five-minute history. Label it clearly as an IEX-only ranking.

For historical SIP, the project can request `feed=sip` with an end time older than 15 minutes. This allows research on completed historical dates without buying the real-time SIP subscription. Do not assume that this guarantees historical bars for a delisted security: Alpaca's asset inventory is current-state metadata, not a point-in-time security master. The first data milestone must test representative renamed and delisted tickers and record missing coverage by symbol/date/feed.

### Mandatory inactive/delisted coverage audit

Before any full-universe backtest, build a table containing:

- historical ticker and canonical security ID (where independently available);
- date(s) when it was in the S&P 500 point-in-time membership list;
- Alpaca daily SIP request result;
- Alpaca 1-minute/5-minute SIP request result;
- Alpaca IEX request result;
- earliest/latest returned bar and missing dates;
- rename/as-of handling used;
- whether the asset is now inactive;
- final inclusion/exclusion reason.

If Alpaca does not return intraday bars for a former constituent, do not silently drop it and continue calling the result survivorship-free. Report the missing-coverage rate and classify the backtest as incomplete. Do not fill missing intraday bars using a different feed without labeling a separate dataset/experiment.

## C. Build a broad U.S. listing archive from today forward (free)

This is the way to avoid survivorship bias prospectively without paying for a historical vendor.

1. On every market day before the open, save the current Nasdaq Trader `All Issues` / symbol directory file. The page states that its downloadable symbol information is as of the current trading day; archive the raw file rather than assuming it remains historically queryable. [Nasdaq Trader Symbol Lookup](https://nasdaqtrader.com/trader.aspx?id=symbollookup)
2. Save the SEC's company submissions bulk JSON and daily filing/index evidence. The SEC says its submissions APIs are public and do not require API keys. The API exposes current issuer metadata and filing histories; it is not itself a complete historical security master. [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
3. Process relevant exchange delisting notices and SEC Form 25 filings as evidence of listing removals. Preserve the filing timestamp, effective date when separately specified, CIK, exchange, old ticker, and source URL. Form 25 alone will not catch every possible security lifecycle event, so classify this archive as reconstructed and audit exceptions.
4. Store each daily snapshot immutably with SHA-256 hash, retrieval timestamp, source URL, and parser version. Never overwrite a prior snapshot with a refreshed current symbol list.
5. Continue archiving Alpaca IEX and historical SIP bars for the eligible symbols. The broad-market point-in-time history will become valid only from the first successfully archived date forward.

This prospective archive can eventually support full-market ORB research from its start date. It cannot eliminate survivorship bias from years before collection began.

## D. Optional free intraday dataset — research-only restriction

The HF Data Library publishes free 1-minute IEX OHLCV for 1,253 U.S. stocks/ETFs from March 2022 onward, under CC BY 4.0. However, its own terms state **academic research only, not for trading**. Because this project is intended to evaluate a strategy that could later be paper traded, do not use this dataset as the strategy's operational/backtest source unless the maintainer provides written permission that covers this use. It can be considered only for a separate, genuinely academic study that complies with its terms.

Sources: [HF Data Library](https://hfdatalibrary.com/) · [data coverage and limitations](https://hfdatalibrary.com/pages/data)

## E. Mandatory limitations in every report

For the retrospective S&P 500 subset, every report must state:

> Universe coverage is limited to point-in-time S&P 500 constituents, not all U.S.-listed equities. It is not a full replication of the paper's approximately 7,000-stock universe. Historical membership is sourced from a community-maintained dataset. Intraday availability for subsequently delisted securities is separately audited through Alpaca; missing bars are not imputed or silently excluded. These results may retain survivorship/coverage bias where historical inactive-security data are unavailable.

For the prospective broad universe, every report must state its first complete snapshot date and any exchange/security categories missing from the snapshot. Before that date, the archive does not claim complete point-in-time coverage.

## F. Cost

Target recurring data cost: **$0**. This plan uses public GitHub membership files, SEC public APIs/filings, Nasdaq Trader downloadable current-day files, and Alpaca's free data entitlement. It has material universe/coverage limitations compared with paid commercial data. No paid vendor or subscription is required or assumed.

## Sources

- https://github.com/shardul0701/SP500-Survivorship-bias-data-2004-2026
- https://docs.alpaca.markets/us/docs/market-data-faq
- https://docs.alpaca.markets/us/reference/stockbars
- https://nasdaqtrader.com/trader.aspx?id=symbollookup
- https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- https://hfdatalibrary.com/ (academic-only alternative; not the project default)
