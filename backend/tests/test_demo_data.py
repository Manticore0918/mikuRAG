import json
from pathlib import Path

from pypdf import PdfReader

from app.chunking_smoke import run_smoke

DATA = Path(__file__).parents[1] / "app" / "demo_data" / "v1"


def test_demo_manifest_covers_checkpoint_one_scenarios() -> None:
    manifest = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    categories = {case["category"] for case in manifest["cases"]}

    assert manifest["dataset_version"] == "checkpoint1_demo_v1"
    assert categories == {
        "authorization",
        "citation",
        "code_provenance",
        "exact_match",
        "follow_up",
        "html_provenance",
        "insufficient_evidence",
        "paraphrase",
    }


def test_demo_sources_contain_stable_expected_evidence() -> None:
    markdown = (DATA / "release-guide.md").read_text(encoding="utf-8")
    pdf_text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(DATA / "operations-handbook.pdf").pages
    )

    assert "Melody Harbor" in markdown
    assert "thirty minutes" in markdown
    assert "MIKU-4271" in pdf_text
    assert "Telemetry must never contain Document text" in pdf_text
    assert "HTML-8830" in (DATA / "recovery-runbook.html").read_text(
        encoding="utf-8"
    )
    assert "PY-2048" in (DATA / "recovery_worker.py").read_text(encoding="utf-8")
    assert "TS-7319" in (DATA / "recovery-client.ts").read_text(encoding="utf-8")


def test_demo_sources_pass_legacy_and_hierarchical_chunking_smoke() -> None:
    report = run_smoke()

    assert set(report) == {
        "operations-handbook.pdf",
        "recovery-client.ts",
        "recovery-runbook.html",
        "recovery_worker.py",
        "release-guide.md",
    }
    assert all(row["legacy_children"] > 0 for row in report.values())
    assert all(row["hierarchical_parents"] > 0 for row in report.values())
    assert all(row["hierarchical_children"] > 0 for row in report.values())
