"""DocuMind AI worker configuration (CAB432 Assessment 3, student n12136514).

All values are supplied via the ECS task definition environment. Nothing
secret is baked into the image; AWS credentials come from the ECS task role
at runtime, so the container itself holds no static credentials.
"""

import os
import time

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

# Messaging (validated in main(), left permissive here so local tests can
# import this module without AWS configuration).
QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
DLQ_URL = os.environ.get("SQS_DLQ_URL", "")

# Storage
BUCKET = os.environ.get("S3_BUCKET", "")
JOBS_TABLE = os.environ.get("JOBS_TABLE", "")
# Vector store (Amazon S3 Vectors)
VECTOR_BUCKET = os.environ.get("VECTOR_BUCKET", "")
VECTOR_INDEX = os.environ.get("VECTOR_INDEX", "")

# ---------------------------------------------------------------------------
# Bedrock models
# ---------------------------------------------------------------------------
# Embeddings: Titan Text Embeddings V2 (1024 dims by default) or V1 (1536).
EMBED_MODEL_ID = os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))
# Summary generation for the downloadable markdown export.
SUMMARY_MODEL_ID = os.environ.get("SUMMARY_MODEL_ID", "amazon.nova-lite-v1:0")

# ---------------------------------------------------------------------------
# Pipeline tuning
# ---------------------------------------------------------------------------
CHUNK_TARGET_CHARS = int(os.environ.get("CHUNK_TARGET_CHARS", "1000"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", "180"))
MAX_CHUNKS = int(os.environ.get("MAX_CHUNKS", "400"))
EMBED_WORKERS = int(os.environ.get("EMBED_WORKERS", "4"))
MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_BYTES", str(25 * 1024 * 1024)))
MIN_TEXT_CHARS = int(os.environ.get("MIN_TEXT_CHARS", "200"))
SUMMARY_INPUT_CHARS = int(os.environ.get("SUMMARY_INPUT_CHARS", "14000"))

# Artifact lifetime: DynamoDB TTL rows and (matching) S3 lifecycle rules.
ARTIFACT_TTL_HOURS = int(os.environ.get("ARTIFACT_TTL_HOURS", "24"))

# Task-pool mode: standalone tasks exit after this many idle seconds (0 = run
# forever, used when the worker is attached to a long-running ECS service).
WORKER_IDLE_EXIT_SECONDS = int(os.environ.get("WORKER_IDLE_EXIT_SECONDS", "0"))


def ttl_epoch(hours=None) -> int:
    """Unix epoch seconds for DynamoDB TTL (now + artifact lifetime)."""
    return int(time.time()) + (hours if hours is not None else ARTIFACT_TTL_HOURS) * 3600


def now_iso() -> str:
    """UTC timestamp in ISO-8601 (seconds precision)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
