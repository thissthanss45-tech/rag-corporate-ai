import logging

from sentence_transformers import SentenceTransformer
from app.config import settings
import numpy as np

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self):
        logger.info("Загружаю модель эмбеддингов: %s", settings.EMBEDDING_MODEL)
        self.model = SentenceTransformer(settings.EMBEDDING_MODEL, device='cpu')
        logger.info("Модель эмбеддингов загружена")

    def get_embedding(self, text: str) -> np.ndarray:
        """Превращает строку в вектор."""
        # normalize_embeddings=True важно для косинусного сходства (поиска)
        embedding = self.model.encode(text, normalize_embeddings=True)
        return np.array(embedding, dtype='float32')

    def get_embeddings(self, texts: list[str]) -> np.ndarray:
        """Converts a list of strings into an embedding matrix (for batches)."""
        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.array(embeddings, dtype='float32')