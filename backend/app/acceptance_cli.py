import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.acceptance import (
    AcceptanceThresholds,
    OperationalAcceptanceEvidence,
    evaluate_acceptance,
)
from app.config import get_settings
from app.rag.evaluation import (
    load_evaluation_observations,
    load_evaluation_set,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate every hierarchical chunking default-rollout gate."
    )
    parser.add_argument("--evaluation-set", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--operational-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    evaluation_version, cases = load_evaluation_set(arguments.evaluation_set)
    baseline_version, baseline = load_evaluation_observations(arguments.baseline)
    candidate_version, candidate = load_evaluation_observations(arguments.candidate)
    if {evaluation_version, baseline_version, candidate_version} != {
        evaluation_version
    }:
        raise ValueError(
            "Evaluation set, baseline, and candidate versions must match"
        )
    benchmark = json.loads(arguments.benchmark.read_text(encoding="utf-8"))
    if benchmark.get("schema_version") != "capacity_benchmark_v1":
        raise ValueError("Unsupported capacity benchmark schema")
    operational = OperationalAcceptanceEvidence.load(
        arguments.operational_evidence
    )
    report = evaluate_acceptance(
        evaluation_set_version=evaluation_version,
        cases=cases,
        baseline_observations=baseline,
        candidate_observations=candidate,
        benchmark_report=benchmark,
        operational_evidence=operational,
        thresholds=AcceptanceThresholds.from_settings(get_settings()),
    )
    serialized = json.dumps(asdict(report), indent=2, sort_keys=True)
    if arguments.output is not None:
        arguments.output.write_text(f"{serialized}\n", encoding="utf-8")
    print(serialized)
    raise SystemExit(0 if report.ready_for_default_rollout else 2)


if __name__ == "__main__":
    main()
