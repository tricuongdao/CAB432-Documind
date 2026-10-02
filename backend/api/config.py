"""DocuMind AI API configuration (CAB432 Assessment 3, student n12136514).

All values are injected via the Lambda function environment; nothing secret
lives in code. The admin API key is read from Secrets Manager at runtime.
"""

import os
import time

AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

# Storage / data
JOBS_TABLE = os.environ.get("JOBS_TABLE", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")        # document storage (private)
WEB_BUCKET = os.environ.get("WEB_BUCKET", "")      # built SPA assets
VECTOR_BUCKET = os.environ.get("VECTOR_BUCKET", "")  # Amazon S3 Vectors
VECTOR_INDEX = os.environ.get("VECTOR_INDEX", "")

# Bedrock models
EMBED_MODEL_ID = os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))
RAG_MODEL_ID = os.environ.get("RAG_MODEL_ID", "google.gemma-3-12b-it")
RAG_MAX_TOKENS = int(os.environ.get("RAG_MAX_TOKENS", "550"))

# Secrets
ADMIN_SECRET_ARN = os.environ.get("ADMIN_SECRET_ARN", "")
SECRET_TTL = int(os.environ.get("SECRET_TTL", "300"))

# Uploads
UPLOAD_URL_TTL = int(os.environ.get("UPLOAD_URL_TTL", "900"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

# Caching
ANSWER_CACHE_TTL = int(os.environ.get("ANSWER_CACHE_TTL", "3600"))
STATUS_CACHE_TTL = int(os.environ.get("STATUS_CACHE_TTL", "3"))
LIST_CACHE_TTL = int(os.environ.get("LIST_CACHE_TTL", "2"))
SUMMARY_CACHE_TTL = int(os.environ.get("SUMMARY_CACHE_TTL", "300"))
STATIC_CACHE_TTL = int(os.environ.get("STATIC_CACHE_TTL", "600"))
L1_CAPACITY = int(os.environ.get("L1_CAPACITY", "512"))

# Retrieval
DEFAULT_TOP_K = int(os.environ.get("DEFAULT_TOP_K", "4"))
MAX_TOP_K = int(os.environ.get("MAX_TOP_K", "8"))
RETRIEVE_OVERFETCH = int(os.environ.get("RETRIEVE_OVERFETCH", "3"))

APP_VERSION = "1.0.0"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ttl_epoch(seconds: int) -> int:
    return int(time.time()) + seconds
