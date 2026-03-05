from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    APP_NAME: str = "rag-worker"
    LOG_LEVEL: str = "INFO"

    CELERY_BROKER_URL: str = "amqp://guest:guest@rabbitmq:5672//"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"

    CELERY_TASK_DEFAULT_QUEUE: str = "documents.ingest"
    CELERY_TASK_SOFT_TIME_LIMIT: int = Field(default=900, ge=30)
    CELERY_TASK_TIME_LIMIT: int = Field(default=1200, ge=60)
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = Field(default=1, ge=1)
    CELERY_TASK_ACKS_LATE: bool = True
    CELERY_TASK_REJECT_ON_WORKER_LOST: bool = True

    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_COLLECTION_NAME: str = "documents_chunks"
    VECTOR_SIZE: int = Field(default=384, ge=32)
    EMBEDDING_MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"
    SPARSE_EMBEDDING_MODEL_NAME: str = "Qdrant/bm25"

    CHUNK_SIZE: int = Field(default=1200, ge=100)
    CHUNK_OVERLAP: int = Field(default=200, ge=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )


settings = WorkerSettings()
