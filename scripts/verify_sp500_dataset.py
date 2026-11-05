from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path.cwd()
    repo = root / "reference_data" / "sp500-pit"
    validate = repo / "scripts" / "validate_yaml.py"
    audit = repo / "scripts" / "audit_membership.py"
    if not validate.exists() or not audit.exists():
        print(f"Dataset not found under {repo}", file=sys.stderr)
        return 2
    print("Running downloaded dataset YAML validation...")
    checked = subprocess.run([sys.executable, str(validate)], cwd=repo)
    if checked.returncode:
        return checked.returncode
    print("\nRunning membership audit...")
    audited = subprocess.run([sys.executable, str(audit)], cwd=repo)
    if audited.returncode:
        return audited.returncode
    print("\nDataset validation and audit completed.")
    print("Reminder: this is point-in-time S&P 500 membership, not the full U.S. equity universe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
