from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigurationError, load_config
from .mcp_bars import MCPConnectionError, UnsafeMCPConfiguration, open_alpaca_bars
from .service import retrieve_stock_bars
from orb_screener.screen import ScreeningError, run_screen
from orb_screener.reference_data import (
    ReferenceDataError, classify_nasdaq_directories, classify_nasdaq_traded_file,
    normalize_halt_events, write_halt_coverage_template,
)
from .universe import UniverseDatasetError, get_all_sp500_tickers, get_sp500_universe


def _parse_datetime(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid ISO datetime: {value}") from exc
    if result.tzinfo is None:
        raise argparse.ArgumentTypeError("Datetime must include timezone, e.g. 2025-01-01T00:00:00Z")
    return result.astimezone(timezone.utc)


def _read_symbols(value: str | None, symbols_file: str | None) -> list[str]:
    items: list[str] = []
    if value:
        items.extend(x.strip().upper() for x in value.split(",") if x.strip())
    if symbols_file:
        path = Path(symbols_file)
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            sample = stream.read(4096)
            stream.seek(0)
            if "," in sample or "symbol" in sample.lower():
                reader = csv.reader(stream)
                rows = list(reader)
                if rows:
                    header = [cell.strip().lower() for cell in rows[0]]
                    col = header.index("symbol") if "symbol" in header else 0
                    start_index = 1 if "symbol" in header else 0
                    items.extend(row[col].strip().upper() for row in rows[start_index:] if len(row) > col and row[col].strip())
            else:
                items.extend(line.strip().upper() for line in stream if line.strip())
    return sorted(set(items))


async def _doctor(root: Path) -> int:
    load_config(root)
    async with open_alpaca_bars(root) as client:
        print("Alpaca MCP connection: OK")
        print("ALPACA_TOOLSETS: stock-data")
        print("Application call allowlist: get_stock_bars only")
        print("Known additional server tools: read-only docs + 3 historical crypto GET overrides")
        print("Account/order/position/replace/cancel/close tools: not exposed, imported, or called")
        return 0


async def _fetch(args: argparse.Namespace, root: Path) -> int:
    cfg = load_config(root)
    symbols = _read_symbols(args.symbols, args.symbols_file)
    if not symbols:
        raise ValueError("Supply --symbols or --symbols-file.")
    feed = (args.feed or cfg.default_feed).lower()
    adjustment = (args.adjustment or cfg.default_adjustment).lower()
    async with open_alpaca_bars(root) as client:
        csv_path, report_path, summary = await retrieve_stock_bars(
            client, project_root=root, symbols=symbols, timeframe=args.timeframe,
            start=args.start, end=args.end, feed=feed, adjustment=adjustment, asof=args.asof,
        )
    print(json.dumps(summary, indent=2))
    print(f"Bars CSV: {csv_path}")
    print(f"Quality report: {report_path}")
    if not summary["symbols_with_data"]:
        return 2
    return 1 if summary["symbols_failed"] else 0



def _project_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _screen(args: argparse.Namespace, root: Path) -> int:
    universe_file = _project_path(root, args.universe_file)
    daily_bars = _project_path(root, args.daily_bars)
    intraday_bars = _project_path(root, args.intraday_bars)
    output_dir = _project_path(root, args.output_dir) if args.output_dir else None
    candidates_path, audit_path, report_path, report = run_screen(
        screen_date=args.date,
        universe_file=universe_file,
        daily_bars_file=daily_bars,
        intraday_bars_file=intraday_bars,
        expected_feed=args.feed,
        top_n=args.top_n,
        output_dir=output_dir,
        security_reference_file=_project_path(root, args.security_reference) if args.security_reference else None,
        halt_events_file=_project_path(root, args.halt_events) if args.halt_events else None,
        halt_coverage_manifest=_project_path(root, args.halt_coverage_manifest) if args.halt_coverage_manifest else None,
    )
    print(json.dumps(report["counts"], indent=2))
    print(f"Feed: {report['feed']} ({report['adjustment']})")
    print(f"Candidates CSV: {candidates_path}")
    print(f"Audit CSV: {audit_path}")
    print(f"Screen report: {report_path}")
    print(f"Halt coverage verified: {report['halt_coverage_verified']}")
    for category, path in report.get("candidate_files_by_security_type", {}).items():
        print(f"{category} candidates: {path}")
    print("Execution: read-only deterministic screen; no orders or positions created or modified")
    return 0


def _universe(args: argparse.Namespace, root: Path) -> int:
    if args.all:
        tickers = get_all_sp500_tickers(root)
        label = "all_historical_tickers_2004_2026"
    else:
        if not args.date:
            raise ValueError("Use --date YYYY-MM-DD or --all.")
        tickers = get_sp500_universe(root, args.date)
        label = f"sp500_asof_{args.date}"
    out = root / "data" / "universe"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{label}.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["symbol"])
        writer.writerows([[ticker] for ticker in tickers])
    print(f"Universe tickers: {len(tickers)}")
    print(f"Universe CSV: {path}")
    print("Scope: point-in-time S&P 500 only; not the full U.S. listed universe.")
    return 0



def _reference(args: argparse.Namespace, root: Path) -> int:
    if args.reference_command == "classify-nasdaq-traded":
        output, manifest_path, manifest = classify_nasdaq_traded_file(
            nasdaq_traded_file=_project_path(root, args.nasdaq_traded),
            output_file=_project_path(root, args.output),
            effective_date=args.effective_date,
            max_age_calendar_days=args.max_age_calendar_days,
        )
        print(json.dumps(manifest["counts"], indent=2))
        print(f"Security classification CSV: {output}")
        print(f"Security classification manifest: {manifest_path}")
        print(f"Nasdaq directory age: {manifest['directory_age_calendar_days']} calendar days")
        return 0
    if args.reference_command == "classify-nasdaq":
        output, manifest_path, manifest = classify_nasdaq_directories(
            nasdaq_listed_file=_project_path(root, args.nasdaq_listed),
            other_listed_file=_project_path(root, args.other_listed),
            output_file=_project_path(root, args.output),
            as_of_date=args.as_of_date,
        )
        print(json.dumps(manifest["counts"], indent=2))
        print(f"Security classification CSV: {output}")
        print(f"Security classification manifest: {manifest_path}")
        return 0
    if args.reference_command == "normalize-halts":
        output, manifest_path, manifest = normalize_halt_events(
            input_file=_project_path(root, args.input),
            output_file=_project_path(root, args.output),
            source_name=args.source_name,
            source_url=args.source_url,
            input_format=args.format,
        )
        print(json.dumps({
            "input_rows": manifest["input_rows"],
            "rows": manifest["rows"],
            "duplicate_event_groups": manifest["duplicate_event_groups"],
            "duplicate_rows_removed": manifest["duplicate_rows_removed"],
            "rows_with_parsed_halt_time": manifest["rows_with_parsed_halt_time"],
            "rows_missing_halt_time": manifest["rows_missing_halt_time"],
            "input_format": manifest["input_format"],
        }, indent=2))
        print(f"Normalized halt events CSV: {output}")
        print(f"Halt-event source manifest: {manifest_path}")
        print("Note: the source manifest does not certify complete universe or venue coverage.")
        return 0
    if args.reference_command == "halt-coverage-template":
        output, template = write_halt_coverage_template(
            screen_date=args.date,
            universe_file=_project_path(root, args.universe_file),
            halt_events_file=_project_path(root, args.halt_events),
            output_file=_project_path(root, args.output),
        )
        print(f"Halt coverage template: {output}")
        print("coverage_complete: false")
        print("coverage_attested: false")
        print("source_data_available_by_cutoff: false")
        print("Review exact-universe coverage and source publication availability before attesting.")
        return 0
    raise ValueError("Choose a reference subcommand.")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orb-data", description="Read-only Alpaca stock bars, point-in-time universe, and deterministic ORB screener.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check .env, connect to the stock-data-only MCP server, and verify get_stock_bars.")

    fetch = sub.add_parser("fetch", help="Retrieve daily or five-minute historical stock bars.")
    fetch.add_argument("--symbols", help="Comma-separated tickers, e.g. AAPL,MSFT.")
    fetch.add_argument("--symbols-file", help="CSV with a symbol column or newline-separated symbols.")
    fetch.add_argument("--timeframe", choices=["1Day", "5Min"], required=True)
    fetch.add_argument("--start", type=_parse_datetime, required=True)
    fetch.add_argument("--end", type=_parse_datetime, required=True)
    fetch.add_argument("--feed", choices=["sip", "iex"], default=None)
    fetch.add_argument("--adjustment", choices=["raw", "split", "dividend", "all"], default=None)
    fetch.add_argument("--asof", help="Optional YYYY-MM-DD point-in-time ticker mapping date supported by Alpaca.")

    screen = sub.add_parser(
        "screen",
        help="Run the deterministic ORB screener against existing local daily and 5-minute CSV files (offline).",
    )
    screen.add_argument("--date", required=True, help="Screen date YYYY-MM-DD; the current session's 09:30 bar must be complete.")
    screen.add_argument("--universe-file", required=True, help="Point-in-time ticker CSV with a symbol column, or newline-delimited symbols.")
    screen.add_argument("--daily-bars", required=True, help="Normalized 1Day CSV from orb-data fetch.")
    screen.add_argument("--intraday-bars", required=True, help="Normalized 5Min CSV from orb-data fetch.")
    screen.add_argument("--feed", choices=["sip", "iex"], default=None, help="Optional assertion for the feed used in both inputs.")
    screen.add_argument("--top-n", type=int, default=20, help="Combined long/short ranking size (default: 20).")
    screen.add_argument("--output-dir", help="Optional output directory; default is data/screeners/<date>/<feed>.")
    screen.add_argument("--security-reference", help="Optional date-matched Nasdaq classification CSV produced by 'reference classify-nasdaq-traded'.")
    screen.add_argument("--halt-events", help="Optional normalized halt-event CSV produced by 'reference normalize-halts'.")
    screen.add_argument("--halt-coverage-manifest", help="Optional exact-universe coverage attestation JSON; required to mark no-event candidates halt-clear.")

    reference = sub.add_parser("reference", help="Import date-stamped security classifications and normalize halt-event reference data.")
    reference_sub = reference.add_subparsers(dest="reference_command", required=True)

    classify_traded = reference_sub.add_parser("classify-nasdaq-traded", help="Build ETF/non-ETF/unknown classification from the consolidated Nasdaq traded directory.")
    classify_traded.add_argument("--nasdaq-traded", required=True, help="Downloaded Nasdaq nasdaqtraded.txt file (contains the consolidated ETF Y/N field).")
    classify_traded.add_argument("--effective-date", required=True, help="Trading/effective date for which the file is being applied.")
    classify_traded.add_argument("--max-age-calendar-days", type=int, default=7, help="Maximum allowed directory age; default 7 days.")
    classify_traded.add_argument("--output", required=True, help="Classification CSV output path.")

    classify = reference_sub.add_parser("classify-nasdaq", help="Legacy two-file directory importer; some Nasdaq-listed symbols may remain UNKNOWN if no ETF flag is supplied.")
    classify.add_argument("--nasdaq-listed", required=True, help="Downloaded Nasdaq nasdaqlisted.txt file.")
    classify.add_argument("--other-listed", required=True, help="Downloaded Nasdaq otherlisted.txt file.")
    classify.add_argument("--as-of-date", help="Optional expected date; must match each file's File Creation Time footer.")
    classify.add_argument("--output", required=True, help="Classification CSV output path.")

    normalize = reference_sub.add_parser("normalize-halts", help="Normalize a source halt CSV and retain event provenance.")
    normalize.add_argument("--input", required=True, help="Downloaded halt CSV from Cboe, Nasdaq, or Quant-Lodge.")
    normalize.add_argument("--output", required=True, help="Normalized halt-event CSV output path.")
    normalize.add_argument("--format", choices=["auto", "cboe", "nasdaq", "quant-lodge", "canonical"], default="auto")
    normalize.add_argument("--source-name", required=True, help="Human-readable source name.")
    normalize.add_argument("--source-url", required=True, help="Exact source URL or versioned release URL.")

    coverage = reference_sub.add_parser("halt-coverage-template", help="Create an intentionally incomplete halt-coverage attestation template.")
    coverage.add_argument("--date", required=True, help="Screen date YYYY-MM-DD.")
    coverage.add_argument("--universe-file", required=True)
    coverage.add_argument("--halt-events", required=True, help="Normalized halt-event CSV.")
    coverage.add_argument("--output", required=True, help="Coverage JSON template output path.")

    universe = sub.add_parser("universe", help="Export a point-in-time S&P 500 ticker list from the downloaded YAML dataset.")
    universe.add_argument("--date", help="As-of date YYYY-MM-DD.")
    universe.add_argument("--all", action="store_true", help="All tickers that appeared in the data set.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        if args.command == "doctor":
            return asyncio.run(_doctor(root))
        if args.command == "fetch":
            return asyncio.run(_fetch(args, root))
        if args.command == "universe":
            return _universe(args, root)
        if args.command == "screen":
            return _screen(args, root)
        if args.command == "reference":
            return _reference(args, root)
    except (ConfigurationError, MCPConnectionError, UnsafeMCPConfiguration, UniverseDatasetError, ScreeningError, ReferenceDataError, ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
