from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orb_data.mcp_bars import (
    MCPBarsSession,
    UnsafeMCPConfiguration,
    build_stock_bars_arguments,
    extract_bars,
    validate_tool_inventory,
)


START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 1, 2, tzinfo=timezone.utc)


def test_build_args_supports_symbol_schema_and_page_token():
    schema = {
        "properties": {
            "symbol": {"type": "string"}, "timeframe": {"type": "string"},
            "start": {"type": "string"}, "end": {"type": "string"},
            "feed": {"type": "string"}, "adjustment": {"type": "string"},
            "limit": {"type": "integer"}, "sort": {"type": "string"},
            "page_token": {"type": "string"}, "asof": {"type": "string"},
        },
        "required": ["symbol", "timeframe", "start", "end"],
    }
    args = build_stock_bars_arguments(
        schema, symbol="aapl", timeframe="1Day", start=START, end=END,
        feed="sip", adjustment="raw", page_token="next", asof="2025-01-01", limit=500,
    )
    assert args["symbol"] == "aapl"
    assert args["page_token"] == "next"
    assert args["feed"] == "sip"
    assert args["limit"] == 500
    assert args["asof"] == "2025-01-01"


def test_asof_fails_closed_when_mcp_schema_does_not_support_it():
    schema = {"properties": {"symbol": {}, "timeframe": {}, "start": {}, "end": {}}}
    with pytest.raises(Exception, match="does not expose the asof parameter"):
        build_stock_bars_arguments(
            schema, symbol="AAPL", timeframe="1Day", start=START, end=END,
            feed="sip", adjustment="raw", asof="2025-01-02",
        )


def test_build_args_supports_plural_symbols_schema():
    schema = {"properties": {"symbols": {}, "timeframe": {}, "start": {}, "end": {}}}
    args = build_stock_bars_arguments(
        schema, symbol="MSFT", timeframe="5Min", start=START, end=END,
        feed="iex", adjustment="raw",
    )
    assert args["symbols"] == "MSFT"


def test_inventory_fails_closed_when_order_tools_are_exposed():
    with pytest.raises(UnsafeMCPConfiguration):
        validate_tool_inventory(["get_stock_bars", "place_order"])


def test_inventory_fails_closed_when_non_stock_data_tools_are_exposed():
    with pytest.raises(UnsafeMCPConfiguration):
        validate_tool_inventory(["get_stock_bars", "get_account_info"])


def test_inventory_accepts_stock_data_only():
    validate_tool_inventory(["get_stock_bars", "get_stock_quotes", "get_stock_trades"])


def test_extract_bars_from_multi_symbol_response():
    payload = {"bars": {"AAPL": [{"t": "2025-01-01T14:30:00Z", "o": 1}]}}
    rows = extract_bars(payload, "AAPL")
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["o"] == 1


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        token = arguments.get("page_token")
        if not token:
            return SimpleNamespace(
                isError=False,
                structuredContent={"bars": {"AAPL": [{"t": "2025-01-01T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}]}, "next_page_token": "p2"},
                content=[],
            )
        return SimpleNamespace(
            isError=False,
            structuredContent={"bars": {"AAPL": [{"t": "2025-01-01T14:31:00Z", "o": 2, "h": 3, "l": 2, "c": 3, "v": 20}]}},
            content=[],
        )


@pytest.mark.asyncio
async def test_session_consumes_all_pages():
    schema = {"properties": {"symbol": {}, "timeframe": {}, "start": {}, "end": {}, "feed": {}, "limit": {}, "page_token": {}}}
    tool = SimpleNamespace(inputSchema=schema)
    fake = FakeSession()
    client = MCPBarsSession(fake, tool, page_limit=100)
    rows = await client.fetch_symbol(
        symbol="AAPL", timeframe="5Min", start=START, end=END,
        feed="sip", adjustment="raw",
    )
    assert len(rows) == 2
    assert len(fake.calls) == 2
    assert fake.calls[1][1]["page_token"] == "p2"


@pytest.mark.asyncio
async def test_no_page_token_schema_splits_truncated_range():
    class SplitSession:
        def __init__(self):
            self.calls = 0
        async def call_tool(self, name, arguments):
            self.calls += 1
            start = datetime.fromisoformat(arguments["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(arguments["end"].replace("Z", "+00:00"))
            if (end - start).total_seconds() > 3600:
                return SimpleNamespace(
                    isError=False,
                    structuredContent={"bars": {"AAPL": [{"t": "2025-01-01T14:30:00Z"}]}, "next_page_token": "truncated"},
                    content=[],
                )
            return SimpleNamespace(isError=False, structuredContent={"bars": {"AAPL": []}}, content=[])

    schema = {"properties": {"symbol": {}, "timeframe": {}, "start": {}, "end": {}, "feed": {}, "limit": {}}}
    fake = SplitSession()
    client = MCPBarsSession(fake, SimpleNamespace(inputSchema=schema), page_limit=100)
    rows = await client.fetch_symbol(
        symbol="AAPL", timeframe="1Day", start=START, end=END,
        feed="sip", adjustment="raw",
    )
    assert rows == []
    assert fake.calls > 1
