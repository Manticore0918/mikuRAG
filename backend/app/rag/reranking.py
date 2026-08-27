import asyncio
import logging
import re
import threading
from dataclasses import replace
from time import perf_counter
from typing import Protocol

from app.config import Settings
from app.rag.retrieval_types import Candidate

logger = logging.getLogger(__name__)

_TERM = re.compile(r"\w+", re.UNICODE)


class RerankerProviderError(RuntimeError):
    """Raised when a reranker provider cannot run; callers fall back to fused order."""


class Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[Candidate],
    ) -> list[Candidate]: ...


class DeterministicReranker:
    """Lexical-overlap reranker kept as the test and fallback implementation."""

    provider_name = "deterministic"
    model_name: str | None = None
    version = "deterministic_v1"

    async def rerank(
        self,
        query: str,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        query_terms = {term.casefold() for term in _TERM.findall(query)}
        reranked: list[Candidate] = []
        for candidate in candidates:
            searchable = " ".join([*candidate.heading_path, candidate.text])
            candidate_terms = {term.casefold() for term in _TERM.findall(searchable)}
            lexical_overlap = (
                len(query_terms & candidate_terms) / len(query_terms) if query_terms else 0.0
            )
            semantic = max(candidate.semantic_similarity or 0.0, 0.0)
            lexical = max(candidate.lexical_score or 0.0, 0.0)
            score = (
                candidate.fused_score
                + lexical_overlap * 0.04
                + semantic * 0.01
                + min(lexical, 1.0) * 0.005
            )
            reranked.append(replace(candidate, rerank_score=score))
        return sorted(
            reranked,
            key=lambda candidate: (
                candidate.effective_score,
                candidate.fused_score,
                candidate.semantic_similarity or -2.0,
                candidate.lexical_score or -1.0,
            ),
            reverse=True,
        )


class CrossEncoderReranker:
    """Local cross-encoder reranker behind the Reranker interface.

    The sentence-transformers model is loaded lazily on the first rerank so the
    stable retrieval path works without the optional `rerank` extra installed.
    Inference runs in a worker thread under a concurrency semaphore with a
    wall-clock timeout; any failure raises `RerankerProviderError` so the caller
    falls back to the fused order.
    """

    provider_name = "cross_encoder"

    def __init__(
        self,
        *,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        batch_size: int = 8,
        timeout_seconds: float = 15.0,
        max_concurrency: int = 1,
        encoder_factory: object | None = None,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.timeout_seconds = timeout_seconds
        self.max_concurrency = max_concurrency
        self.version = "cross_encoder_v1"
        self._encoder: object | None = None
        self._encoder_factory = encoder_factory
        self._load_attempted = False
        self._semaphore: asyncio.Semaphore | None = None
        self._load_lock = threading.Lock()

    def _load(self) -> object | None:
        with self._load_lock:
            if self._load_attempted:
                return self._encoder
            self._load_attempted = True
            try:
                if self._encoder_factory is not None:
                    self._encoder = self._encoder_factory()
                else:
                    from sentence_transformers import CrossEncoder

                    self._encoder = CrossEncoder(self.model_name)
            except Exception as error:  # pragma: no cover - optional dependency
                self._encoder = None
                logger.warning(
                    "Cross-encoder reranker unavailable (%s: %s); falling back",
                    type(error).__name__,
                    error,
                )
            return self._encoder

    def _predict(self, pairs: list[tuple[str, str]]) -> object:
        encoder = self._load()
        if encoder is None:
            raise RerankerProviderError("cross-encoder model is unavailable")
        return encoder.predict(
            pairs,
            batch_size=self.batch_size,
            show_progress_bar=False,
        )

    async def rerank(
        self,
        query: str,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        if not candidates:
            return candidates
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
        pairs = [(query, candidate.text) for candidate in candidates]
        started = perf_counter()
        acquired = False
        worker: asyncio.Task | None = None
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self.timeout_seconds
            )
            acquired = True
            remaining = max(
                0.001,
                self.timeout_seconds - (perf_counter() - started),
            )
            worker = asyncio.create_task(asyncio.to_thread(self._predict, pairs))
            # Shield the thread-backed Task so a wall-clock timeout returns to
            # the caller without pretending the underlying inference stopped.
            scores = await asyncio.wait_for(asyncio.shield(worker), timeout=remaining)
        except Exception as error:
            if acquired and worker is not None and not worker.done():
                # Keep the concurrency slot occupied until the uncancellable
                # worker thread really exits.
                worker.add_done_callback(lambda _: self._semaphore.release())
                acquired = False
            if isinstance(error, TimeoutError):
                logger.warning(
                    "Cross-encoder rerank exceeded %ss; falling back to fused order",
                    self.timeout_seconds,
                )
            else:
                logger.warning(
                    "Cross-encoder rerank failed (%s: %s); falling back to fused order",
                    type(error).__name__,
                    error,
                )
            raise RerankerProviderError("cross-encoder rerank failed") from error
        finally:
            if acquired:
                self._semaphore.release()
        reranked = [
            replace(candidate, rerank_score=float(score))
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        return sorted(
            reranked,
            key=lambda candidate: (
                candidate.effective_score,
                candidate.fused_score,
                candidate.semantic_similarity or -2.0,
                candidate.lexical_score or -1.0,
            ),
            reverse=True,
        )


def build_reranker(provider: str | None, settings: Settings) -> Reranker:
    """Return a reranker for ``provider``, defaulting to the configured provider.

    Passing ``None`` selects ``settings.reranker_provider`` so the stable path
    and the evaluation runner share one code path. The cross-encoder provider is
    only selected when requested; because the model loads lazily, selecting it
    never breaks the stable path when the optional dependency or model download
    is missing (that surfaces as a `RerankerProviderError` at rerank time and
    the caller falls back).
    """
    provider = provider or settings.reranker_provider
    if provider == "cross_encoder":
        return CrossEncoderReranker(
            model_name=settings.reranker_model,
            batch_size=settings.reranker_batch_size,
            timeout_seconds=settings.reranker_timeout_seconds,
            max_concurrency=settings.reranker_max_concurrency,
        )
    return DeterministicReranker()


def build_default_reranker(settings: Settings) -> Reranker:
    """Return a process-scoped configured provider, avoiding per-turn model loads."""
    key = (
        settings.reranker_provider,
        settings.reranker_model,
        settings.reranker_batch_size,
        settings.reranker_timeout_seconds,
        settings.reranker_max_concurrency,
    )
    with _DEFAULT_RERANKERS_LOCK:
        reranker = _DEFAULT_RERANKERS.get(key)
        if reranker is None:
            reranker = build_reranker(None, settings)
            _DEFAULT_RERANKERS[key] = reranker
        return reranker


_DEFAULT_RERANKERS: dict[tuple[object, ...], Reranker] = {}
_DEFAULT_RERANKERS_LOCK = threading.Lock()
