import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import get_settings
from app.database import session_factory
from app.ingestion.embeddings import embed_texts
from app.ingestion.errors import EmbeddingProviderError
from app.ingestion.tokenization import create_tokenizer
from app.models import Citation, Conversation, Message, MessageStatus
from app.observability import emit_observation
from app.rag.citations import public_locator
from app.rag.generation import GenerationProviderError, complete_json, stream_json
from app.rag.grounding import (
    GroundingValidationError,
    HistoryMessage,
    RenderedAnswer,
    grounded_messages,
    grounded_repair_messages,
    parse_rewrite,
    rewrite_messages,
    validate_and_render,
)
from app.rag.query_classification import (
    DeterministicQueryClassifier,
    QueryClassifier,
    QueryKind,
)
from app.rag.retrieval import Evidence, retrieve_evidence
from app.rag.summary_retrieval import SummaryContext, retrieve_summary_context

logger = logging.getLogger(__name__)

INSUFFICIENT_EVIDENCE = (
    "I cannot answer reliably from the available Documents. No sufficiently relevant "
    "evidence was retrieved from Ready Documents in this Knowledge Base."
)
UNVERIFIABLE_ANSWER = (
    "I cannot answer reliably because the generated response could not be verified against "
    "the retrieved Documents. Try rephrasing the question or add more specific Documents."
)
FAILED_ANSWER = "The answer could not be generated. Please try again later."

FOLLOW_UP_PREFIX = re.compile(
    r"^\s*(?:and\b|also\b|what about\b|how about\b|what else\b)", re.IGNORECASE
)
FOLLOW_UP_REFERENCE = re.compile(
    r"\b(?:it|its|this|that|these|those|they|them|same|former|latter)\b",
    re.IGNORECASE,
)
query_classifier: QueryClassifier = DeterministicQueryClassifier()


def sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def citation_payload(citation: Citation, conversation_id: uuid.UUID) -> dict[str, object]:
    return {
        "id": str(citation.id),
        "document_name": citation.document_name,
        "locator": public_locator(citation.locator),
        "excerpt": citation.excerpt,
        "retrieval_rank": citation.retrieval_rank,
        "retrieval_score": citation.retrieval_score,
        "source_available": citation.document_id is not None,
        "source_url": (
            f"/api/v1/conversations/{conversation_id}/citations/{citation.id}/source"
            if citation.document_id is not None
            else None
        ),
    }


async def _load_turn(
    conversation_id: uuid.UUID,
    user_message_id: uuid.UUID,
) -> tuple[Conversation, Message, list[HistoryMessage]]:
    settings = get_settings()
    async with session_factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        user_message = await session.get(Message, user_message_id)
        if conversation is None or user_message is None:
            raise RuntimeError("Turn records disappeared before generation")
        result = await session.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.sequence < user_message.sequence,
                Message.status == MessageStatus.COMPLETE,
            )
            .order_by(Message.sequence.desc())
            .limit(settings.recent_history_messages)
        )
        history = [
            HistoryMessage(role=item.role, content=item.content)
            for item in reversed(result.all())
        ]
        return conversation, user_message, history


async def _persist_complete(
    assistant_message_id: uuid.UUID,
    conversation_id: uuid.UUID,
    content: str,
    evidence: list[Evidence],
    *,
    outcome: str,
    usage: dict[str, int] | None = None,
    query_kind: str | None = None,
    summary_context_count: int = 0,
) -> list[Citation]:
    settings = get_settings()
    citations = [
        Citation(
            id=uuid.uuid4(),
            message_id=assistant_message_id,
            document_id=item.document_id,
            document_name=item.document_name,
            locator=public_locator(item.locator),
            excerpt=item.text[:1_500],
            retrieval_rank=item.retrieval_rank,
            retrieval_score=item.retrieval_score,
        )
        for item in evidence
    ]
    async with session_factory() as session:
        message = await session.get(Message, assistant_message_id, with_for_update=True)
        conversation = await session.get(Conversation, conversation_id)
        if message is None or conversation is None:
            raise RuntimeError("Turn records disappeared while saving the answer")
        message.content = content
        message.status = MessageStatus.COMPLETE
        message.model_metadata = {
            "outcome": outcome,
            "embedding_model": settings.embedding_model_id,
            "generation_model": settings.generation_model_id,
            "evidence_count": len(evidence),
            "query_kind": query_kind,
            "summary_context_count": summary_context_count,
            "usage": usage or {},
        }
        conversation.updated_at = datetime.now(UTC)
        session.add_all(citations)
        await session.commit()
    return citations


async def _mark_failed(assistant_message_id: uuid.UUID, safe_reason: str) -> None:
    async with session_factory() as session:
        message = await session.get(Message, assistant_message_id, with_for_update=True)
        if message is None or message.status != MessageStatus.STREAMING:
            return
        message.content = FAILED_ANSWER
        message.status = MessageStatus.FAILED
        message.model_metadata = {"outcome": "generation_failed", "reason": safe_reason}
        await session.commit()


async def _resolve_query(
    question: str,
    history: list[HistoryMessage],
) -> tuple[str, dict[str, int]]:
    is_short_follow_up = len(question.split()) <= 20 and (
        FOLLOW_UP_PREFIX.search(question) is not None
        or FOLLOW_UP_REFERENCE.search(question) is not None
    )
    if not history or not is_short_follow_up:
        return question, {}
    try:
        rewrite = await complete_json(rewrite_messages(question, history))
        return parse_rewrite(rewrite.payload), rewrite.usage
    except (GenerationProviderError, GroundingValidationError) as error:
        logger.warning("Using the original question after query rewrite failed: %s", error)
        return question, {}


def _merge_usage(*items: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            merged[key] = merged.get(key, 0) + value
    return merged


async def _generate_grounded_answer(
    question: str,
    history: list[HistoryMessage],
    evidence: list[Evidence],
    summary_context: list[SummaryContext] | None = None,
) -> tuple[RenderedAnswer, dict[str, int]]:
    generation = await stream_json(
        grounded_messages(question, history, evidence, summary_context)
    )
    try:
        return validate_and_render(generation.payload, evidence), generation.usage
    except GroundingValidationError as error:
        logger.warning("Retrying a generated answer rejected by grounding validation: %s", error)
        rejection_reason = str(error)

    try:
        repair = await complete_json(
            grounded_repair_messages(
                question,
                evidence,
                rejection_reason,
                summary_context,
            )
        )
        rendered = validate_and_render(repair.payload, evidence)
    except (GenerationProviderError, GroundingValidationError) as repair_error:
        logger.warning("Rejected unverifiable generated answer after retry: %s", repair_error)
        return (
            RenderedAnswer(
                content=UNVERIFIABLE_ANSWER,
                used_evidence=[],
                outcome="validation_rejected",
            ),
            generation.usage,
        )
    return rendered, _merge_usage(generation.usage, repair.usage)


async def turn_events(
    conversation_id: uuid.UUID,
    user_message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
) -> AsyncIterator[str]:
    turn_stage = "loading"
    try:
        yield sse_event("status", {"stage": "retrieving"})
        turn_stage = "retrieving"
        conversation, user_message, history = await _load_turn(
            conversation_id, user_message_id
        )
        query, rewrite_usage = await _resolve_query(user_message.content, history)
        classification = query_classifier.classify(query)
        settings = get_settings()
        query_vector = (await embed_texts([query]))[0]
        summary_context: list[SummaryContext] = []
        async with session_factory() as session:
            evidence, sufficient = await retrieve_evidence(
                session,
                conversation.knowledge_base_id,
                query,
                query_vector,
                settings,
            )
            if (
                classification.kind == QueryKind.BROAD
                and settings.hierarchical_retrieval_enabled
            ):
                summary_context = await retrieve_summary_context(
                    session,
                    conversation.knowledge_base_id,
                    query,
                    query_vector,
                    settings,
                    create_tokenizer(settings.chunk_tokenizer),
                )

        if not sufficient:
            citations = await _persist_complete(
                assistant_message_id,
                conversation_id,
                INSUFFICIENT_EVIDENCE,
                [],
                outcome="insufficient_evidence",
                usage=rewrite_usage,
                query_kind=classification.kind,
                summary_context_count=len(summary_context),
            )
            yield sse_event(
                "start", {"message_id": str(assistant_message_id), "status": "complete"}
            )
            yield sse_event("delta", {"content": INSUFFICIENT_EVIDENCE})
            yield sse_event("citations", {"items": citations})
            yield sse_event("done", {"outcome": "insufficient_evidence"})
            return

        yield sse_event("status", {"stage": "generating"})
        turn_stage = "generating"
        rendered, generation_usage = await _generate_grounded_answer(
            user_message.content,
            history,
            evidence,
            summary_context,
        )
        yield sse_event("status", {"stage": "validating"})
        turn_stage = "validating"

        citations = await _persist_complete(
            assistant_message_id,
            conversation_id,
            rendered.content,
            rendered.used_evidence,
            outcome=rendered.outcome,
            usage=_merge_usage(rewrite_usage, generation_usage),
            query_kind=classification.kind,
            summary_context_count=len(summary_context),
        )
        yield sse_event(
            "start", {"message_id": str(assistant_message_id), "status": "complete"}
        )
        for start in range(0, len(rendered.content), 120):
            yield sse_event("delta", {"content": rendered.content[start : start + 120]})
        yield sse_event(
            "citations",
            {"items": [citation_payload(item, conversation_id) for item in citations]},
        )
        yield sse_event("done", {"outcome": rendered.outcome})
    except asyncio.CancelledError:
        emit_observation(
            logger,
            "rag_turn_failure",
            conversation_id=str(conversation_id),
            terminal_stage=turn_stage,
            failure_category="client_disconnected",
        )
        await asyncio.shield(_mark_failed(assistant_message_id, "client_disconnected"))
        raise
    except (EmbeddingProviderError, GenerationProviderError, GroundingValidationError) as error:
        emit_observation(
            logger,
            "rag_turn_failure",
            conversation_id=str(conversation_id),
            terminal_stage=turn_stage,
            failure_category="provider_or_grounding_error",
        )
        logger.warning("RAG turn failed for conversation %s: %s", conversation_id, error)
        await _mark_failed(assistant_message_id, "provider_or_grounding_error")
        yield sse_event("error", {"message": FAILED_ANSWER})
    except Exception:
        emit_observation(
            logger,
            "rag_turn_failure",
            conversation_id=str(conversation_id),
            terminal_stage=turn_stage,
            failure_category="unexpected_error",
        )
        logger.exception("Unexpected RAG turn failure for conversation %s", conversation_id)
        await _mark_failed(assistant_message_id, "unexpected_error")
        yield sse_event("error", {"message": FAILED_ANSWER})
