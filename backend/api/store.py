"""Data access for the API service: DynamoDB (jobs + shared cache items),
S3 (presigned uploads, summaries, web assets), Secrets Manager (admin key)."""

import json

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from config import (
    ADMIN_SECRET_ARN,
    AWS_REGION,
    JOBS_TABLE,
    S3_BUCKET,
    UPLOAD_URL_TTL,
    WEB_BUCKET,
    now_iso,
    ttl_epoch,
)

_cfg = Config(retries={"max_attempts": 3, "mode": "standard"},
              connect_timeout=3, read_timeout=10)

_ddb = boto3.resource("dynamodb", region_name=AWS_REGION, config=_cfg)
# Force SigV4 + the regional S3 endpoint: presigned URLs must point straight
# at s3.<region>.amazonaws.com. A global-endpoint URL (s3.amazonaws.com)
# answers presigned PUTs with a 307 redirect, which browsers fail cross-origin
# (CORS on the redirect hop) even though command-line clients follow it.
_s3 = boto3.client(
    "s3", region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=Config(signature_version="s3v4",
                  s3={"addressing_style": "virtual"},
                  retries={"max_attempts": 3, "mode": "standard"},
                  connect_timeout=3, read_timeout=10),
)
_sm = boto3.client("secretsmanager", region_name=AWS_REGION, config=_cfg)

jobs = _ddb.Table(JOBS_TABLE) if JOBS_TABLE else None


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def put_new_job(job_id: str, filename: str, size_bytes: int = 0):
    jobs.put_item(Item={
        "jobId": job_id,
        "listKey": "all",
        "status": "CREATED",
        "filename": filename,
        "s3Key": f"uploads/{job_id}/{filename}",
        "sizeBytes": size_bytes,
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "ttl": ttl_epoch(24 * 3600),
        "source": "api",
    })


def get_job(job_id: str) -> dict:
    resp = jobs.get_item(Key={"jobId": job_id})
    return resp.get("Item") or {}


def list_jobs(limit: int = 25) -> list:
    resp = jobs.query(
        IndexName="byCreated",
        KeyConditionExpression="listKey = :k",
        ExpressionAttributeValues={":k": "all"},
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


# ---------------------------------------------------------------------------
# L2 shared cache (DynamoDB items in the jobs table; heterogeneous items are
# an explicit design choice, see the report).
# ---------------------------------------------------------------------------

def get_cache_item(key: str) -> dict:
    resp = jobs.get_item(Key={"jobId": f"cache#{key}"})
    return resp.get("Item") or {}


def put_cache_item(key: str, payload: dict, ttl_seconds: int):
    """Store the answer payload as a JSON string.

    A JSON string attribute sidesteps DynamoDB's number-type constraints
    (boto3 rejects Python floats and returns Decimals) so cached answers
    round-trip byte-for-byte through the L2 tier.
    """
    jobs.put_item(Item={
        "jobId": f"cache#{key}",
        "listKey": "cache",
        "payloadJson": json.dumps(payload),
        "cachedAt": now_iso(),
        "ttl": ttl_epoch(ttl_seconds),
    })


def delete_cache_item(key: str) -> bool:
    try:
        jobs.delete_item(Key={"jobId": f"cache#{key}"})
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def presign_put(key: str, content_type: str = "application/pdf",
                expires: int = UPLOAD_URL_TTL) -> str:
    return _s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": S3_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def get_summary_bytes(job_id: str):
    try:
        obj = _s3.get_object(Bucket=S3_BUCKET, Key=f"summaries/{job_id}.md")
        return obj["Body"].read()
    except ClientError:
        return None


_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".json": "application/json",
    ".map": "application/json",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def get_web_asset(path: str):
    """Fetch a built SPA asset from the web bucket. Returns (bytes, ctype)."""
    key = path.lstrip("/") or "index.html"
    try:
        obj = _s3.get_object(Bucket=WEB_BUCKET, Key=key)
        ctype = obj.get("ContentType") or "application/octet-stream"
    except ClientError:
        return None, None
    data = obj["Body"].read()
    if ctype in ("binary/octet-stream", "application/octet-stream"):
        for ext, guess in _CTYPES.items():
            if key.endswith(ext):
                ctype = guess
                break
    return data, ctype


# ---------------------------------------------------------------------------
# Secrets Manager (admin key)
# ---------------------------------------------------------------------------

def get_admin_key():
    if not ADMIN_SECRET_ARN:
        return None
    try:
        raw = _sm.get_secret_value(SecretId=ADMIN_SECRET_ARN)["SecretString"]
        return json.loads(raw).get("apiAdminKey")
    except Exception:
        return None
