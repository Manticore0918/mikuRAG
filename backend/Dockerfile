FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY evaluation ./evaluation
COPY alembic.ini ./
COPY migrations ./migrations

# The reranker is CPU-bound in this image. Install PyTorch from its CPU wheel
# index first so pip cannot silently pull multi-gigabyte CUDA runtime packages.
# The model itself remains lazy-loaded on first use.
ARG PYTORCH_VERSION=2.12.1
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==${PYTORCH_VERSION}" \
    && pip install --no-cache-dir ".[otel,rerank]"

RUN useradd --create-home --uid 10001 mikurag \
    && mkdir -p /data/uploads /data/models \
    && chown -R mikurag:mikurag /app /data/uploads /data/models

USER mikurag

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
