"""Tests for the executable evaluation CLI: retrieval-mode flags and the
ablation-table builder used to publish per-config metrics.
"""

from types import SimpleNamespace

from app import evaluation_cli
from app.evaluation.contracts import EvaluationExecutionResult


def _result(
    *,
    aggregate: dict[str, object],
    run_id: str = "run-1",
    status: str = "completed",
    version: str = "gold_v1",
    mode: str = "vector",
    query_planning: bool = True,
    bm25_hybrid_enabled: bool = False,
) -> EvaluationExecutionResult:
    case = SimpleNamespace(
        split="test",
        retrieval_metrics={
            "lexical_kind": "bm25" if mode == "bm25" else "fts",
            "reranker_provider": (
                "cross_encoder" if mode == "hybrid_rrf_reranked" else None
            ),
        },
    )
    run = SimpleNamespace(
        run_id=run_id,
        status=status,
        evaluation_set_version=version,
        cases=(case,),
        configuration={
            "retrieval_mode": mode,
            "query_planning": query_planning,
            "bm25_hybrid_enabled": bm25_hybrid_enabled,
        },
    )
    return EvaluationExecutionResult(run=run, aggregate=aggregate, artifacts=object())


def _aggregate(
    metrics: dict[str, object],
    by_split: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"metrics": metrics}
    if by_split is not None:
        payload["by_split"] = by_split
    return payload


def _full_metrics(prefix: str) -> dict[str, object]:
    return {
        "recall_at_10": 0.5,
        "mean_reciprocal_rank": 0.25,
        "ndcg_at_10": 0.6,
        "retrieval_latency_p95_ms": 120.0,
        "mean_evidence_tokens": 800.0,
        "answer_faithfulness": None,
        f"recall_at_1_{prefix}": 0.2,
    }


def test_run_parser_accepts_retrieval_flags() -> None:
    args = evaluation_cli.build_parser().parse_args(
        [
            "run",
            "--retrieval-mode",
            "bm25",
            "--reranker",
            "cross_encoder",
            "--no-query-planning",
            "--bm25-hybrid-enabled",
            "--dataset",
            "manifest.json",
            "--output-dir",
            "results",
        ]
    )

    assert args.retrieval_mode == "bm25"
    assert args.reranker == "cross_encoder"
    assert args.query_planning is False
    assert args.bm25_hybrid_enabled is True


def test_run_parser_defaults_keep_configuration_authoritative() -> None:
    args = evaluation_cli.build_parser().parse_args(
        ["run", "--dataset", "manifest.json", "--output-dir", "results"]
    )

    assert args.retrieval_mode is None
    assert args.reranker is None
    assert args.bm25_hybrid_enabled is None
    assert args.query_planning is True


def test_ablation_parser_accepts_modes_reranker_and_split() -> None:
    args = evaluation_cli.build_parser().parse_args(
        [
            "ablation",
            "--modes",
            "vector",
            "hybrid_rrf",
            "--reranker",
            "cross_encoder",
            "--split",
            "test",
        ]
    )

    assert args.modes == ["vector", "hybrid_rrf"]
    assert args.reranker == "cross_encoder"
    assert args.split == "test"
    assert args.query_planning == "both"


def test_ablation_parser_defaults_to_all_five_modes() -> None:
    args = evaluation_cli.build_parser().parse_args(["ablation"])

    assert args.modes == list(evaluation_cli._RETRIEVAL_MODES)
    assert args.split == "test"
    assert args.query_planning == "both"


def test_ablation_parser_can_select_original_queries_only() -> None:
    args = evaluation_cli.build_parser().parse_args(
        ["ablation", "--query-planning", "off"]
    )

    assert args.query_planning == "off"


def test_ablation_table_picks_split_metrics() -> None:
    aggregate = _aggregate(
        metrics=_full_metrics("overall"),
        by_split={
            "test": _full_metrics("test"),
        },
    )
    aggregate["by_split"]["test"]["recall_at_10"] = 0.9
    result = _result(aggregate=aggregate)

    table = evaluation_cli._ablation_table({"vector": result}, "test")

    assert table["evaluation_set_version"] == "gold_v1"
    assert table["headline_split"] == "test"
    assert table["configs"][0]["mode"] == "vector"
    assert table["configs"][0]["recall_at_10"] == 0.9
    assert table["configs"][0]["mean_evidence_tokens"] == 800.0


def test_ablation_table_uses_overall_metrics_for_all_split() -> None:
    result = _result(aggregate=_aggregate(_full_metrics("overall")))
    table = evaluation_cli._ablation_table({"vector": result}, "all")

    assert table["headline_split"] == "all"
    assert table["configs"][0]["recall_at_10"] == 0.5


def test_ablation_table_tolerates_missing_split_metrics() -> None:
    aggregate = _aggregate(metrics=_full_metrics("overall"))
    result = _result(aggregate=aggregate)

    table = evaluation_cli._ablation_table({"vector": result}, "dev")

    assert table["configs"][0]["recall_at_10"] == 0.5


def test_ablation_markdown_renders_real_values_not_placeholders() -> None:
    result = _result(
        aggregate=_aggregate(
            metrics=_full_metrics("overall"),
            by_split={"test": _full_metrics("test")},
        )
    )
    aggregate = result.aggregate
    aggregate["by_split"]["test"]["recall_at_10"] = 0.75  # type: ignore[index]

    table = evaluation_cli._ablation_table({"vector": result}, "test")
    markdown = evaluation_cli._render_ablation_markdown(table)

    assert "| Requested mode | Query | Effective lexical leg |" in markdown
    assert "rewritten" in markdown
    assert "`vector`" in markdown
    assert "0.7500" in markdown
    assert "120.0" in markdown
    assert "800" in markdown
    assert "?" not in markdown


def test_ablation_markdown_renders_missing_values_as_dash() -> None:
    result = _result(aggregate=_aggregate({"metrics": {}}))

    table = evaluation_cli._ablation_table({"vector": result}, "all")
    markdown = evaluation_cli._render_ablation_markdown(table)

    assert "| `vector` | rewritten |" in markdown
    assert markdown.count("-") >= 6


def test_ablation_run_id_is_distinct_per_mode() -> None:
    assert evaluation_cli._ablation_run_id("vector") != evaluation_cli._ablation_run_id("bm25")
    assert ":" not in evaluation_cli._ablation_run_id("vector:rewritten")


def test_ablation_marks_bm25_fallback_as_not_headline_valid() -> None:
    result = _result(
        aggregate=_aggregate(_full_metrics("overall")),
        run_id="run-fts",
        mode="bm25",
    )
    result.run.cases[0].retrieval_metrics["lexical_kind"] = "fts_fallback"

    row = evaluation_cli._ablation_table({"bm25": result}, "test")["configs"][0]

    assert row["fallback_used"] is True
    assert row["valid_for_headline"] is False
    assert row["effective_lexical_kinds"] == ["fts_fallback"]


def test_ablation_marks_enabled_hybrid_bm25_fallback_as_not_headline_valid() -> None:
    result = _result(
        aggregate=_aggregate(_full_metrics("overall")),
        mode="hybrid_rrf",
        bm25_hybrid_enabled=True,
    )
    result.run.cases[0].retrieval_metrics["lexical_kind"] = "fts"

    row = evaluation_cli._ablation_table({"hybrid_rrf": result}, "test")[
        "configs"
    ][0]

    assert row["fallback_used"] is True
    assert row["valid_for_headline"] is False


def test_ablation_keeps_rewrite_variants_as_distinct_rows() -> None:
    rewritten = _result(
        aggregate=_aggregate(_full_metrics("overall")),
        run_id="rewritten",
        query_planning=True,
    )
    original = _result(
        aggregate=_aggregate(_full_metrics("overall")),
        run_id="original",
        query_planning=False,
    )

    rows = evaluation_cli._ablation_table(
        {"vector:rewritten": rewritten, "vector:original": original}, "test"
    )["configs"]

    assert [row["query_planning"] for row in rows] == [True, False]
