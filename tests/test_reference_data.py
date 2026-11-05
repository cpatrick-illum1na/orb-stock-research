from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from orb_screener.reference_data import (
    ReferenceDataError,
    classify_nasdaq_directories,
    classify_nasdaq_traded_file,
    normalize_halt_events,
    write_halt_coverage_template,
)
from orb_screener.screen import ScreeningError, run_screen
from test_screener import _build_inputs


def _write_nasdaq_files(tmp_path: Path, *, created: str = "020320250900") -> tuple[Path, Path]:
    nasdaq = tmp_path / "nasdaqlisted.txt"
    other = tmp_path / "otherlisted.txt"
    nasdaq.write_text(
        "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares|\n"
        "AAPL|Apple Inc.|Q|N|N|100|Y|N|\n"
        "MSFT|Microsoft Corp.|Q|N|N|100|N|N|\n"
        "UNKN|Unknown Security|Q|N|N|100||N|\n"
        f"File Creation Time: {created}|||||||\n",
        encoding="utf-8",
    )
    other.write_text(
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|Nasdaq Symbol|\n"
        "DOJI|Doji Holdings Common Stock|N|DOJI|N|100|N|DOJI|\n"
        f"File Creation Time: {created}|||||||\n",
        encoding="utf-8",
    )
    return nasdaq, other


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_halt_file(tmp_path: Path, events: list[dict[str, str]]) -> Path:
    path = tmp_path / "normalized_halts.csv"
    columns = [
        "symbol", "halt_date", "halt_start_et", "resume_trade_et", "reason_code", "reason",
        "listing_market", "source_name", "source_url", "source_event_id", "source_row_number",
        "source_input_sha256", "halt_time_parse_status", "resume_time_parse_status",
    ]
    pd.DataFrame(events, columns=columns).to_csv(path, index=False)
    manifest = {
        "schema_version": 1,
        "kind": "normalized_halt_events",
        "source_name": "Test Exchange Halt Archive",
        "source_url": "https://example.invalid/halts.csv",
        "output_sha256": _hash(path),
        "rows": len(events),
    }
    path.with_name(f"{path.stem}_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _halt_row(symbol: str, start: str, resume: str = "", event_id: str = "ev1") -> dict[str, str]:
    return {
        "symbol": symbol,
        "halt_date": start[:10] if start else "2025-02-03",
        "halt_start_et": start,
        "resume_trade_et": resume,
        "reason_code": "T1",
        "reason": "News pending",
        "listing_market": "NASDAQ",
        "source_name": "Test Exchange Halt Archive",
        "source_url": "https://example.invalid/halts.csv",
        "source_event_id": event_id,
        "source_row_number": "2",
        "source_input_sha256": "fixture",
        "halt_time_parse_status": "PARSED" if start else "MISSING_OR_UNPARSEABLE",
        "resume_time_parse_status": "PARSED" if resume else "NOT_PROVIDED_OR_UNPARSEABLE",
    }


def _write_coverage(tmp_path: Path, universe: Path, halt_file: Path, *, complete=True, attested=True) -> Path:
    symbols = sorted(pd.read_csv(universe)["symbol"].astype(str).tolist())
    payload = {
        "schema_version": 1,
        "kind": "halt_coverage_attestation",
        "screen_date": "2025-02-03",
        "opening_window_start_et": "09:30:00",
        "opening_window_end_et": "09:35:00",
        "coverage_scope": "EXACT_UNIVERSE",
        "coverage_complete": complete,
        "coverage_attested": attested,
        "universe_file_sha256": _hash(universe),
        "universe_symbols_sha256": hashlib.sha256(("\n".join(symbols) + "\n").encode()).hexdigest(),
        "halt_events_file_sha256": _hash(halt_file),
        "source_name": "Test Exchange Halt Archive",
        "source_url": "https://example.invalid/halts.csv",
        "source_data_available_by_cutoff": True,
        "source_available_at_et": "2025-02-03T09:34:00-05:00",
        "source_availability_proof": "Test fixture has a source publication timestamp before the 09:35 cutoff.",
        "attested_by": "unit-test",
        "attestation_notes": "Fixture explicitly covers every symbol in this test universe for the full ORB window.",
    }
    path = tmp_path / "halt_coverage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_with_refs(tmp_path, paths, *, security=None, halts=None, coverage=None):
    universe, daily, intraday, _ = paths
    return run_screen(
        screen_date="2025-02-03",
        universe_file=universe,
        daily_bars_file=daily,
        intraday_bars_file=intraday,
        expected_feed="sip",
        output_dir=tmp_path / "out_refs",
        security_reference_file=security,
        halt_events_file=halts,
        halt_coverage_manifest=coverage,
    )


def test_classifies_explicit_nasdaq_etf_flags_and_preserves_unknown(tmp_path):
    nasdaq, other = _write_nasdaq_files(tmp_path)
    # Remove ETF information from an explicit third symbol to verify UNKNOWN.
    text = nasdaq.read_text().replace("ETF|NextShares|", "ETF|NextShares|")
    nasdaq.write_text(text, encoding="utf-8")
    out, manifest_path, manifest = classify_nasdaq_directories(
        nasdaq_listed_file=nasdaq,
        other_listed_file=other,
        output_file=tmp_path / "classification.csv",
        as_of_date="2025-02-03",
    )
    frame = pd.read_csv(out).set_index("symbol")
    assert frame.loc["AAPL", "security_type"] == "ETF"
    assert frame.loc["MSFT", "security_type"] == "NON_ETF"
    assert frame.loc["DOJI", "security_type"] == "NON_ETF"
    assert frame.loc["UNKN", "security_type"] == "UNKNOWN"
    assert manifest["counts"]["etf"] == 1
    assert manifest_path.exists()
    assert "not asserted to mean COMMON_STOCK" in manifest["classification_semantics"]



def test_consolidated_nasdaq_traded_file_classifies_all_listing_venues(tmp_path):
    source = tmp_path / "nasdaqtraded.txt"
    source.write_text(
        "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares\n"
        "Y|AAPL|Apple Inc. Common Stock|Q|Q|N|40|N|N||AAPL|N\n"
        "Y|SPY|SPDR S&P 500 ETF|P||Y|100|N||SPY|SPY|N\n"
        "Y|XYZ|Unknown issue|N|| |100|N||XYZ|XYZ|N\n"
        "N|NOPE|Not Traded|N||N|100|N||NOPE|NOPE|N\n"
        "File Creation Time: 020220252045|||||||||||\n",
        encoding="utf-8",
    )
    output, manifest_path, manifest = classify_nasdaq_traded_file(
        nasdaq_traded_file=source,
        output_file=tmp_path / "classification.csv",
        effective_date="2025-02-03",
    )
    frame = pd.read_csv(output).set_index("symbol")
    assert frame.loc["AAPL", "security_type"] == "NON_ETF"
    assert frame.loc["SPY", "security_type"] == "ETF"
    assert frame.loc["XYZ", "security_type"] == "UNKNOWN"
    # The first "Nasdaq Traded" flag is preserved as metadata, not used to
    # discard other-exchange securities from the consolidated directory.
    assert frame.loc["NOPE", "security_type"] == "NON_ETF"
    assert frame.loc["NOPE", "nasdaq_traded"] == "N"
    assert frame.loc["AAPL", "nasdaq_traded"] == "Y"
    assert manifest["directory_age_calendar_days"] == 1
    assert manifest_path.exists()


def test_consolidated_nasdaq_directory_created_after_0935_is_rejected(tmp_path):
    source = tmp_path / "nasdaqtraded.txt"
    source.write_text(
        "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares\n"
        "Y|AAPL|Apple Inc.|Q|Q|N|40|N|N||AAPL|N\n"
        "File Creation Time: 020320251000|||||||||||\n",
        encoding="utf-8",
    )
    with pytest.raises(ReferenceDataError, match="after 2025-02-03 09:35 ET"):
        classify_nasdaq_traded_file(
            nasdaq_traded_file=source,
            output_file=tmp_path / "classification.csv",
            effective_date="2025-02-03",
        )

def test_nasdaq_directory_snapshot_cannot_be_backdated(tmp_path):
    nasdaq, other = _write_nasdaq_files(tmp_path, created="020420250900")
    with pytest.raises(ReferenceDataError, match="does not match Nasdaq file creation date"):
        classify_nasdaq_directories(
            nasdaq_listed_file=nasdaq,
            other_listed_file=other,
            output_file=tmp_path / "classification.csv",
            as_of_date="2025-02-03",
        )


def test_normalizes_cboe_halt_format_with_event_time_and_source_provenance(tmp_path):
    source = tmp_path / "cboe_halts.csv"
    source.write_text(
        "Symbol,Company,Market,Halt,Reason,Resume Date,Quote Time,Resume Time\n"
        "AAPL,Apple Inc.,NASDAQ,02/03/2025 09:32:00,News Pending,02/03/2025,09:35:00,09:40:00\n",
        encoding="utf-8",
    )
    output, manifest_path, manifest = normalize_halt_events(
        input_file=source,
        output_file=tmp_path / "normalized.csv",
        source_name="Cboe Halts",
        source_url="https://www.cboe.com/markets/us/equities/notices/halts",
        input_format="auto",
    )
    row = pd.read_csv(output).iloc[0]
    assert manifest["input_format"] == "cboe"
    assert row["symbol"] == "AAPL"
    assert row["halt_start_et"].startswith("2025-02-03T09:32:00")
    assert row["resume_trade_et"].startswith("2025-02-03T09:40:00")
    assert row["source_name"] == "Cboe Halts"
    assert row["source_event_id"]
    assert manifest_path.exists()




def test_normalizes_and_deduplicates_exact_repeated_halt_events_preserving_provenance(tmp_path):
    source = tmp_path / "cboe_repeated_events.csv"
    source.write_text(
        "Symbol,Company Name,Market,Halt Date,Halt Time,Reason,Resume Date,Quote Time,Resume Time\n"
        "AAPL,Apple Inc.,BZX,2025-02-03,09:32:00,Volatility Pause,2025-02-03,09:35:00,09:40:00\n"
        "AAPL,Apple Inc.,BZX,2025-02-03,09:32:00,Volatility Pause,2025-02-03,09:35:00,09:40:00\n"
        "AAPL,Apple Inc.,BZX,2025-02-03,09:32:00,Volatility Pause,2025-02-03,09:35:00,09:45:00\n",
        encoding="utf-8",
    )
    output, manifest_path, manifest = normalize_halt_events(
        input_file=source,
        output_file=tmp_path / "deduplicated.csv",
        source_name="Cboe Halts",
        source_url="https://example.invalid/cboe.csv",
        input_format="cboe",
    )
    frame = pd.read_csv(output)
    assert len(frame) == 2
    assert manifest["input_rows"] == 3
    assert manifest["parsed_source_rows"] == 3
    assert manifest["rows"] == 2
    assert manifest["duplicate_event_groups"] == 1
    assert manifest["duplicate_rows_removed"] == 1
    assert manifest["max_duplicate_multiplicity"] == 2
    duplicate = frame.loc[frame["resume_trade_et"].str.endswith("09:40:00.000-05:00")].iloc[0]
    assert duplicate["duplicate_count"] == 2
    assert json.loads(duplicate["source_row_numbers"]) == [2, 3]
    all_ids = json.loads(duplicate["source_event_ids"])
    assert len(all_ids) == 2
    assert duplicate["source_event_id"] == all_ids[0]
    assert manifest_path.exists()


def test_deduplication_does_not_merge_distinct_halt_reasons_or_markets(tmp_path):
    source = tmp_path / "distinct_events.csv"
    source.write_text(
        "Symbol,Company Name,Market,Halt Date,Halt Time,Reason,Resume Date,Quote Time,Resume Time\n"
        "AAPL,Apple Inc.,BZX,2025-02-03,09:32:00,Volatility Pause,2025-02-03,09:35:00,09:40:00\n"
        "AAPL,Apple Inc.,BZX,2025-02-03,09:32:00,News Pending,2025-02-03,09:35:00,09:40:00\n"
        "AAPL,Apple Inc.,NASDAQ,2025-02-03,09:32:00,Volatility Pause,2025-02-03,09:35:00,09:40:00\n",
        encoding="utf-8",
    )
    output, _, manifest = normalize_halt_events(
        input_file=source,
        output_file=tmp_path / "distinct_normalized.csv",
        source_name="Multi-source halt sample",
        source_url="https://example.invalid/multi.csv",
        input_format="cboe",
    )
    frame = pd.read_csv(output)
    assert len(frame) == 3
    assert manifest["duplicate_rows_removed"] == 0
    assert frame["duplicate_count"].tolist() == [1, 1, 1]


def test_normalizes_cboe_ampm_halt_timestamp(tmp_path):
    source = tmp_path / "cboe_halts_ampm.csv"
    source.write_text(
        "Symbol,Company,Market,Halt,Reason,Resume Date,Quote Time,Resume Time\n"
        "AAPL,Apple Inc.,NASDAQ,02/03/2025 09:32:00 AM,News Pending,02/03/2025,09:35:00 AM,09:40:00 AM\n",
        encoding="utf-8",
    )
    output, _, manifest = normalize_halt_events(
        input_file=source,
        output_file=tmp_path / "normalized_ampm.csv",
        source_name="Cboe Halts",
        source_url="https://www.cboe.com/markets/us/equities/notices/halts",
        input_format="auto",
    )
    row = pd.read_csv(output).iloc[0]
    assert manifest["input_format"] == "cboe"
    assert row["halt_start_et"].startswith("2025-02-03T09:32:00")
    assert row["resume_trade_et"].startswith("2025-02-03T09:40:00")


def test_normalizes_quant_lodge_symbol_raw_and_trade_resume_not_quote_resume(tmp_path):
    source = tmp_path / "halts_2025.csv"
    source.write_text(
        "symbol,symbol_raw,halt_date,halt_time,halt_ms,reason_code,reason_raw,name,listing_market,resume_date,resume_quote_time,resume_trade_time,fuentes\n"
        "AAPL,AAPL,2025-02-03,09:32:00.123,123,T1,News Pending,Apple Inc.,NASDAQ,2025-02-03,09:38:00,09:40:00,cboe|nasdaq_rss\n",
        encoding="utf-8",
    )
    output, _, manifest = normalize_halt_events(
        input_file=source,
        output_file=tmp_path / "normalized_quant.csv",
        source_name="Quant-Lodge dated halt release",
        source_url="https://github.com/Quant-Lodge/ticker-reference-data/releases/download/halts-2025-02-03/halts_2025.csv",
        input_format="auto",
    )
    row = pd.read_csv(output).iloc[0]
    assert manifest["input_format"] == "quant-lodge"
    assert row["symbol"] == "AAPL"
    assert row["halt_start_et"].startswith("2025-02-03T09:32:00.123")
    # The executable time must use resume_trade_time; quote resume is not tradable resumption.
    assert row["resume_trade_et"].startswith("2025-02-03T09:40:00")


def test_coverage_template_is_not_self_certifying(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, _, _, _ = paths
    halt_file = _make_halt_file(tmp_path, [])
    out, template = write_halt_coverage_template(
        screen_date="2025-02-03",
        universe_file=universe,
        halt_events_file=halt_file,
        output_file=tmp_path / "coverage-template.json",
    )
    saved = json.loads(out.read_text())
    assert saved["coverage_complete"] is False
    assert saved["coverage_attested"] is False
    assert saved["source_data_available_by_cutoff"] is False
    assert saved["source_available_at_et"] == ""
    assert template["universe_file_sha256"] == _hash(universe)


def test_incomplete_coverage_template_is_accepted_and_keeps_candidate_pending(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, _, _, _ = paths
    halt_file = _make_halt_file(tmp_path, [])
    coverage_path, _ = write_halt_coverage_template(
        screen_date="2025-02-03",
        universe_file=universe,
        halt_events_file=halt_file,
        output_file=tmp_path / "coverage-template.json",
    )

    _, audit_path, _, report = _run_with_refs(
        tmp_path, paths, halts=halt_file, coverage=coverage_path
    )
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "SELECTED_PENDING_HALT_CHECK"
    assert audit.loc["AAPL", "halt_status"] == "HALT_COVERAGE_UNVERIFIED"
    assert report["halt_coverage_verified"] is False
    assert report["halt_coverage_attestation"]["coverage_complete"] is False
    assert report["halt_coverage_attestation"]["coverage_attested"] is False
    assert report["halt_coverage_attestation"]["source_data_available_by_cutoff"] is False


def test_partially_true_halt_coverage_flags_are_rejected(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, _, _, _ = paths
    halt_file = _make_halt_file(tmp_path, [])
    coverage = _write_coverage(tmp_path, universe, halt_file)
    payload = json.loads(coverage.read_text())
    payload["source_data_available_by_cutoff"] = False
    coverage.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScreeningError, match="either all be false.*or all be true"):
        _run_with_refs(tmp_path, paths, halts=halt_file, coverage=coverage)


def test_incomplete_halt_coverage_template_still_checks_exact_universe_hash(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, _, _, _ = paths
    halt_file = _make_halt_file(tmp_path, [])
    coverage_path, _ = write_halt_coverage_template(
        screen_date="2025-02-03",
        universe_file=universe,
        halt_events_file=halt_file,
        output_file=tmp_path / "coverage-template.json",
    )
    payload = json.loads(coverage_path.read_text())
    payload["universe_file_sha256"] = "0" * 64
    coverage_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScreeningError, match="universe_file_sha256"):
        _run_with_refs(tmp_path, paths, halts=halt_file, coverage=coverage_path)


def test_selected_candidate_is_pending_without_halt_coverage(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    _, audit_path, _, report = _run_with_refs(tmp_path, paths)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "SELECTED_PENDING_HALT_CHECK"
    assert audit.loc["AAPL", "halt_status"] == "HALT_DATA_UNAVAILABLE"
    assert report["halt_coverage_verified"] is False


def test_verified_halt_during_opening_range_excludes_candidate(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, _, _, _ = paths
    halt_file = _make_halt_file(tmp_path, [
        _halt_row("AAPL", "2025-02-03T09:32:00-05:00", "2025-02-03T09:40:00-05:00")
    ])
    _, audit_path, _, report = _run_with_refs(tmp_path, paths, halts=halt_file)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "REJECTED"
    assert "VERIFIED_HALT_DURING_OPENING_RANGE" in audit.loc["AAPL", "reason_codes"]
    assert report["counts"]["selected_top_n"] == 0


def test_halt_starting_at_or_after_0935_does_not_apply_lookahead(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    halt_file = _make_halt_file(tmp_path, [
        _halt_row("AAPL", "2025-02-03T09:35:00-05:00", "2025-02-03T09:40:00-05:00")
    ])
    _, audit_path, _, _ = _run_with_refs(tmp_path, paths, halts=halt_file)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "SELECTED_PENDING_HALT_CHECK"
    assert audit.loc["AAPL", "halt_status"] == "HALT_COVERAGE_UNVERIFIED"


def test_premarket_halt_resumed_before_open_does_not_exclude(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    halt_file = _make_halt_file(tmp_path, [
        _halt_row("AAPL", "2025-02-03T08:55:00-05:00", "2025-02-03T09:25:00-05:00")
    ])
    _, audit_path, _, _ = _run_with_refs(tmp_path, paths, halts=halt_file)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "SELECTED_PENDING_HALT_CHECK"
    assert audit.loc["AAPL", "halt_status"] == "HALT_COVERAGE_UNVERIFIED"


def test_same_day_halt_without_time_requires_review_and_is_not_selected(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    halt_file = _make_halt_file(tmp_path, [_halt_row("AAPL", "", "")])
    _, audit_path, _, _ = _run_with_refs(tmp_path, paths, halts=halt_file)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "REJECTED"
    assert audit.loc["AAPL", "halt_status"] == "HALT_REVIEW_REQUIRED"
    assert "HALT_EVENT_TIME_OR_RESUMPTION_UNVERIFIABLE" in audit.loc["AAPL", "reason_codes"]



def test_halt_coverage_rejects_source_available_after_0935(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, _, _, _ = paths
    halt_file = _make_halt_file(tmp_path, [])
    coverage = _write_coverage(tmp_path, universe, halt_file)
    payload = json.loads(coverage.read_text())
    payload["source_available_at_et"] = "2025-02-03T09:36:00-05:00"
    coverage.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScreeningError, match="after the 09:35 ET screen cutoff"):
        _run_with_refs(tmp_path, paths, halts=halt_file, coverage=coverage)


def test_exact_attested_coverage_allows_verified_clear_status(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, _, _, _ = paths
    halt_file = _make_halt_file(tmp_path, [])
    coverage = _write_coverage(tmp_path, universe, halt_file)
    _, audit_path, _, report = _run_with_refs(tmp_path, paths, halts=halt_file, coverage=coverage)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "SELECTED_HALT_CLEAR_VERIFIED"
    assert audit.loc["AAPL", "halt_status"] == "HALT_CLEAR_VERIFIED"
    assert report["halt_coverage_verified"] is True


def test_wrong_universe_hash_cannot_verify_halt_clearance(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, _, _, _ = paths
    halt_file = _make_halt_file(tmp_path, [])
    coverage = _write_coverage(tmp_path, universe, halt_file)
    payload = json.loads(coverage.read_text())
    payload["universe_file_sha256"] = "0" * 64
    coverage.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScreeningError, match="universe_file_sha256"):
        _run_with_refs(tmp_path, paths, halts=halt_file, coverage=coverage)


def test_security_reference_classifies_and_separates_etf_candidate(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL", "MSFT"))
    nasdaq, other = _write_nasdaq_files(tmp_path)
    security_file, _, _ = classify_nasdaq_directories(
        nasdaq_listed_file=nasdaq,
        other_listed_file=other,
        output_file=tmp_path / "security_classification.csv",
        as_of_date="2025-02-03",
    )
    candidates_path, _, _, report = _run_with_refs(tmp_path, paths, security=security_file)
    candidates = pd.read_csv(candidates_path).set_index("symbol")
    assert candidates.loc["AAPL", "security_type"] == "ETF"
    assert candidates.loc["MSFT", "security_type"] == "NON_ETF"
    assert report["counts"]["security_type_counts_candidates"] == {"ETF": 1, "NON_ETF": 1}
    assert Path(report["candidate_files_by_security_type"]["ETF"]).exists()
    assert Path(report["candidate_files_by_security_type"]["NON_ETF"]).exists()


def test_security_reference_created_after_screen_cutoff_is_rejected(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    nasdaq, other = _write_nasdaq_files(tmp_path, created="020320251000")
    security_file, _, _ = classify_nasdaq_directories(
        nasdaq_listed_file=nasdaq,
        other_listed_file=other,
        output_file=tmp_path / "security_classification.csv",
        as_of_date="2025-02-03",
    )
    with pytest.raises(ScreeningError, match="after the 09:35 ET screening cutoff"):
        _run_with_refs(tmp_path, paths, security=security_file)


def test_consolidated_nasdaq_directory_parses_colon_separated_creation_time(tmp_path):
    source = tmp_path / "nasdaqtraded.txt"
    source.write_text(
        "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares\n"
        "Y|AAPL|Apple Inc. Common Stock|Q|Q|N|40|N|N||AAPL|N\n"
        "File Creation Time: 1104202617:02|||||\n",
        encoding="utf-8",
    )
    output, manifest_path, manifest = classify_nasdaq_traded_file(
        nasdaq_traded_file=source,
        output_file=tmp_path / "classification.csv",
        effective_date="2026-11-05",
        max_age_calendar_days=7,
    )
    assert manifest["source_created_at_et"] == "2026-11-04T17:02:00-05:00"
    with output.open(newline="", encoding="utf-8") as stream:
        classified = next(csv.DictReader(stream))
    assert classified["as_of_date"] == "2026-11-05"
    assert output.exists()
    assert manifest_path.exists()


def test_consolidated_nasdaq_directory_rejects_malformed_colon_time(tmp_path):
    source = tmp_path / "nasdaqtraded.txt"
    source.write_text(
        "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares\n"
        "Y|AAPL|Apple Inc. Common Stock|Q|Q|N|40|N|N||AAPL|N\n"
        "File Creation Time: 1104202617:XX|||||\n",
        encoding="utf-8",
    )
    with pytest.raises(ReferenceDataError, match="Could not parse File Creation Time"):
        classify_nasdaq_traded_file(
            nasdaq_traded_file=source,
            output_file=tmp_path / "classification.csv",
            effective_date="2026-11-05",
        )
