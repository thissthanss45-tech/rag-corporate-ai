from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "RAG Core API"
    APP_ENV: str = "dev"
    LOG_LEVEL: str = "INFO"
    API_PREFIX: str = "/api/v1"

    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672//"
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "amqp://guest:guest@rabbitmq:5672//"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"
    CELERY_DOCUMENT_TASK_NAME: str = "app.tasks.process_document_task"
    CELERY_TASK_DEFAULT_QUEUE: str = "documents.ingest"

    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_COLLECTION_NAME: str = "documents_chunks"
    EMBEDDING_MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"
    SPARSE_EMBEDDING_MODEL_NAME: str = "Qdrant/bm25"
    RETRIEVAL_LIMIT: int = Field(default=30, ge=1)

    GROQ_API_KEY: str = ""
    GROQ_MODEL_NAME: str = "llama-3.3-70b-versatile"
    GROQ_TRANSCRIBE_MODEL_NAME: str = "whisper-large-v3"
    GROQ_REQUEST_TIMEOUT_SEC: int = Field(default=35, ge=5)
    GROQ_MAX_RETRIES: int = Field(default=0, ge=0, le=5)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL_NAME: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_REQUEST_TIMEOUT_SEC: int = Field(default=70, ge=10)
    DEEPSEEK_MAX_RETRIES: int = Field(default=1, ge=1, le=10)
    DEEPSEEK_RETRY_BACKOFF_SEC: float = Field(default=1.5, ge=0.1)
    DEEPSEEK_FALLBACK_TO_LLAMA: bool = True
    LLAMA_FALLBACK_TO_DEEPSEEK: bool = True
    ASK_MAX_RETRIES: int = Field(default=2, ge=1, le=5)
    ASK_RETRY_BACKOFF_SEC: float = Field(default=0.8, ge=0.1)
    ASK_MIN_RETRIEVAL_CHUNKS: int = Field(default=12, ge=1, le=40)
    ASK_CONTEXT_MAX_CHUNKS: int = Field(default=9, ge=3, le=30)
    ASK_CONTEXT_MAX_CHARS: int = Field(default=14000, ge=2000, le=60000)
    ASK_MIN_CONTEXT_COVERAGE: float = Field(default=0.15, ge=0.0, le=1.0)
    ASK_ENABLE_ANSWER_VERIFICATION: bool = True
    ASK_OUTPUT_MODE: str = "standard"
    ASK_STRICT_GROUNDED_MODE: bool = True
    ASK_STRICT_MIN_SENTENCE_SUPPORT: float = Field(default=0.6, ge=0.3, le=1.0)
    ASK_STRICT_MIN_CONTEXT_CHARS: int = Field(default=500, ge=0, le=5000)
    ASK_MAX_CONCURRENT_GENERATIONS: int = Field(default=8, ge=1, le=64)
    ASK_QUEUE_TIMEOUT_SEC: float = Field(default=8.0, ge=0.1, le=30.0)
    ASK_FALLBACK_ANSWER: str = (
        "Сервис сейчас перегружен. Я не успел подготовить точный ответ. "
        "Повтори запрос через 20–40 секунд или сократи вопрос."
    )

    # ── Reranker (cross-encoder) ────────────────────────────────────────
    # Включить cross-encoder reranking после hybrid search.
    # При RERANKER_ENABLED=false — hybrid-only, без изменений поведения.
    RERANKER_ENABLED: bool = True
    # flashrank model name (ms-marco-MiniLM-L-12-v2 — лёгкий ONNX, ~22MB)
    RERANKER_MODEL_NAME: str = "ms-marco-MiniLM-L-12-v2"
    # Сколько chunks оставить после rerank (финальный context window)
    RERANKER_TOP_K: int = Field(default=9, ge=1, le=30)
    # Сколько candidates забрать из hybrid search перед rerank
    RERANKER_PREFETCH_LIMIT: int = Field(default=30, ge=10, le=100)
    # Директория кэша моделей (пусто = ~/.cache по умолчанию)
    RERANKER_CACHE_DIR: str = ""

    UPLOAD_DIR: str = "/shared/uploads"
    MAX_UPLOAD_SIZE_MB: int = Field(default=1024, ge=1)
    MAX_AUDIO_SIZE_MB: int = Field(default=25, ge=1)
    MIN_FREE_DISK_MB: int = Field(default=1024, ge=0)
    RATE_LIMIT_PER_MINUTE: int = Field(default=120, ge=0)
    SERVICE_AUTH_TOKEN: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")


settings = Settings()
