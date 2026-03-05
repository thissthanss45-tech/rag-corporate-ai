from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Обязательные ключи
    GROQ_API_KEY: str
    TELEGRAM_BOT_TOKEN: str
    OWNER_ID: int | None = None
    ADMIN_IDS: str = ""

    # Настройки путей
    DATA_PATH: str = "data"
    INDICES_PATH: str = "indices"
    MAX_UPLOAD_SIZE_MB: int = Field(default=20, ge=1)
    TOP_K: int = Field(default=7, ge=1)
    INCLUDE_SOURCES_IN_ANSWER: bool = True
    MAX_SOURCES_IN_ANSWER: int = Field(default=3, ge=1)
    EVAL_DATASET_PATH: str = "evaluation/dataset.jsonl"
    EVAL_MIN_RECALL_AT_K: float = Field(default=0.60, ge=0.0, le=1.0)
    EVAL_FAIL_ON_MISSING_DATASET: bool = False
    MAX_CONTEXT_CHARS_PER_CHUNK: int = Field(default=1200, ge=200)
    CONTEXT_CHUNKS_FOR_PROMPT: int = Field(default=6, ge=1)
    CONTEXT_INJECTION_GUARD: bool = True

    RETENTION_DAYS: int = Field(default=90, ge=1)
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    APP_ENV: str = "production"
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.0, ge=0.0, le=1.0)

    METRICS_ENABLED: bool = False
    METRICS_PORT: int = Field(default=9108, ge=1, le=65535)

    HEARTBEAT_FILE: str = "/tmp/rag_bot_heartbeat"
    HEARTBEAT_INTERVAL_SEC: int = Field(default=15, ge=5)
    
    # Параметры моделей
    MODEL_NAME: str = "llama-3.3-70b-versatile"
    EMBEDDING_MODEL: str = "paraphrase-multilingual-MiniLM-L12-v2"

    # Чтение .env
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

settings = Settings()

if __name__ == "__main__":
    print("✅ Настройки загружены!")
    print(f"🤖 LLM Модель: {settings.MODEL_NAME}")
    print(f"🧠 Embeddings: {settings.EMBEDDING_MODEL} (Локально)")