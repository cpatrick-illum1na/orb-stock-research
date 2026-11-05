# Milestone 3 reference-data operations

This document describes the optional ETF/security-type and trading-halt controls added to the deterministic screener.

## 1. Create a date-specific ETF/non-ETF classification

### Source

Nasdaq Trader's consolidated directory:

`https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt`

The file has a header containing `Nasdaq Traded`, `Symbol`, `Security Name`, `Listing Exchange`, `ETF`, `Test Issue`, `CQS Symbol`, and `NASDAQ Symbol`, followed by a `File Creation Time` footer. The importer keeps all rows in the consolidated directory, records the `Nasdaq Traded` field as metadata, and classifies ETF status only from an explicit `ETF=Y` or `ETF=N` flag. It does not infer common stock from `ETF=N`.

### Download and classify

Set the date to the session being screened. This example uses November 4, 2026:

```powershell
$ScreenDate = "2026-11-04"
New-Item -ItemType Directory -Force .\reference_data\nasdaq, .\data\reference | Out-Null

Invoke-WebRequest `
  -Uri "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt" `
  -OutFile ".\reference_data\nasdaq\nasdaqtraded_$ScreenDate.txt"

python -m orb_data.cli reference classify-nasdaq-traded `
  --nasdaq-traded ".\reference_data\nasdaq\nasdaqtraded_$ScreenDate.txt" `
  --effective-date $ScreenDate `
  --max-age-calendar-days 7 `
  --output ".\data\reference\security_classification_$ScreenDate.csv"
```

The command writes a classification CSV plus a `{stem}_manifest.json` sidecar. The manifest captures the raw input hash, output hash, source URL, source creation timestamp, age, and counts.

The file is rejected if its Nasdaq creation timestamp is after 09:35 ET on the effective date, or if it exceeds the configured maximum age. Save each raw downloaded file permanently with its date. Nasdaq's current directory is not a historical archive; a file downloaded today must not be assigned to an earlier historical date.

`security_type` has only `ETF`, `NON_ETF`, and `UNKNOWN`. `NON_ETF` is not synonymous with `COMMON_STOCK`. Securities lacking an explicit ETF flag or with conflicting aliases remain `UNKNOWN`. A `Test Issue=Y` record is rejected by the screener.

## 2. Obtain and normalize halt events

The normalizer accepts Cboe, Nasdaq, Quant-Lodge, or project-canonical CSV layouts. Always record the exact source name and exact download/release URL.

### Cboe 2026 annual CSV

The Cboe 2026 halt file is available at:

`https://www-api.cboe.com/us/equities/market_statistics/halts/downloads/BatsHalts2026.csv`

Download and normalize it with:

```powershell
New-Item -ItemType Directory -Force .\reference_data\halts, .\data\reference | Out-Null

Invoke-WebRequest `
  -Uri "https://www-api.cboe.com/us/equities/market_statistics/halts/downloads/BatsHalts2026.csv" `
  -OutFile ".\reference_data\halts\cboe_halts_2026.csv"

python -m orb_data.cli reference normalize-halts `
  --input ".\reference_data\halts\cboe_halts_2026.csv" `
  --output ".\data\reference\cboe_halts_2026_normalized.csv" `
  --format cboe `
  --source-name "Cboe 2026 Historical Halts CSV" `
  --source-url "https://www-api.cboe.com/us/equities/market_statistics/halts/downloads/BatsHalts2026.csv"
```

The output is a normalized CSV plus a source manifest. This confirms provenance and file integrity, **not** complete coverage for a particular universe or screen window.

### Quant-Lodge yearly CSV (third-party option)

The Quant-Lodge repository documents immutable daily release tags in the `halts-YYYY-MM-DD` namespace and yearly CSVs named `halts_YYYY.csv`. Example URL pattern:

`https://github.com/Quant-Lodge/ticker-reference-data/releases/download/halts-<release-date>/halts_<year>.csv`

Only use an actual published release tag; do not invent one. The repository describes historical coverage from 2019-02-22 onward and a reconciled set of Cboe/Nasdaq/NYSE feed events. It is community-maintained, not an official exchange source, and its own documentation disclaims guarantees. Cross-check material events against primary exchange sources before relying on it.

To normalize a downloaded release:

```powershell
python -m orb_data.cli reference normalize-halts `
  --input ".\reference_data\halts\halts_2026.csv" `
  --output ".\data\reference\halts_2026_normalized.csv" `
  --format quant-lodge `
  --source-name "Quant-Lodge ticker-reference-data dated halt release" `
  --source-url "<paste the exact immutable release URL here>"
```

The parser uses `resume_trade_time` rather than `resume_quote_time`. Empty actual trade-resumption time is not imputed. Multiple halts for a symbol on the same day remain separate events.

### Nasdaq halt search

Nasdaq's Trading Halt Search is another primary reference, but its public historical search window is limited (documented as approximately one year). The normalized interface is available for CSV exports whose columns include halt date/time and actual resumption trade time. The project does not automatically scrape the web search page.

## 3. Create the exact-universe coverage template

The template binds the attestation to the exact universe CSV, normalized symbol list, halt-event CSV, and source manifest:

```powershell
$ScreenDate = "2026-11-04"

python -m orb_data.cli reference halt-coverage-template `
  --date $ScreenDate `
  --universe-file ".\data\universe\sp500_asof_$ScreenDate.csv" `
  --halt-events ".\data\reference\halts_2026_normalized.csv" `
  --output ".\data\reference\halt_coverage_$ScreenDate.json"
```

The generated JSON is intentionally **not** an approval. It sets `coverage_complete=false`, `coverage_attested=false`, and `source_data_available_by_cutoff=false`.

Only change those fields after independently establishing all of the following:

- the source covers every symbol in the exact point-in-time universe;
- the source covers all relevant US listing venues and events, including multiple halts per symbol;
- the source's time and resumption fields have the required meaning;
- the source was actually available by 09:35 ET on the screen date;
- a source publication/availability timestamp and evidence are available;
- the source scope includes the entire 09:30–09:35 ET opening interval.

The required attestation fields are:

- `coverage_complete: true`
- `coverage_attested: true`
- `source_data_available_by_cutoff: true`
- `source_available_at_et`: ISO datetime with a timezone offset, no later than the screen-date 09:35 ET cutoff
- `source_availability_proof`: concise evidence explaining the timestamp and how it was established
- `attested_by` and `attestation_notes`

Do not use a retrospective end-of-day halt archive as proof that halt data was available at 09:35 ET. Its event timestamps can show when a halt occurred, but not when the record was published or ingested. If that cannot be established, leave the attestation false. The screen output will remain pending rather than falsely claim a verified-clear candidate.

The code validates the exact hashes, screen date, source attribution, exact-universe scope, interval, and source-availability timestamp. It cannot independently validate a human's truthfulness or replace due diligence on the source's completeness.

## 4. Run the screen with reference controls

```powershell
python -m orb_data.cli screen `
  --date $ScreenDate `
  --universe-file ".\data\universe\sp500_asof_$ScreenDate.csv" `
  --daily-bars "<daily bars CSV path>" `
  --intraday-bars "<five-minute bars CSV path>" `
  --feed sip --top-n 20 `
  --security-reference ".\data\reference\security_classification_$ScreenDate.csv" `
  --halt-events ".\data\reference\halts_2026_normalized.csv" `
  --halt-coverage-manifest ".\data\reference\halt_coverage_$ScreenDate.json"
```

The generated all-false template may be passed to `--halt-coverage-manifest` for an auditable **pending-only** screen. It must match the exact screen date, universe file/symbol hashes, halt-event file hash, source attribution, and opening window, but it does not confer clearance. The screener accepts only two coherent flag states: all three flags false (pending review), or all three true with every supporting evidence field valid. Mixed/partially true flags are rejected. Passing a normalized events file without a complete attestation still applies positive, source-attributed halts as exclusions, but no-event candidates are `SELECTED_PENDING_HALT_CHECK`.

## 5. Interpret the output

- `SELECTED_HALT_CLEAR_VERIFIED`: passed the numeric/price rules, ranked in the top N, and exact-universe/source-availability halt coverage attestation passed.
- `SELECTED_PENDING_HALT_CHECK`: passed the screen, but absence of a matching halt event was not proven to mean no halt.
- `REJECTED` with `HALTED_DURING_OPENING_WINDOW`: a source-attributed halt overlaps 09:30–09:35 ET.
- `REJECTED` with `HALT_REVIEW_REQUIRED`: event timing/resumption is ambiguous and the code fails closed.

The combined candidate CSV includes pending rows for auditability; pending rows are not cleared for downstream use. Separate candidate files are created for ETFs, non-ETFs, and unknown classifications. The combined ranking retains ETFs as required by the M1 design.

## 6. Historical limits

The official Cboe CSV cited above is for 2026. Nasdaq's public halt search has a limited recent lookback. The community Quant-Lodge archive claims a history floor of 2019-02-22, but is not official and has no guarantee. This milestone has **not** established a complete free halt archive from 2004 onward. Pre-2019 periods cannot be marked fully halt-clear using these sources alone.
