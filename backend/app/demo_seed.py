import argparse
import asyncio
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic

from sqlalchemy import func, select

from app.config import get_settings
from app.database import close_database, session_factory
from app.ingestion.dispatch import enqueue_ingestion
from app.ingestion.storage import (
    language_for_suffix,
    source_kind_for_suffix,
    storage_path,
)
from app.models import (
    Document,
    DocumentStatus,
    KnowledgeBase,
    KnowledgeBaseAccess,
    User,
)
from app.security import hash_password

DATASET_VERSION = "checkpoint1_demo_v1"
PUBLIC_KNOWLEDGE_BASE = "mikuRAG Baseline Demo"
RESTRICTED_KNOWLEDGE_BASE = "mikuRAG Baseline Restricted"
DATA_DIR = Path(__file__).with_name("demo_data") / "v1"
ASSETS = (
    ("operations-handbook.pdf", "application/pdf"),
    ("release-guide.md", "text/markdown"),
    ("recovery-runbook.html", "text/html"),
    ("recovery_worker.py", "text/x-python"),
    ("recovery-client.ts", "text/typescript"),
)


@dataclass(frozen=True)
class SeedResult:
    dataset_version: str
    administrator: str
    demo_user: str
    public_knowledge_base_id: str
    restricted_knowledge_base_id: str
    document_ids: list[str]
    queued_document_ids: list[str]


def _required_password(value: str, variable: str) -> str:
    if len(value) < 12:
        raise SystemExit(f"{variable} must contain at least 12 characters")
    return value


async def _ensure_users(
    *,
    administrator_username: str,
    administrator_password: str,
    administrator_password_variable: str,
    demo_username: str,
    demo_password: str,
    demo_password_variable: str,
) -> tuple[User, User]:
    async with session_factory() as session:
        administrator = await session.scalar(
            select(User).where(User.is_administrator.is_(True)).order_by(User.created_at)
        )
        if administrator is None:
            administrator_password = _required_password(
                administrator_password,
                administrator_password_variable,
            )
            existing = await session.scalar(
                select(User).where(
                    func.lower(User.username) == administrator_username.lower()
                )
            )
            if existing is not None:
                raise SystemExit(
                    f"User '{administrator_username}' exists but is not an Administrator"
                )
            administrator = User(
                username=administrator_username.lower(),
                password_hash=hash_password(administrator_password),
                is_administrator=True,
                is_enabled=True,
            )
            session.add(administrator)

        demo_user = await session.scalar(
            select(User).where(func.lower(User.username) == demo_username.lower())
        )
        if demo_user is None:
            demo_password = _required_password(
                demo_password,
                demo_password_variable,
            )
            demo_user = User(
                username=demo_username.lower(),
                password_hash=hash_password(demo_password),
                is_administrator=False,
                is_enabled=True,
            )
            session.add(demo_user)
        elif demo_user.is_administrator:
            raise SystemExit("The baseline demo User must not be an Administrator")
        await session.commit()
        await session.refresh(administrator)
        await session.refresh(demo_user)
        return administrator, demo_user


async def _ensure_knowledge_base(name: str, description: str) -> KnowledgeBase:
    async with session_factory() as session:
        knowledge_base = await session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.name == name)
        )
        if knowledge_base is None:
            knowledge_base = KnowledgeBase(name=name, description=description)
            session.add(knowledge_base)
            await session.commit()
            await session.refresh(knowledge_base)
        elif knowledge_base.description != description:
            knowledge_base.description = description
            await session.commit()
            await session.refresh(knowledge_base)
        return knowledge_base


async def _ensure_grant(user_id: uuid.UUID, knowledge_base_id: uuid.UUID) -> None:
    async with session_factory() as session:
        grant = await session.get(
            KnowledgeBaseAccess,
            {"user_id": user_id, "knowledge_base_id": knowledge_base_id},
        )
        if grant is None:
            session.add(
                KnowledgeBaseAccess(
                    user_id=user_id,
                    knowledge_base_id=knowledge_base_id,
                )
            )
            await session.commit()


async def _ensure_document(
    knowledge_base_id: uuid.UUID,
    *,
    filename: str,
    media_type: str,
) -> tuple[Document, bool]:
    settings = get_settings()
    source = DATA_DIR / filename
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    storage_key = f"demo/{DATASET_VERSION}/{filename}"
    suffix = Path(filename).suffix.casefold()
    source_kind = source_kind_for_suffix(suffix)
    language = language_for_suffix(suffix)
    async with session_factory() as session:
        document = await session.scalar(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.sha256 == digest,
            )
        )
        if document is not None:
            document.source_kind = source_kind
            document.language = language
            document.tags = ["baseline-demo", source_kind]
            document.source_path = (
                f"demo/{filename}" if source_kind == "code" else None
            )
            document.source_metadata = {
                **dict(document.source_metadata or {}),
                "dataset_version": DATASET_VERSION,
            }
            needs_provenance_refresh = (
                document.status == DocumentStatus.READY
                and (
                    document.parser_version is None
                    or document.chunking_version is None
                )
            )
            if needs_provenance_refresh:
                document.status = DocumentStatus.PENDING
                document.safe_error = None
                document.ingestion_stage = "queued"
                document.ingestion_progress = 0
                document.ingestion_warnings = []
            await session.commit()
            await session.refresh(document)
            return document, document.status in {
                DocumentStatus.PENDING,
                DocumentStatus.FAILED,
                DocumentStatus.PROCESSING,
            }
        conflicting = await session.scalar(
            select(Document).where(Document.storage_key == storage_key)
        )
        if conflicting is not None:
            raise SystemExit(
                f"Seed storage key {storage_key!r} belongs to different content"
            )
        destination = storage_path(settings.upload_dir, storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        document = Document(
            knowledge_base_id=knowledge_base_id,
            original_name=filename,
            storage_key=storage_key,
            sha256=digest,
            media_type=media_type,
            size_bytes=len(content),
            status=DocumentStatus.PENDING,
            source_kind=source_kind,
            language=language,
            tags=["baseline-demo", source_kind],
            source_path=f"demo/{filename}" if source_kind == "code" else None,
            source_metadata={"dataset_version": DATASET_VERSION},
            ingestion_stage="queued",
            ingestion_progress=0,
            ingestion_attempts=0,
            ingestion_warnings=[],
        )
        session.add(document)
        await session.commit()
        await session.refresh(document)
        return document, True


async def _wait_for_documents(
    document_ids: list[uuid.UUID],
    timeout_seconds: int,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(Document.id, Document.status, Document.safe_error).where(
                        Document.id.in_(document_ids)
                    )
                )
            ).all()
        by_id = {row.id: row for row in rows}
        if all(
            by_id.get(identity)
            and by_id[identity].status == DocumentStatus.READY
            for identity in document_ids
        ):
            return
        failures = [row for row in rows if row.status == DocumentStatus.FAILED]
        if failures:
            details = "; ".join(
                f"{row.id}: {row.safe_error or 'unknown failure'}" for row in failures
            )
            raise SystemExit(f"Demo Ingestion failed: {details}")
        await asyncio.sleep(2)
    raise SystemExit(
        f"Demo Documents did not become Ready within {timeout_seconds} seconds"
    )


async def seed(args: argparse.Namespace) -> SeedResult:
    administrator, demo_user = await _ensure_users(
        administrator_username=args.administrator_username,
        administrator_password=os.getenv(args.administrator_password_env, ""),
        administrator_password_variable=args.administrator_password_env,
        demo_username=args.demo_username,
        demo_password=os.getenv(args.demo_password_env, ""),
        demo_password_variable=args.demo_password_env,
    )
    public = await _ensure_knowledge_base(
        PUBLIC_KNOWLEDGE_BASE,
        "Deterministic PDF, HTML, Markdown, Python, and TypeScript provenance demo.",
    )
    restricted = await _ensure_knowledge_base(
        RESTRICTED_KNOWLEDGE_BASE,
        "Authorization-boundary probe. The baseline demo User has no access.",
    )
    await _ensure_grant(demo_user.id, public.id)

    documents: list[Document] = []
    queued: list[str] = []
    for filename, media_type in ASSETS:
        document, should_enqueue = await _ensure_document(
            public.id,
            filename=filename,
            media_type=media_type,
        )
        documents.append(document)
        if should_enqueue:
            if not enqueue_ingestion(document.id):
                raise SystemExit(
                    f"Could not enqueue {filename}; start Redis and a worker, then rerun seed"
                )
            queued.append(str(document.id))
    if args.wait:
        await _wait_for_documents(
            [document.id for document in documents],
            args.timeout_seconds,
        )
    return SeedResult(
        dataset_version=DATASET_VERSION,
        administrator=administrator.username,
        demo_user=demo_user.username,
        public_knowledge_base_id=str(public.id),
        restricted_knowledge_base_id=str(restricted.id),
        document_ids=[str(document.id) for document in documents],
        queued_document_ids=queued,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed the reproducible checkpoint-1 multi-source demo"
    )
    parser.add_argument("--administrator-username", default="admin")
    parser.add_argument("--demo-username", default="baseline-demo")
    parser.add_argument(
        "--administrator-password-env",
        default="MIKURAG_DEMO_ADMIN_PASSWORD",
    )
    parser.add_argument(
        "--demo-password-env",
        default="MIKURAG_DEMO_USER_PASSWORD",
    )
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> SeedResult:
    try:
        return await seed(args)
    finally:
        await close_database()


def main() -> None:
    result = asyncio.run(_run(parse_args()))
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
