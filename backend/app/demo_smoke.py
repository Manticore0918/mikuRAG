import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from app.database import close_database, session_factory
from app.demo_seed import (
    DATASET_VERSION,
    PUBLIC_KNOWLEDGE_BASE,
    RESTRICTED_KNOWLEDGE_BASE,
)
from app.models import (
    Chunk,
    ChunkLevel,
    Document,
    DocumentStatus,
    KnowledgeBase,
    KnowledgeBaseAccess,
    User,
)

QUESTIONS = Path(__file__).with_name("demo_data") / "v1" / "questions.json"


async def run_smoke() -> dict[str, object]:
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
        if len(documents) != 2 or any(
            document.status != DocumentStatus.READY for document in documents
        ):
            raise SystemExit("Both baseline demo Documents must be Ready")
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
    return {
        "dataset_version": DATASET_VERSION,
        "documents": [document.original_name for document in documents],
        "child_chunks": chunk_counts,
        "question_cases": len(manifest["cases"]),
        "authorization_boundary": "pass",
        "exact_evidence": "pass",
    }


async def _run() -> dict[str, object]:
    try:
        return await run_smoke()
    finally:
        await close_database()


def main() -> None:
    print(json.dumps(asyncio.run(_run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
