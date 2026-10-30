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


def delete_job(job_id: str) -> bool:
    """Remove the job row. Returns True when the delete call succeeded."""
    try:
        jobs.delete_item(Key={"jobId": job_id})
        return True
    except Exception:
        return False


def purge_all_cache_items() -> int:
    """Delete every L2 answer-cache row.

    Deleting a document invalidates any cached answer that cited it, so the
    whole answer cache is dropped rather than surgically edited: cache keys
    hash the question, not the corpus, so there is no reverse index from a
    cached answer back to the documents it cited.

    This scans rather than querying the byCreated GSI on purpose. The GSI is
    hash=listKey / range=createdAt, and cache rows are written without a
    `createdAt`, so they are absent from the index and a GSI query returns
    nothing. A scan is exact for both current and older rows, and the table
    holds tens of items.
    """
    deleted = 0
    kwargs = {
        "FilterExpression": "listKey = :k",
        "ExpressionAttributeValues": {":k": "cache"},
        "ProjectionExpression": "jobId",
    }
    while True:
        resp = jobs.scan(**kwargs)
        for it in resp.get("Items", []):
            jobs.delete_item(Key={"jobId": it["jobId"]})
            deleted += 1
        if "LastEvaluatedKey" not in resp:
            return deleted
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]



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
        # createdAt is the byCreated GSI's range key; without it the row is not
        # indexed at all. Kept so the index stays a complete view of the table.
        "createdAt": now_iso(),
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


def delete_job_objects(job_id: str, s3_key: str = "") -> int:
    """Delete every S3 artifact derived from a job.

    Covers the raw upload (uploads/<job>/), the worker's extraction and chunk
    artifacts (processed/<job>/) and the generated summary (summaries/<job>.md).
    Deleting a key that does not exist is a no-op in S3, so this is safe to
    call for partially processed jobs.
    """
    keys = []
    for prefix in (f"uploads/{job_id}/", f"processed/{job_id}/"):
        token = None
        while True:
            kw = {"Bucket": S3_BUCKET, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            r = _s3.list_objects_v2(**kw)
            keys += [o["Key"] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                break
            token = r.get("NextContinuationToken")
    keys.append(f"summaries/{job_id}.md")
    if s3_key:
        keys.append(s3_key)

    keys = sorted(set(keys))
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = keys[i:i + 1000]
        _s3.delete_objects(Bucket=S3_BUCKET,
                           Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True})
        deleted += len(batch)
    return deleted



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
