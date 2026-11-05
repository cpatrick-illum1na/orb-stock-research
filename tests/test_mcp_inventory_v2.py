from __future__ import annotations

import pytest

from orb_data.mcp_bars import (
    ALLOWED_READ_ONLY_CRYPTO_OVERRIDE_TOOLS,
    ALLOWED_READ_ONLY_DOC_TOOLS,
    ALLOWED_STOCK_DATA_TOOLS,
    UnsafeMCPConfiguration,
    validate_tool_inventory,
)


def test_current_alpaca_v2_stock_data_inventory_is_accepted():
    # The current Alpaca v2 server registers docs tools globally and its shared
    # historical market-data override module adds these three crypto GET tools
    # when stock-data is enabled. The client still calls only get_stock_bars.
    validate_tool_inventory(sorted(
        ALLOWED_STOCK_DATA_TOOLS
        | ALLOWED_READ_ONLY_DOC_TOOLS
        | ALLOWED_READ_ONLY_CRYPTO_OVERRIDE_TOOLS
    ))


@pytest.mark.parametrize(
    "unsafe_tool",
    [
        "place_stock_order",
        "replace_order_by_id",
        "cancel_order_by_id",
        "cancel_all_orders",
        "close_position",
        "close_all_positions",
        "get_account_info",
        "get_all_positions",
        "get_crypto_latest_bar",
        "get_crypto_latest_quote",
        "get_crypto_latest_trade",
        "get_crypto_snapshot",
        "get_crypto_latest_orderbook",
        "get_option_bars",
        "get_all_assets",
        "create_watchlist",
        "unknown_future_tool",
    ],
)
def test_order_account_and_non_allowlisted_toolsets_remain_blocked(unsafe_tool: str):
    with pytest.raises(UnsafeMCPConfiguration, match=unsafe_tool):
        validate_tool_inventory(["get_stock_bars", unsafe_tool])


def test_stock_bars_is_still_required():
    with pytest.raises(Exception, match="does not expose get_stock_bars"):
        validate_tool_inventory(sorted(
            ALLOWED_READ_ONLY_DOC_TOOLS | ALLOWED_READ_ONLY_CRYPTO_OVERRIDE_TOOLS
        ))


def test_crypto_allowlist_is_exactly_three_historical_get_tools():
    assert ALLOWED_READ_ONLY_CRYPTO_OVERRIDE_TOOLS == {
        "get_crypto_bars", "get_crypto_quotes", "get_crypto_trades"
    }


def test_legacy_inventory_with_trading_tools_is_blocked():
    with pytest.raises(UnsafeMCPConfiguration):
        validate_tool_inventory(
            ["get_stock_bars", "get_account_info", "place_stock_order"]
        )
