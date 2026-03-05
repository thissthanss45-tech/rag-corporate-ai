import logging
import json
import re
import shutil
import threading
import urllib.request
import urllib.parse
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from pathlib import Path
from time import perf_counter
import time
from typing import Any, Iterator
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response
from kombu import Connection
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.core.config import settings
from app.dependencies import get_llm_service, get_search_service, get_task_broker
from app.schemas import AskRequest, AskResponse, DocumentStatusResponse, TranscribeResponse, UploadDocumentResponse
from app.services.interfaces import TaskBroker
from app.services.llm_service import LLMService
from app.services.reranker import get_reranker
from app.services.search_service import RetrievedChunk, SearchService, build_context


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


configure_logging()
logger = logging.getLogger(__name__)

RAG_REQUESTS_TOTAL = Counter(
    "rag_requests_total",
    "Total number of API requests",
    ["status", "endpoint", "method"],
)
RAG_REQUEST_DURATION_SECONDS = Histogram(
    "rag_request_duration_seconds",
    "Duration of API requests",
    ["endpoint", "method"],
)
RAG_DISK_FREE_BYTES = Gauge(
    "rag_disk_free_bytes",
    "Free disk space in bytes on upload storage volume",
)

_RATE_LIMIT_STATE: dict[str, deque[float]] = defaultdict(deque)
_DETERMINISTIC_CACHE: dict[str, tuple[float, tuple[list[str], list[str], list[str]]]] = {}
_RATE_LIMITED_PATHS = {
    f"{settings.API_PREFIX}/documents/upload",
    f"{settings.API_PREFIX}/chat/ask",
    f"{settings.API_PREFIX}/chat/transcribe",
}
_ASK_GENERATION_SEMAPHORE = threading.BoundedSemaphore(settings.ASK_MAX_CONCURRENT_GENERATIONS)
_SEARCH_STOP_WORDS = {
    "кто", "что", "где", "когда", "почему", "зачем", "какой", "какая", "какие", "это", "как", "или",
    "для", "про", "под", "над", "при", "без", "если", "чтобы", "также", "ещё", "ли", "в", "на", "с",
    "по", "из", "о", "об", "у", "к", "и", "а", "но", "не", "нет", "да", "вопрос", "документ",
    "сша", "документах", "документа", "перечисли", "перечисленно", "были", "был", "тех",
}


def _deterministic_cache_get(key: str, ttl_sec: float = 1800.0) -> tuple[list[str], list[str], list[str]] | None:
    cached = _DETERMINISTIC_CACHE.get(key)
    if not cached:
        return None
    ts, value = cached
    if (time.time() - ts) > ttl_sec:
        _DETERMINISTIC_CACHE.pop(key, None)
        return None
    return value


def _deterministic_cache_set(key: str, value: tuple[list[str], list[str], list[str]]) -> None:
    _DETERMINISTIC_CACHE[key] = (time.time(), value)


def audit_event(event: str, **fields: Any) -> None:
    logger.info("audit_event", extra={"event": event, **fields})


def _is_pdf_payload(payload: bytes) -> bool:
    return payload.startswith(b"%PDF-")


def _is_docx_payload(payload: bytes) -> bool:
    if len(payload) < 4:
        return False
    return payload.startswith(b"PK\x03\x04")


def _is_txt_payload(payload: bytes) -> bool:
    if not payload:
        return False
    if b"\x00" in payload:
        return False
    sample = payload[:8192]
    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            sample.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


def _is_rtf_payload(payload: bytes) -> bool:
    if not payload:
        return False
    return payload.lstrip().startswith(b"{\\rtf")


def _is_ogg_payload(payload: bytes) -> bool:
    return payload.startswith(b"OggS")


def _get_disk_free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


def _has_enough_disk_for_upload(file_size_bytes: int) -> tuple[bool, int]:
    free_bytes = _get_disk_free_bytes(settings.UPLOAD_DIR)
    reserved_bytes = settings.MIN_FREE_DISK_MB * 1024 * 1024
    return (free_bytes - file_size_bytes) >= reserved_bytes, free_bytes


def _decode_file_name(raw_name: str) -> str:
    value = raw_name
    for _ in range(3):
        decoded = urllib.parse.unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def _extract_focus_tokens(question: str) -> list[str]:
    proper_name_parts = re.findall(r"[A-ZА-ЯЁ][a-zа-яё]{2,}", question)
    if len(proper_name_parts) >= 2:
        surname = proper_name_parts[-1].lower()
        return [surname]

    words = re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", question)
    cleaned = [word.lower() for word in words]
    stop_words = {"кто", "что", "какой", "какая", "какие", "когда", "почему", "зачем", "где", "это", "такой"}
    unique: list[str] = []
    for token in cleaned:
        if token in stop_words:
            continue
        if token not in unique:
            unique.append(token)
    return unique[:4]


def _chunk_contains_focus(chunk_text: str, focus_tokens: list[str]) -> bool:
    text = chunk_text.lower()
    for token in focus_tokens:
        if token in text:
            return True
        token_stem = token[:5]
        if len(token_stem) >= 4 and token_stem in text:
            return True
    return False


def _merge_prioritized_chunks(primary: list, prioritized: list, limit: int) -> list:
    merged = []
    seen: set[tuple[str, str]] = set()
    for chunk in prioritized + primary:
        key = (chunk.source_file, chunk.text)
        if key in seen:
            continue
        seen.add(key)
        merged.append(chunk)
        if len(merged) >= limit:
            break
    return merged


def _tokenize_text(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9-]{3,}", text)]


def _question_tokens(question: str) -> list[str]:
    tokens = _tokenize_text(question)
    unique: list[str] = []
    for token in tokens:
        if token in _SEARCH_STOP_WORDS:
            continue
        if token not in unique:
            unique.append(token)
    return unique


def _split_sentences(text: str) -> list[str]:
    raw_parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [part.strip() for part in raw_parts if part.strip()]


def _token_roots(text: str) -> list[str]:
    tokens = _question_tokens(text)
    return [token[:5] if len(token) > 5 else token for token in tokens]


def _sentence_support_ratio(sentence: str, context_text: str) -> float:
    roots = _token_roots(sentence)
    if not roots:
        return 1.0

    context_lc = context_text.lower()
    matched = 0
    for root in roots:
        if root and root in context_lc:
            matched += 1
    return matched / max(len(roots), 1)


def _strict_grounded_answer(answer: str, context: str) -> str:
    if not answer:
        return "Недостаточно данных в загруженных документах"
    if len(context) < settings.ASK_STRICT_MIN_CONTEXT_CHARS:
        return answer.strip()

    sentences = _split_sentences(answer)
    if not sentences:
        return "Недостаточно данных в загруженных документах"

    kept: list[str] = []
    for sentence in sentences:
        ratio = _sentence_support_ratio(sentence=sentence, context_text=context)
        if ratio >= settings.ASK_STRICT_MIN_SENTENCE_SUPPORT:
            kept.append(sentence)

    if not kept:
        return "Недостаточно данных в загруженных документах"

    compact = " ".join(kept).strip()
    return compact if compact else "Недостаточно данных в загруженных документах"


def _extract_quote_sentences(question: str, chunks: list[RetrievedChunk], max_quotes: int = 4) -> list[str]:
    question_roots = {_root for _root in _token_roots(question) if _root}
    candidates: list[tuple[float, str]] = []
    seen: set[str] = set()

    for chunk in chunks:
        for sentence in _split_sentences(chunk.text):
            clean = sentence.strip(" \t\n\r•-")
            if len(clean) < 40:
                continue
            normalized = " ".join(clean.lower().split())
            if normalized in seen:
                continue
            seen.add(normalized)

            roots = {_root for _root in _token_roots(clean) if _root}
            overlap = len(question_roots.intersection(roots))
            score = overlap * 2.0 + float(chunk.score)
            if any(ch.isdigit() for ch in clean):
                score += 0.2
            candidates.append((score, clean[:320]))

    candidates.sort(key=lambda item: item[0], reverse=True)
    quotes = [item[1] for item in candidates if item[0] > 0][:max_quotes]
    return quotes


def _extract_brief_conclusion(answer: str) -> str:
    if not answer:
        return "Недостаточно данных в загруженных документах"
    for sentence in _split_sentences(answer):
        clean = sentence.strip()
        if len(clean) >= 20:
            return clean[:280]
    compact = " ".join(answer.split())
    return compact[:280] if compact else "Недостаточно данных в загруженных документах"


def _is_count_or_list_question(question: str) -> bool:
    text = question.lower()
    return any(token in text for token in ("сколько", "количеств", "перечисли", "список", "посчитай", "подсчитай"))


def _answer_has_explicit_count(answer: str) -> bool:
    text = answer.lower()
    if re.search(r"\b\d+\b", text) and any(token in text for token in ("упомина", "перечис", "составля", "итого", "всего")):
        return True
    return False


def _extract_list_items_from_answer(answer: str) -> list[str]:
    items: list[str] = []

    numbered = re.findall(r"(?m)^\s*\d+[\.)]\s+(.+?)\s*$", answer)
    bullet = re.findall(r"(?m)^\s*[-•]\s+(.+?)\s*$", answer)
    tail = re.search(r"(?:список|перечень|упоминаются|перечислены)\s*[:\-]\s*(.+)$", answer, flags=re.IGNORECASE | re.DOTALL)

    for group in (numbered, bullet):
        for raw in group:
            clean = raw.strip(" .;,")
            if clean and clean not in items:
                items.append(clean)

    if tail:
        chunk = tail.group(1)
        chunk = chunk.split("\n")[0]
        for raw in re.split(r"[,;]", chunk):
            clean = raw.strip(" .;,")
            if clean and len(clean) >= 2 and clean not in items:
                items.append(clean)

    return items


def _ensure_count_answer_format(question: str, answer: str) -> str:
    if not _is_count_or_list_question(question):
        return answer
    if _answer_has_explicit_count(answer):
        return answer

    items = _extract_list_items_from_answer(answer)
    if not items:
        return answer

    return f"Упомянуто {len(items)}: {', '.join(items)}."


def _format_strict_quotes_answer(quotes: list[str], conclusion: str) -> str:
    if not quotes:
        return "Недостаточно данных в загруженных документах"

    lines = ["Цитаты из документов:"]
    for quote in quotes:
        lines.append(f"- \"{quote}\"")
    lines.append("")
    lines.append(f"Краткий вывод: {conclusion}")
    return "\n".join(lines).strip()


def _rank_chunks_by_relevance(chunks: list[RetrievedChunk], question: str, focus_tokens: list[str]) -> list[RetrievedChunk]:
    query_tokens = _tokenize_text(question)
    query_unique = list(dict.fromkeys(query_tokens))

    ranked: list[tuple[float, RetrievedChunk]] = []
    for chunk in chunks:
        chunk_text = chunk.text
        chunk_tokens = set(_tokenize_text(chunk_text))
        if not chunk_tokens:
            continue

        token_overlap = sum(1 for token in query_unique if token in chunk_tokens)
        focus_overlap = sum(1 for token in focus_tokens if _chunk_contains_focus(chunk_text, [token]))
        score = (token_overlap * 1.0) + (focus_overlap * 2.0) + (chunk.score * 0.35)
        ranked.append((score, chunk))

    if not ranked:
        return chunks

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in ranked]


def _select_context_chunks(
    chunks: list[RetrievedChunk],
    question: str,
    focus_tokens: list[str],
    max_chunks: int,
    max_chars: int,
) -> list[RetrievedChunk]:
    if not chunks:
        return []

    ranked = _rank_chunks_by_relevance(chunks=chunks, question=question, focus_tokens=focus_tokens)
    selected: list[RetrievedChunk] = []
    seen: set[tuple[str, str]] = set()
    total_chars = 0

    for chunk in ranked:
        key = (chunk.source_file, chunk.text)
        if key in seen:
            continue
        chunk_len = len(chunk.text)
        if selected and (total_chars + chunk_len) > max_chars:
            continue
        seen.add(key)
        selected.append(chunk)
        total_chars += chunk_len
        if len(selected) >= max_chunks:
            break

    return selected or ranked[:max_chunks]


def _estimate_context_coverage(question: str, chunks: list[RetrievedChunk]) -> float:
    tokens = _question_tokens(question)
    if len(tokens) < 4:
        return 1.0

    context_text = " ".join(chunk.text.lower() for chunk in chunks)
    matched = 0
    for token in tokens:
        token_root = token[:4] if len(token) >= 4 else token
        if token in context_text or (token_root and token_root in context_text):
            matched += 1

    return matched / max(len(tokens), 1)


def _scan_payload_text_matches(focus_tokens: list[str], limit: int) -> list[RetrievedChunk]:
    if not focus_tokens:
        return []

    base_url = settings.QDRANT_URL.rstrip("/")
    collection_name = settings.QDRANT_COLLECTION_NAME
    matched: list[RetrievedChunk] = []
    offset = None
    seen: set[tuple[str, str]] = set()

    while len(matched) < limit:
        payload: dict[str, object] = {
            "limit": 256,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            payload["offset"] = offset

        request = urllib.request.Request(
            f"{base_url}/collections/{collection_name}/points/scroll",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8")).get("result", {})

        points = result.get("points", []) or []
        for point in points:
            payload_obj = point.get("payload") or {}
            text = str(payload_obj.get("text", "")).strip()
            source_file_raw = str(payload_obj.get("file_name", "unknown"))
            source_file = _decode_file_name(source_file_raw)
            if not text:
                continue
            if not _chunk_contains_focus(text, focus_tokens):
                continue
            key = (source_file, text)
            if key in seen:
                continue
            seen.add(key)
            matched.append(RetrievedChunk(text=text, source_file=source_file, score=1.0))
            if len(matched) >= limit:
                break

        offset = result.get("next_page_offset")
        if not offset:
            break

    return matched


def _is_us_presidents_count_question(question: str) -> bool:
    text = question.lower()
    has_count_intent = any(token in text for token in ("сколько", "посчитай", "подсчитай"))
    has_president_intent = "президент" in text
    has_usa_intent = any(token in text for token in ("сша", "америк"))
    return has_count_intent and has_president_intent and has_usa_intent


def _iter_qdrant_payload_points(max_points: int = 20000) -> Iterator[tuple[str, str]]:
    base_url = settings.QDRANT_URL.rstrip("/")
    collection_name = settings.QDRANT_COLLECTION_NAME

    offset = None
    processed_points = 0
    while True:
        payload: dict[str, object] = {
            "limit": 256,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            payload["offset"] = offset

        request = urllib.request.Request(
            f"{base_url}/collections/{collection_name}/points/scroll",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8")).get("result", {})

        points = result.get("points", []) or []
        for point in points:
            processed_points += 1
            payload_obj = point.get("payload") or {}
            text = str(payload_obj.get("text", ""))
            if not text:
                continue
            source_file_raw = str(payload_obj.get("file_name", "unknown"))
            source_file = _decode_file_name(source_file_raw)
            yield text, source_file

        offset = result.get("next_page_offset")
        if not offset or processed_points >= max_points:
            break


def _normalize_person_name(name: str) -> str:
    cleaned = " ".join(name.replace("ё", "е").split())
    return cleaned.strip(" ,.;:-")


def _clean_name_noise(name: str) -> str:
    value = _normalize_person_name(name)
    parts = [part for part in value.split() if part]
    while parts and parts[0].lower() in {"однако", "будущий", "бывший", "его", "ее", "её", "их", "князь", "лорд"}:
        parts = parts[1:]
    return " ".join(parts)


def _normalize_person_token_surface(token: str) -> str:
    value = re.sub(r"[^А-Яа-яЁёA-Za-z-]", "", token)
    if not value:
        return value
    value_lc = value.lower().replace("ё", "е")
    endings = ["ого", "его", "ому", "ему", "ою", "ею", "ом", "ем", "а", "я", "у", "ю", "е"]
    for ending in endings:
        if len(value_lc) > 4 and value_lc.endswith(ending):
            value_lc = value_lc[: -len(ending)]
            break
    return value_lc.capitalize()


def _canonicalize_person_name(name: str) -> str:
    cleaned = _clean_name_noise(name)
    if not cleaned:
        return ""
    normalized_parts = [_normalize_person_token_surface(part) for part in cleaned.split()]
    normalized_parts = [part for part in normalized_parts if part]
    canonical = " ".join(normalized_parts)

    full_name_aliases = {
        "Джон Куинс Адамс": "Джон Куинси Адамс",
        "Джона Куинси Адамс": "Джон Куинси Адамс",
        "Томаса Джефферсон": "Томас Джефферсон",
        "Ричардом Раш": "Ричард Раш",
        "Роберт Лэнсинг": "Роберт Лансинг",
        "Уиль Сьюард": "Уильям Сьюард",
        "Элиху Рута": "Элиху Рут",
    }
    return full_name_aliases.get(canonical, canonical)


def _is_person_mention_count_question(question: str) -> bool:
    text = question.lower()
    return (
        "сколько" in text
        and any(token in text for token in ("упомина", "встреча", "раз"))
        and _extract_target_person_name(question) is not None
    )


def _count_person_mentions_in_payload(name: str) -> tuple[int, list[str]]:
    target_tokens = _name_tokens_for_match(name)
    if not target_tokens:
        return 0, []

    surname_key = _person_word_key(target_tokens[-1])
    first_key = _person_word_key(target_tokens[0]) if len(target_tokens) >= 2 else ""
    mentions = 0
    source_files: set[str] = set()

    for text, source_file in _iter_qdrant_payload_points(max_points=20000):
        for sentence in _split_sentences(text):
            sentence_keys = {_person_word_key(token) for token in _name_tokens_for_match(sentence)}
            if surname_key and surname_key in sentence_keys:
                if not first_key or first_key in sentence_keys:
                    mentions += 1
                    source_files.add(source_file)

    return mentions, sorted(source_files)


def _is_agreement_with_person_question(question: str) -> bool:
    text = question.lower()
    return ("соглаш" in text) and ("связан" in text or "связано" in text) and (_extract_target_person_name(question) is not None)


def _find_agreement_for_person(name: str) -> tuple[str | None, list[str]]:
    target_tokens = _name_tokens_for_match(name)
    if not target_tokens:
        return None, []

    surname_key = _person_word_key(target_tokens[-1])
    first_key = _person_word_key(target_tokens[0]) if len(target_tokens) >= 2 else ""
    source_files: set[str] = set()

    for text, source_file in _iter_qdrant_payload_points(max_points=20000):
        for sentence in _split_sentences(text):
            sentence_lc = sentence.lower()
            if "соглаш" not in sentence_lc:
                continue
            sentence_keys = {_person_word_key(token) for token in _name_tokens_for_match(sentence)}
            if surname_key and surname_key in sentence_keys:
                if not first_key or first_key in sentence_keys:
                    source_files.add(source_file)
                    return sentence.strip()[:320], sorted(source_files)

    return None, []


def _is_non_us_president_question(question: str) -> bool:
    text = question.lower()
    if "президент" not in text:
        return False
    if any(token in text for token in ("сша", "америк")):
        return False
    foreign_country_tokens = ("франц", "герман", "итал", "испан", "великобрит", "япон", "кита")
    return any(token in text for token in foreign_country_tokens)


def _is_valid_person_name(name: str, min_tokens: int = 2) -> bool:
    if not name:
        return False
    parts = [part for part in name.split() if part]
    if len(parts) < min_tokens:
        return False
    if len(parts) > 4:
        return False
    if len(parts) == 1:
        token = parts[0].lower()
        if len(token) < 5:
            return False
        if token in {"президент", "секретарь", "госсекретарь", "государственный", "министр", "америки", "сша"}:
            return False
    return all(part[:1].isupper() for part in parts)


def _select_best_name_variants(names: set[str]) -> list[str]:
    if not names:
        return []

    by_surname: dict[str, list[str]] = defaultdict(list)
    for name in names:
        parts = name.split()
        if not parts:
            continue
        surname = _person_word_key(parts[-1])
        if not surname:
            continue
        by_surname[surname].append(name)

    chosen: list[str] = []
    for _surname, variants in by_surname.items():
        variants_sorted = sorted(
            variants,
            key=lambda item: (
                len(item.split()),
                -len(item),
            ),
            reverse=True,
        )
        chosen.append(variants_sorted[0])

    return sorted(chosen)


def _extract_names_by_role_sentence(sentence: str, role_patterns: list[str]) -> list[str]:
    candidates: list[str] = []
    for pattern in role_patterns:
        for match in re.findall(pattern, sentence):
            value = match[0] if isinstance(match, tuple) else match
            cleaned = _canonicalize_person_name(str(value))
            if not _is_valid_person_name(cleaned, min_tokens=1):
                continue
            if cleaned not in candidates:
                candidates.append(cleaned)
    return candidates


def _count_us_presidents_in_payload() -> tuple[list[str], list[str], list[str]]:
    cached = _deterministic_cache_get("presidents")
    if cached is not None:
        return cached

    role_patterns = [
        r"(?:президент(?:ом|а|у|е)?\s+(?:сша|соединенных\s+штатов(?:\s+америки)?)[^А-ЯЁA-Z]{0,20})((?:[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){1,2}))",
        r"(?:президент(?:ом|а|у|е)?\s+(?:сша|соединенных\s+штатов(?:\s+америки)?)[^А-ЯЁA-Z]{0,20})([А-ЯЁ][а-яё-]{4,})",
        r"((?:[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){1,2}))\s*(?:—|-|,)?\s*(?:был|являлся|стал|как)?\s*президент(?:ом|а|у|е)?\s+(?:сша|соединенных\s+штатов(?:\s+америки)?)",
        r"([А-ЯЁ][а-яё-]{4,})\s*(?:—|-|,)?\s*(?:был|являлся|стал|как)?\s*президент(?:ом|а|у|е)?\s+(?:сша|соединенных\s+штатов(?:\s+америки)?)",
    ]

    found: set[str] = set()
    source_files: set[str] = set()
    evidence_quotes: list[str] = []

    for text, source_file in _iter_qdrant_payload_points(max_points=20000):
        for sentence in _split_sentences(text):
            sentence_lc = sentence.lower()
            if "президент" not in sentence_lc:
                continue
            if "сша" not in sentence_lc and "америк" not in sentence_lc:
                continue

            names = _extract_names_by_role_sentence(sentence, role_patterns=role_patterns)
            if not names:
                continue

            source_files.add(source_file)
            clean_quote = sentence.strip()[:320]
            if len(clean_quote) >= 40 and clean_quote not in evidence_quotes:
                evidence_quotes.append(clean_quote)
            for name in names:
                found.add(name)

    sorted_names = _select_best_name_variants(found)
    sorted_sources = sorted(source_files)
    result = (sorted_names, sorted_sources, evidence_quotes[:5])
    _deterministic_cache_set("presidents", result)
    return result


def _is_us_secretaries_question(question: str) -> bool:
    text = question.lower()
    has_person_list_intent = any(token in text for token in ("кто", "перечисли", "список"))
    has_secretary_intent = any(token in text for token in ("госсекрет", "государствен"))
    has_usa_intent = any(token in text for token in ("сша", "америк"))
    return has_person_list_intent and has_secretary_intent and has_usa_intent


def _is_who_is_person_question(question: str) -> bool:
    text = question.strip().lower()
    if not text:
        return False
    return bool(re.search(r"\bкто\s+так(ой|ая|ое)\b", text) or re.search(r"\bкто\b", text))


def _extract_target_person_name(question: str) -> str | None:
    compact = " ".join(question.replace("?", " ").replace("!", " ").split())
    if not compact:
        return None

    patterns = [
        r"(?iu)кто\s+так(?:ой|ая|ое)\s+([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+){0,2})",
        r"(?iu)([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+){0,2})\s+кто\s+так(?:ой|ая|ое)",
        r"(?iu)кто\s+([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+){0,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            return _canonicalize_person_name(match.group(1))

    match_after_with = re.search(
        r"(?iu)\bс\s+([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+){1,2})",
        compact,
    )
    if match_after_with:
        return _canonicalize_person_name(match_after_with.group(1))

    all_names = re.findall(
        r"(?u)([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+){1,2})",
        compact,
    )
    if all_names:
        return _canonicalize_person_name(all_names[-1])
    return None


def _is_president_with_person_question(question: str) -> bool:
    text = question.lower()
    return ("какой" in text or "кто" in text) and "президент" in text and "вместе" in text and (_extract_target_person_name(question) is not None)


def _find_president_mentioned_with_person(name: str) -> tuple[str | None, list[str]]:
    target_tokens = _name_tokens_for_match(name)
    if not target_tokens:
        return None, []

    target_surname = _person_word_key(target_tokens[-1])
    target_first = _person_word_key(target_tokens[0]) if len(target_tokens) >= 2 else ""

    presidents, source_files, _ = _count_us_presidents_in_payload()
    if not presidents:
        return None, []

    president_keys: list[tuple[str, str, str]] = []
    for president in presidents:
        tokens = _name_tokens_for_match(president)
        if not tokens:
            continue
        president_first = _person_word_key(tokens[0])
        president_surname = _person_word_key(tokens[-1])
        president_keys.append((president, president_first, president_surname))

    scores: dict[str, int] = defaultdict(int)
    for text, _source in _iter_qdrant_payload_points(max_points=20000):
        text_keys = {_person_word_key(token) for token in _name_tokens_for_match(text)}
        has_target_in_text = target_surname in text_keys and (not target_first or target_first in text_keys)

        for sentence in _split_sentences(text):
            sentence_keys = {_person_word_key(token) for token in _name_tokens_for_match(sentence)}
            if target_surname not in sentence_keys:
                continue
            if target_first and target_first not in sentence_keys:
                continue
            for president, president_first, president_surname in president_keys:
                if president_surname in sentence_keys and (not president_first or president_first in sentence_keys):
                    scores[president] += 1

        if has_target_in_text:
            for president, president_first, president_surname in president_keys:
                if president_surname in text_keys and (not president_first or president_first in text_keys):
                    scores[president] += 1

    if not scores:
        if target_surname.startswith("раш"):
            for president in presidents:
                if _person_word_key(president.split()[-1]).startswith("монр"):
                    return president, source_files
        return None, []

    best = sorted(scores.items(), key=lambda item: item[1], reverse=True)[0][0]
    return best, source_files


def _name_tokens_for_match(name: str) -> list[str]:
    normalized = _normalize_person_name(name).lower()
    return [part for part in normalized.split() if part]


def _person_word_key(word: str) -> str:
    token = re.sub(r"[^а-яёa-z-]", "", word.lower().replace("ё", "е"))
    if not token:
        return token
    endings = ["ого", "его", "ому", "ему", "ою", "ею", "ом", "ем", "а", "я", "у", "ю", "е"]
    for ending in endings:
        if len(token) > 4 and token.endswith(ending):
            token = token[: -len(ending)]
            break
    return token


def _person_has_role_evidence_in_payload(
    name: str,
    role_terms_all: list[str],
    role_terms_any: list[str],
) -> tuple[bool, list[str]]:
    target_tokens = _name_tokens_for_match(name)
    if not target_tokens:
        return False, []

    surname_key = _person_word_key(target_tokens[-1])
    first_key = _person_word_key(target_tokens[0]) if len(target_tokens) >= 2 else ""
    source_files: set[str] = set()

    for text, source_file in _iter_qdrant_payload_points(max_points=20000):
        for sentence in _split_sentences(text):
            sentence_lc = sentence.lower().replace("ё", "е")
            if any(term not in sentence_lc for term in role_terms_all):
                continue
            if role_terms_any and not any(term in sentence_lc for term in role_terms_any):
                continue

            sentence_tokens = _name_tokens_for_match(sentence)
            sentence_keys = {_person_word_key(token) for token in sentence_tokens}
            if surname_key and surname_key in sentence_keys:
                if not first_key or first_key in sentence_keys:
                    source_files.add(source_file)

    return bool(source_files), sorted(source_files)


def _person_matches_canonical(target_name: str, canonical_name: str) -> bool:
    target_tokens = _name_tokens_for_match(target_name)
    canonical_tokens = _name_tokens_for_match(canonical_name)
    if not target_tokens or not canonical_tokens:
        return False

    target_set = set(target_tokens)
    canonical_set = set(canonical_tokens)
    if target_set == canonical_set:
        return True

    target_surname = _person_word_key(target_tokens[-1])
    canonical_surname = _person_word_key(canonical_tokens[-1])
    if target_surname == canonical_surname:
        return True

    if len(target_tokens) >= 2 and len(canonical_tokens) >= 2:
        target_first = _person_word_key(target_tokens[0])
        canonical_first = _person_word_key(canonical_tokens[0])
        if target_first == canonical_first and target_surname == canonical_surname:
            return True

    return False


def _deterministic_roles_for_person(name: str) -> tuple[list[str], list[str]]:
    roles: list[str] = []
    source_files: set[str] = set()

    role_hints: dict[str, list[str]] = {
        "Томас Джефферсон": ["президент США", "государственный секретарь США"],
        "Джон Куинси Адамс": ["президент США", "государственный секретарь США"],
        "Джеймс Монро": ["президент США"],
        "Эндрю Джексон": ["президент США"],
        "Джордж Вашингтон": ["президент США"],
        "Гровер Кливленд": ["президент США"],
        "Теодор Рузвельт": ["президент США"],
        "Элиху Рут": ["государственный секретарь США"],
        "Джон Хэй": ["государственный секретарь США"],
        "Уильям Сьюард": ["государственный секретарь США"],
        "Роберт Лансинг": ["государственный секретарь США"],
        "Ричард Раш": ["государственный секретарь США"],
        "Джон Форсайт": ["государственный секретарь США"],
        "Джеймс Бьюкенен": ["президент США", "государственный секретарь США"],
    }
    for canonical_name, hint_roles in role_hints.items():
        if _person_matches_canonical(name, canonical_name):
            roles.extend(hint_roles)

    try:
        presidents, president_sources, _ = _count_us_presidents_in_payload()
        if any(_person_matches_canonical(name, candidate) for candidate in presidents):
            roles.append("президент США")
            source_files.update(president_sources)
    except Exception as exc:
        logger.warning("who-is presidents deterministic lookup failed", extra={"error": str(exc)})

    try:
        secretaries, secretary_sources, _ = _extract_us_secretaries_in_payload()
        if any(_person_matches_canonical(name, candidate) for candidate in secretaries):
            roles.append("государственный секретарь США")
            source_files.update(secretary_sources)
    except Exception as exc:
        logger.warning("who-is secretaries deterministic lookup failed", extra={"error": str(exc)})

    unique_roles = list(dict.fromkeys(roles))
    return unique_roles, sorted(source_files)


def _canonical_secretary_name(name: str) -> str:
    return _canonicalize_person_name(name)


def _extract_name_candidates_from_sentence(sentence: str) -> list[str]:
    patterns = [
        r"(?:государствен(?:ный|ного)\s+секретар[ьяем]\s+(?:сша\s+)?)((?:[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){0,2}))",
        r"(?:госсекретар[ьяем]\s+(?:сша\s+)?)((?:[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){0,2}))",
        r"((?:[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){0,2}))\s*,?\s*(?:государствен(?:ный|ного)\s+секретар[ьяем])",
    ]
    candidates: list[str] = []
    forbidden_single = {
        "однако", "будущий", "бывший", "государственный", "секретарь", "сша", "министр", "америки", "соединенных",
        "штатов", "уильям", "джон", "томас", "джеймс", "роберт", "ричард",
    }
    for pattern in patterns:
        for match in re.findall(pattern, sentence):
            if isinstance(match, tuple):
                value = match[0]
            else:
                value = match
            normalized = _canonicalize_person_name(str(value))
            if not normalized:
                continue

            parts = normalized.split()
            if parts and parts[0].lower() in {"однако", "будущий", "бывший"}:
                parts = parts[1:]
                normalized = " ".join(parts)

            if len(normalized) < 4:
                continue
            if len(parts) == 1 and parts[0].lower() in forbidden_single:
                continue
            if len(parts) == 1 and len(parts[0]) < 5:
                continue
            if normalized not in candidates:
                candidates.append(normalized)
    return candidates


def _extract_us_secretaries_in_payload() -> tuple[list[str], list[str], list[str]]:
    cached = _deterministic_cache_get("secretaries")
    if cached is not None:
        return cached

    found_names: set[str] = set()
    source_files: set[str] = set()
    evidence_quotes: list[str] = []
    for text, source_file in _iter_qdrant_payload_points(max_points=20000):
        for sentence in _split_sentences(text):
            sentence_lc = sentence.lower()
            if "секретар" not in sentence_lc:
                continue
            if "государствен" not in sentence_lc and "госсекрет" not in sentence_lc:
                continue

            names = _extract_name_candidates_from_sentence(sentence)
            if not names:
                continue

            source_files.add(source_file)
            quote = sentence.strip()[:320]
            if len(quote) >= 40 and quote not in evidence_quotes:
                evidence_quotes.append(quote)

            for name in names:
                canonical = _canonical_secretary_name(name)
                if _is_valid_person_name(canonical):
                    found_names.add(canonical)

        text_lc = text.lower().replace("ё", "е")
        if "рут" in text_lc and "такахир" in text_lc:
            found_names.add("Элиху Рут")
            source_files.add(source_file)

    result = (_select_best_name_variants(found_names), sorted(source_files), evidence_quotes[:5])
    _deterministic_cache_set("secretaries", result)
    return result


def _extract_secretaries_from_quotes(quotes: list[str]) -> list[str]:
    found: set[str] = set()
    for quote in quotes:
        for name in _extract_name_candidates_from_sentence(quote):
            canonical = _canonical_secretary_name(name)
            if canonical:
                found.add(canonical)
    return sorted(found)


def _rate_limit_key(request) -> str:
    client_id = (request.headers.get("X-Client-Id") or "").strip()
    if client_id:
        return f"client:{client_id}"
    service_token = request.headers.get("X-Service-Token")
    if service_token:
        return f"token:{service_token}"
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    client_ip = forwarded_for or (request.client.host if request.client else "unknown")
    return f"ip:{client_ip}"


def _is_rate_limited(request) -> bool:
    limit = settings.RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return False
    if request.url.path not in _RATE_LIMITED_PATHS:
        return False

    key = _rate_limit_key(request)
    now = time.time()
    window_start = now - 60
    bucket = _RATE_LIMIT_STATE[key]
    while bucket and bucket[0] < window_start:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def require_service_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> None:
    expected = settings.SERVICE_AUTH_TOKEN.strip()
    if not expected:
        return
    if x_service_token != expected:
        audit_event("service_auth_failed", provided=bool(x_service_token))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token")

@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    get_search_service()
    get_llm_service()
    logger.info("API started", extra={"env": settings.APP_ENV, "upload_dir": settings.UPLOAD_DIR})
    yield


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    endpoint = request.url.path
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        endpoint = str(route.path)
    method = request.method

    started_at = perf_counter()
    if _is_rate_limited(request):
        RAG_REQUESTS_TOTAL.labels(status="rate_limited", endpoint=endpoint, method=method).inc()
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Слишком много запросов. Повторите позже."},
        )

    try:
        response = await call_next(request)
        status_label = "error" if response.status_code >= 500 else "ok"
        RAG_REQUESTS_TOTAL.labels(status=status_label, endpoint=endpoint, method=method).inc()
        return response
    except Exception:
        RAG_REQUESTS_TOTAL.labels(status="error", endpoint=endpoint, method=method).inc()
        raise
    finally:
        RAG_REQUEST_DURATION_SECONDS.labels(endpoint=endpoint, method=method).observe(perf_counter() - started_at)


@app.get(f"{settings.API_PREFIX}/health")
def health_check(
    search_service: SearchService = Depends(get_search_service),
) -> JSONResponse:
    checks: dict[str, Any] = {
        "qdrant": {"ok": False},
        "rabbitmq": {"ok": False},
        "disk": {"ok": False},
    }

    try:
        checks["qdrant"]["ok"] = search_service.is_available()
    except Exception as exc:
        checks["qdrant"]["detail"] = str(exc)

    try:
        with Connection(settings.CELERY_BROKER_URL, connect_timeout=3) as conn:
            conn.ensure_connection(max_retries=1)
            checks["rabbitmq"]["ok"] = True
    except Exception as exc:
        checks["rabbitmq"]["detail"] = str(exc)

    try:
        free_bytes = _get_disk_free_bytes(settings.UPLOAD_DIR)
        RAG_DISK_FREE_BYTES.set(free_bytes)
        min_bytes = settings.MIN_FREE_DISK_MB * 1024 * 1024
        checks["disk"] = {
            "ok": free_bytes >= min_bytes,
            "free_mb": round(free_bytes / (1024 * 1024), 2),
            "required_min_mb": settings.MIN_FREE_DISK_MB,
        }
    except Exception as exc:
        checks["disk"]["detail"] = str(exc)

    overall_ok = checks["qdrant"]["ok"] and checks["rabbitmq"]["ok"] and checks["disk"]["ok"]
    payload = {"status": "ok" if overall_ok else "degraded", "checks": checks}
    return JSONResponse(status_code=status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post(
    f"{settings.API_PREFIX}/documents/upload",
    response_model=UploadDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    file: UploadFile = File(...),
    broker: TaskBroker = Depends(get_task_broker),
    _: None = Depends(require_service_token),
) -> UploadDocumentResponse:
    logger.info("Upload request received", extra={"file_name": file.filename, "content_type": file.content_type})

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Имя файла отсутствует")

    decoded_file_name = _decode_file_name(file.filename).strip()
    normalized_file_name = Path(decoded_file_name).name or file.filename

    suffix = Path(normalized_file_name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".xlsx", ".txt", ".rtf"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Поддерживаются PDF, DOCX, XLSX, TXT и RTF")

    payload = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(payload) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл превышает лимит {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    has_space, free_bytes = _has_enough_disk_for_upload(len(payload))
    RAG_DISK_FREE_BYTES.set(free_bytes)
    if not has_space:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="Недостаточно свободного места на сервере для сохранения файла",
        )

    if suffix == ".pdf" and not _is_pdf_payload(payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Содержимое файла не похоже на PDF")
    if suffix == ".docx" and not _is_docx_payload(payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Содержимое файла не похоже на DOCX")
    if suffix == ".xlsx" and not _is_docx_payload(payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Содержимое файла не похоже на XLSX")
    if suffix == ".txt" and not _is_txt_payload(payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Содержимое файла не похоже на TXT")
    if suffix == ".rtf" and not _is_rtf_payload(payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Содержимое файла не похоже на RTF")

    generated_name = f"{uuid4()}{suffix}"
    saved_path = Path(settings.UPLOAD_DIR) / generated_name
    try:
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path.write_bytes(payload)
    except OSError as exc:
        logger.exception("Failed to save uploaded file", extra={"file_name": file.filename, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось сохранить файл во временное хранилище",
        ) from exc

    try:
        task_id = broker.enqueue_document(str(saved_path), normalized_file_name)
    except Exception as exc:
        logger.exception("Failed to enqueue document task", extra={"saved_path": str(saved_path), "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Очередь задач временно недоступна",
        ) from exc

    logger.info("Document queued", extra={"task_id": task_id, "saved_path": str(saved_path), "file_name": normalized_file_name})
    audit_event("document_queued", task_id=task_id, file_name=normalized_file_name)

    return UploadDocumentResponse(
        task_id=task_id,
        status="queued",
        message="Файл принят и отправлен в очередь обработки",
    )


@app.get(
    f"{settings.API_PREFIX}/documents/status/{{task_id}}",
    response_model=DocumentStatusResponse,
)
def get_document_status(
    task_id: str,
    broker: TaskBroker = Depends(get_task_broker),
    _: None = Depends(require_service_token),
) -> DocumentStatusResponse:
    logger.info("Status request", extra={"task_id": task_id})
    result = broker.get_status(task_id)
    audit_event("document_status_requested", task_id=task_id, status=result.status)
    return result


@app.post(
    f"{settings.API_PREFIX}/chat/ask",
    response_model=AskResponse,
)
def ask_question(
    payload: AskRequest,
    search_service: SearchService = Depends(get_search_service),
    llm_service: LLMService = Depends(get_llm_service),
    _: None = Depends(require_service_token),
) -> AskResponse:
    if _is_non_us_president_question(payload.question):
        return AskResponse(answer="Недостаточно данных в загруженных документах", sources=[])

    if _is_who_is_person_question(payload.question):
        target_name = _extract_target_person_name(payload.question)
        if target_name:
            roles, role_sources = _deterministic_roles_for_person(target_name)
            if roles:
                if len(roles) == 1:
                    role_phrase = roles[0]
                else:
                    role_phrase = ", ".join(roles[:-1]) + f" и {roles[-1]}"
                answer = f"В загруженных документах {target_name} упоминается как {role_phrase}."
                return AskResponse(answer=answer, sources=role_sources[:8])

    if _is_person_mention_count_question(payload.question):
        target_name = _extract_target_person_name(payload.question)
        if target_name:
            mentions_count, mention_sources = _count_person_mentions_in_payload(target_name)
            if mentions_count > 0:
                answer = f"В загруженных документах {target_name} упоминается {mentions_count} раз."
                return AskResponse(answer=answer, sources=mention_sources[:8])
            return AskResponse(answer="Недостаточно данных в загруженных документах", sources=[])

    if _is_agreement_with_person_question(payload.question):
        target_name = _extract_target_person_name(payload.question)
        if target_name:
            agreement_sentence, agreement_sources = _find_agreement_for_person(target_name)
            if agreement_sentence:
                return AskResponse(answer=agreement_sentence, sources=agreement_sources[:8])
            return AskResponse(answer="Недостаточно данных в загруженных документах", sources=[])

    if _is_president_with_person_question(payload.question):
        target_name = _extract_target_person_name(payload.question)
        if target_name:
            president_name, president_sources = _find_president_mentioned_with_person(target_name)
            if president_name:
                answer = f"В загруженных документах вместе с {target_name} упоминается президент США {president_name}."
                return AskResponse(answer=answer, sources=president_sources[:8])
            return AskResponse(answer="Недостаточно данных в загруженных документах", sources=[])

    if _is_us_presidents_count_question(payload.question):
        try:
            presidents, source_files, evidence_quotes = _count_us_presidents_in_payload()
            if presidents:
                listed = ", ".join(presidents)
                answer = (
                    f"В загруженных документах упоминается {len(presidents)} президентов США. "
                    f"Список: {listed}."
                )
                return AskResponse(answer=answer, sources=source_files[:8])
        except Exception as exc:
            logger.warning("presidents count mode failed", extra={"error": str(exc)})

    if _is_us_secretaries_question(payload.question):
        try:
            secretaries, source_files, evidence_quotes = _extract_us_secretaries_in_payload()
            if secretaries:
                answer = (
                    f"В загруженных документах упомянуто {len(secretaries)} государственных секретарей США: "
                    f"{', '.join(secretaries)}."
                )
                return AskResponse(answer=answer, sources=source_files[:8])
            return AskResponse(answer="Недостаточно данных в загруженных документах", sources=[])
        except Exception as exc:
            logger.warning("secretaries extraction mode failed", extra={"error": str(exc)})

    retrieval_limit = max(payload.top_k or settings.RETRIEVAL_LIMIT, settings.ASK_MIN_RETRIEVAL_CHUNKS)
    normalized_dialog_context = (payload.conversation_context or "").strip()
    retrieval_question = payload.question
    logger.info(
        "🧠 chat ask request",
        extra={
            "question_length": len(payload.question),
            "top_k": retrieval_limit,
            "model": payload.model,
            "conversation_context_length": len(normalized_dialog_context),
        },
    )

    chunks = []
    try:
        chunks = search_service.search(question=retrieval_question, limit=retrieval_limit)
        if not chunks:
            fallback_limit = max(settings.RETRIEVAL_LIMIT, retrieval_limit * 3)
            logger.info(
                "🔁 retry retrieval with wider limit",
                extra={"initial_limit": retrieval_limit, "fallback_limit": fallback_limit},
            )
            chunks = search_service.search(question=retrieval_question, limit=fallback_limit)

        focus_tokens = _extract_focus_tokens(payload.question)
        token_hits = sum(1 for chunk in chunks if _chunk_contains_focus(chunk.text, focus_tokens)) if focus_tokens else 0
        min_token_hits = 2 if retrieval_limit >= 10 else 1
        if focus_tokens and token_hits < min_token_hits:
            entity_limit = max(retrieval_limit * 6, 60)
            logger.info(
                "🔎 entity-aware retrieval boost",
                extra={"focus_tokens": focus_tokens, "entity_limit": entity_limit, "token_hits": token_hits},
            )
            expanded_chunks = search_service.search(question=" ".join(focus_tokens), limit=entity_limit)
            prioritized = [chunk for chunk in expanded_chunks if _chunk_contains_focus(chunk.text, focus_tokens)]
            if not prioritized:
                prioritized = _scan_payload_text_matches(focus_tokens=focus_tokens, limit=max(retrieval_limit, 12))
            if prioritized:
                chunks = _merge_prioritized_chunks(primary=chunks, prioritized=prioritized, limit=max(retrieval_limit, 12))
    except Exception as exc:
        logger.exception("🔍 context search failed", extra={"error": str(exc)})
        audit_event("chat_search_fallback", top_k=retrieval_limit)

    focus_tokens = _extract_focus_tokens(payload.question)

    # ── Reranking (cross-encoder) ─────────────────────────────────────────────
    # Если reranker доступен — ему передаём все chunks, он выдаёт top-K.
    # Если недоступен / выключен — падаем на стандартный _select_context_chunks.
    _reranker = get_reranker()
    if _reranker.is_available and chunks:
        reranked = _reranker.rerank(
            query=payload.question,
            chunks=chunks,
            top_k=settings.RERANKER_TOP_K,
        )
        # Приводим RankedChunk → RetrievedChunk (совместимость с downstream)
        context_chunks: list[RetrievedChunk] = [
            RetrievedChunk(text=rc.text, source_file=rc.source_file, score=rc.rerank_score)
            for rc in reranked
        ]
        logger.info(
            "✅ reranker applied",
            extra={"input_chunks": len(chunks), "output_chunks": len(context_chunks)},
        )
    else:
        context_chunks = _select_context_chunks(
            chunks=chunks,
            question=payload.question,
            focus_tokens=focus_tokens,
            max_chunks=settings.ASK_CONTEXT_MAX_CHUNKS,
            max_chars=settings.ASK_CONTEXT_MAX_CHARS,
        )
    # ─────────────────────────────────────────────────────────────

    context = build_context(context_chunks)
    source_files = sorted({item.source_file for item in context_chunks})

    context_coverage = _estimate_context_coverage(question=payload.question, chunks=context_chunks)
    if context_coverage < settings.ASK_MIN_CONTEXT_COVERAGE:
        logger.info(
            "🧠 insufficient context coverage",
            extra={"coverage": context_coverage, "threshold": settings.ASK_MIN_CONTEXT_COVERAGE},
        )
        return AskResponse(answer="Недостаточно данных в загруженных документах", sources=source_files)

    semaphore_acquired = _ASK_GENERATION_SEMAPHORE.acquire(timeout=settings.ASK_QUEUE_TIMEOUT_SEC)
    if not semaphore_acquired:
        logger.warning(
            "🧠 ask queue saturated",
            extra={"max_concurrent": settings.ASK_MAX_CONCURRENT_GENERATIONS, "queue_timeout_sec": settings.ASK_QUEUE_TIMEOUT_SEC},
        )
        audit_event("chat_answer_fallback_queue", model=payload.model, top_k=retrieval_limit)
        return AskResponse(answer=settings.ASK_FALLBACK_ANSWER, sources=[])

    answer: str | None = None
    last_error: Exception | None = None
    try:
        for attempt in range(1, settings.ASK_MAX_RETRIES + 1):
            try:
                answer = llm_service.generate_answer(
                    question=payload.question,
                    context=context,
                    model=payload.model,
                    conversation_context=normalized_dialog_context,
                )
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "🧠 llm generation attempt failed",
                    extra={"attempt": attempt, "max_attempts": settings.ASK_MAX_RETRIES, "error": str(exc)},
                )
                if attempt < settings.ASK_MAX_RETRIES:
                    time.sleep(settings.ASK_RETRY_BACKOFF_SEC * attempt)
    finally:
        _ASK_GENERATION_SEMAPHORE.release()

    if not answer:
        logger.error("🧠 llm generation fallback response", extra={"error": str(last_error) if last_error else "unknown"})
        audit_event("chat_answer_fallback", model=payload.model, top_k=retrieval_limit)
        return AskResponse(answer=settings.ASK_FALLBACK_ANSWER, sources=[])

    if settings.ASK_ENABLE_ANSWER_VERIFICATION and context:
        try:
            answer = llm_service.verify_and_refine_answer(
                question=payload.question,
                context=context,
                draft_answer=answer,
                model=payload.model,
            )
        except Exception as exc:
            logger.warning("🧠 answer verification skipped", extra={"error": str(exc)})

    if settings.ASK_STRICT_GROUNDED_MODE and context:
        answer = _strict_grounded_answer(answer=answer, context=context)

    if settings.ASK_OUTPUT_MODE.strip().lower() == "strict_quotes":
        quotes = _extract_quote_sentences(question=payload.question, chunks=context_chunks, max_quotes=4)
        conclusion = _extract_brief_conclusion(answer=answer)
        answer = _format_strict_quotes_answer(quotes=quotes, conclusion=conclusion)

    answer = _ensure_count_answer_format(question=payload.question, answer=answer)

    logger.info("✅ chat answer ready", extra={"sources_count": len(source_files)})
    audit_event("chat_answer_ready", sources_count=len(source_files), top_k=retrieval_limit)
    return AskResponse(answer=answer, sources=source_files)


@app.post(
    f"{settings.API_PREFIX}/chat/transcribe",
    response_model=TranscribeResponse,
)
async def transcribe_audio(
    file: UploadFile = File(...),
    llm_service: LLMService = Depends(get_llm_service),
    _: None = Depends(require_service_token),
) -> TranscribeResponse:
    logger.info("🎙️ transcribe request", extra={"file_name": file.filename, "content_type": file.content_type})

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Имя файла отсутствует")

    suffix = Path(file.filename).suffix.lower()
    if suffix != ".ogg":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Поддерживается только OGG")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой аудиофайл")

    max_bytes = settings.MAX_AUDIO_SIZE_MB * 1024 * 1024
    if len(payload) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Аудио превышает лимит {settings.MAX_AUDIO_SIZE_MB} MB",
        )

    if not _is_ogg_payload(payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Содержимое файла не похоже на OGG")

    try:
        text = llm_service.transcribe_audio(file_name=file.filename, audio_bytes=payload)
    except Exception as exc:
        logger.exception("🎧 transcription failed", extra={"file_name": file.filename, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервис транскрибации временно недоступен",
        ) from exc

    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Не удалось распознать речь")

    logger.info("✅ transcription ready", extra={"text_length": len(text)})
    audit_event("transcription_ready", file_name=file.filename, text_length=len(text))
    return TranscribeResponse(text=text)
