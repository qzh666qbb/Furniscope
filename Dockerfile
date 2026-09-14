FROM python:3.12-slim

ARG PIP_INDEX_URL
ARG PIP_DEFAULT_TIMEOUT=600

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend

WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY forecast_assets ./forecast_assets

EXPOSE 8000
CMD ["python", "backend/docker_start.py"]
