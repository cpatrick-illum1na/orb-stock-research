from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ET = ZoneInfo("America/New_York")
REQUIRED_BAR_COLUMNS = {
    "symbol", "timestamp", "open", "high", "low", "close", "volume",
    "timeframe", "feed", "adjustment",
}
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]*$")
MIN_ATR_WARMUP_SESSIONS = 100  # completed sessions before the screen date


class ScreeningError(RuntimeError):
    """Raised when input data cannot support a safe, reproducible screen."""


@dataclass(frozen=True)
class ScreenInputs:
    screen_date: date
    universe_symbols: list[str]
    daily_path: Path
    intraday_path: Path
    feed: str
    adjustment: str
    daily_asof: str | None
    intraday_asof: str | None
    top_n: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_universe_symbols(path: Path) -> list[str]:
    """Load a symbol-column CSV or a newline-delimited ticker file."""
    if not path.exists():
        raise ScreeningError(f"Universe file does not exist: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ScreeningError(f"Universe file is empty: {path}")

    symbols: list[str] = []
    sample = text[:4096]
    first_line = text.splitlines()[0].strip().lower()
    if "," in sample or first_line in {"symbol", "ticker", "ticker_symbol"}:
        rows = list(csv.reader(text.splitlines()))
        if not rows:
            raise ScreeningError(f"Universe file is empty: {path}")
        header = [cell.strip().lower() for cell in rows[0]]
        if "symbol" not in header:
            raise ScreeningError("Universe CSV must contain a 'symbol' column.")
        index = header.index("symbol")
        symbols = [row[index].strip().upper() for row in rows[1:] if len(row) > index and row[index].strip()]
    else:
        symbols = [line.strip().upper() for line in text.splitlines() if line.strip()]

    if not symbols:
        raise ScreeningError("Universe file contains no symbols.")
    invalid = sorted({symbol for symbol in symbols if not TICKER_PATTERN.fullmatch(symbol)})
    if invalid:
        raise ScreeningError(f"Universe contains malformed ticker symbols: {', '.join(invalid[:10])}")
    return sorted(set(symbols))


def _read_bars(path: Path, expected_timeframe: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise ScreeningError(f"Bar CSV does not exist: {path}")
    try:
        frame = pd.read_csv(path, dtype={"symbol": "string", "feed": "string", "adjustment": "string"})
    except Exception as exc:
        raise ScreeningError(f"Could not read bar CSV {path}: {type(exc).__name__}") from exc
    if frame.empty:
        raise ScreeningError(f"Bar CSV contains no rows: {path}")
    missing = sorted(REQUIRED_BAR_COLUMNS - set(frame.columns))
    if missing:
        raise ScreeningError(f"{path.name} is missing required columns: {', '.join(missing)}")

    frame = frame.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.upper()
    if frame["symbol"].isna().any() or (frame["symbol"] == "").any():
        raise ScreeningError(f"{path.name} contains blank symbols.")
    malformed = sorted({str(x) for x in frame["symbol"].unique() if not TICKER_PATTERN.fullmatch(str(x))})
    if malformed:
        raise ScreeningError(f"{path.name} contains malformed symbols: {', '.join(malformed[:10])}")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    if frame["timestamp"].isna().any():
        raise ScreeningError(f"{path.name} contains invalid or timezone-naive timestamps.")

    numeric = ("open", "high", "low", "close", "volume")
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    matrix = frame[list(numeric)].to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        raise ScreeningError(f"{path.name} contains non-finite OHLCV values.")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame["volume"] < 0).any():
        raise ScreeningError(f"{path.name} contains non-positive prices or negative volume.")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ScreeningError(f"{path.name} contains an invalid high price relationship.")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ScreeningError(f"{path.name} contains an invalid low price relationship.")

    timeframe_values = sorted(set(frame["timeframe"].astype(str)))
    if timeframe_values != [expected_timeframe]:
        raise ScreeningError(
            f"{path.name} must contain only {expected_timeframe} bars; found {timeframe_values}."
        )
    feeds = sorted(set(frame["feed"].astype(str).str.lower()))
    adjustments = sorted(set(frame["adjustment"].astype(str).str.lower()))
    if len(feeds) != 1 or feeds[0] not in {"sip", "iex"}:
        raise ScreeningError(f"{path.name} must contain exactly one recognized feed (sip or iex).")
    if len(adjustments) != 1:
        raise ScreeningError(f"{path.name} contains mixed price adjustments.")

    local = frame["timestamp"].dt.tz_convert(ET)
    frame["session_date"] = local.dt.date
    frame["local_hour"] = local.dt.hour
    frame["local_minute"] = local.dt.minute
    frame["local_second"] = local.dt.second

    if expected_timeframe == "1Day":
        duplicate_key = ["symbol", "session_date"]
    else:
        duplicate_key = ["symbol", "timestamp"]
    duplicate_mask = frame.duplicated(duplicate_key, keep=False)
    if duplicate_mask.any():
        examples = frame.loc[duplicate_mask, duplicate_key].head(5).astype(str).to_dict("records")
        raise ScreeningError(f"{path.name} contains duplicate {expected_timeframe} bar keys: {examples}")

    sidecar = path.with_name(f"{path.stem}_quality.json")
    metadata: dict[str, Any] = {
        "feed": feeds[0],
        "adjustment": adjustments[0],
        "asof": None,
        "sidecar": str(sidecar) if sidecar.exists() else None,
    }
    if sidecar.exists():
        try:
            report = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ScreeningError(f"Could not parse quality sidecar {sidecar.name}: {type(exc).__name__}") from exc
        if str(report.get("feed", "")).lower() != feeds[0]:
            raise ScreeningError(f"Feed in {sidecar.name} does not match its CSV rows.")
        if str(report.get("adjustment", "")).lower() != adjustments[0]:
            raise ScreeningError(f"Adjustment in {sidecar.name} does not match its CSV rows.")
        if str(report.get("timeframe", "")) != expected_timeframe:
            raise ScreeningError(f"Timeframe in {sidecar.name} does not match its CSV rows.")
        metadata["asof"] = report.get("asof_symbol_mapping_date")
    return frame, metadata


def _wilder_atr_from_contiguous_suffix(daily_records: list[dict[str, Any]]) -> float:
    """Compute Wilder ATR(14), seeded by the first 14 TRs in a contiguous suffix."""
    if len(daily_records) < 15:
        raise ValueError("At least 15 consecutive completed daily bars are required for ATR(14).")
    true_ranges: list[float] = []
    for index in range(1, len(daily_records)):
        current = daily_records[index]
        previous_close = float(daily_records[index - 1]["close"])
        high = float(current["high"])
        low = float(current["low"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if len(true_ranges) < 14:
        raise ValueError("Insufficient true-range observations for ATR(14).")
    atr = float(np.mean(true_ranges[:14]))
    for true_range in true_ranges[14:]:
        atr = ((13.0 * atr) + true_range) / 14.0
    return atr


def _format_session_dates(values: list[date]) -> list[str]:
    return [value.isoformat() for value in values]



def _load_security_reference(path: Path | None, screen_date: date) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    if path is None:
        return {}, None
    if not path.exists():
        raise ScreeningError(f"Security-reference CSV does not exist: {path}")
    manifest_path = path.with_name(f"{path.stem}_manifest.json")
    if not manifest_path.exists():
        raise ScreeningError(f"Security-reference manifest is required: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frame = pd.read_csv(path, dtype={"symbol": "string", "security_type": "string"})
    except Exception as exc:
        raise ScreeningError(f"Could not read security reference: {type(exc).__name__}") from exc
    if manifest.get("kind") != "security_classification":
        raise ScreeningError("Security-reference manifest has an unexpected kind.")
    if manifest.get("output_sha256") != _sha256(path):
        raise ScreeningError("Security-reference CSV hash does not match its manifest.")
    if str(manifest.get("as_of_date")) != screen_date.isoformat():
        raise ScreeningError(
            f"Security-reference snapshot date must equal screen date {screen_date}; "
            f"found {manifest.get('as_of_date')}. Use an archived point-in-time snapshot, not a newer file."
        )
    created_text = str(manifest.get("source_created_at_et", ""))
    try:
        created_at = datetime.fromisoformat(created_text)
    except ValueError as exc:
        raise ScreeningError("Security-reference manifest has an invalid source_created_at_et timestamp.") from exc
    if created_at.tzinfo is None:
        raise ScreeningError("Security-reference creation timestamp must include its timezone.")
    cutoff = datetime.combine(screen_date, time(9, 35), tzinfo=ET)
    if created_at.astimezone(ET) > cutoff:
        raise ScreeningError(
            "Security-reference directory was created after the 09:35 ET screening cutoff; "
            "refusing possible look-ahead classification."
        )
    required = {"symbol", "security_type", "as_of_date", "test_issue"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ScreeningError(f"Security-reference file is missing columns: {', '.join(missing)}")
    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.upper()
    if frame["symbol"].duplicated().any():
        raise ScreeningError("Security-reference file contains duplicate symbols.")
    allowed_types = {"ETF", "NON_ETF", "UNKNOWN"}
    invalid = sorted(set(frame["security_type"].astype(str)) - allowed_types)
    if invalid:
        raise ScreeningError(f"Unknown security_type values in security-reference file: {invalid}")
    if set(frame["as_of_date"].astype(str)) != {screen_date.isoformat()}:
        raise ScreeningError("Security-reference rows do not all match the requested screen date.")
    mapping = {str(row["symbol"]): row.to_dict() for _, row in frame.iterrows()}
    return mapping, manifest


def _load_halt_events(path: Path | None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any] | None]:
    if path is None:
        return {}, None
    if not path.exists():
        raise ScreeningError(f"Normalized halt-event CSV does not exist: {path}")
    manifest_path = path.with_name(f"{path.stem}_manifest.json")
    if not manifest_path.exists():
        raise ScreeningError(f"Normalized halt-event manifest is required: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frame = pd.read_csv(path, dtype={"symbol": "string"})
    except Exception as exc:
        raise ScreeningError(f"Could not read normalized halt events: {type(exc).__name__}") from exc
    if manifest.get("kind") != "normalized_halt_events":
        raise ScreeningError("Halt-event manifest has an unexpected kind.")
    if manifest.get("output_sha256") != _sha256(path):
        raise ScreeningError("Halt-event CSV hash does not match its source manifest.")
    required = {"symbol", "halt_date", "halt_start_et", "resume_trade_et", "source_name", "source_url", "source_event_id"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ScreeningError(f"Normalized halt-event CSV is missing columns: {', '.join(missing)}")
    events: dict[str, list[dict[str, Any]]] = {}
    for row in frame.to_dict("records"):
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            raise ScreeningError("Normalized halt-event file contains a blank symbol.")
        if not row.get("source_name") or not row.get("source_url") or not row.get("source_event_id"):
            raise ScreeningError(f"Halt event for {symbol} is missing source provenance.")
        events.setdefault(symbol, []).append(row)
    return events, manifest


def _validate_halt_coverage(
    manifest_path: Path | None,
    *,
    screen_date: date,
    universe_file: Path,
    universe_symbols: list[str],
    halt_events_file: Path | None,
    halt_events_manifest: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Validate a coverage manifest and separately report whether it clears halts.

    An intentionally incomplete template is valid input for an auditable screen:
    it must match the exact screen/universe/event files, but it must not confer
    HALT_CLEAR_VERIFIED.  Only an all-true, fully evidenced attestation can do so.
    """
    if manifest_path is None:
        return None, False
    if halt_events_file is None:
        raise ScreeningError("--halt-coverage-manifest requires --halt-events.")
    if not manifest_path.exists():
        raise ScreeningError(f"Halt coverage manifest does not exist: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ScreeningError(f"Could not parse halt coverage manifest: {type(exc).__name__}") from exc
    expected = {
        "kind": "halt_coverage_attestation",
        "screen_date": screen_date.isoformat(),
        "coverage_scope": "EXACT_UNIVERSE",
        "universe_file_sha256": _sha256(universe_file),
        "universe_symbols_sha256": hashlib.sha256(("\n".join(sorted(universe_symbols)) + "\n").encode("utf-8")).hexdigest(),
        "halt_events_file_sha256": _sha256(halt_events_file),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ScreeningError(f"Halt coverage manifest mismatch for {key}; refusing verified-clear status.")
    try:
        start = time.fromisoformat(str(manifest.get("opening_window_start_et", "")))
        end = time.fromisoformat(str(manifest.get("opening_window_end_et", "")))
    except ValueError as exc:
        raise ScreeningError("Halt coverage manifest has invalid opening-window times.") from exc
    if start > time(9, 30) or end < time(9, 35):
        raise ScreeningError("Halt coverage manifest does not cover the full 09:30-09:35 ET opening bar.")
    if not str(manifest.get("source_name", "")).strip() or not str(manifest.get("source_url", "")).strip():
        raise ScreeningError("Halt coverage manifest requires source_name and source_url.")
    if halt_events_manifest is not None and (
        manifest.get("source_name") != halt_events_manifest.get("source_name")
        or manifest.get("source_url") != halt_events_manifest.get("source_url")
    ):
        raise ScreeningError("Halt coverage source attribution does not match the normalized halt-event source manifest.")

    attestation_flags = (
        manifest.get("coverage_complete"),
        manifest.get("coverage_attested"),
        manifest.get("source_data_available_by_cutoff"),
    )
    # The coverage-template command deliberately writes all three flags false.
    # Accept that exact state for screening, but never treat it as verified.
    if attestation_flags == (False, False, False):
        return manifest, False
    # Mixed, missing, string-valued, or otherwise partial flags are not a valid
    # incomplete template and must not accidentally become a clearance claim.
    if attestation_flags != (True, True, True):
        raise ScreeningError(
            "Halt coverage attestation flags must either all be false (pending review) "
            "or all be true with complete supporting evidence."
        )
    try:
        source_available_at = datetime.fromisoformat(str(manifest.get("source_available_at_et", "")))
    except ValueError as exc:
        raise ScreeningError("Halt coverage manifest requires source_available_at_et with a timezone.") from exc
    if source_available_at.tzinfo is None:
        raise ScreeningError("source_available_at_et must include an explicit timezone offset.")
    cutoff = datetime.combine(screen_date, time(9, 35), tzinfo=ET)
    if source_available_at.astimezone(ET) > cutoff:
        raise ScreeningError(
            "Halt source data became available after the 09:35 ET screen cutoff; refusing look-ahead clearance."
        )
    if not str(manifest.get("source_availability_proof", "")).strip():
        raise ScreeningError("Halt coverage manifest requires source_availability_proof describing the timestamp evidence.")
    if not str(manifest.get("attested_by", "")).strip() or not str(manifest.get("attestation_notes", "")).strip():
        raise ScreeningError("Halt coverage manifest requires attested_by and non-empty attestation_notes.")
    return manifest, True


def _evaluate_halt_status(
    symbol: str,
    screen_date: date,
    events_by_symbol: dict[str, list[dict[str, Any]]],
    coverage_verified: bool,
    halt_data_available: bool,
) -> tuple[str, str, list[str]]:
    """Evaluate only halts overlapping [09:30, 09:35) America/New_York.

    Starts after 09:35 are ignored to avoid look-ahead. A date-only or
    pre-open event with unknown resumption is review-required, not silently
    treated as clear.
    """
    window_start = datetime.combine(screen_date, time(9, 30), tzinfo=ET)
    window_end = datetime.combine(screen_date, time(9, 35), tzinfo=ET)
    ambiguous_ids: list[str] = []
    for event in events_by_symbol.get(symbol, []):
        try:
            event_date = date.fromisoformat(str(event.get("halt_date", "")))
        except ValueError:
            return "HALT_REVIEW_REQUIRED", "HALT_EVENT_DATE_UNVERIFIABLE", [str(event.get("source_event_id", ""))]
        if event_date > screen_date:
            continue
        start_text = str(event.get("halt_start_et") or "").strip()
        resume_text = str(event.get("resume_trade_et") or "").strip()
        start_dt: datetime | None = None
        resume_dt: datetime | None = None
        if start_text:
            try:
                start_dt = datetime.fromisoformat(start_text)
                if start_dt.tzinfo is None:
                    return "HALT_REVIEW_REQUIRED", "HALT_EVENT_TIME_UNVERIFIABLE", [str(event.get("source_event_id", ""))]
                start_dt = start_dt.astimezone(ET)
            except ValueError:
                start_dt = None
        if resume_text:
            try:
                resume_dt = datetime.fromisoformat(resume_text)
                if resume_dt.tzinfo is None:
                    resume_dt = None
                else:
                    resume_dt = resume_dt.astimezone(ET)
            except ValueError:
                resume_dt = None

        event_id = str(event.get("source_event_id", ""))
        if start_dt is None:
            # Without the exact start time a same-day event cannot be placed
            # on either side of the ORB cutoff; fail closed for that symbol.
            if event_date == screen_date:
                ambiguous_ids.append(event_id)
            elif resume_dt is None or resume_dt.date() >= screen_date:
                ambiguous_ids.append(event_id)
            continue

        # Event begins during the opening bar: it is a confirmed overlap,
        # irrespective of whether the later resumption time is available.
        if window_start <= start_dt < window_end:
            return "HALTED_DURING_OPENING_WINDOW", "VERIFIED_HALT_DURING_OPENING_RANGE", [event_id]

        if start_dt < window_start:
            if resume_dt is None:
                ambiguous_ids.append(event_id)
            elif resume_dt > window_start:
                return "HALTED_DURING_OPENING_WINDOW", "VERIFIED_HALT_DURING_OPENING_RANGE", [event_id]

    if ambiguous_ids:
        return "HALT_REVIEW_REQUIRED", "HALT_EVENT_TIME_OR_RESUMPTION_UNVERIFIABLE", sorted(set(ambiguous_ids))
    if coverage_verified:
        return "HALT_CLEAR_VERIFIED", "", []
    return ("HALT_COVERAGE_UNVERIFIED" if halt_data_available else "HALT_DATA_UNAVAILABLE"), "", []

def run_screen(
    *,
    screen_date: str | date,
    universe_file: Path,
    daily_bars_file: Path,
    intraday_bars_file: Path,
    expected_feed: str | None = None,
    top_n: int = 20,
    output_dir: Path | None = None,
    security_reference_file: Path | None = None,
    halt_events_file: Path | None = None,
    halt_coverage_manifest: Path | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    """Run a deterministic ORB screen on existing, normalized local CSV files.

    This function is offline and read-only with respect to market data: it does not
    connect to Alpaca, request quotes, or expose any trading operation.
    """
    try:
        target_date = screen_date if isinstance(screen_date, date) else date.fromisoformat(str(screen_date))
    except ValueError as exc:
        raise ScreeningError("screen_date must use YYYY-MM-DD.") from exc
    if top_n < 1:
        raise ScreeningError("top_n must be at least 1.")

    universe = load_universe_symbols(universe_file)
    daily, daily_meta = _read_bars(daily_bars_file, "1Day")
    intraday, intraday_meta = _read_bars(intraday_bars_file, "5Min")
    security_reference, security_reference_manifest = _load_security_reference(security_reference_file, target_date)
    halt_events, halt_events_manifest = _load_halt_events(halt_events_file)
    halt_coverage, halt_coverage_verified = _validate_halt_coverage(
        halt_coverage_manifest,
        screen_date=target_date,
        universe_file=universe_file,
        universe_symbols=universe,
        halt_events_file=halt_events_file,
        halt_events_manifest=halt_events_manifest,
    )

    if daily_meta["feed"] != intraday_meta["feed"]:
        raise ScreeningError(
            f"Feed mismatch: daily={daily_meta['feed']} and intraday={intraday_meta['feed']}. "
            "A screen must use one feed throughout."
        )
    if expected_feed and daily_meta["feed"] != expected_feed.lower():
        raise ScreeningError(f"Requested feed {expected_feed} does not match input feed {daily_meta['feed']}.")
    if daily_meta["adjustment"] != intraday_meta["adjustment"]:
        raise ScreeningError("Daily and intraday files use different price adjustments.")
    if daily_meta["adjustment"] != "raw":
        raise ScreeningError(
            "The paper-baseline screener currently requires raw/unadjusted bars. "
            "Adjusted variants must be implemented and validated separately."
        )
    if daily_meta["asof"] or intraday_meta["asof"]:
        if daily_meta_asof_mismatch(daily_meta, intraday_meta):
            raise ScreeningError("Daily and intraday files use different Alpaca as-of symbol-mapping dates.")
        if daily_meta["asof"] != target_date.isoformat():
            raise ScreeningError(
                f"Alpaca as-of symbol mapping must match screen date {target_date.isoformat()}; "
                f"input mapping date is {daily_meta['asof']}."
            )

    # Use the union of daily rows and observed 09:30 intraday bars as the
    # session calendar. This exposes a missing daily row for a symbol when
    # other symbols still show that session in the intraday file.
    intraday_open_session_dates = {
        row.session_date
        for row in intraday.itertuples(index=False)
        if row.local_hour == 9 and row.local_minute == 30 and row.local_second == 0 and row.session_date < target_date
    }
    global_sessions = sorted(
        {session for session in daily["session_date"].tolist() if session < target_date}
        | intraday_open_session_dates
    )
    if len(global_sessions) < MIN_ATR_WARMUP_SESSIONS:
        raise ScreeningError(
            f"Daily input contains only {len(global_sessions)} completed sessions before {target_date}; "
            f"at least {MIN_ATR_WARMUP_SESSIONS} are required for the ATR(14) warm-up. "
            "Fetch a longer daily history; the screen will not emit candidates with an under-warmed ATR."
        )
    prior_15_sessions = global_sessions[-15:]
    prior_14_sessions = prior_15_sessions[-14:]

    daily_by_symbol: dict[str, dict[date, dict[str, Any]]] = {}
    for symbol, group in daily[daily["session_date"] < target_date].groupby("symbol", sort=False):
        daily_by_symbol[str(symbol)] = {
            row["session_date"]: row._asdict() if hasattr(row, "_asdict") else row.to_dict()
            for _, row in group.iterrows()
        }

    # First 5-minute bars are the rows stamped exactly 09:30:00 New York time.
    opening_rows = intraday[
        (intraday["local_hour"] == 9) &
        (intraday["local_minute"] == 30) &
        (intraday["local_second"] == 0)
    ]
    opening_by_symbol_date: dict[str, dict[date, dict[str, Any]]] = {}
    for symbol, group in opening_rows.groupby("symbol", sort=False):
        opening_by_symbol_date[str(symbol)] = {
            row["session_date"]: row.to_dict() for _, row in group.iterrows()
        }

    audit_rows: list[dict[str, Any]] = []
    qualified_indices: list[int] = []
    for symbol in universe:
        reasons: list[str] = []
        daily_map = daily_by_symbol.get(symbol, {})
        opening_map = opening_by_symbol_date.get(symbol, {})

        missing_daily = [session for session in prior_15_sessions if session not in daily_map]
        daily_records: list[dict[str, Any]] = []
        adv14: float | None = None
        atr14: float | None = None
        atr_history_sessions: int | None = None
        atr_true_range_observations: int | None = None
        if missing_daily:
            reasons.append("MISSING_DAILY_HISTORY")
        else:
            daily_records = [daily_map[session] for session in prior_15_sessions]
            adv14 = float(np.mean([float(row["volume"]) for row in daily_records[-14:]]))
            # Extend backwards through every contiguous available daily session to
            # initialize and recursively update Wilder ATR without crossing a gap.
            suffix_reversed: list[dict[str, Any]] = []
            for session in reversed(global_sessions):
                if session not in daily_map:
                    break
                suffix_reversed.append(daily_map[session])
            suffix_records = list(reversed(suffix_reversed))
            if len(suffix_records) < MIN_ATR_WARMUP_SESSIONS:
                reasons.append("INSUFFICIENT_CONTIGUOUS_ATR_HISTORY")
            else:
                atr14 = _wilder_atr_from_contiguous_suffix(suffix_records)
                atr_history_sessions = len(suffix_records)
                atr_true_range_observations = len(suffix_records) - 1

        missing_opening = [session for session in prior_14_sessions if session not in opening_map]
        prior_open_volumes: list[float] = []
        average_opening_volume: float | None = None
        if missing_opening:
            reasons.append("MISSING_OPENING_VOLUME_HISTORY")
        else:
            prior_open_volumes = [float(opening_map[session]["volume"]) for session in prior_14_sessions]
            average_opening_volume = float(np.mean(prior_open_volumes))
            if average_opening_volume <= 0:
                reasons.append("INVALID_RV_DENOMINATOR")

        current_opening = opening_map.get(target_date)
        if current_opening is None:
            reasons.append("MISSING_CURRENT_OPENING_BAR")

        opening_price: float | None = None
        opening_close: float | None = None
        opening_volume: float | None = None
        opening_high: float | None = None
        opening_low: float | None = None
        relative_volume: float | None = None
        direction = "UNAVAILABLE"
        entry_reference: float | None = None

        if current_opening is not None:
            opening_price = float(current_opening["open"])
            opening_close = float(current_opening["close"])
            opening_volume = float(current_opening["volume"])
            opening_high = float(current_opening["high"])
            opening_low = float(current_opening["low"])
            if opening_close > opening_price:
                direction = "LONG"
                entry_reference = opening_high
            elif opening_close < opening_price:
                direction = "SHORT"
                entry_reference = opening_low
            else:
                direction = "DOJI_SKIP"
            if opening_price <= 5.0:
                reasons.append("OPEN_PRICE_NOT_ABOVE_5")

        if adv14 is not None and adv14 < 1_000_000:
            reasons.append("ADV14_BELOW_1M")
        if atr14 is not None and atr14 <= 0.50:
            reasons.append("ATR14_NOT_ABOVE_0_50")
        if average_opening_volume is not None and average_opening_volume > 0 and opening_volume is not None:
            relative_volume = opening_volume / average_opening_volume
            if relative_volume < 1.0:
                reasons.append("RV_BELOW_1_0")

        # The paper's sequence ranks the stocks passing numeric filters first,
        # then applies candle direction to the selected names. A doji may therefore
        # occupy a top-N slot, but is explicitly marked as non-actionable.
        ref_row = security_reference.get(symbol, {})
        security_type = str(ref_row.get("security_type", "UNKNOWN") or "UNKNOWN")
        test_issue = str(ref_row.get("test_issue", "") or "").upper() == "Y"
        if test_issue:
            reasons.append("TEST_ISSUE_LISTING")

        halt_status, halt_reason, halt_event_ids = _evaluate_halt_status(
            symbol, target_date, halt_events, halt_coverage_verified, halt_events_file is not None
        )
        if halt_reason:
            reasons.append(halt_reason)

        failed = bool(reasons)
        row = {
            "symbol": symbol,
            "screen_date": target_date.isoformat(),
            "status": "REJECTED" if failed else "QUALIFIED_PENDING_RANK",
            "reason_codes": ";".join(dict.fromkeys(reasons)),
            "opening_price": opening_price,
            "opening_bar_close": opening_close,
            "opening_bar_high": opening_high,
            "opening_bar_low": opening_low,
            "opening_volume": opening_volume,
            "prior_14_opening_volume_mean": average_opening_volume,
            "relative_volume": relative_volume,
            "adv14_shares": adv14,
            "atr14_dollars": atr14,
            "atr_history_sessions_used": atr_history_sessions,
            "atr_true_range_observations": atr_true_range_observations,
            "missing_daily_sessions": ";".join(_format_session_dates(missing_daily)),
            "missing_prior_opening_sessions": ";".join(_format_session_dates(missing_opening)),
            "candle_direction": direction,
            "relative_volume_rank": None,
            "selected_top_n": False,
            "entry_reference_price": entry_reference,
            "security_type": security_type,
            "listing_venue": str(ref_row.get("listing_venue", "") or ""),
            "security_name": str(ref_row.get("security_name", "") or ""),
            "security_classification_note": str(ref_row.get("classification_note", "NO_REFERENCE_DATA") or ""),
            "security_reference_date": str(ref_row.get("as_of_date", "") or ""),
            "halt_status": halt_status,
            "halt_event_ids": ";".join(halt_event_ids),
            "feed": daily_meta["feed"],
            "adjustment": daily_meta["adjustment"],
        }
        audit_rows.append(row)
        if not failed:
            qualified_indices.append(len(audit_rows) - 1)

    qualified = sorted(
        qualified_indices,
        key=lambda index: (-float(audit_rows[index]["relative_volume"]), audit_rows[index]["symbol"]),
    )
    for rank, index in enumerate(qualified, start=1):
        audit_rows[index]["relative_volume_rank"] = rank
        if rank <= top_n:
            audit_rows[index]["selected_top_n"] = True
            if audit_rows[index]["halt_status"] == "HALT_CLEAR_VERIFIED":
                audit_rows[index]["status"] = "SELECTED_HALT_CLEAR_VERIFIED"
                audit_rows[index]["reason_codes"] = "SELECTED_TOP_N;HALT_CLEAR_VERIFIED"
            else:
                audit_rows[index]["status"] = "SELECTED_PENDING_HALT_CHECK"
                audit_rows[index]["reason_codes"] = "SELECTED_TOP_N;" + audit_rows[index]["halt_status"]
        else:
            audit_rows[index]["status"] = "QUALIFIED_OUTSIDE_TOP_N"
            audit_rows[index]["reason_codes"] = "OUTSIDE_TOP_N"

    audit = pd.DataFrame(audit_rows).sort_values("symbol", kind="stable").reset_index(drop=True)
    candidates = pd.DataFrame([audit_rows[index] for index in qualified[:top_n]])
    if candidates.empty:
        candidates = pd.DataFrame(columns=audit.columns)

    reason_counts: Counter[str] = Counter()
    for row in audit_rows:
        for reason in str(row["reason_codes"]).split(";"):
            if reason and reason not in {"SELECTED_TOP_N", "OUTSIDE_TOP_N", "HALT_CLEAR_VERIFIED"}:
                reason_counts[reason] += 1
    selected_directions = candidates["candle_direction"].value_counts().to_dict() if not candidates.empty else {}
    candidate_counts_by_security_type = (
        candidates["security_type"].value_counts().to_dict() if not candidates.empty else {}
    )
    audit_counts_by_security_type = audit["security_type"].value_counts().to_dict() if not audit.empty else {}
    halt_status_counts = audit["halt_status"].value_counts().to_dict() if not audit.empty else {}

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "screen_date": target_date.isoformat(),
        "strategy": "5-minute opening-range breakout / stocks-in-play screen",
        "universe_file": str(universe_file),
        "universe_file_sha256": _sha256(universe_file),
        "universe_symbols": len(universe),
        "universe_scope": "user-supplied ticker list; point-in-time membership is only as valid as the supplied universe file",
        "daily_bars_file": str(daily_bars_file),
        "daily_bars_sha256": _sha256(daily_bars_file),
        "intraday_bars_file": str(intraday_bars_file),
        "intraday_bars_sha256": _sha256(intraday_bars_file),
        "security_reference_file": str(security_reference_file) if security_reference_file else None,
        "security_reference_sha256": _sha256(security_reference_file) if security_reference_file else None,
        "security_reference_manifest": security_reference_manifest,
        "halt_events_file": str(halt_events_file) if halt_events_file else None,
        "halt_events_sha256": _sha256(halt_events_file) if halt_events_file else None,
        "halt_events_manifest": halt_events_manifest,
        "halt_coverage_manifest_file": str(halt_coverage_manifest) if halt_coverage_manifest else None,
        "halt_coverage_verified": halt_coverage_verified,
        "halt_coverage_attestation": halt_coverage,
        "feed": daily_meta["feed"],
        "adjustment": daily_meta["adjustment"],
        "daily_asof_symbol_mapping_date": daily_meta["asof"],
        "intraday_asof_symbol_mapping_date": intraday_meta["asof"],
        "prior_15_daily_sessions": _format_session_dates(prior_15_sessions),
        "prior_14_opening_sessions": _format_session_dates(prior_14_sessions),
        "filters": {
            "opening_price_strictly_above": 5.0,
            "adv14_minimum_shares_inclusive": 1_000_000,
            "atr14_minimum_dollars_strict": 0.50,
            "atr_method": "Wilder ATR(14): first 14 true ranges seeded by arithmetic mean, then recursive Wilder smoothing",
            "atr_warmup_minimum_completed_sessions": MIN_ATR_WARMUP_SESSIONS,
            "opening_bar_local_time": "09:30:00 America/New_York",
            "relative_volume_formula": "current 09:30 5-minute bar volume / arithmetic mean of prior 14 09:30 5-minute bar volumes",
            "relative_volume_minimum_inclusive": 1.0,
            "top_n": top_n,
            "ranking": "relative_volume descending, then ticker ascending; one combined long/short ranking",
            "doji_handling": "rank after numeric filters; if selected, mark DOJI_SKIP and do not provide an entry reference",
        },
        "counts": {
            "universe": len(universe),
            "qualified_before_top_n": len(qualified),
            "selected_top_n": len(candidates),
            "selected_long": int(selected_directions.get("LONG", 0)),
            "selected_short": int(selected_directions.get("SHORT", 0)),
            "selected_doji_skip": int(selected_directions.get("DOJI_SKIP", 0)),
            "rejected": int(sum(1 for row in audit_rows if row["status"] == "REJECTED")),
            "qualified_outside_top_n": int(sum(1 for row in audit_rows if row["status"] == "QUALIFIED_OUTSIDE_TOP_N")),
            "selected_pending_halt_check": int(sum(1 for row in audit_rows if row["status"] == "SELECTED_PENDING_HALT_CHECK")),
            "selected_halt_clear_verified": int(sum(1 for row in audit_rows if row["status"] == "SELECTED_HALT_CLEAR_VERIFIED")),
            "security_type_counts_universe": dict(sorted(audit_counts_by_security_type.items())),
            "security_type_counts_candidates": dict(sorted(candidate_counts_by_security_type.items())),
            "halt_status_counts": dict(sorted(halt_status_counts.items())),
        },
        "rejection_reason_counts": dict(sorted(reason_counts.items())),
        "execution_status": "read_only_deterministic_screen_only; no orders or positions created or modified",
        "limitations": [
            "The universe is only as complete as the supplied point-in-time S&P 500 file; it is not the full U.S.-listed market.",
            "Missing daily sessions or opening bars are rejected; no bars or volumes are imputed.",
            "A positive source-attributed halt overlapping 09:30-09:35 ET is excluded; absence of an event is not treated as clearance without an exact-universe coverage attestation.",
            "Without a verified halt-coverage attestation, selected candidates are labeled SELECTED_PENDING_HALT_CHECK and are not cleared for downstream use.",
            "ETF classification is source- and date-specific. NON_ETF does not mean COMMON_STOCK; absent or ambiguous flags remain UNKNOWN.",
            "ETFs are retained in the combined ranking and reported separately by security_type.",
            "IEX and SIP are separate feed-specific variants. This run enforces one feed across daily and intraday inputs.",
            "The output contains screen candidates and price reference levels only; it does not submit orders or simulate fills.",
        ],
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = output_dir or (Path.cwd() / "data" / "screeners" / target_date.isoformat() / daily_meta["feed"])
    out.mkdir(parents=True, exist_ok=True)
    stem = f"orb_screen_{target_date.isoformat()}_{daily_meta['feed']}_{stamp}"
    candidates_path = out / f"{stem}_candidates.csv"
    audit_path = out / f"{stem}_audit.csv"
    report_path = out / f"{stem}_report.json"
    candidates.to_csv(candidates_path, index=False)
    audit.to_csv(audit_path, index=False)
    category_files: dict[str, str] = {}
    for category in ("ETF", "NON_ETF", "UNKNOWN"):
        category_path = out / f"{stem}_candidates_{category.lower()}.csv"
        subset = candidates[candidates["security_type"] == category] if not candidates.empty else candidates
        subset.to_csv(category_path, index=False)
        category_files[category] = str(category_path)
    report["candidate_files_by_security_type"] = category_files
    tmp = report_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    tmp.replace(report_path)
    return candidates_path, audit_path, report_path, report


def daily_meta_asof_mismatch(daily_meta: dict[str, Any], intraday_meta: dict[str, Any]) -> bool:
    return daily_meta.get("asof") != intraday_meta.get("asof")
