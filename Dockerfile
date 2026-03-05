FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root user for security
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

COPY requirements.txt ./
# Install only prod deps (skip dev extras that only matter for CI)
RUN pip install --no-cache-dir \
      aiogram groq sentence-transformers faiss-cpu numpy \
      pymupdf docx2txt pydantic-settings \
      python-json-logger sentry-sdk prometheus-client

COPY app ./app
COPY scripts ./scripts

# Prepare runtime directories and transfer ownership
RUN mkdir -p /app/data /app/indices && \
    chown -R appuser:appgroup /app

USER appuser

CMD ["python", "-m", "app.main", "bot"]
