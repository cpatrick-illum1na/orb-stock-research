$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.env")) {
    throw "Project .env was not found. The existing read-only fetch client requires local credentials; do not print or share the file."
}

$screenDate = "2026-11-03"
$symbolFile = ".\data\universe\m3_smoke_symbols.txt"
New-Item -ItemType Directory -Force ".\data\universe" | Out-Null
@("AAPL", "MSFT", "AVY", "CINF", "NVDA") | Set-Content -Path $symbolFile -Encoding ascii

Write-Output "=== Fetch daily history (SIP/raw) ==="
python -m orb_data.cli fetch `
    --symbols-file $symbolFile `
    --timeframe 1Day `
    --start "2026-10-01T00:00:00Z" `
    --end "2026-11-03T23:59:59Z" `
    --feed sip `
    --adjustment raw `
    --asof $screenDate
if ($LASTEXITCODE -ne 0) { throw "Daily historical fetch failed with exit code $LASTEXITCODE." }

$dailyFile = Get-ChildItem ".\data\raw\1Day\sip\bars_1Day_sip_raw_2026-10-01_2026-11-03_*.csv" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if (-not $dailyFile) { throw "Could not locate the generated daily bars CSV." }

Write-Output "=== Fetch 5-minute history (SIP/raw) ==="
python -m orb_data.cli fetch `
    --symbols-file $symbolFile `
    --timeframe 5Min `
    --start "2026-10-01T00:00:00Z" `
    --end "2026-11-03T21:00:00Z" `
    --feed sip `
    --adjustment raw `
    --asof $screenDate
if ($LASTEXITCODE -ne 0) { throw "5-minute historical fetch failed with exit code $LASTEXITCODE." }

$intradayFile = Get-ChildItem ".\data\raw\5Min\sip\bars_5Min_sip_raw_2026-10-01_2026-11-03_*.csv" |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if (-not $intradayFile) { throw "Could not locate the generated 5-minute bars CSV." }

Write-Output "=== Run deterministic local ORB screen ==="
python -m orb_data.cli screen `
    --date $screenDate `
    --universe-file $symbolFile `
    --daily-bars $dailyFile.FullName `
    --intraday-bars $intradayFile.FullName `
    --feed sip `
    --top-n 20
if ($LASTEXITCODE -ne 0) { throw "ORB screen failed with exit code $LASTEXITCODE." }
