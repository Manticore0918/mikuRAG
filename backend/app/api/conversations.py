import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.dependencies import CurrentUser, DatabaseSession, ensure_knowledge_base_access
from app.ingestion.storage import storage_path
from app.models import (
    Citation,
    Conversation,
    Document,
    DocumentStatus,
    KnowledgeBase,
    Message,
    MessageRole,
    MessageStatus,
)
from app.rag.service import citation_payload, turn_events
from app.schemas import (
    CitationRead,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageRead,
    TurnCreate,
)
from app.security import require_csrf

router = APIRouter(prefix="/conversations", tags=["conversations"])
CsrfCheck = Annotated[None, Depends(require_csrf)]


async def require_owned_conversation(
    session: DatabaseSession,
    current_user: CurrentUser,
    conversation_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.owner_id == current_user.id,
    )
    if lock:
        statement = statement.with_for_update()
    conversation = await session.scalar(statement)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    await ensure_knowledge_base_access(
        session, current_user, conversation.knowledge_base_id
    )
    return conversation


def conversation_read(
    conversation: Conversation, knowledge_base_name: str
) -> ConversationRead:
    return ConversationRead(
        id=conversation.id,
        knowledge_base_id=conversation.knowledge_base_id,
        knowledge_base_name=knowledge_base_name,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[ConversationRead]:
    rows = await session.execute(
        select(Conversation, KnowledgeBase.name)
        .join(KnowledgeBase, KnowledgeBase.id == Conversation.knowledge_base_id)
        .where(Conversation.owner_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
    )
    output: list[ConversationRead] = []
    for conversation, knowledge_base_name in rows.all():
        try:
            await ensure_knowledge_base_access(
                session, current_user, conversation.knowledge_base_id
            )
        except HTTPException:
            continue
        output.append(conversation_read(conversation, knowledge_base_name))
    return output


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    _csrf: CsrfCheck,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> ConversationRead:
    knowledge_base = await session.get(KnowledgeBase, payload.knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge Base not found"
        )
    await ensure_knowledge_base_access(session, current_user, payload.knowledge_base_id)
    conversation = Conversation(
        id=uuid.uuid4(),
        owner_id=current_user.id,
        knowledge_base_id=payload.knowledge_base_id,
        title="New Conversation",
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation_read(conversation, knowledge_base.name)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> ConversationDetail:
    conversation = await require_owned_conversation(
        session, current_user, conversation_id
    )
    knowledge_base_name = await session.scalar(
        select(KnowledgeBase.name).where(KnowledgeBase.id == conversation.knowledge_base_id)
    )
    messages = list(
        (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence)
            )
        ).all()
    )
    message_ids = [message.id for message in messages]
    citation_rows = (
        list(
            (
                await session.scalars(
                    select(Citation)
                    .where(Citation.message_id.in_(message_ids))
                    .order_by(Citation.retrieval_rank)
                )
            ).all()
        )
        if message_ids
        else []
    )
    document_ids = {
        citation.document_id
        for citation in citation_rows
        if citation.document_id is not None
    }
    ready_document_ids = (
        set(
            (
                await session.scalars(
                    select(Document.id).where(
                        Document.id.in_(document_ids),
                        Document.knowledge_base_id == conversation.knowledge_base_id,
                        Document.status == DocumentStatus.READY,
                    )
                )
            ).all()
        )
        if document_ids
        else set()
    )
    citations_by_message: dict[uuid.UUID, list[CitationRead]] = {}
    for citation in citation_rows:
        payload = citation_payload(citation, conversation.id)
        source_available = citation.document_id in ready_document_ids
        payload["source_available"] = source_available
        if not source_available:
            payload["source_url"] = None
        citations_by_message.setdefault(citation.message_id, []).append(
            CitationRead.model_validate(payload)
        )
    base = conversation_read(conversation, knowledge_base_name or "Deleted Knowledge Base")
    return ConversationDetail(
        **base.model_dump(),
        messages=[
            MessageRead(
                id=message.id,
                sequence=message.sequence,
                role=message.role,
                status=message.status,
                content=message.content,
                created_at=message.created_at,
                citations=citations_by_message.get(message.id, []),
            )
            for message in messages
        ],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    _csrf: CsrfCheck,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> Response:
    conversation = await require_owned_conversation(
        session, current_user, conversation_id
    )
    await session.delete(conversation)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/turns")
async def create_turn(
    conversation_id: uuid.UUID,
    payload: TurnCreate,
    _csrf: CsrfCheck,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> StreamingResponse:
    settings = get_settings()
    conversation = await require_owned_conversation(
        session, current_user, conversation_id, lock=True
    )
    stale_before = datetime.now(UTC) - timedelta(seconds=settings.stale_turn_seconds)
    streaming = await session.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            Message.role == MessageRole.ASSISTANT,
            Message.status == MessageStatus.STREAMING,
        )
    )
    if streaming is not None and streaming.created_at >= stale_before:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A response is already being generated for this Conversation",
        )
    if streaming is not None:
        streaming.status = MessageStatus.FAILED
        streaming.content = "The previous response was interrupted."
        streaming.model_metadata = {"outcome": "stale_turn_recovered"}
        await session.flush()

    last_sequence = await session.scalar(
        select(func.coalesce(func.max(Message.sequence), 0)).where(
            Message.conversation_id == conversation.id
        )
    )
    user_message = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sequence=int(last_sequence or 0) + 1,
        role=MessageRole.USER,
        status=MessageStatus.COMPLETE,
        content=payload.question,
        model_metadata={},
    )
    assistant_message = Message(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        sequence=user_message.sequence + 1,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.STREAMING,
        content="",
        model_metadata={"outcome": "streaming"},
    )
    if conversation.title == "New Conversation":
        conversation.title = payload.question[:157] + (
            "..." if len(payload.question) > 157 else ""
        )
    session.add_all([user_message, assistant_message])
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A response is already being generated for this Conversation",
        ) from error
    await session.close()
    return StreamingResponse(
        turn_events(conversation.id, user_message.id, assistant_message.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{conversation_id}/citations/{citation_id}/source")
async def get_citation_source(
    conversation_id: uuid.UUID,
    citation_id: uuid.UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> FileResponse:
    conversation = await require_owned_conversation(
        session, current_user, conversation_id
    )
    citation = await session.scalar(
        select(Citation)
        .join(Message, Message.id == Citation.message_id)
        .where(
            Citation.id == citation_id,
            Message.conversation_id == conversation.id,
        )
    )
    if citation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Citation not found")
    document = (
        await session.get(Document, citation.document_id)
        if citation.document_id is not None
        else None
    )
    if (
        document is None
        or document.knowledge_base_id != conversation.knowledge_base_id
        or document.status != DocumentStatus.READY
    ):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="The cited source is no longer available",
        )
    path = storage_path(get_settings().upload_dir, document.storage_key)
    if not await asyncio.to_thread(path.is_file):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="The cited source is no longer available",
        )
    return FileResponse(
        path,
        media_type=document.media_type,
        filename=document.original_name,
        content_disposition_type="inline",
    )
