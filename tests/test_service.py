from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from orb_data.service import retrieve_stock_bars


class FakeBarsClient:
    def __init__(self):
        self.requests = []
    async def fetch_symbol(self, **kwargs):
        self.requests.append(kwargs)
        return [{
            "t": "2025-01-02T14:30:00Z", "o": 10, "h": 11, "l": 9,
            "c": 10.5, "v": 100, "n": 5, "vw": 10.2,
        }]


def test_sip_requires_end_older_than_15_minutes(tmp_path):
    client = FakeBarsClient()
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="15 minutes"):
        asyncio.run(retrieve_stock_bars(
            client, project_root=tmp_path, symbols=["AAPL"], timeframe="5Min",
            start=now - timedelta(hours=1), end=now - timedelta(minutes=2), feed="sip",
        ))
    assert not client.requests


def test_retrieval_writes_feed_labeled_csv_and_quality_json(tmp_path):
    client = FakeBarsClient()
    start = datetime(2025, 1, 2, tzinfo=timezone.utc)
    end = datetime(2025, 1, 3, tzinfo=timezone.utc)
    csv_path, report_path, summary = asyncio.run(retrieve_stock_bars(
        client, project_root=tmp_path, symbols=["AAPL"], timeframe="1Day",
        start=start, end=end, feed="iex", adjustment="raw", asof="2025-01-02",
    ))
    assert csv_path.exists() and report_path.exists()
    assert "iex" in csv_path.parts
    assert summary["rows"] == 1
    text = csv_path.read_text(encoding="utf-8")
    assert ",iex,raw," in text
    report = report_path.read_text(encoding="utf-8")
    assert '"feed": "iex"' in report
    assert '"asof_symbol_mapping_date": "2025-01-02"' in report
