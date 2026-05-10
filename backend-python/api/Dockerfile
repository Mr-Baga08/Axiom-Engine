# Python FastAPI API — dev image.
# Packages are installed at build time so the container starts immediately.
# Source code is volume-mounted for --reload hot-reloading.

FROM python:3.12-slim

# Install system deps needed by pymupdf
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (separate layer — rebuilt only when requirements.txt changes)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -q -r /app/requirements.txt

# Download spaCy model for Presidio PII detection at build time (not at startup).
# en_core_web_sm (~12 MB) is sufficient for PERSON/EMAIL/PHONE/IP detection.
RUN python -m spacy download en_core_web_sm

# Working directory for uvicorn — source is volume-mounted over this at runtime
WORKDIR /app/api

ENV PYTHONPATH=/app/api
ENV PYTHONWARNINGS=ignore::FutureWarning

EXPOSE 8000

# --reload watches for file changes in the volume-mounted source
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
