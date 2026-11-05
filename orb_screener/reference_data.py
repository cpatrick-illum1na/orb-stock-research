from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
NASDAQ_DIRECTORY_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/"
NASDAQ_HALT_DEFINITIONS_URL = "https://www.nasdaqtrader.com/trader.aspx?id=tradehaltcodes"
CBOE_HALTS_URL = "https://www.cboe.com/markets/us/equities/notices/halts"
QUANT_LODGE_HALTS_URL = "https://github.com/Quant-Lodge/ticker-reference-data"


class ReferenceDataError(ValueError):
    """Raised for malformed or insufficient provenance in reference data."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_symbol(value: Any) -> str:
    # Nasdaq ACT/CQS identifiers can use slash class separators while the
    # project/Alpaca universe uses dot separators (e.g. BRK/B -> BRK.B).
    return str(value or "").strip().upper().replace("/", ".")


def _parse_nasdaq_created_at(path: Path, lines: list[str]) -> datetime:
    footer = next((line for line in reversed(lines) if "File Creation Time" in line), None)
    if footer is None:
        raise ReferenceDataError(f"{path.name} has no Nasdaq 'File Creation Time' footer; cannot establish snapshot date.")
    # Nasdaq's consolidated directory currently emits MMDDYYYYHH:MM, e.g.
    # ``1104202617:02``. Some archived snapshots and existing tests use the
    # compact MMDDYYYYHHMM form. Parse both explicitly; do not accept a partial
    # numeric match, because that can silently lose the minute component.
    colon_match = re.search(r"(?<!\d)(\d{8})(\d{2}):(\d{2})(?!\d)", footer)
    if colon_match:
        stamp = "".join(colon_match.groups())
        fmt = "%m%d%Y%H%M"
    else:
        compact_match = re.search(r"(?<!\d)(\d{12})(?!\d)", footer)
        if not compact_match:
            raise ReferenceDataError(f"Could not parse File Creation Time footer in {path.name}.")
        stamp = compact_match.group(1)
        fmt = "%m%d%Y%H%M"
    try:
        value = datetime.strptime(stamp, fmt)
    except ValueError as exc:
        raise ReferenceDataError(f"Invalid File Creation Time value in {path.name}: {stamp!r}.") from exc
    return value.replace(tzinfo=ET)


def _read_pipe_file(path: Path, kind: str) -> tuple[list[dict[str, str]], datetime]:
    if not path.exists():
        raise ReferenceDataError(f"Reference input does not exist: {path}")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    created_at = _parse_nasdaq_created_at(path, lines)
    data_lines = [line for line in lines if "File Creation Time" not in line and line.strip()]
    if not data_lines:
        raise ReferenceDataError(f"No symbol rows found in {path.name}.")

    if kind == "nasdaqlisted":
        fallback = ["Symbol", "Security Name", "Market Category", "Test Issue", "Financial Status", "Round Lot Size", "ETF", "NextShares"]
    elif kind == "nasdaqtraded":
        fallback = ["Nasdaq Traded", "Symbol", "Security Name", "Listing Exchange", "Market Category", "ETF", "Round Lot Size", "Test Issue", "Financial Status", "CQS Symbol", "NASDAQ Symbol", "NextShares"]
    elif kind == "otherlisted":
        fallback = ["ACT Symbol", "Security Name", "Exchange", "CQS Symbol", "ETF", "Round Lot Size", "Test Issue", "Nasdaq Symbol"]
    else:
        raise ReferenceDataError(f"Unsupported Nasdaq directory type: {kind}")

    first = next(csv.reader([data_lines[0]], delimiter="|"))
    known = {"symbol", "act symbol", "security name"}
    if any(cell.strip().lower() in known for cell in first):
        headers = [cell.strip() for cell in first]
        reader = csv.DictReader(data_lines, delimiter="|")
        rows = [{str(k).strip(): str(v or "").strip() for k, v in row.items() if k is not None} for row in reader]
    else:
        rows = []
        for line in data_lines:
            cells = next(csv.reader([line], delimiter="|"))
            if len(cells) < 2:
                continue
            # A new optional field may be appended by Nasdaq; retain known
            # positional fields and ignore fields beyond this documented shape.
            padded = cells + [""] * max(0, len(fallback) - len(cells))
            rows.append({fallback[i]: str(padded[i]).strip() for i in range(len(fallback))})
    rows = [row for row in rows if not str(row.get("Symbol") or row.get("ACT Symbol") or "").startswith("File Creation Time")]
    return rows, created_at


def classify_nasdaq_directories(
    *,
    nasdaq_listed_file: Path,
    other_listed_file: Path,
    output_file: Path,
    as_of_date: str | date | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Build a versioned ETF/non-ETF classification from Nasdaq symbol files.

    ETF is only asserted when a source row explicitly says Y or N. Missing ETF
    indicators remain UNKNOWN; this does not infer COMMON_STOCK from a name.
    """
    nasdaq_rows, nasdaq_created = _read_pipe_file(nasdaq_listed_file, "nasdaqlisted")
    other_rows, other_created = _read_pipe_file(other_listed_file, "otherlisted")
    if nasdaq_created.date() != other_created.date():
        raise ReferenceDataError(
            "Nasdaq listed and other-listed files have different File Creation Time dates; use a matched directory snapshot."
        )
    observed_date = nasdaq_created.date()
    if as_of_date is not None:
        requested = as_of_date if isinstance(as_of_date, date) else date.fromisoformat(str(as_of_date))
        if requested != observed_date:
            raise ReferenceDataError(
                f"Requested as-of date {requested} does not match Nasdaq file creation date {observed_date}; refusing to backdate a current directory."
            )

    records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add(symbol: Any, name: Any, exchange: str, etf_flag: Any, test_issue: Any, source_file: Path) -> None:
        key = _norm_symbol(symbol)
        if not key:
            return
        flag = str(etf_flag or "").strip().upper()
        security_type = "ETF" if flag == "Y" else "NON_ETF" if flag == "N" else "UNKNOWN"
        test = str(test_issue or "").strip().upper()
        records[key].append({
            "symbol": key,
            "security_type": security_type,
            "listing_venue": exchange or "UNKNOWN",
            "security_name": str(name or "").strip(),
            "etf_flag": flag if flag in {"Y", "N"} else "",
            "test_issue": test if test in {"Y", "N"} else "",
            "source_file": source_file.name,
        })

    for row in nasdaq_rows:
        symbol = row.get("Symbol") or row.get("symbol")
        add(symbol, row.get("Security Name"), "NASDAQ", row.get("ETF"), row.get("Test Issue"), nasdaq_listed_file)

    venue_map = {"A": "NYSE_AMERICAN", "N": "NYSE", "P": "NYSE_ARCA", "Z": "CBOE_BZX", "V": "IEXG"}
    for row in other_rows:
        venue = venue_map.get(str(row.get("Exchange", "")).strip().upper(), str(row.get("Exchange", "")).strip().upper() or "UNKNOWN")
        # Include every published alias to make the mapping auditable. Slash and
        # dot class separators are normalized; other symbols are not guessed.
        for field in ("ACT Symbol", "CQS Symbol", "Nasdaq Symbol"):
            value = row.get(field)
            if value:
                add(value, row.get("Security Name"), venue, row.get("ETF"), row.get("Test Issue"), other_listed_file)

    output_rows: list[dict[str, Any]] = []
    for symbol in sorted(records):
        entries = records[symbol]
        types = {entry["security_type"] for entry in entries if entry["security_type"] != "UNKNOWN"}
        venues = sorted({entry["listing_venue"] for entry in entries})
        names = sorted({entry["security_name"] for entry in entries if entry["security_name"]})
        tests = {entry["test_issue"] for entry in entries if entry["test_issue"]}
        flags = {entry["etf_flag"] for entry in entries if entry["etf_flag"]}
        if len(types) > 1 or len(flags) > 1:
            security_type = "UNKNOWN"
            classification_note = "CONFLICTING_SOURCE_FLAGS"
        elif types:
            security_type = next(iter(types))
            classification_note = "EXPLICIT_NASDAQ_ETF_FLAG"
        else:
            security_type = "UNKNOWN"
            classification_note = "ETF_FLAG_NOT_PUBLISHED_OR_NOT_AVAILABLE"
        test_issue = "Y" if "Y" in tests else "N" if tests == {"N"} else ""
        output_rows.append({
            "symbol": symbol,
            "security_type": security_type,
            "listing_venue": ";".join(venues),
            "security_name": " | ".join(names),
            "etf_flag": next(iter(flags)) if len(flags) == 1 else "",
            "test_issue": test_issue,
            "classification_note": classification_note,
            "as_of_date": observed_date.isoformat(),
            "source_created_at_et": max(nasdaq_created, other_created).isoformat(),
            "source": "Nasdaq Trader Symbol Directory",
        })

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output_rows[0]) if output_rows else [
            "symbol", "security_type", "listing_venue", "security_name", "etf_flag", "test_issue",
            "classification_note", "as_of_date", "source_created_at_et", "source",
        ])
        writer.writeheader()
        writer.writerows(output_rows)

    manifest = {
        "schema_version": 1,
        "kind": "security_classification",
        "as_of_date": observed_date.isoformat(),
        "source_created_at_et": max(nasdaq_created, other_created).isoformat(),
        "nasdaq_listed_file": str(nasdaq_listed_file),
        "nasdaq_listed_sha256": sha256_file(nasdaq_listed_file),
        "other_listed_file": str(other_listed_file),
        "other_listed_sha256": sha256_file(other_listed_file),
        "output_file": str(output_file),
        "output_sha256": sha256_file(output_file),
        "source_urls": {
            "symbol_directory_definitions": "https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs",
            "nasdaq_listed": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
            "other_listed": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
        },
        "counts": {
            "symbols": len(output_rows),
            "etf": sum(row["security_type"] == "ETF" for row in output_rows),
            "non_etf": sum(row["security_type"] == "NON_ETF" for row in output_rows),
            "unknown": sum(row["security_type"] == "UNKNOWN" for row in output_rows),
            "test_issue": sum(row["test_issue"] == "Y" for row in output_rows),
        },
        "classification_semantics": "ETF/NON_ETF only when the directory explicitly supplies ETF=Y/N; otherwise UNKNOWN. NON_ETF is not asserted to mean COMMON_STOCK.",
    }
    manifest_path = output_file.with_name(f"{output_file.stem}_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_file, manifest_path, manifest



def classify_nasdaq_traded_file(
    *,
    nasdaq_traded_file: Path,
    output_file: Path,
    effective_date: str | date,
    max_age_calendar_days: int = 7,
) -> tuple[Path, Path, dict[str, Any]]:
    """Build a dated security-type reference from Nasdaq's consolidated directory.

    Nasdaq's `nasdaqtraded.txt` includes a direct ETF Y/N field for Nasdaq-traded
    US securities. The effective date is explicit because the directory is
    typically refreshed the prior evening for the next trading day.
    """
    if max_age_calendar_days < 0:
        raise ReferenceDataError("max_age_calendar_days cannot be negative.")
    target = effective_date if isinstance(effective_date, date) else date.fromisoformat(str(effective_date))
    rows, created_at = _read_pipe_file(nasdaq_traded_file, "nasdaqtraded")
    cutoff = datetime.combine(target, time(9, 35), tzinfo=ET)
    if created_at.astimezone(ET) > cutoff:
        raise ReferenceDataError(
            f"Nasdaq traded directory was created after {target} 09:35 ET; refusing look-ahead classification."
        )
    age = (target - created_at.date()).days
    if age < 0 or age > max_age_calendar_days:
        raise ReferenceDataError(
            f"Nasdaq traded directory age is {age} calendar days; allowed maximum is {max_age_calendar_days}."
        )

    venue_map = {"Q": "NASDAQ", "N": "NYSE", "A": "NYSE_AMERICAN", "P": "NYSE_ARCA", "Z": "CBOE_BZX", "V": "IEXG", "F": "TXSE"}
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        # Keep every directory row regardless of the `Nasdaq Traded` flag.
        # This file is a consolidated symbol directory, not a Nasdaq-only
        # universe filter. Preserve that flag as metadata; listing venue is
        # carried separately in `Listing Exchange`.
        base_symbol = _norm_symbol(row.get("Symbol"))
        if not base_symbol:
            continue
        flag = str(row.get("ETF", "")).strip().upper()
        security_type = "ETF" if flag == "Y" else "NON_ETF" if flag == "N" else "UNKNOWN"
        venue_code = str(row.get("Listing Exchange", "")).strip().upper()
        venue = venue_map.get(venue_code, venue_code or "UNKNOWN")
        test_flag = str(row.get("Test Issue", "")).strip().upper()
        entry = {
            "security_type": security_type,
            "nasdaq_traded": str(row.get("Nasdaq Traded", "")).strip().upper() if str(row.get("Nasdaq Traded", "")).strip().upper() in {"Y", "N"} else "",
            "listing_venue": venue,
            "security_name": str(row.get("Security Name", "")).strip(),
            "etf_flag": flag if flag in {"Y", "N"} else "",
            "test_issue": test_flag if test_flag in {"Y", "N"} else "",
        }
        for alias in (base_symbol, _norm_symbol(row.get("CQS Symbol")), _norm_symbol(row.get("NASDAQ Symbol"))):
            if alias:
                records[alias].append(entry)

    output_rows: list[dict[str, Any]] = []
    for symbol in sorted(records):
        entries = records[symbol]
        types = {entry["security_type"] for entry in entries if entry["security_type"] != "UNKNOWN"}
        flags = {entry["etf_flag"] for entry in entries if entry["etf_flag"]}
        if len(types) > 1 or len(flags) > 1:
            security_type = "UNKNOWN"
            note = "CONFLICTING_SOURCE_FLAGS"
        elif types:
            security_type = next(iter(types))
            note = "EXPLICIT_NASDAQ_TRADED_ETF_FLAG"
        else:
            security_type = "UNKNOWN"
            note = "ETF_FLAG_NOT_AVAILABLE"
        venues = sorted({entry["listing_venue"] for entry in entries})
        names = sorted({entry["security_name"] for entry in entries if entry["security_name"]})
        test_flags = {entry["test_issue"] for entry in entries if entry["test_issue"]}
        output_rows.append({
            "symbol": symbol,
            "security_type": security_type,
            "nasdaq_traded": "Y" if any(entry.get("nasdaq_traded") == "Y" for entry in entries) else "N" if entries and all(entry.get("nasdaq_traded") == "N" for entry in entries) else "",
            "listing_venue": ";".join(venues),
            "security_name": " | ".join(names),
            "etf_flag": next(iter(flags)) if len(flags) == 1 else "",
            "test_issue": "Y" if "Y" in test_flags else "N" if test_flags == {"N"} else "",
            "classification_note": note,
            "as_of_date": target.isoformat(),
            "source_created_at_et": created_at.isoformat(),
            "source": "Nasdaq Trader Consolidated Symbol Directory",
        })

    output_file.parent.mkdir(parents=True, exist_ok=True)
    fields = list(output_rows[0]) if output_rows else [
        "symbol", "security_type", "nasdaq_traded", "listing_venue", "security_name", "etf_flag", "test_issue",
        "classification_note", "as_of_date", "source_created_at_et", "source",
    ]
    with output_file.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    manifest = {
        "schema_version": 1,
        "kind": "security_classification",
        "as_of_date": target.isoformat(),
        "source_created_at_et": created_at.isoformat(),
        "directory_age_calendar_days": age,
        "nasdaq_traded_file": str(nasdaq_traded_file),
        "nasdaq_traded_sha256": sha256_file(nasdaq_traded_file),
        "output_file": str(output_file),
        "output_sha256": sha256_file(output_file),
        "source_urls": {
            "nasdaq_traded": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt",
            "symbol_directory_alerts": "https://www.nasdaqtrader.com/",
            "symbol_directory_definitions": "https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs",
        },
        "counts": {
            "symbols": len(output_rows),
            "etf": sum(row["security_type"] == "ETF" for row in output_rows),
            "non_etf": sum(row["security_type"] == "NON_ETF" for row in output_rows),
            "unknown": sum(row["security_type"] == "UNKNOWN" for row in output_rows),
            "test_issue": sum(row["test_issue"] == "Y" for row in output_rows),
        },
        "classification_semantics": "ETF/NON_ETF only when Nasdaq Trader explicitly supplies ETF=Y/N. NON_ETF is not asserted to mean COMMON_STOCK.",
    }
    manifest_path = output_file.with_name(f"{output_file.stem}_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_file, manifest_path, manifest

def _header_map(fieldnames: list[str]) -> dict[str, str]:
    def key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())
    return {key(name): name for name in fieldnames if name}


def _pick(row: dict[str, str], names: tuple[str, ...]) -> str:
    mapping = _header_map(list(row))
    for name in names:
        actual = mapping.get(re.sub(r"[^a-z0-9]+", "", name.lower()))
        if actual is not None:
            value = str(row.get(actual) or "").strip()
            if value:
                return value
    return ""


def _parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_time(value: str) -> time | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _parse_et_datetime(combined: str, date_value: str = "", time_value: str = "") -> datetime | None:
    combined = combined.strip()
    if combined:
        # ISO strings may already contain timezone information.
        try:
            parsed = datetime.fromisoformat(combined.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ET)
            return parsed.astimezone(ET)
        except ValueError:
            for fmt in (
                "%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                "%m/%d/%Y %I:%M:%S.%f %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %I:%M:%S %p",
            ):
                try:
                    return datetime.strptime(combined, fmt).replace(tzinfo=ET)
                except ValueError:
                    pass
    day = _parse_date(date_value)
    clock = _parse_time(time_value)
    if day is None or clock is None:
        return None
    return datetime.combine(day, clock, tzinfo=ET)


def normalize_halt_events(
    *,
    input_file: Path,
    output_file: Path,
    source_name: str,
    source_url: str,
    input_format: str = "auto",
) -> tuple[Path, Path, dict[str, Any]]:
    """Normalize Cboe, Nasdaq Trader, or Quant-Lodge halt CSVs with provenance."""
    if not input_file.exists():
        raise ReferenceDataError(f"Halt input file does not exist: {input_file}")
    if not source_name.strip() or not source_url.strip():
        raise ReferenceDataError("--source-name and --source-url are required for halt-event provenance.")
    try:
        with input_file.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise ReferenceDataError("Halt input CSV has no header row.")
            raw_rows = list(reader)
            headers = _header_map(reader.fieldnames)
    except ReferenceDataError:
        raise
    except Exception as exc:
        raise ReferenceDataError(f"Could not read halt CSV: {type(exc).__name__}") from exc

    if input_format not in {"auto", "cboe", "nasdaq", "quant-lodge", "canonical"}:
        raise ReferenceDataError("input_format must be auto, cboe, nasdaq, quant-lodge, or canonical.")
    if input_format == "auto":
        if "haltsymbol" in headers or "issuesymbol" in headers:
            detected = "nasdaq"
        elif "symbolraw" in headers or "haltdate" in headers and "halttime" in headers:
            detected = "quant-lodge"
        elif "halt" in headers and "symbol" in headers:
            detected = "cboe"
        elif "haltstartet" in headers:
            detected = "canonical"
        else:
            detected = "unknown"
        if detected == "unknown":
            raise ReferenceDataError(f"Could not infer halt CSV format from columns: {reader.fieldnames}")
        input_format = detected

    input_hash = sha256_file(input_file)
    # Exact repeated source records are common in some historical halt exports.
    # Collapse them on a semantic event key, not source row number / generated ID.
    # Keep every original source ID and row number on the canonical row so the
    # deduplication remains fully auditable.
    out_rows: list[dict[str, Any]] = []
    event_index: dict[tuple[str, ...], dict[str, Any]] = {}
    parsed_source_rows = 0
    for idx, row in enumerate(raw_rows, start=2):
        raw = {str(k): str(v or "").strip() for k, v in row.items() if k is not None}
        symbol = _norm_symbol(_pick(raw, ("symbol", "symbol raw", "issue symbol", "ticker", "act symbol", "cqs symbol")))
        if not symbol:
            # Empty footer/blank lines are ignored; populated but symbol-less rows fail.
            if not any(raw.values()):
                continue
            raise ReferenceDataError(f"Missing symbol in halt input row {idx}.")

        if input_format == "canonical":
            halt_start = _parse_et_datetime(_pick(raw, ("halt start et", "halt_start_et")))
            resume_trade = _parse_et_datetime(_pick(raw, ("resume trade et", "resume_trade_et")))
            halt_day = _parse_date(_pick(raw, ("halt date", "halt_date"))) or (halt_start.date() if halt_start else None)
            reason_code = _pick(raw, ("reason code", "reason_code"))
            reason = _pick(raw, ("reason", "reason raw"))
            market = _pick(raw, ("listing market", "market", "mkt"))
            event_id = _pick(raw, ("source event id", "source_event_id"))
        else:
            combined_halt = _pick(raw, ("halt", "halt datetime", "halt timestamp")) if input_format == "cboe" else ""
            halt_date_text = _pick(raw, ("halt date", "halt_date", "date of halt"))
            halt_time_text = _pick(raw, ("halt time", "halt_time", "time of halt"))
            if combined_halt and not halt_date_text:
                halt_start = _parse_et_datetime(combined_halt)
            else:
                halt_start = _parse_et_datetime("", halt_date_text, halt_time_text)
            halt_day = _parse_date(halt_date_text) or (halt_start.date() if halt_start else None)

            resume_date_text = _pick(raw, ("resume date", "resume_date", "date"))
            resume_time_text = _pick(raw, ("resume time", "resume_trade_time", "resumption trade time", "resumption trade time et"))
            if not resume_time_text:
                # A quote resumption is not equivalent to tradable resumption.
                resume_time_text = ""
            resume_trade = _parse_et_datetime("", resume_date_text, resume_time_text)
            reason_code = _pick(raw, ("reason code", "reason_code", "code"))
            reason = _pick(raw, ("reason", "reason raw", "halt reason"))
            market = _pick(raw, ("market", "mkt", "listing market", "exchange"))
            event_id = _pick(raw, ("source event id", "event id", "id"))

        if halt_day is None:
            raise ReferenceDataError(f"Could not parse halt date in input row {idx} for {symbol}.")
        halt_time_et = halt_start.isoformat(timespec="milliseconds") if halt_start else ""
        resume_trade_et = resume_trade.isoformat(timespec="milliseconds") if resume_trade else ""
        reason_code = reason_code.strip()
        reason = reason.strip()
        market = market.strip()
        parsed_source_rows += 1
        if not event_id:
            event_id = f"{input_hash[:16]}:{idx}:{symbol}:{halt_time_et or halt_day.isoformat()}"
        # Case/whitespace normalization is limited to descriptive fields. Event
        # timestamps, symbol, reason code, and market remain explicit key parts.
        event_key = (
            symbol,
            halt_day.isoformat(),
            halt_time_et,
            resume_trade_et,
            reason_code.casefold(),
            " ".join(reason.casefold().split()),
            market.upper(),
        )
        if event_key in event_index:
            existing = event_index[event_key]
            source_ids = json.loads(existing["source_event_ids"])
            if event_id and event_id not in source_ids:
                source_ids.append(event_id)
            existing["source_event_ids"] = json.dumps(source_ids, separators=(",", ":"))
            source_rows = json.loads(existing["source_row_numbers"])
            source_rows.append(idx)
            existing["source_row_numbers"] = json.dumps(source_rows, separators=(",", ":"))
            existing["duplicate_count"] = len(source_rows)
            continue

        canonical_row = {
            "symbol": symbol,
            "halt_date": halt_day.isoformat(),
            "halt_start_et": halt_time_et,
            "resume_trade_et": resume_trade_et,
            "reason_code": reason_code,
            "reason": reason,
            "listing_market": market,
            "source_name": source_name.strip(),
            "source_url": source_url.strip(),
            # Keep the first legacy ID stable for existing downstream consumers;
            # source_event_ids and source_row_numbers retain all collapsed copies.
            "source_event_id": event_id,
            "source_event_ids": json.dumps([event_id], separators=(",", ":")),
            "source_row_number": idx,
            "source_row_numbers": json.dumps([idx], separators=(",", ":")),
            "duplicate_count": 1,
            "source_input_sha256": input_hash,
            "halt_time_parse_status": "PARSED" if halt_start else "MISSING_OR_UNPARSEABLE",
            "resume_time_parse_status": "PARSED" if resume_trade else "NOT_PROVIDED_OR_UNPARSEABLE",
        }
        event_index[event_key] = canonical_row
        out_rows.append(canonical_row)

    duplicate_event_groups = sum(int(row["duplicate_count"]) > 1 for row in out_rows)
    duplicate_rows_removed = parsed_source_rows - len(out_rows)
    max_duplicate_multiplicity = max((int(row["duplicate_count"]) for row in out_rows), default=1)
    if not out_rows:
        raise ReferenceDataError("No valid halt-event rows found in input CSV.")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fields = list(out_rows[0].keys())
    with output_file.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)
    manifest = {
        "schema_version": 1,
        "kind": "normalized_halt_events",
        "source_name": source_name.strip(),
        "source_url": source_url.strip(),
        "input_format": input_format,
        "input_file": str(input_file),
        "input_sha256": input_hash,
        "output_file": str(output_file),
        "output_sha256": sha256_file(output_file),
        # `rows` is the deduplicated count of canonical events. Raw input row
        # counts and removed duplicate counts are exposed separately.
        "input_rows": len(raw_rows),
        "parsed_source_rows": parsed_source_rows,
        "rows": len(out_rows),
        "duplicate_event_groups": duplicate_event_groups,
        "duplicate_rows_removed": duplicate_rows_removed,
        "max_duplicate_multiplicity": max_duplicate_multiplicity,
        "rows_with_parsed_halt_time": sum(bool(row["halt_start_et"]) for row in out_rows),
        "rows_missing_halt_time": sum(not bool(row["halt_start_et"]) for row in out_rows),
        "first_halt_date": min(row["halt_date"] for row in out_rows),
        "last_halt_date": max(row["halt_date"] for row in out_rows),
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "warning": "This manifest proves file provenance and integrity only; it does not assert complete universe or venue coverage.",
    }
    manifest_path = output_file.with_name(f"{output_file.stem}_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_file, manifest_path, manifest


def _read_universe_symbols(universe_file: Path) -> list[str]:
    text = universe_file.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ReferenceDataError("Universe file is empty.")
    first = text.splitlines()[0].strip().lower()
    if "," in text[:4096] or first in {"symbol", "ticker", "ticker_symbol"}:
        rows = list(csv.DictReader(text.splitlines()))
        if not rows or "symbol" not in {str(k).lower() for k in rows[0]}:
            raise ReferenceDataError("Universe CSV must contain a symbol column.")
        key = next(k for k in rows[0] if k.lower() == "symbol")
        symbols = [_norm_symbol(row.get(key)) for row in rows if row.get(key)]
    else:
        symbols = [_norm_symbol(line) for line in text.splitlines() if line.strip()]
    if not symbols or any(not item for item in symbols):
        raise ReferenceDataError("Universe file contains no usable symbols.")
    if len(symbols) != len(set(symbols)):
        raise ReferenceDataError("Universe file contains duplicate symbols.")
    return sorted(symbols)


def write_halt_coverage_template(
    *,
    screen_date: str | date,
    universe_file: Path,
    halt_events_file: Path,
    output_file: Path,
) -> tuple[Path, dict[str, Any]]:
    """Write a deliberately UNATTESTED coverage template for human review."""
    target = screen_date if isinstance(screen_date, date) else date.fromisoformat(str(screen_date))
    manifest_path = halt_events_file.with_name(f"{halt_events_file.stem}_manifest.json")
    if not manifest_path.exists():
        raise ReferenceDataError(f"Normalized halt manifest not found: {manifest_path}")
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    symbols = _read_universe_symbols(universe_file)
    symbol_digest = hashlib.sha256(("\n".join(symbols) + "\n").encode("utf-8")).hexdigest()
    template = {
        "schema_version": 1,
        "kind": "halt_coverage_attestation",
        "screen_date": target.isoformat(),
        "opening_window_start_et": "09:30:00",
        "opening_window_end_et": "09:35:00",
        "coverage_scope": "EXACT_UNIVERSE",
        "coverage_complete": False,
        "coverage_attested": False,
        "universe_file_sha256": sha256_file(universe_file),
        "universe_symbols_sha256": symbol_digest,
        "universe_symbol_count": len(symbols),
        "halt_events_file_sha256": sha256_file(halt_events_file),
        "source_name": source_manifest.get("source_name", ""),
        "source_url": source_manifest.get("source_url", ""),
        "source_data_available_by_cutoff": False,
        "source_available_at_et": "",
        "source_availability_proof": "",
        "attested_by": "",
        "attestation_notes": "",
        "warning": "Keep all attestation fields false/blank until source methodology, venue scope, exact-universe coverage, event-time semantics, and availability by 09:35 ET are independently established. Retrospective end-of-day archives cannot establish real-time availability at the opening-range cutoff.",
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(template, indent=2), encoding="utf-8")
    return output_file, template
