from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client import models

from app.config import settings

logger = logging.getLogger(__name__)


class QdrantService:
    def __init__(self, url: str | None = None, collection_name: str | None = None) -> None:
        self._url = url or settings.QDRANT_URL
        self._collection_name = collection_name or settings.QDRANT_COLLECTION_NAME
        self._client = QdrantClient(url=self._url)

    @property
    def collection_name(self) -> str:
        return self._collection_name

    def ensure_collection(self, vector_size: int) -> None:
        collections = self._client.get_collections().collections
        collection_names = {item.name for item in collections}
        if self._collection_name in collection_names:
            return

        logger.info(
            "Creating Qdrant collection",
            extra={"collection": self._collection_name, "vector_size": vector_size},
        )
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config={
                "dense": models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(),
            },
        )

    def upsert_chunks(
        self,
        dense_vectors: list[list[float]],
        sparse_vectors: list[tuple[list[int], list[float]]],
        file_name: str,
        chunks: list[str],
    ) -> int:
        if len(dense_vectors) != len(chunks):
            raise ValueError("Dense vectors/chunks length mismatch")
        if len(sparse_vectors) != len(chunks):
            raise ValueError("Sparse vectors/chunks length mismatch")

        points: list[models.PointStruct] = []
        for idx, (dense_vector, sparse_vector, chunk_text) in enumerate(zip(dense_vectors, sparse_vectors, chunks)):
            sparse_indices, sparse_values = sparse_vector
            payload: dict[str, Any] = {
                "file_name": file_name,
                "chunk_index": idx,
                "text": chunk_text,
            }
            points.append(
                models.PointStruct(
                    id=str(uuid4()),
                    vector={
                        "dense": dense_vector,
                        "sparse": models.SparseVector(indices=sparse_indices, values=sparse_values),
                    },
                    payload=payload,
                )
            )

        self._client.upsert(collection_name=self._collection_name, points=points, wait=True)
        return len(points)
