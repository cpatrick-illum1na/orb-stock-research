from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    """Raised when local configuration is missing or unsafe."""


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    alpaca_api_key: str
    alpaca_secret_key: str
    mcp_command: str = "uvx"
    mcp_package: str = "alpaca-mcp-server"
    toolsets: str = "stock-data"
    paper_trade: str = "true"
    default_feed: str = "sip"
    default_adjustment: str = "raw"


def load_config(project_root: Path | None = None) -> AppConfig:
    root = (project_root or Path.cwd()).resolve()
    env_file = root / ".env"
    if not env_file.exists():
        raise ConfigurationError(
            f"Missing {env_file}. Copy .env.example to .env and enter your paper API credentials locally."
        )
    load_dotenv(env_file, override=True)

    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not key or not secret:
        raise ConfigurationError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in the project .env file."
        )

    feed = os.getenv("ORB_DEFAULT_FEED", "sip").strip().lower()
    if feed not in {"sip", "iex"}:
        raise ConfigurationError("ORB_DEFAULT_FEED must be either 'sip' or 'iex'.")

    adjustment = os.getenv("ORB_DEFAULT_ADJUSTMENT", "raw").strip().lower()
    if adjustment not in {"raw", "split", "dividend", "all"}:
        raise ConfigurationError("ORB_DEFAULT_ADJUSTMENT must be raw, split, dividend, or all.")

    # The MCP process is intentionally restricted to stock market-data tools.
    # This client never loads, imports, or calls an order/position toolset.
    return AppConfig(
        project_root=root,
        alpaca_api_key=key,
        alpaca_secret_key=secret,
        mcp_command=os.getenv("ALPACA_MCP_COMMAND", "uvx").strip() or "uvx",
        mcp_package=os.getenv("ALPACA_MCP_PACKAGE", "alpaca-mcp-server").strip() or "alpaca-mcp-server",
        toolsets="stock-data",
        paper_trade="true",
        default_feed=feed,
        default_adjustment=adjustment,
    )


def build_child_environment(config: AppConfig, inherited: dict[str, str] | None = None) -> dict[str, str]:
    """Build a minimal child environment; never print or serialize secrets."""
    source = inherited if inherited is not None else dict(os.environ)
    keep = (
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
        "APPDATA", "LOCALAPPDATA", "HOME", "VIRTUAL_ENV", "UV_CACHE_DIR",
    )
    env = {name: source[name] for name in keep if source.get(name)}
    env.update(
        {
            "ALPACA_API_KEY": config.alpaca_api_key,
            "ALPACA_SECRET_KEY": config.alpaca_secret_key,
            "ALPACA_TOOLSETS": "stock-data",
            "ALPACA_PAPER_TRADE": "true",
        }
    )
    return env
