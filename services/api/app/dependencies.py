from functools import lru_cache

from app.services.celery_broker import CeleryTaskBroker
from app.services.interfaces import TaskBroker
from app.services.llm_service import LLMService, create_llm_service
from app.services.search_service import SearchService, create_search_service


@lru_cache(maxsize=1)
def get_task_broker() -> TaskBroker:
    return CeleryTaskBroker()


@lru_cache(maxsize=1)
def get_search_service() -> SearchService:
    return create_search_service()


@lru_cache(maxsize=1)
def get_llm_service() -> LLMService:
    return create_llm_service()
