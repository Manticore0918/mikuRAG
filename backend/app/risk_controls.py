import ast
import json
from dataclasses import dataclass
from pathlib import Path

REQUIRED_RISK_IDS = {
    "R1_embedding_storage_growth",
    "R2_parent_prompt_flood",
    "R3_heading_detection",
    "R4_ocr_latency_cost",
    "R5_overlap_redundancy",
    "R6_reranker_latency",
    "R7_citation_migration",
    "R8_format_overfitting",
    "R9_reindex_overload",
}


@dataclass(frozen=True)
class RiskControl:
    control_id: str
    description: str
    code_paths: tuple[str, ...]


@dataclass(frozen=True)
class ArchitectureRisk:
    risk_id: str
    risk: str
    controls: tuple[RiskControl, ...]
    verification: tuple[str, ...]
    monitoring_signals: tuple[str, ...]
    residual_risk: str


@dataclass(frozen=True)
class RiskRegister:
    schema_version: str
    risks: tuple[ArchitectureRisk, ...]


@dataclass(frozen=True)
class RiskVerificationReport:
    schema_version: str
    passed: bool
    risk_count: int
    missing_risk_ids: tuple[str, ...]
    unexpected_risk_ids: tuple[str, ...]
    missing_code_paths: tuple[str, ...]
    missing_test_references: tuple[str, ...]


def load_risk_register(path: Path) -> RiskRegister:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "chunking_risks_v1":
        raise ValueError("Unsupported chunking risk-register schema")
    raw_risks = payload.get("risks")
    if not isinstance(raw_risks, list):
        raise ValueError("Risk register requires a risks list")
    risks: list[ArchitectureRisk] = []
    for raw_risk in raw_risks:
        raw_controls = raw_risk.get("controls")
        if not isinstance(raw_controls, list) or not raw_controls:
            raise ValueError("Every architecture risk requires controls")
        controls = tuple(
            RiskControl(
                control_id=str(control["control_id"]),
                description=str(control["description"]),
                code_paths=tuple(control["code_paths"]),
            )
            for control in raw_controls
        )
        verification = tuple(raw_risk.get("verification", ()))
        monitoring = tuple(raw_risk.get("monitoring_signals", ()))
        residual_risk = str(raw_risk.get("residual_risk", "")).strip()
        if not verification or not monitoring or not residual_risk:
            raise ValueError(
                "Every architecture risk requires verification, monitoring, "
                "and residual-risk documentation"
            )
        risks.append(
            ArchitectureRisk(
                risk_id=str(raw_risk["risk_id"]),
                risk=str(raw_risk["risk"]),
                controls=controls,
                verification=verification,
                monitoring_signals=monitoring,
                residual_risk=residual_risk,
            )
        )
    if len({risk.risk_id for risk in risks}) != len(risks):
        raise ValueError("Architecture risk IDs must be unique")
    return RiskRegister(schema_version="chunking_risks_v1", risks=tuple(risks))


def verify_risk_register(
    register: RiskRegister,
    *,
    repository_root: Path,
) -> RiskVerificationReport:
    root = repository_root.resolve()
    risk_ids = {risk.risk_id for risk in register.risks}
    missing_paths: set[str] = set()
    missing_tests: set[str] = set()
    parsed_tests: dict[Path, set[str]] = {}

    for risk in register.risks:
        for control in risk.controls:
            if not control.code_paths:
                missing_paths.add(f"{risk.risk_id}:{control.control_id}:<none>")
            for relative_path in control.code_paths:
                resolved = (root / relative_path).resolve()
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    missing_paths.add(relative_path)
        for reference in risk.verification:
            path_text, separator, test_name = reference.partition("::")
            if not separator or not test_name:
                missing_tests.add(reference)
                continue
            test_path = (root / path_text).resolve()
            if not test_path.is_relative_to(root) or not test_path.is_file():
                missing_tests.add(reference)
                continue
            names = parsed_tests.get(test_path)
            if names is None:
                tree = ast.parse(test_path.read_text(encoding="utf-8"))
                names = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_")
                }
                parsed_tests[test_path] = names
            if test_name not in names:
                missing_tests.add(reference)

    missing_ids = REQUIRED_RISK_IDS - risk_ids
    unexpected_ids = risk_ids - REQUIRED_RISK_IDS
    passed = not (
        missing_ids
        or unexpected_ids
        or missing_paths
        or missing_tests
    )
    return RiskVerificationReport(
        schema_version=register.schema_version,
        passed=passed,
        risk_count=len(register.risks),
        missing_risk_ids=tuple(sorted(missing_ids)),
        unexpected_risk_ids=tuple(sorted(unexpected_ids)),
        missing_code_paths=tuple(sorted(missing_paths)),
        missing_test_references=tuple(sorted(missing_tests)),
    )
