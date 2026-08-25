from dataclasses import replace
from pathlib import Path

from app.risk_controls import (
    REQUIRED_RISK_IDS,
    load_risk_register,
    verify_risk_register,
)


def test_section_18_risk_register_has_live_controls_and_tests() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    register = load_risk_register(
        repository_root / "backend" / "risk_register" / "chunking_risks_v1.json"
    )

    report = verify_risk_register(register, repository_root=repository_root)

    assert report.passed
    assert report.risk_count == len(REQUIRED_RISK_IDS) == 9
    assert not report.missing_code_paths
    assert not report.missing_test_references


def test_risk_verifier_fails_closed_for_stale_control_references() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    register = load_risk_register(
        repository_root / "backend" / "risk_register" / "chunking_risks_v1.json"
    )
    first_risk = register.risks[0]
    first_control = replace(
        first_risk.controls[0],
        code_paths=("backend/app/does_not_exist.py",),
    )
    stale_risk = replace(
        first_risk,
        controls=(first_control,),
        verification=("backend/tests/test_models.py::test_does_not_exist",),
    )
    stale_register = replace(
        register,
        risks=(stale_risk, *register.risks[1:]),
    )

    report = verify_risk_register(
        stale_register,
        repository_root=repository_root,
    )

    assert not report.passed
    assert report.missing_code_paths == ("backend/app/does_not_exist.py",)
    assert report.missing_test_references == (
        "backend/tests/test_models.py::test_does_not_exist",
    )
