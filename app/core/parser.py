import logging
import os
from typing import Generator

import fitz  # PyMuPDF
import docx2txt

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path: str) -> str:
    """Читает PDF через мощный PyMuPDF (fitz)."""
    text = ""
    try:
        # Открываем документ
        with fitz.open(file_path) as doc:
            for page in doc:
                # get_text("text") достает чистый текст, игнорируя сложную верстку
                text += page.get_text("text") + "\n"
    except Exception as e:
        logger.warning("Ошибка чтения PDF %s: %s", file_path, e)
    return text

def extract_text_from_docx(file_path: str) -> str:
    """Читает DOCX."""
    try:
        text = docx2txt.process(file_path)
        return text
    except Exception as e:
        logger.warning("Ошибка чтения DOCX %s: %s", file_path, e)
        return ""

def read_data_folder(data_path: str) -> Generator[tuple[str, str], None, None]:
    """GENERATOR: Проходит по папке и ретурнирует (имяфайла, текст)."""
    if not os.path.exists(data_path):
        logger.error("Папка %s не найдена", data_path)
        return

    files_count = 0
    logger.info("Сканирую папку: %s", data_path)

    for filename in os.listdir(data_path):
        file_path = os.path.join(data_path, filename)
        
        if not os.path.isfile(file_path):
            continue

        text = ""
        # Теперь у нас мощный движок для PDF
        if filename.lower().endswith(".pdf"):
            text = extract_text_from_pdf(file_path)
        elif filename.lower().endswith(".docx"):
            text = extract_text_from_docx(file_path)
        else:
            continue

        if text.strip():
            files_count += 1
            yield filename, text 
    
    logger.info("Обработка завершена. Прочитано файлов: %d", files_count)