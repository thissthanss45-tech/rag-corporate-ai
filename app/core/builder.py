import logging
import numpy as np

from app.core.parser import read_data_folder
from app.core.splitter import TextSplitter
from app.retrieval.embeddings import Embedder
from app.retrieval.indexer import FAISSIndex
from app.config import settings

logger = logging.getLogger(__name__)


def build_knowledge_base():
    """
    Основная функция пересборки базы знаний.
    """
    splitter = TextSplitter(chunk_size=800, chunk_overlap=100)
    embedder = Embedder()
    indexer = FAISSIndex()
    
    buffer_vectors = []
    buffer_meta = []
    
    TRAIN_THRESHOLD = 2000
    BATCH_SIZE = 500
    
    total_chunks = 0
    is_trained = False

    logger.info("Начинаю индексацию папки: %s", settings.DATA_PATH)
    
    file_generator = read_data_folder(settings.DATA_PATH)
    
    for filename, text in file_generator:
        chunks = splitter.split_text(text)
        if not chunks:
            continue
            
        # vectors = embedder.get_embeddings(chunks) 
        # Чтобы не спамить в консоль бота полосками прогресса, можно убрать show_progress_bar внутри embedder, 
        # но пока оставим как есть.
        vectors = embedder.get_embeddings(chunks)
        
        for chunk_index, chunk in enumerate(chunks):
            buffer_meta.append(
                {
                    "source": filename,
                    "text": chunk,
                    "chunk_id": f"{filename}:{chunk_index}",
                }
            )
        
        if len(buffer_vectors) == 0:
            buffer_vectors = vectors
        else:
            buffer_vectors = np.concatenate([buffer_vectors, vectors])
            
        # Обучение
        if not is_trained and len(buffer_vectors) >= TRAIN_THRESHOLD:
            indexer.train(buffer_vectors)
            is_trained = True
            indexer.add_vectors(buffer_vectors, buffer_meta)
            buffer_vectors = []
            buffer_meta = []
            
        elif is_trained and len(buffer_vectors) >= BATCH_SIZE:
            indexer.add_vectors(buffer_vectors, buffer_meta)
            buffer_vectors = []
            buffer_meta = []
            
        total_chunks += len(chunks)

    # Хвосты
    if len(buffer_vectors) > 0:
        if not is_trained:
            indexer.train(buffer_vectors)
        indexer.add_vectors(buffer_vectors, buffer_meta)

    indexer.save()
    logger.info("Индексация завершена. Всего фрагментов: %d", total_chunks)
    return total_chunks

if __name__ == "__main__":
    build_knowledge_base()