from __future__ import annotations

from pathlib import Path

from orb_data.config import AppConfig, build_child_environment


def test_child_environment_is_stock_data_only_and_paper_true():
    cfg = AppConfig(
        project_root=Path("."), alpaca_api_key="not-a-real-key", alpaca_secret_key="not-a-real-secret"
    )
    env = build_child_environment(cfg, inherited={"PATH": "C:/Windows", "OTHER_SECRET": "must-not-pass"})
    assert env["ALPACA_TOOLSETS"] == "stock-data"
    assert env["ALPACA_PAPER_TRADE"] == "true"
    assert "OTHER_SECRET" not in env
    assert env["ALPACA_API_KEY"] == "not-a-real-key"
