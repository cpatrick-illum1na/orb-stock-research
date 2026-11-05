from __future__ import annotations

import asyncio
import json
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator


class MCPConnectionError(RuntimeError):
    pass


class MCPToolError(RuntimeError):
    pass


class UnsafeMCPConfiguration(RuntimeError):
    pass


class PaginationError(RuntimeError):
    pass


ALLOWED_STOCK_DATA_TOOLS = {
    "get_stock_bars", "get_stock_quotes", "get_stock_trades",
    "get_stock_latest_bar", "get_stock_latest_quote", "get_stock_latest_trade",
    "get_stock_snapshot", "get_most_active_stocks", "get_market_movers",
}
# Alpaca MCP v2 currently registers these documentation lookup tools outside
# ALPACA_TOOLSETS filtering. They are read-only and do not submit API actions.
# Keep this list explicit: do not broaden the allowlist to arbitrary tools.
ALLOWED_READ_ONLY_DOC_TOOLS = {
    "search_alpaca_docs",
    "fetch_alpaca_doc",
    "search_alpaca_api_specs",
    "list_alpaca_api_endpoints",
    "get_alpaca_endpoint_docs",
}
# Alpaca MCP v2's current server.py registers the combined historical-market-data
# overrides whenever either stock-data OR crypto-data is selected. The override
# module currently registers these three crypto GET tools alongside the stock
# tools, even when ALPACA_TOOLSETS=stock-data. They are explicitly read-only.
# Allow only these exact names; the application never calls them.
ALLOWED_READ_ONLY_CRYPTO_OVERRIDE_TOOLS = {
    "get_crypto_bars",
    "get_crypto_quotes",
    "get_crypto_trades",
}
SYMBOL_FIELDS = ("symbol", "symbols", "symbol_or_symbols", "symbol_or_asset_id")
PAGE_TOKEN_FIELDS = ("page_token", "pageToken")


def validate_tool_inventory(tool_names: list[str]) -> None:
    """Fail closed unless the server exposes only the official stock-data toolset."""
    allowed = (
        ALLOWED_STOCK_DATA_TOOLS
        | ALLOWED_READ_ONLY_DOC_TOOLS
        | ALLOWED_READ_ONLY_CRYPTO_OVERRIDE_TOOLS
    )
    unexpected = sorted(set(tool_names) - allowed)
    if unexpected:
        raise UnsafeMCPConfiguration(
            "Alpaca MCP exposed tools outside the stock-data, read-only documentation, "
            "and three known read-only crypto override allowlists: "
            + ", ".join(unexpected)
            + ". No data request was made. Keep ALPACA_TOOLSETS=stock-data. "
            "All account, trading, order, position, crypto snapshot/latest/orderbook, "
            "options, watchlist, and unknown tools remain blocked."
        )
    if "get_stock_bars" not in tool_names:
        raise MCPConnectionError(
            "The connected MCP server does not expose get_stock_bars. "
            "Check the installed Alpaca MCP version and stock-data toolset."
        )


def _schema_dict(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    if schema is None:
        return {}
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_dump"):
        return schema.model_dump()
    if hasattr(schema, "dict"):
        return schema.dict()
    return {}


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Datetime arguments must include a timezone.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_stock_bars_arguments(
    schema: dict[str, Any],
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    feed: str,
    adjustment: str,
    page_token: str | None = None,
    asof: str | None = None,
    limit: int = 10_000,
) -> dict[str, Any]:
    """Adapt to MCP v2 parameter naming using the tool's declared input schema."""
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    args: dict[str, Any] = {}
    if asof is not None and "asof" not in properties:
        raise MCPConnectionError(
            "The connected get_stock_bars schema does not expose the asof parameter; "
            "refusing a point-in-time ticker request without explicit symbol mapping support."
        )

    symbol_field = next((field for field in SYMBOL_FIELDS if field in properties), None)
    if symbol_field is None:
        raise MCPConnectionError("get_stock_bars has no recognized symbol input field.")
    args[symbol_field] = symbol

    candidates = {
        "timeframe": timeframe,
        "start": _iso_utc(start),
        "end": _iso_utc(end),
        "feed": feed,
        "adjustment": adjustment,
        "asof": asof,
        "sort": "asc",
        "limit": limit,
    }
    for name, value in candidates.items():
        if name in properties and value is not None:
            args[name] = value

    token_field = next((field for field in PAGE_TOKEN_FIELDS if field in properties), None)
    if page_token is not None:
        if token_field is None:
            raise PaginationError("MCP schema does not accept a page token.")
        args[token_field] = page_token

    missing = [field for field in required if field not in args]
    if missing:
        raise MCPConnectionError(
            "get_stock_bars requires unsupported inputs: " + ", ".join(sorted(missing))
        )
    if "start" not in args or "end" not in args or "timeframe" not in args:
        raise MCPConnectionError("get_stock_bars schema must support start, end, and timeframe.")
    return args


def _decode_json_text(text: str) -> Any:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # Some MCP wrappers add a short preamble; extract the outer JSON object only.
        left, right = stripped.find("{"), stripped.rfind("}")
        if left >= 0 and right > left:
            try:
                return json.loads(stripped[left : right + 1])
            except json.JSONDecodeError:
                pass
    raise MCPToolError("get_stock_bars returned text that was not valid JSON.")


def decode_tool_result(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise MCPToolError("Alpaca MCP get_stock_bars returned an error. Check permissions, feed access, and request bounds.")

    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict):
            return structured
        if hasattr(structured, "model_dump"):
            return structured.model_dump()

    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            decoded = _decode_json_text(text)
            if isinstance(decoded, dict):
                return decoded
    if isinstance(result, dict):
        return result
    raise MCPToolError("Alpaca MCP returned no structured bar payload.")


def extract_bars(payload: dict[str, Any], requested_symbol: str) -> list[dict[str, Any]]:
    """Accept both multi-symbol and single-symbol Alpaca bar response shapes."""
    node: Any = payload
    for key in ("data", "result", "response"):
        if isinstance(node, dict) and key in node and "bars" not in node:
            node = node[key]

    if isinstance(node, list):
        rows = node
    elif isinstance(node, dict):
        bars = node.get("bars")
        if isinstance(bars, dict):
            rows = bars.get(requested_symbol)
            if rows is None and len(bars) == 1:
                rows = next(iter(bars.values()))
            if rows is None:
                rows = []
        elif isinstance(bars, list):
            rows = bars
        elif isinstance(node.get("bar"), dict):
            rows = [node["bar"]]
        else:
            # Some adapters return the single-symbol bar array under the symbol key.
            candidate = node.get(requested_symbol)
            rows = candidate if isinstance(candidate, list) else []
    else:
        rows = []

    if not isinstance(rows, list):
        raise MCPToolError("Unexpected get_stock_bars payload: bars is not a list.")
    output: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
            item.setdefault("symbol", requested_symbol)
            output.append(item)
    return output


def extract_next_page_token(payload: dict[str, Any]) -> str | None:
    for node in (payload, payload.get("data"), payload.get("result"), payload.get("response")):
        if isinstance(node, dict):
            token = node.get("next_page_token") or node.get("nextPageToken")
            if token:
                return str(token)
    return None


class MCPBarsSession:
    """Small allowlisted client facade; only the get_stock_bars tool is callable."""

    def __init__(self, session: Any, tool: Any, *, page_limit: int = 10_000, max_pages: int = 2_000):
        self._session = session
        self._tool = tool
        self._schema = _schema_dict(tool)
        self._properties = self._schema.get("properties") or {}
        self._page_token_supported = any(name in self._properties for name in PAGE_TOKEN_FIELDS)
        self._page_limit = min(page_limit, 10_000)
        self._max_pages = max_pages

    async def _request(
        self, symbol: str, timeframe: str, start: datetime, end: datetime,
        feed: str, adjustment: str, page_token: str | None = None, asof: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        args = build_stock_bars_arguments(
            self._schema, symbol=symbol, timeframe=timeframe,
            start=start, end=end, feed=feed, adjustment=adjustment,
            page_token=page_token, asof=asof, limit=self._page_limit,
        )
        result = await self._session.call_tool("get_stock_bars", arguments=args)
        payload = decode_tool_result(result)
        return extract_bars(payload, symbol), extract_next_page_token(payload)

    async def fetch_symbol(
        self, *, symbol: str, timeframe: str, start: datetime, end: datetime,
        feed: str, adjustment: str, asof: str | None = None,
    ) -> list[dict[str, Any]]:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("start/end must be timezone-aware and end must be after start.")
        symbol = symbol.strip().upper()
        if not symbol or not re.fullmatch(r"[A-Z0-9.\-]+", symbol):
            raise ValueError(f"Invalid ticker symbol: {symbol!r}")
        feed = feed.lower()
        if asof is not None:
            try:
                datetime.strptime(asof, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("asof must be YYYY-MM-DD.") from exc
        if feed not in {"sip", "iex"}:
            raise ValueError("feed must be 'sip' or 'iex'.")
        if timeframe not in {"1Day", "5Min"}:
            raise ValueError("Milestone 2 supports only 1Day and 5Min bars.")

        if self._page_token_supported:
            all_rows: list[dict[str, Any]] = []
            token: str | None = None
            seen: set[str] = set()
            for _ in range(self._max_pages):
                rows, next_token = await self._request(
                    symbol, timeframe, start, end, feed, adjustment, token, asof
                )
                all_rows.extend(rows)
                if not next_token:
                    if len(rows) >= self._page_limit:
                        # A full page without a token is suspicious; fail rather than silently truncate.
                        raise PaginationError("Received a full bar page without a next_page_token.")
                    return all_rows
                if next_token in seen:
                    raise PaginationError("Alpaca repeated a page token; refusing a possible pagination loop.")
                seen.add(next_token)
                token = next_token
            raise PaginationError("Exceeded the maximum number of Alpaca bar pages.")

        # Older/custom MCP schemas may not expose page_token. Split the date range
        # recursively if the API indicates truncation or the response reaches the limit.
        async def fetch_range(left: datetime, right: datetime, depth: int = 0) -> list[dict[str, Any]]:
            rows, next_token = await self._request(symbol, timeframe, left, right, feed, adjustment, asof=asof)
            truncated = bool(next_token) or len(rows) >= self._page_limit
            if not truncated:
                return rows
            if depth >= 24 or (right - left) <= timedelta(microseconds=2):
                raise PaginationError("Could not safely subdivide an oversized bar range.")
            midpoint = left + (right - left) / 2
            right_start = midpoint + timedelta(microseconds=1)
            if midpoint <= left or right_start >= right:
                raise PaginationError("Date range cannot be subdivided further without overlap.")
            first = await fetch_range(left, midpoint, depth + 1)
            second = await fetch_range(right_start, right, depth + 1)
            return first + second

        return await fetch_range(start, end)


def _safe_exception_details(exc: BaseException, secrets: tuple[str, ...] = ()) -> str:
    """Summarize nested startup errors without exposing credentials or tracebacks."""
    leaves: list[BaseException] = []

    def walk(item: BaseException) -> None:
        # ExceptionGroup can wrap the actionable OSError raised while spawning uvx.
        nested = getattr(item, "exceptions", None)
        if nested and isinstance(nested, tuple):
            for child in nested:
                if isinstance(child, BaseException):
                    walk(child)
            return
        leaves.append(item)

    walk(exc)
    details: list[str] = []
    for item in leaves:
        try:
            message = str(item)
        except Exception:
            message = "(message unavailable)"
        for secret in secrets:
            if secret:
                message = message.replace(secret, "[REDACTED]")
        message = re.sub(
            r"(?i)(ALPACA_(?:API_KEY|SECRET_KEY)\s*[=:]\s*)[^\s,;]+",
            r"\1[REDACTED]", message,
        )
        message = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", message)
        details.append(f"{type(item).__name__}: {message}" if message else type(item).__name__)
    # Keep the doctor output short if several AnyIO tasks report the same shutdown.
    unique = list(dict.fromkeys(details))
    return "; ".join(unique[:6]) if unique else type(exc).__name__


@asynccontextmanager
async def open_alpaca_bars(project_root: Any) -> AsyncIterator[MCPBarsSession]:
    """Start a stock-data-only Alpaca MCP subprocess using local .env credentials."""
    from .config import build_child_environment, load_config

    config = load_config(project_root)
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise MCPConnectionError("MCP SDK is missing. Install requirements.txt in the project venv.") from exc

    params = StdioServerParameters(
        command=config.mcp_command,
        args=[config.mcp_package],
        env=build_child_environment(config),
    )
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()
                tools = list(getattr(response, "tools", []) or [])
                names = [tool.name for tool in tools]
                validate_tool_inventory(names)
                bars_tool = next(tool for tool in tools if tool.name == "get_stock_bars")
                yield MCPBarsSession(session, bars_tool)
    except (UnsafeMCPConfiguration, MCPConnectionError):
        raise
    except FileNotFoundError as exc:
        raise MCPConnectionError(
            f"Could not launch {config.mcp_command}. Install Astral uv and confirm uvx is on PATH."
        ) from exc
    except Exception as exc:
        # AnyIO may wrap subprocess startup failures (including FileNotFoundError for uvx)
        # inside an ExceptionGroup. Show only sanitized leaf exceptions, never env/traceback.
        details = _safe_exception_details(
            exc, secrets=(config.alpaca_api_key, config.alpaca_secret_key)
        )
        if "FileNotFoundError" in details and config.mcp_command.lower() in {"uvx", "uv"}:
            hint = " Install Astral uv and verify that uvx is available in this PowerShell session."
        elif "FileNotFoundError" in details:
            hint = f" Verify that '{config.mcp_command}' exists and is on PATH."
        else:
            hint = " Check the sanitized root cause below; no credentials are printed."
        raise MCPConnectionError(
            f"Alpaca MCP startup/initialization failed. Root cause: {details}.{hint}"
        ) from exc
