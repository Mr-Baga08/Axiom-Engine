# Python FastAPI application — multi-stage build.
# Stage 1 installs dependencies into a prefix; Stage 2 copies them into a slim image.

FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
# Download spaCy model needed by Presidio PII detection
RUN python -m spacy download en_core_web_sm --target /install/lib/python3.12/site-packages

FROM python:3.12-slim
RUN useradd -u 1000 -m appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
