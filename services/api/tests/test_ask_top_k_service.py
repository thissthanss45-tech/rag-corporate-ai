from app.main import ask_question
from app.core.config import settings
from app.schemas import AskRequest
from app.services.search_service import RetrievedChunk


class _FakeSearchService:
    def __init__(self) -> None:
        self.last_limit: int | None = None

    def search(self, question: str, limit: int = 30) -> list[RetrievedChunk]:
        self.last_limit = limit
        return [
            RetrievedChunk(text="A", source_file="policy.pdf", score=0.9),
            RetrievedChunk(text="B", source_file="runbook.docx", score=0.8),
            RetrievedChunk(text="C", source_file="policy.pdf", score=0.7),
        ]


class _FakeLLMService:
    def __init__(self) -> None:
        self.last_model: str | None = None
        self.last_conversation_context: str | None = None
        self.verification_calls: int = 0

    def generate_answer(
        self,
        question: str,
        context: str,
        model: str = "llama",
        conversation_context: str | None = None,
    ) -> str:
        self.last_model = model
        self.last_conversation_context = conversation_context
        return f"ok: {question}"

    def verify_and_refine_answer(
        self,
        question: str,
        context: str,
        draft_answer: str,
        model: str = "llama",
    ) -> str:
        self.verification_calls += 1
        return draft_answer


def test_ask_question_uses_payload_top_k() -> None:
    search = _FakeSearchService()
    llm = _FakeLLMService()

    response = ask_question(
        payload=AskRequest(
            question="abc",
            top_k=3,
            model="deepseek",
            conversation_context="предыдущий вопрос",
        ),
        search_service=search,
        llm_service=llm,
    )

    assert search.last_limit == max(3, settings.ASK_MIN_RETRIEVAL_CHUNKS)
    assert llm.last_model == "deepseek"
    assert llm.last_conversation_context == "предыдущий вопрос"
    assert llm.verification_calls == 1
    assert response.answer.startswith("ok:")
    assert response.sources == ["policy.pdf", "runbook.docx"]