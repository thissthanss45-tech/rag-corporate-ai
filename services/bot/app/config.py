from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    APP_NAME: str = "rag-telegram-bot"
    LOG_LEVEL: str = "INFO"

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_API_BASE: str = "http://telegram-bot-api:8081"
    TELEGRAM_LOCAL: bool = True
    API_BASE_URL: str = "http://api:8000"
    API_PREFIX: str = "/api/v1"
    API_SERVICE_TOKEN: str = ""
    API_TIMEOUT_SECONDS: int = Field(default=90, ge=3)
    STATUS_POLL_INTERVAL_SEC: int = Field(default=3, ge=1)
    STATUS_POLL_MAX_ATTEMPTS: int = Field(default=40, ge=1)

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")


settings = BotSettings()
