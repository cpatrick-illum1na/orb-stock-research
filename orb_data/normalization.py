from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


CANONICAL_COLUMNS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "trade_count", "vwap", "timeframe", "feed",
    "adjustment", "source", "retrieved_at_utc",
]


def _first_present(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def normalize_bars(
    raw_rows: list[dict[str, Any]], *, symbol: str, timeframe: str,
    feed: str, adjustment: str, retrieved_at: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Normalize Alpaca bar payloads. Zero-volume bars are retained; gaps are never filled."""
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    normalized: list[dict[str, Any]] = []
    for row in raw_rows:
        normalized.append(
            {
                "symbol": str(row.get("symbol") or symbol).upper(),
                "timestamp": _first_present(row, ("t", "timestamp", "time")),
                "open": _first_present(row, ("o", "open")),
                "high": _first_present(row, ("h", "high")),
                "low": _first_present(row, ("l", "low")),
                "close": _first_present(row, ("c", "close")),
                "volume": _first_present(row, ("v", "volume")),
                "trade_count": _first_present(row, ("n", "trade_count", "tradeCount")),
                "vwap": _first_present(row, ("vw", "vwap")),
            }
        )

    frame = pd.DataFrame(normalized)
    if frame.empty:
        frame = pd.DataFrame(columns=CANONICAL_COLUMNS)
        report = _quality_report(frame, rows_received=len(raw_rows), duplicates=0, invalid=0, timeframe=timeframe)
        return frame, report

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close", "volume", "trade_count", "vwap"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    required_numeric = ["open", "high", "low", "close", "volume"]
    finite = np.ones(len(frame), dtype=bool)
    for col in required_numeric:
        finite &= np.isfinite(frame[col].to_numpy(dtype=float, na_value=np.nan))
    finite &= frame["timestamp"].notna().to_numpy()
    finite &= (frame["open"] > 0).to_numpy()
    finite &= (frame["high"] > 0).to_numpy()
    finite &= (frame["low"] > 0).to_numpy()
    finite &= (frame["close"] > 0).to_numpy()
    finite &= (frame["volume"] >= 0).to_numpy()
    finite &= (frame["high"] >= frame[["open", "close", "low"]].max(axis=1)).to_numpy()
    finite &= (frame["low"] <= frame[["open", "close", "high"]].min(axis=1)).to_numpy()
    trade_count_known = frame["trade_count"].notna().to_numpy()
    finite &= (~trade_count_known | (frame["trade_count"].fillna(0).to_numpy() >= 0))

    invalid_count = int((~finite).sum())
    frame = frame.loc[finite].copy()
    before_dedup = len(frame)
    frame = frame.sort_values(["symbol", "timestamp"], kind="stable")
    frame = frame.drop_duplicates(subset=["symbol", "timestamp"], keep="last")
    duplicate_count = before_dedup - len(frame)

    frame["timeframe"] = timeframe
    frame["feed"] = feed.lower()
    frame["adjustment"] = adjustment.lower()
    frame["source"] = "alpaca_mcp:get_stock_bars"
    frame["retrieved_at_utc"] = pd.Timestamp(retrieved_at).tz_convert("UTC")
    frame = frame[CANONICAL_COLUMNS].reset_index(drop=True)
    report = _quality_report(
        frame, rows_received=len(raw_rows), duplicates=duplicate_count,
        invalid=invalid_count, timeframe=timeframe,
    )
    return frame, report


def _quality_report(
    frame: pd.DataFrame, *, rows_received: int, duplicates: int,
    invalid: int, timeframe: str,
) -> dict[str, Any]:
    kept = len(frame)
    volume = frame["volume"] if kept and "volume" in frame else pd.Series(dtype=float)
    trade_count = frame["trade_count"] if kept and "trade_count" in frame else pd.Series(dtype=float)
    zero_volume = int((volume == 0).sum()) if kept else 0
    zero_trade_count = int((trade_count == 0).sum()) if kept else 0

    potential_gap_pairs = 0
    estimated_missing = 0
    if kept and timeframe == "5Min":
        local = frame[["symbol", "timestamp"]].copy()
        local["local_time"] = local["timestamp"].dt.tz_convert("America/New_York")
        local["local_date"] = local["local_time"].dt.date
        local["minute_of_day"] = local["local_time"].dt.hour * 60 + local["local_time"].dt.minute
        # Only assess gaps between returned bars inside one regular session. Overnight,
        # weekends, and holidays do not count as missing intraday bars.
        local = local[(local["minute_of_day"] >= 570) & (local["minute_of_day"] < 960)]
        local = local.sort_values(["symbol", "local_date", "timestamp"])
        delta = local.groupby(["symbol", "local_date"])["timestamp"].diff().dropna()
        unexpected = delta[delta > pd.Timedelta(minutes=5)]
        potential_gap_pairs = int(len(unexpected))
        estimated_missing = int(((unexpected / pd.Timedelta(minutes=5)).apply(np.floor) - 1).clip(lower=1).sum())

    first_ts = frame["timestamp"].min().isoformat() if kept else None
    last_ts = frame["timestamp"].max().isoformat() if kept else None
    return {
        "rows_received": int(rows_received),
        "rows_kept": int(kept),
        "duplicate_timestamps_removed": int(duplicates),
        "invalid_rows_removed": int(invalid),
        "potential_intraday_gap_pairs": potential_gap_pairs,
        "estimated_unreturned_5m_intervals": estimated_missing,
        "zero_volume_bars": zero_volume,
        "zero_volume_pct": round(100 * zero_volume / kept, 4) if kept else 0.0,
        "zero_trade_count_bars": zero_trade_count,
        "zero_trade_count_pct": round(100 * zero_trade_count / int(trade_count.notna().sum()), 4)
        if kept and int(trade_count.notna().sum()) else None,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "feed": str(frame["feed"].iloc[0]) if kept else None,
        "timeframe": timeframe,
        "gap_interpretation": (
            "A gap between returned bars may represent no reported trade or missing data; "
            "the data layer does not infer which and never interpolates."
        ),
    }
