import logging
from typing import Any

from app.retrieval.indexer import FAISSIndex
from app.retrieval.embeddings import Embedder

logger = logging.getLogger(__name__)


class SearchEngine:
    def __init__(self):
        self.indexer = FAISSIndex()
        self.embedder = Embedder()
        
        # Загружаем индекс сразу при старте
        loaded = self.indexer.load()
        if not loaded:
            logger.warning("Индекс не найден. Поиск не будет работать до индексации.")

    def reload_index(self) -> None:
        """Force-reload the index from disk."""
        logger.info("Reloading FAISS index from disk")
        self.indexer.load()
        logger.info("FAISS index reloaded")

    def search_with_meta(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """
        Ищет top_k самых похожих кусков текста и возвращает метаданные.
        """
        if not self.indexer.index:
            return []
        if top_k <= 0:
            return []

        top_k = min(top_k, self.indexer.index.ntotal)
        if top_k == 0:
            return []

        query_vector = self.embedder.get_embedding(query)
        query_vector = query_vector.reshape(1, -1)

        distances, indices = self.indexer.index.search(query_vector, top_k)

        results: list[dict[str, Any]] = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx == -1 or idx >= len(self.indexer.metadata):
                continue

            item = self.indexer.metadata[idx]
            results.append(
                {
                    "text": item.get("text", ""),
                    "source": item.get("source", "unknown"),
                    "chunk_id": item.get("chunk_id", str(idx)),
                    "distance": float(distance),
                    "score": float(1.0 / (1.0 + distance)),
                }
            )

        return results

    def search(self, query: str, top_k: int = 3) -> list[str]:
        """Returns top_k most similar text chunks."""
        return [item["text"] for item in self.search_with_meta(query, top_k=top_k)]