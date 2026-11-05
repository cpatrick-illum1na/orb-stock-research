from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def save_bars_and_report(
    project_root: Path, frame: pd.DataFrame, *, timeframe: str, feed: str,
    adjustment: str, start: str, end: str, symbol_reports: dict[str, dict[str, Any]], asof: str | None = None,
) -> tuple[Path, Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start_tag, end_tag = _safe(start[:10]), _safe(end[:10])
    stem = f"bars_{timeframe}_{feed}_{adjustment}_{start_tag}_{end_tag}_{stamp}"
    out_dir = project_root / "data" / "raw" / timeframe / feed
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{stem}.csv"
    json_path = out_dir / f"{stem}_quality.json"

    export = frame.copy()
    if not export.empty:
        export["timestamp"] = export["timestamp"].map(lambda x: x.isoformat())
        export["retrieved_at_utc"] = export["retrieved_at_utc"].map(lambda x: x.isoformat())
    export.to_csv(csv_path, index=False)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Alpaca MCP get_stock_bars",
        "feed": feed,
        "timeframe": timeframe,
        "adjustment": adjustment,
        "requested_start": start,
        "requested_end": end,
        "asof_symbol_mapping_date": asof,
        "symbols": sorted(symbol_reports),
        "total_rows": int(len(frame)),
        "per_symbol_quality": symbol_reports,
        "universe_scope": "point-in-time S&P 500 subset when symbols come from reference_data/sp500-pit; otherwise user-supplied symbols",
        "survivorship_and_coverage_limitation": (
            "The S&P 500 YAML records dated index membership, but it is not a complete U.S.-listed security master. "
            "Alpaca bar availability for delisted/inactive tickers is not guaranteed. Missing symbols/bars are not "
            "silently treated as non-members and are not imputed. A separate coverage audit is required before backtesting."
        ),
        "execution_status": "read_only_data_retrieval_only; no order or position tools are configured or called",
        "csv_file": str(csv_path),
    }
    tmp = json_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    tmp.replace(json_path)
    return csv_path, json_path
