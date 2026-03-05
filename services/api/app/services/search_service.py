from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models
from fastembed import SparseTextEmbedding, TextEmbedding

from app.core.config import settings

logger = logging.getLogger(__name__)


def _decode_file_name(raw_name: str) -> str:
    value = raw_name
    for _ in range(3):
        decoded = urllib.parse.unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    source_file: str
    score: float


class SearchService:
    def __init__(
        self,
        qdrant_url: str,
        collection_name: str,
        embedding_model_name: str,
        sparse_embedding_model_name: str,
    ) -> None:
        self._qdrant_client = QdrantClient(url=qdrant_url)
        self._collection_name = collection_name
        self._embedder = TextEmbedding(model_name=embedding_model_name)
        self._sparse_embedder = SparseTextEmbedding(model_name=sparse_embedding_model_name)

        logger.info(
            "🔍 search service initialized",
            extra={
                "qdrant_url": qdrant_url,
                "collection": collection_name,
                "embedding_model": embedding_model_name,
                "sparse_embedding_model": sparse_embedding_model_name,
            },
        )

    def search(self, question: str, limit: int = 30) -> list[RetrievedChunk]:
        logger.info("🔍 hybrid search started", extra={"query_length": len(question), "limit": limit})

        query_variants = self._build_query_variants(question)
        logger.info("🔀 retrieval query variants", extra={"variants_count": len(query_variants)})
        results = self._search_with_query_variants(query_variants=query_variants, limit=limit)

        chunks: list[RetrievedChunk] = []
        for item in results:
            payload = item.payload or {}
            text = str(payload.get("text", "")).strip()
            source_file_raw = str(payload.get("file_name", "unknown"))
            source_file = _decode_file_name(source_file_raw)
            if not text:
                continue
            chunks.append(
                RetrievedChunk(
                    text=text,
                    source_file=source_file,
                    score=float(item.score),
                )
            )

        logger.info("🔍 hybrid context ready", extra={"chunks": len(chunks)})
        return chunks

    def _search_with_query_variants(self, query_variants: list[str], limit: int) -> list[Any]:
        combined: dict[str, dict[str, Any]] = {}

        for variant_index, query in enumerate(query_variants):
            points = self._search_single_query(question=query, limit=max(limit, 24))
            for rank, point in enumerate(points, start=1):
                point_id = str(point.id)
                record = combined.setdefault(
                    point_id,
                    {
                        "point": point,
                        "score": 0.0,
                    },
                )
                rank_score = 1.0 / (40 + rank)
                variant_penalty = 1.0 if variant_index == 0 else 0.9
                raw_score = float(point.score)
                record["score"] += (rank_score + raw_score) * variant_penalty

        ranked = sorted(combined.values(), key=lambda item: item["score"], reverse=True)
        return [item["point"] for item in ranked[:limit]]

    def _search_single_query(self, question: str, limit: int) -> list[Any]:
        dense_raw = list(self._embedder.embed([question]))
        dense_vector = [float(item) for item in dense_raw[0]]

        sparse_raw = list(self._sparse_embedder.embed([question]))
        sparse_indices, sparse_values = self._to_sparse_parts(sparse_raw[0])
        sparse_vector = models.SparseVector(indices=sparse_indices, values=sparse_values)

        try:
            response = self._qdrant_client.query_points(
                collection_name=self._collection_name,
                prefetch=[
                    models.Prefetch(query=dense_vector, using="dense", limit=limit),
                    models.Prefetch(query=sparse_vector, using="sparse", limit=limit),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
            return response.points
        except Exception as exc:
            logger.warning("↩️ RRF unavailable, fallback to weighted fusion", extra={"error": str(exc)})
            return self._weighted_fusion_search(
                dense_vector=dense_vector,
                sparse_vector=sparse_vector,
                limit=limit,
            )

    @staticmethod
    def _build_query_variants(question: str) -> list[str]:
        variants: list[str] = []

        raw = question.strip()
        if raw:
            variants.append(raw)

        normalized = SearchService._normalize_text(raw)
        if normalized and normalized != raw:
            variants.append(normalized)

        keywords = SearchService._extract_keywords(raw)
        if keywords:
            keyword_query = " ".join(keywords)
            if keyword_query not in variants:
                variants.append(keyword_query)

        return variants[:3]

    @staticmethod
    def _normalize_text(text: str) -> str:
        cleaned = re.sub(r"[^\w\sа-яА-ЯёЁ-]", " ", text)
        return " ".join(cleaned.split())

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        stop_words = {
            "кто", "что", "где", "когда", "почему", "зачем", "какой", "какая", "какие", "это", "как", "или",
            "для", "про", "под", "над", "при", "без", "если", "чтобы", "также", "ещё", "ли", "в", "на", "с",
            "по", "из", "о", "об", "у", "к", "и", "а", "но", "не", "нет", "да",
        }
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{4,}", text.lower())
        unique: list[str] = []
        for word in words:
            if word in stop_words:
                continue
            if word not in unique:
                unique.append(word)
        return unique[:8]

    def is_available(self) -> bool:
        try:
            self._qdrant_client.get_collections()
            return True
        except Exception:
            return False

    def _weighted_fusion_search(
        self,
        dense_vector: list[float],
        sparse_vector: models.SparseVector,
        limit: int,
    ) -> list[Any]:
        dense_response = self._qdrant_client.query_points(
            collection_name=self._collection_name,
            query=dense_vector,
            using="dense",
            limit=limit,
            with_payload=True,
        )
        sparse_response = self._qdrant_client.query_points(
            collection_name=self._collection_name,
            query=sparse_vector,
            using="sparse",
            limit=limit,
            with_payload=True,
        )

        dense_points = dense_response.points
        sparse_points = sparse_response.points

        return self._merge_weighted(dense_points=dense_points, sparse_points=sparse_points, limit=limit)

    @staticmethod
    def _merge_weighted(
        dense_points: list[Any],
        sparse_points: list[Any],
        limit: int,
        dense_weight: float = 0.65,
        sparse_weight: float = 0.35,
    ) -> list[Any]:
        merged: dict[str, dict[str, Any]] = {}

        dense_scores = [float(point.score) for point in dense_points]
        sparse_scores = [float(point.score) for point in sparse_points]

        norm_dense = SearchService._normalize_scores(dense_scores)
        norm_sparse = SearchService._normalize_scores(sparse_scores)

        for point, score in zip(dense_points, norm_dense):
            point_id = str(point.id)
            merged.setdefault(point_id, {"point": point, "score": 0.0})
            merged[point_id]["score"] += dense_weight * score

        for point, score in zip(sparse_points, norm_sparse):
            point_id = str(point.id)
            merged.setdefault(point_id, {"point": point, "score": 0.0})
            merged[point_id]["score"] += sparse_weight * score

        ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)
        return [item["point"] for item in ranked[:limit]]

    @staticmethod
    def _normalize_scores(scores: list[float]) -> list[float]:
        if not scores:
            return []
        min_score = min(scores)
        max_score = max(scores)
        if max_score == min_score:
            return [1.0 for _ in scores]
        return [(score - min_score) / (max_score - min_score) for score in scores]

    @staticmethod
    def _to_sparse_parts(vector: Any) -> tuple[list[int], list[float]]:
        indices = getattr(vector, "indices", None)
        values = getattr(vector, "values", None)

        if indices is None and isinstance(vector, dict):
            indices = vector.get("indices")
            values = vector.get("values")

        if indices is None or values is None:
            raise ValueError("Unsupported sparse query vector format")

        return [int(item) for item in indices], [float(item) for item in values]


def build_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return ""
    return "\n\n".join([f"[ИСТОЧНИК: {chunk.source_file}] {chunk.text}" for chunk in chunks])


def create_search_service() -> SearchService:
    return SearchService(
        qdrant_url=settings.QDRANT_URL,
        collection_name=settings.QDRANT_COLLECTION_NAME,
        embedding_model_name=settings.EMBEDDING_MODEL_NAME,
        sparse_embedding_model_name=settings.SPARSE_EMBEDDING_MODEL_NAME,
    )
