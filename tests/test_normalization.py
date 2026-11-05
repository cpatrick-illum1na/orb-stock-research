from __future__ import annotations

from datetime import datetime, timezone

from orb_data.normalization import normalize_bars


def test_normalizes_alpaca_short_fields_and_records_feed():
    rows = [{"t": "2025-01-02T14:30:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100, "n": 4, "vw": 10.2}]
    frame, report = normalize_bars(
        rows, symbol="aapl", timeframe="5Min", feed="sip", adjustment="raw",
        retrieved_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
    )
    assert len(frame) == 1
    assert frame.iloc[0]["symbol"] == "AAPL"
    assert frame.iloc[0]["feed"] == "sip"
    assert frame.iloc[0]["timeframe"] == "5Min"
    assert report["rows_kept"] == 1


def test_deduplicates_timestamps_and_removes_invalid_ohlc():
    valid = {"t": "2025-01-02T14:30:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100, "n": 4}
    duplicate = dict(valid, c=10.4)
    invalid = {"t": "2025-01-02T14:35:00Z", "o": 10, "h": 8, "l": 9, "c": 10.5, "v": 100, "n": 4}
    frame, report = normalize_bars(
        [valid, duplicate, invalid], symbol="AAPL", timeframe="5Min", feed="sip", adjustment="raw"
    )
    assert len(frame) == 1
    assert report["duplicate_timestamps_removed"] == 1
    assert report["invalid_rows_removed"] == 1


def test_zero_volume_bars_are_retained_and_counted():
    row = {"t": "2025-01-02T14:30:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 0, "n": 0}
    frame, report = normalize_bars([row], symbol="AAPL", timeframe="5Min", feed="iex", adjustment="raw")
    assert len(frame) == 1
    assert report["zero_volume_bars"] == 1
    assert report["zero_trade_count_bars"] == 1


def test_overnight_not_counted_as_intraday_gap():
    rows = [
        {"t": "2025-01-02T20:55:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 5},
        {"t": "2025-01-03T14:30:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 5},
    ]
    _, report = normalize_bars(rows, symbol="AAPL", timeframe="5Min", feed="sip", adjustment="raw")
    assert report["potential_intraday_gap_pairs"] == 0


def test_intraday_missing_intervals_are_flagged_not_filled():
    rows = [
        {"t": "2025-01-02T14:30:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 5},
        {"t": "2025-01-02T14:40:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 5},
    ]
    frame, report = normalize_bars(rows, symbol="AAPL", timeframe="5Min", feed="sip", adjustment="raw")
    assert len(frame) == 2
    assert report["potential_intraday_gap_pairs"] == 1
    assert report["estimated_unreturned_5m_intervals"] == 1
