# DocuMind AI worker image (CAB432 A3, n12136514)
# Multi-stage build: wheels are compiled/installed in the builder stage and
# copied into a slim, non-root runtime image for ECS Fargate (linux/amd64).
#
# Build (from backend/):  docker buildx build --platform linux/amd64 -t documind-worker .

FROM python:3.12-slim AS builder
WORKDIR /build
COPY worker/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app
RUN useradd --create-home --uid 10001 worker
COPY --from=builder /install /usr/local
WORKDIR /app
COPY worker/ ./worker/
COPY scaler/ ./scaler/
USER worker
CMD ["python", "-u", "worker/worker.py"]
