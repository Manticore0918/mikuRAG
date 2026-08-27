FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY evaluation ./evaluation
COPY alembic.ini ./
COPY migrations ./migrations

# The production image includes the optional local reranker so selecting
# MIKURAG_RERANKER_PROVIDER=cross_encoder never degrades merely because the
# package was omitted at build time. The model itself remains lazy-loaded.
RUN pip install --no-cache-dir ".[rerank]"

RUN useradd --create-home --uid 10001 mikurag \
    && mkdir -p /data/uploads \
    && chown -R mikurag:mikurag /app /data/uploads

USER mikurag

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
