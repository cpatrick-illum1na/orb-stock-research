from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .mcp_bars import MCPBarsSession
from .normalization import normalize_bars
from .storage import save_bars_and_report


async def retrieve_stock_bars(
    client: MCPBarsSession,
    *,
    project_root: Path,
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    feed: str,
    adjustment: str = "raw",
    asof: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    if not symbols:
        raise ValueError("At least one symbol is required.")
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must include a UTC offset or timezone.")
    if end <= start:
        raise ValueError("end must be after start.")
    feed = feed.lower()
    if feed not in {"sip", "iex"}:
        raise ValueError("feed must be sip or iex.")
    if feed == "sip" and end.astimezone(timezone.utc) > datetime.now(timezone.utc) - pd.Timedelta(minutes=15):
        raise ValueError(
            "For the free historical SIP entitlement, set end at least 15 minutes in the past. "
            "Use feed=iex for free real-time IEX data."
        )

    frames: list[pd.DataFrame] = []
    reports: dict[str, dict[str, Any]] = {}
    raw_errors: dict[str, str] = {}
    for symbol in sorted({str(s).strip().upper() for s in symbols if str(s).strip()}):
        try:
            rows = await client.fetch_symbol(
                symbol=symbol, timeframe=timeframe, start=start, end=end,
                feed=feed, adjustment=adjustment, asof=asof,
            )
            frame, quality = normalize_bars(
                rows, symbol=symbol, timeframe=timeframe, feed=feed,
                adjustment=adjustment,
            )
            frames.append(frame)
            reports[symbol] = quality
        except Exception as exc:
            # Keep error output free of request environment and credential values.
            raw_errors[symbol] = type(exc).__name__
            reports[symbol] = {"error_type": type(exc).__name__, "rows_received": 0, "rows_kept": 0}

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not combined.empty:
        combined = combined.sort_values(["timestamp", "symbol"], kind="stable").reset_index(drop=True)
    csv_path, report_path = save_bars_and_report(
        project_root, combined, timeframe=timeframe, feed=feed, adjustment=adjustment,
        start=start.isoformat(), end=end.isoformat(), symbol_reports=reports, asof=asof,
    )
    summary = {
        "symbols_requested": len(set(symbols)),
        "symbols_with_data": sum(1 for r in reports.values() if r.get("rows_kept", 0) > 0),
        "symbols_failed": raw_errors,
        "rows": len(combined),
        "feed": feed,
        "timeframe": timeframe,
    }
    return csv_path, report_path, summary
