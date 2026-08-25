import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings
from app.database import close_database, session_factory
from app.demo_seed import (
    ASSETS,
    DATASET_VERSION,
    PUBLIC_KNOWLEDGE_BASE,
    RESTRICTED_KNOWLEDGE_BASE,
)
from app.ingestion.embeddings import embed_texts
from app.models import (
    Chunk,
    ChunkLevel,
    Document,
    DocumentStatus,
    KnowledgeBase,
    KnowledgeBaseAccess,
    User,
)
from app.rag.retrieval import retrieve_evidence

QUESTIONS = Path(__file__).with_name("demo_data") / "v1" / "questions.json"
RETRIEVAL_CASES = (
    ("MIKU-4271", "operations-handbook.pdf", {"page"}),
    ("Melody Harbor release window", "release-guide.md", {"heading_path", "line_start"}),
    ("HTML-8830", "recovery-runbook.html", {"element", "line_start"}),
    ("PY-2048", "recovery_worker.py", {"path", "line_start", "symbol"}),
    ("TS-7319", "recovery-client.ts", {"path", "line_start", "symbol"}),
)


async def run_smoke(*, retrieval: bool = False) -> dict[str, object]:
    manifest = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if manifest["dataset_version"] != DATASET_VERSION:
        raise SystemExit("The demo manifest version does not match the seed command")
    async with session_factory() as session:
        public = await session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.name == PUBLIC_KNOWLEDGE_BASE)
        )
        restricted = await session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.name == RESTRICTED_KNOWLEDGE_BASE
            )
        )
        demo_user = await session.scalar(
            select(User).where(func.lower(User.username) == "baseline-demo")
        )
        if public is None or restricted is None or demo_user is None:
            raise SystemExit("Run the baseline seed before the smoke test")
        public_grant = await session.get(
            KnowledgeBaseAccess,
            {"user_id": demo_user.id, "knowledge_base_id": public.id},
        )
        restricted_grant = await session.get(
            KnowledgeBaseAccess,
            {"user_id": demo_user.id, "knowledge_base_id": restricted.id},
        )
        if public_grant is None or restricted_grant is not None:
            raise SystemExit("The demo authorization boundary is not configured correctly")
        documents = list(
            await session.scalars(
                select(Document)
                .where(Document.knowledge_base_id == public.id)
                .order_by(Document.original_name)
            )
        )
        expected_documents = {filename for filename, _media_type in ASSETS}
        if {document.original_name for document in documents} != expected_documents or any(
            document.status != DocumentStatus.READY for document in documents
        ):
            raise SystemExit("Every baseline demo Document must exist and be Ready")
        if any(
            document.parser_version is None
            or document.chunking_version is None
            or document.ingestion_stage != "ready"
            or document.ingestion_progress != 100
            or document.ingestion_attempts < 1
            for document in documents
        ):
            raise SystemExit("Every demo Document must expose complete Ingestion diagnostics")
        chunk_counts = {}
        for document in documents:
            count = await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(
                    Chunk.document_id == document.id,
                    Chunk.chunk_level == ChunkLevel.CHILD,
                )
            )
            if not count:
                raise SystemExit(
                    f"{document.original_name} has no retrievable chunks"
                )
            chunk_counts[document.original_name] = int(count)
        exact_evidence = await session.scalar(
            select(func.count())
            .select_from(Chunk)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Document.knowledge_base_id == public.id,
                Document.status == DocumentStatus.READY,
                Chunk.chunk_level == ChunkLevel.CHILD,
                Chunk.text.contains("MIKU-4271"),
            )
        )
        if not exact_evidence:
            raise SystemExit("The exact-match smoke evidence is missing")
        for marker, document_name, locator_keys in RETRIEVAL_CASES:
            row = await session.execute(
                select(Document.original_name, Chunk.locator)
                .join(Chunk, Chunk.document_id == Document.id)
                .where(
                    Document.knowledge_base_id == public.id,
                    Document.status == DocumentStatus.READY,
                    Chunk.chunk_level == ChunkLevel.CHILD,
                    Chunk.text.contains(marker),
                )
                .limit(1)
            )
            evidence = row.first()
            if evidence is None or evidence.original_name != document_name:
                raise SystemExit(f"{marker} is missing from {document_name} retrieval chunks")
            missing = locator_keys - set(evidence.locator)
            if missing:
                raise SystemExit(
                    f"{document_name} is missing locator fields: {', '.join(sorted(missing))}"
                )
        retrieval_results: dict[str, object] = {}
        if retrieval:
            settings = get_settings()
            vectors = await embed_texts(
                [marker for marker, _document_name, _keys in RETRIEVAL_CASES],
                settings=settings,
            )
            for (marker, document_name, locator_keys), vector in zip(
                RETRIEVAL_CASES, vectors, strict=True
            ):
                evidence_rows, _sufficient = await retrieve_evidence(
                    session,
                    public.id,
                    marker,
                    vector,
                    settings,
                )
                evidence = next(
                    (
                        item
                        for item in evidence_rows
                        if item.document_name == document_name and marker in item.text
                    ),
                    None,
                )
                if evidence is None:
                    raise SystemExit(
                        f"The live retriever did not return {marker} from {document_name}"
                    )
                missing = locator_keys - set(evidence.locator)
                if missing:
                    raise SystemExit(
                        f"The retrieved {document_name} Citation is missing: "
                        f"{', '.join(sorted(missing))}"
                    )
                retrieval_results[marker] = {
                    "document": evidence.document_name,
                    "locator": evidence.locator,
                    "rank": evidence.retrieval_rank,
                }
    return {
        "dataset_version": DATASET_VERSION,
        "documents": [document.original_name for document in documents],
        "child_chunks": chunk_counts,
        "question_cases": len(manifest["cases"]),
        "authorization_boundary": "pass",
        "exact_evidence": "pass",
        "retrieval": retrieval_results if retrieval else "not_requested",
    }


async def _run(*, retrieval: bool) -> dict[str, object]:
    try:
        return await run_smoke(retrieval=retrieval)
    finally:
        await close_database()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the checkpoint-1 demo")
    parser.add_argument(
        "--retrieval",
        action="store_true",
        help="Query the configured embedding provider and the live hybrid retriever",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(_run(retrieval=args.retrieval)),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
