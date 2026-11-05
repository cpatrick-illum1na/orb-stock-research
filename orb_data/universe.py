from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


class UniverseDatasetError(RuntimeError):
    pass


def load_sp500_module(project_root: Path):
    repo = project_root / "reference_data" / "sp500-pit"
    script = repo / "scripts" / "build_universe.py"
    if not script.exists():
        raise UniverseDatasetError(
            f"Could not find {script}. Clone the point-in-time S&P 500 dataset into reference_data\\sp500-pit."
        )
    spec = importlib.util.spec_from_file_location("orb_sp500_build_universe", script)
    if spec is None or spec.loader is None:
        raise UniverseDatasetError("Could not load the downloaded S&P 500 universe module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_sp500_universe(project_root: Path, date_str: str) -> list[str]:
    module = load_sp500_module(project_root)
    tickers = module.get_universe_as_of(date_str)
    if not tickers:
        raise UniverseDatasetError(f"The S&P 500 dataset returned no tickers for {date_str}.")
    return sorted(set(str(t).upper() for t in tickers))


def get_all_sp500_tickers(project_root: Path) -> list[str]:
    module = load_sp500_module(project_root)
    tickers = module.get_all_historical_tickers()
    if not tickers:
        raise UniverseDatasetError("The S&P 500 dataset returned no historical tickers.")
    return sorted(set(str(t).upper() for t in tickers))
