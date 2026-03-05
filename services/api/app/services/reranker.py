"""Cross-encoder reranker для финальной сортировки retrieved chunks.

Pipeline:
    hybrid_search(top_N=30) → [RerankerService.rerank(top_K=9)] → generate_answer

Используется flashrank (ONNX, без torch) — lightweight cross-encoder.
Поддерживает graceful fallback: при unavailability reranker возвращает chunks as-is.
Флаг RERANKER_ENABLED=false отключает полностью.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.search_service import RetrievedChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedChunk:
    text: str
    source_file: str
    score: float          # original hybrid score
    rerank_score: float   # cross-encoder score (0.0 if reranker disabled)


class RerankerService:
    """Cross-encoder reranker на базе flashrank (ms-marco-MiniLM-L-12-v2 ONNX).

    Принимает query + список RetrievedChunk → возвращает reranked + top-K срез.
    При ошибке инициализации / недоступности — graceful fallback без rerank.
    """

    def __init__(self) -> None:
        self._ranker = None
        self._available = False

        if not settings.RERANKER_ENABLED:
            logger.info("⏸️  reranker disabled via RERANKER_ENABLED=false")
            return

        try:
            from flashrank import Ranker  # type: ignore[import-untyped]

            cache_dir = settings.RERANKER_CACHE_DIR or None
            self._ranker = Ranker(
                model_name=settings.RERANKER_MODEL_NAME,
                cache_dir=cache_dir,
            )
            self._available = True
            logger.info(
                "✅ reranker initialized",
                extra={"model": settings.RERANKER_MODEL_NAME},
            )
        except ImportError:
            logger.warning(
                "⚠️  flashrank not installed, reranker unavailable. "
                "Add `flashrank` to requirements.txt."
            )
        except Exception as exc:
            logger.warning(
                "⚠️  reranker init failed, fallback to hybrid-only",
                extra={"error": str(exc)},
            )

    @property
    def is_available(self) -> bool:
        return self._available

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RankedChunk]:
        """Ранжируем chunks по (query, text) cross-encoder score.

        Args:
            query:  исходный вопрос пользователя
            chunks: кандидаты из hybrid search (обычно 20–30 штук)
            top_k:  сколько вернуть (None = RERANKER_TOP_K из конфига)

        Returns:
            Список RankedChunk, отсортированный по rerank_score DESC.
            Если reranker недоступен — исходный порядок, rerank_score=0.0.
        """
        k = top_k if top_k is not None else settings.RERANKER_TOP_K

        if not chunks:
            return []

        if not self._available or self._ranker is None:
            logger.debug("↩️  reranker unavailable — returning hybrid-ordered chunks")
            return [
                RankedChunk(
                    text=c.text,
                    source_file=c.source_file,
                    score=c.score,
                    rerank_score=0.0,
                )
                for c in chunks[:k]
            ]

        t0 = time.perf_counter()
        try:
            from flashrank import RerankRequest  # type: ignore[import-untyped]

            passages = [{"id": i, "text": c.text} for i, c in enumerate(chunks)]
            request = RerankRequest(query=query, passages=passages)
            results = self._ranker.rerank(request)

            # results — список dict {"id": int, "score": float, "text": str, ...}
            id_to_chunk = {i: c for i, c in enumerate(chunks)}
            ranked: list[RankedChunk] = []
            for item in sorted(results, key=lambda r: r["score"], reverse=True):
                original = id_to_chunk[item["id"]]
                ranked.append(
                    RankedChunk(
                        text=original.text,
                        source_file=original.source_file,
                        score=original.score,
                        rerank_score=float(item["score"]),
                    )
                )

            elapsed = time.perf_counter() - t0
            logger.info(
                "✅ rerank done",
                extra={
                    "input_chunks": len(chunks),
                    "output_chunks": len(ranked[:k]),
                    "elapsed_ms": round(elapsed * 1000, 1),
                },
            )
            return ranked[:k]

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            logger.warning(
                "⚠️  rerank failed, fallback to hybrid order",
                extra={"error": str(exc), "elapsed_ms": round(elapsed * 1000, 1)},
            )
            return [
                RankedChunk(
                    text=c.text,
                    source_file=c.source_file,
                    score=c.score,
                    rerank_score=0.0,
                )
                for c in chunks[:k]
            ]


_reranker_instance: RerankerService | None = None


def get_reranker() -> RerankerService:
    """Singleton — инициализируется один раз при первом вызове."""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = RerankerService()
    return _reranker_instance
