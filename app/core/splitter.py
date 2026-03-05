from typing import List

class TextSplitter:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        """
        chunk_size: Максимальный размер кусочка (символов).
        chunk_overlap: Перекрытие (чтобы не терять смысл на стыках).
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> List[str]:
        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = start + self.chunk_size
            
            # Пытаемся найти конец предложения или абзаца, чтобы не резать слова пополам
            if end < text_len:
                # Ищем ближайший перенос строки или пробел
                last_newline = text.rfind('\n', start, end)
                if last_newline != -1 and last_newline > start + self.chunk_size // 2:
                    end = last_newline + 1
                else:
                    last_space = text.rfind(' ', start, end)
                    if last_space != -1 and last_space > start + self.chunk_size // 2:
                        end = last_space + 1
            
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            
            # Сдвигаем курсор назад на величину перекрытия
            start = end - self.chunk_overlap
            
            # Защита от вечного цикла, если overlap слишком большой
            if start >= end:
                start = end
                
        return chunks