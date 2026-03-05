import logging
import os
import pickle

import faiss
import numpy as np
from app.config import settings

logger = logging.getLogger(__name__)


class FAISSIndex:
    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self.index_path = os.path.join(settings.INDICES_PATH, "faiss_store.index")
        self.meta_path = os.path.join(settings.INDICES_PATH, "metadata.pkl")
        
        self.metadata = [] 
        self.index = None

    def create_adaptive_index(self, vector_count: int) -> None:
        """Creates an index based on data volume."""
        if vector_count < 2000:
            logger.info("Small dataset (%d vectors), using FlatL2 index", vector_count)
            self.index = faiss.IndexFlatL2(self.dimension)
        else:
            logger.info("Large dataset (%d vectors), using IVFPQ index", vector_count)
            quantizer_str = "IVF100,PQ32"
            self.index = faiss.index_factory(self.dimension, quantizer_str)

    def train(self, vectors: np.ndarray) -> None:
        """Trains the index if required."""
        if self.index is None:
            self.create_adaptive_index(len(vectors))

        if not self.index.is_trained:
            logger.info("Обучаю индекс...")
            self.index.train(vectors)
            logger.info("Обучение завершено")

    def add_vectors(self, vectors: np.ndarray, new_metadata: list) -> None:
        """Adds vectors to the index."""
        if self.index is None:
            self.create_adaptive_index(len(vectors))

        self.index.add(vectors)
        self.metadata.extend(new_metadata)
        logger.debug("Добавлено %d векторов. Всего в базе: %d", len(vectors), self.index.ntotal)

    def save(self) -> None:
        logger.info("Сохраняю индекс на диск...")
        if not os.path.exists(settings.INDICES_PATH):
            os.makedirs(settings.INDICES_PATH)

        if self.index:
            faiss.write_index(self.index, self.index_path)

        with open(self.meta_path, "wb") as f:
            pickle.dump(self.metadata, f)
        logger.info("Индекс успешно сохранён")

    def load(self) -> bool:
        if not os.path.exists(self.index_path):
            logger.warning("Индекс не найден: %s", self.index_path)
            return False

        logger.info("Загружаю индекс с диска...")
        self.index = faiss.read_index(self.index_path)
        with open(self.meta_path, "rb") as f:
            self.metadata = pickle.load(f)  # noqa: S301
        logger.info("Индекс загружен. Векторов: %d", self.index.ntotal)
        return True