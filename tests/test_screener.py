from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from orb_screener.screen import ScreeningError, run_screen

ET = ZoneInfo("America/New_York")


def _business_dates(end: date, count: int) -> list[date]:
    values: list[date] = []
    cursor = end - timedelta(days=1)
    while len(values) < count:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(values))


def _bar_timestamp(day: date, hour: int = 9, minute: int = 30) -> str:
    return datetime.combine(day, time(hour, minute), tzinfo=ET).isoformat()


def _build_inputs(
    tmp_path: Path,
    *,
    screen_day: date = date(2025, 2, 3),
    symbols: tuple[str, ...] = ("AAPL", "MSFT", "DOJI"),
    feed_daily: str = "sip",
    feed_intraday: str = "sip",
    adjustment_daily: str = "raw",
    adjustment_intraday: str = "raw",
    omit_daily: tuple[tuple[str, date], ...] = (),
    omit_opening: tuple[tuple[str, date], ...] = (),
    current_open_volumes: dict[str, int] | None = None,
    current_open_prices: dict[str, tuple[float, float, float, float]] | None = None,
    daily_volumes: dict[str, int] | None = None,
    daily_ranges: dict[str, tuple[float, float, float]] | None = None,
    top_universe: tuple[str, ...] | None = None,
    history_sessions: int = 110,
) -> tuple[Path, Path, Path, list[date]]:
    sessions = _business_dates(screen_day, history_sessions)
    daily_rows = []
    intraday_rows = []
    current_open_volumes = current_open_volumes or {}
    current_open_prices = current_open_prices or {}
    daily_volumes = daily_volumes or {}
    daily_ranges = daily_ranges or {}
    omit_daily_set = set(omit_daily)
    omit_opening_set = set(omit_opening)

    for symbol in symbols:
        high, low, close = daily_ranges.get(symbol, (12.0, 8.0, 10.0))
        day_volume = daily_volumes.get(symbol, 1_000_000)
        for day in sessions:
            if (symbol, day) in omit_daily_set:
                continue
            daily_rows.append({
                "symbol": symbol,
                "timestamp": _bar_timestamp(day, 0, 0),
                "open": 10.0,
                "high": high,
                "low": low,
                "close": close,
                "volume": day_volume,
                "trade_count": 1000,
                "vwap": 10.0,
                "timeframe": "1Day",
                "feed": feed_daily,
                "adjustment": adjustment_daily,
                "source": "test",
                "retrieved_at_utc": "2025-02-04T00:00:00+00:00",
            })
        # Current-day full-session daily bar must never contaminate ADV/ATR.
        daily_rows.append({
            "symbol": symbol,
            "timestamp": _bar_timestamp(screen_day, 0, 0),
            "open": 10.0,
            "high": 100.0,
            "low": 1.0,
            "close": 50.0,
            "volume": 999_000_000,
            "trade_count": 999_999,
            "vwap": 20.0,
            "timeframe": "1Day",
            "feed": feed_daily,
            "adjustment": adjustment_daily,
            "source": "test",
            "retrieved_at_utc": "2025-02-04T00:00:00+00:00",
        })

        for index, day in enumerate(sessions[-14:]):
            if (symbol, day) in omit_opening_set:
                continue
            intraday_rows.append({
                "symbol": symbol,
                "timestamp": _bar_timestamp(day),
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.2,
                "volume": 100,
                "trade_count": 10,
                "vwap": 10.1,
                "timeframe": "5Min",
                "feed": feed_intraday,
                "adjustment": adjustment_intraday,
                "source": "test",
                "retrieved_at_utc": "2025-02-04T00:00:00+00:00",
            })

        open_, high_, low_, close_ = current_open_prices.get(symbol, (10.0, 10.5, 9.5, 10.2))
        intraday_rows.append({
            "symbol": symbol,
            "timestamp": _bar_timestamp(screen_day),
            "open": open_,
            "high": high_,
            "low": low_,
            "close": close_,
            "volume": current_open_volumes.get(symbol, 200),
            "trade_count": 30,
            "vwap": 10.1,
            "timeframe": "5Min",
            "feed": feed_intraday,
            "adjustment": adjustment_intraday,
            "source": "test",
            "retrieved_at_utc": "2025-02-04T00:00:00+00:00",
        })

    universe = top_universe or symbols
    universe_path = tmp_path / "universe.csv"
    pd.DataFrame({"symbol": list(universe)}).to_csv(universe_path, index=False)
    daily_path = tmp_path / "daily.csv"
    intraday_path = tmp_path / "intraday.csv"
    pd.DataFrame(daily_rows).to_csv(daily_path, index=False)
    pd.DataFrame(intraday_rows).to_csv(intraday_path, index=False)
    return universe_path, daily_path, intraday_path, sessions


def _run(tmp_path: Path, paths, *, top_n: int = 20):
    universe, daily, intraday, _ = paths
    return run_screen(
        screen_date="2025-02-03",
        universe_file=universe,
        daily_bars_file=daily,
        intraday_bars_file=intraday,
        expected_feed="sip",
        top_n=top_n,
        output_dir=tmp_path / "out",
    )


def test_atr_warmup_requires_100_completed_sessions(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",), history_sessions=99)
    with pytest.raises(ScreeningError, match=r"at least 100 are required for the ATR\(14\) warm-up"):
        _run(tmp_path, paths)


def test_screens_candidates_and_uses_only_prior_daily_and_opening_bars(tmp_path):
    paths = _build_inputs(tmp_path, current_open_volumes={"AAPL": 200, "MSFT": 150, "DOJI": 120},
                          current_open_prices={"DOJI": (10.0, 10.5, 9.5, 10.0)})
    candidates_path, audit_path, report_path, report = _run(tmp_path, paths)

    candidates = pd.read_csv(candidates_path)
    aapl = candidates.set_index("symbol").loc["AAPL"]
    assert aapl["relative_volume"] == pytest.approx(2.0)
    assert aapl["adv14_shares"] == pytest.approx(1_000_000)
    assert aapl["atr14_dollars"] == pytest.approx(4.0)
    assert aapl["candle_direction"] == "LONG"
    assert aapl["entry_reference_price"] == pytest.approx(10.5)
    assert candidates.iloc[0]["symbol"] == "AAPL"
    assert candidates.set_index("symbol").loc["DOJI", "candle_direction"] == "DOJI_SKIP"
    assert report["counts"]["selected_top_n"] == 3
    assert report["execution_status"].startswith("read_only")
    assert audit_path.exists() and report_path.exists()


def test_current_day_daily_bar_is_excluded_from_adv_and_atr(tmp_path):
    paths = _build_inputs(tmp_path)
    _, _, _, report = _run(tmp_path, paths)
    candidates_path = Path(report["universe_file"]).parent / "out"  # output location is asserted below
    del candidates_path
    candidate_files = list((tmp_path / "out").glob("*_candidates.csv"))
    aapl = pd.read_csv(candidate_files[0]).set_index("symbol").loc["AAPL"]
    assert aapl["adv14_shares"] == pytest.approx(1_000_000)
    assert aapl["atr14_dollars"] == pytest.approx(4.0)


def test_top_n_is_one_combined_ranking_with_deterministic_ties(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("ZZZ", "AAA", "BBB"), current_open_volumes={"ZZZ": 200, "AAA": 200, "BBB": 100})
    candidates_path, _, _, report = _run(tmp_path, paths, top_n=2)
    candidates = pd.read_csv(candidates_path)
    assert candidates["symbol"].tolist() == ["AAA", "ZZZ"]
    assert report["counts"]["qualified_before_top_n"] == 3
    assert report["counts"]["selected_top_n"] == 2


def test_price_threshold_is_strict_at_five_dollars(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("FIVE", "ABOVE"), current_open_prices={
        "FIVE": (5.0, 5.5, 4.8, 5.2), "ABOVE": (5.01, 5.5, 4.9, 5.2)
    })
    _, audit_path, _, _ = _run(tmp_path, paths)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["FIVE", "status"] == "REJECTED"
    assert "OPEN_PRICE_NOT_ABOVE_5" in audit.loc["FIVE", "reason_codes"]
    assert audit.loc["ABOVE", "status"] == "SELECTED_PENDING_HALT_CHECK"


def test_adv_floor_is_inclusive_but_atr_floor_is_strict(tmp_path):
    paths = _build_inputs(
        tmp_path,
        symbols=("ADVEDGE", "ADVLOW", "ATREDGE"),
        daily_volumes={"ADVEDGE": 1_000_000, "ADVLOW": 999_999, "ATREDGE": 1_000_000},
        daily_ranges={"ATREDGE": (10.25, 9.75, 10.0)},
    )
    _, audit_path, _, _ = _run(tmp_path, paths)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["ADVEDGE", "status"] == "SELECTED_PENDING_HALT_CHECK"
    assert "ADV14_BELOW_1M" in audit.loc["ADVLOW", "reason_codes"]
    assert "ATR14_NOT_ABOVE_0_50" in audit.loc["ATREDGE", "reason_codes"]


def test_relative_volume_floor_is_inclusive(tmp_path):
    paths = _build_inputs(tmp_path, current_open_volumes={"AAPL": 100, "MSFT": 99, "DOJI": 100})
    _, audit_path, _, _ = _run(tmp_path, paths)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "SELECTED_PENDING_HALT_CHECK"
    assert audit.loc["AAPL", "relative_volume"] == pytest.approx(1.0)
    assert "RV_BELOW_1_0" in audit.loc["MSFT", "reason_codes"]


def test_feed_mismatch_fails_closed(tmp_path):
    paths = _build_inputs(tmp_path, feed_daily="sip", feed_intraday="iex")
    with pytest.raises(ScreeningError, match="Feed mismatch"):
        _run(tmp_path, paths)


def test_non_raw_adjustment_fails_closed(tmp_path):
    paths = _build_inputs(tmp_path, adjustment_daily="split", adjustment_intraday="split")
    with pytest.raises(ScreeningError, match="requires raw"):
        _run(tmp_path, paths)


def test_missing_daily_session_is_rejected_not_imputed(tmp_path):
    screen_day = date(2025, 2, 3)
    sessions = _business_dates(screen_day, 20)
    paths = _build_inputs(tmp_path, symbols=("AAPL",), omit_daily=(("AAPL", sessions[-1]),))
    _, audit_path, _, _ = _run(tmp_path, paths)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "REJECTED"
    assert "MISSING_DAILY_HISTORY" in audit.loc["AAPL", "reason_codes"]


def test_missing_historical_opening_bar_is_rejected_not_zero_filled(tmp_path):
    screen_day = date(2025, 2, 3)
    sessions = _business_dates(screen_day, 20)
    paths = _build_inputs(tmp_path, symbols=("AAPL",), omit_opening=(("AAPL", sessions[-1]),))
    _, audit_path, _, _ = _run(tmp_path, paths)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "REJECTED"
    assert "MISSING_OPENING_VOLUME_HISTORY" in audit.loc["AAPL", "reason_codes"]


def test_zero_average_opening_volume_is_invalid_denominator(tmp_path):
    # Rebuild the intraday data with zero-volume bars on all prior 14 opening windows.
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, daily, intraday, _ = paths
    frame = pd.read_csv(intraday)
    screen_ts = _bar_timestamp(date(2025, 2, 3))
    frame.loc[frame["timestamp"] != screen_ts, "volume"] = 0
    frame.to_csv(intraday, index=False)
    _, audit_path, _, _ = _run(tmp_path, paths)
    audit = pd.read_csv(audit_path).set_index("symbol")
    assert audit.loc["AAPL", "status"] == "REJECTED"
    assert "INVALID_RV_DENOMINATOR" in audit.loc["AAPL", "reason_codes"]


def test_asof_sidecars_must_match_when_both_present(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, daily, intraday, _ = paths
    daily.with_name(f"{daily.stem}_quality.json").write_text(json.dumps({"feed": "sip", "adjustment": "raw", "timeframe": "1Day", "asof_symbol_mapping_date": "2025-02-03"}))
    intraday.with_name(f"{intraday.stem}_quality.json").write_text(json.dumps({"feed": "sip", "adjustment": "raw", "timeframe": "5Min", "asof_symbol_mapping_date": "2025-02-02"}))
    with pytest.raises(ScreeningError, match="different Alpaca as-of"):
        _run(tmp_path, paths)


def test_universe_file_requires_symbol_column_and_valid_tickers(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, daily, intraday, _ = paths
    universe.write_text("ticker\nAAPL\n", encoding="utf-8")
    with pytest.raises(ScreeningError, match="symbol.*column"):
        _run(tmp_path, (universe, daily, intraday, []))


def test_asof_mapping_date_must_equal_screen_date(tmp_path):
    paths = _build_inputs(tmp_path, symbols=("AAPL",))
    universe, daily, intraday, _ = paths
    daily.with_name(f"{daily.stem}_quality.json").write_text(json.dumps({"feed": "sip", "adjustment": "raw", "timeframe": "1Day", "asof_symbol_mapping_date": "2025-02-02"}))
    intraday.with_name(f"{intraday.stem}_quality.json").write_text(json.dumps({"feed": "sip", "adjustment": "raw", "timeframe": "5Min", "asof_symbol_mapping_date": "2025-02-02"}))
    with pytest.raises(ScreeningError, match="must match screen date"):
        _run(tmp_path, paths)


def test_selected_doji_is_explicitly_non_actionable_and_can_occupy_top_n_slot(tmp_path):
    paths = _build_inputs(
        tmp_path,
        symbols=("DOJI", "LONG", "SHORT"),
        current_open_volumes={"DOJI": 300, "LONG": 200, "SHORT": 100},
        current_open_prices={"DOJI": (10.0, 10.5, 9.5, 10.0), "SHORT": (10.0, 10.5, 9.5, 9.8)},
    )
    candidates_path, _, _, report = _run(tmp_path, paths, top_n=1)
    candidates = pd.read_csv(candidates_path)
    assert candidates.iloc[0]["symbol"] == "DOJI"
    assert candidates.iloc[0]["candle_direction"] == "DOJI_SKIP"
    assert pd.isna(candidates.iloc[0]["entry_reference_price"])
    assert report["counts"]["selected_doji_skip"] == 1
