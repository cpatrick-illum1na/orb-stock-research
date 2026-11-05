from __future__ import annotations

import pytest

from orb_data.mcp_bars import _safe_exception_details


def test_exception_group_reports_nested_file_not_found():
    error = ExceptionGroup(
        "task group failed",
        [ExceptionGroup("spawn", [FileNotFoundError(2, "No such file or directory", "uvx")])],
    )
    text = _safe_exception_details(error)
    assert "FileNotFoundError" in text
    assert "uvx" in text


def test_exception_group_redacts_exact_credentials_and_env_assignments():
    error = ExceptionGroup(
        "startup",
        [RuntimeError("bad ALPACA_API_KEY=VISIBLEKEY and secret VALUESECRET")],
    )
    text = _safe_exception_details(error, secrets=("VISIBLEKEY", "VALUESECRET"))
    assert "VISIBLEKEY" not in text
    assert "VALUESECRET" not in text
    assert "[REDACTED]" in text


def test_exception_group_deduplicates_task_shutdown_errors():
    error = ExceptionGroup("shutdown", [EOFError("closed"), EOFError("closed")])
    text = _safe_exception_details(error)
    assert text.count("EOFError") == 1
