from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import QdrantClient, models

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChunkRow:
    file_name: str
    chunk_index: int
    text: str


class TextSplitter:
    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> list[str]:
        chunks: list[str] = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = start + self.chunk_size
            if end < text_len:
                last_newline = text.rfind("\n", start, end)
                if last_newline != -1 and last_newline > start + self.chunk_size // 2:
                    end = last_newline + 1
                else:
                    last_space = text.rfind(" ", start, end)
                    if last_space != -1 and last_space > start + self.chunk_size // 2:
                        end = last_space + 1

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            next_start = end - self.chunk_overlap
            start = next_start if next_start > start else end

        return chunks


def _to_sparse_parts(vector: Any) -> tuple[list[int], list[float]]:
    indices = getattr(vector, "indices", None)
    values = getattr(vector, "values", None)

    if indices is None and isinstance(vector, dict):
        indices = vector.get("indices")
        values = vector.get("values")

    if indices is None or values is None:
        raise ValueError("Unsupported sparse vector format")

    return [int(item) for item in indices], [float(item) for item in values]


def _load_existing_chunks(client: QdrantClient, collection_name: str) -> list[ChunkRow]:
    rows: list[ChunkRow] = []
    offset = None

    while True:
        points, next_offset = client.scroll(
            collection_name=collection_name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            text = str(payload.get("text", "")).strip()
            if not text:
                continue
            rows.append(
                ChunkRow(
                    file_name=str(payload.get("file_name", "unknown")),
                    chunk_index=int(payload.get("chunk_index", 0)),
                    text=text,
                )
            )

        if next_offset is None:
            break
        offset = next_offset

    return rows


def _reconstruct_documents(rows: list[ChunkRow]) -> dict[str, str]:
    grouped: dict[str, list[ChunkRow]] = defaultdict(list)
    for row in rows:
        grouped[row.file_name].append(row)

    reconstructed: dict[str, str] = {}
    for file_name, items in grouped.items():
        ordered = sorted(items, key=lambda item: item.chunk_index)
        reconstructed[file_name] = "\n".join(item.text for item in ordered)
    return reconstructed


def _recreate_collection(client: QdrantClient, collection_name: str, vector_size: int) -> None:
    client.delete_collection(collection_name=collection_name)
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "dense": models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(),
        },
    )


def _upsert_file_chunks(
    client: QdrantClient,
    collection_name: str,
    file_name: str,
    chunks: list[str],
    embedder: TextEmbedding,
    sparse_embedder: SparseTextEmbedding,
) -> int:
    if not chunks:
        return 0

    dense_vectors = [list(map(float, vector)) for vector in embedder.embed(chunks)]
    sparse_raw = list(sparse_embedder.embed(chunks))
    sparse_vectors = [_to_sparse_parts(item) for item in sparse_raw]

    points: list[models.PointStruct] = []
    for idx, (chunk_text, dense_vector, sparse_vector) in enumerate(zip(chunks, dense_vectors, sparse_vectors)):
        sparse_indices, sparse_values = sparse_vector
        points.append(
            models.PointStruct(
                id=str(uuid4()),
                vector={
                    "dense": dense_vector,
                    "sparse": models.SparseVector(indices=sparse_indices, values=sparse_values),
                },
                payload={
                    "file_name": file_name,
                    "chunk_index": idx,
                    "text": chunk_text,
                },
            )
        )

    client.upsert(collection_name=collection_name, points=points, wait=True)
    return len(points)


def run_reindex(chunk_size: int, chunk_overlap: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    client = QdrantClient(url=settings.QDRANT_URL)
    collection_name = settings.QDRANT_COLLECTION_NAME

    logger.info("loading existing chunks", extra={"collection": collection_name})
    rows = _load_existing_chunks(client=client, collection_name=collection_name)
    if not rows:
        raise RuntimeError("No chunks found in collection; nothing to reindex")

    docs = _reconstruct_documents(rows)
    logger.info("reconstructed documents", extra={"documents": len(docs), "chunks": len(rows)})

    splitter = TextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    embedder = TextEmbedding(model_name=settings.EMBEDDING_MODEL_NAME)
    sparse_embedder = SparseTextEmbedding(model_name=settings.SPARSE_EMBEDDING_MODEL_NAME)

    sample_chunks = splitter.split_text(next(iter(docs.values())))
    if not sample_chunks:
        raise RuntimeError("Unable to split reconstructed documents")

    sample_vector = list(embedder.embed([sample_chunks[0]]))[0]
    vector_size = len(sample_vector)

    logger.info("recreating collection", extra={"collection": collection_name, "vector_size": vector_size})
    _recreate_collection(client=client, collection_name=collection_name, vector_size=vector_size)

    total_points = 0
    for file_name, reconstructed_text in docs.items():
        chunks = splitter.split_text(reconstructed_text)
        total_points += _upsert_file_chunks(
            client=client,
            collection_name=collection_name,
            file_name=file_name,
            chunks=chunks,
            embedder=embedder,
            sparse_embedder=sparse_embedder,
        )

    logger.info(
        "reindex complete",
        extra={
            "documents": len(docs),
            "old_points": len(rows),
            "new_points": total_points,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reindex current Qdrant corpus with new chunking settings")
    parser.add_argument("--chunk-size", type=int, default=settings.CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=settings.CHUNK_OVERLAP)
    parser.add_argument("--confirm", action="store_true", help="Required flag because operation is destructive")
    args = parser.parse_args()

    if not args.confirm:
        raise SystemExit("Add --confirm to run destructive reindex")

    if args.chunk_overlap >= args.chunk_size:
        raise SystemExit("chunk-overlap must be smaller than chunk-size")

    run_reindex(chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap)
