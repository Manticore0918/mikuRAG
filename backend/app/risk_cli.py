import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.risk_controls import load_risk_register, verify_risk_register


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify section 18 risk controls and regression references."
    )
    parser.add_argument("--register", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    arguments = parser.parse_args()

    register = load_risk_register(arguments.register)
    report = verify_risk_register(
        register,
        repository_root=arguments.repository_root,
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    raise SystemExit(0 if report.passed else 2)


if __name__ == "__main__":
    main()
