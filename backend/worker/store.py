"""Persistence helpers: DynamoDB (jobs + vectors), S3 artifacts, CloudWatch.

Embeddings are stored as native DynamoDB binary attributes (float32 little
endian, 4 bytes per dimension), which keeps each 1024-dim vector under 4 KB
and out of the item size hot path.
"""

import logging
import time

import boto3
from botocore.config import Config

from config import (
    AWS_REGION,
    BUCKET,
    JOBS_TABLE,
    VECTOR_BUCKET,
    VECTOR_INDEX,
    now_iso,
    ttl_epoch,
)

log = logging.getLogger("documind.store")

_cfg = Config(retries={"max_attempts": 5, "mode": "adaptive"})
_ddb = boto3.resource("dynamodb", region_name=AWS_REGION, config=_cfg)
_s3 = boto3.client("s3", region_name=AWS_REGION, config=_cfg)
_s3v = boto3.client("s3vectors", region_name=AWS_REGION, config=_cfg)
_cw = boto3.client("cloudwatch", region_name=AWS_REGION, config=_cfg)

jobs = _ddb.Table(JOBS_TABLE) if JOBS_TABLE else None


# ---------------------------------------------------------------------------
# Jobs table
# ---------------------------------------------------------------------------

def ensure_job(job_id: str, bucket: str, key: str, size_bytes: int):
    """Create the job row if the API has not already (idempotent)."""
    try:
        jobs.put_item(
            Item={
                "jobId": job_id,
                "listKey": "all",  # GSI partition for the recent-jobs query
                "status": "QUEUED",
                "filename": key.rsplit("/", 1)[-1],
                "s3Bucket": bucket,
                "s3Key": key,
                "sizeBytes": size_bytes,
                "createdAt": now_iso(),
                "updatedAt": now_iso(),
                "ttl": ttl_epoch(),
                "source": "s3-event",
            },
            ConditionExpression="attribute_not_exists(jobId)",
        )
    except _ddb.meta.client.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def update_job(job_id: str, **fields):
    """Update arbitrary job attributes (SET semantics)."""
    if not fields:
        return
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate(fields.items()):
        names[f"#f{i}"] = k
        values[f":v{i}"] = v
        sets.append(f"#f{i} = :v{i}")
    names["#updatedAt"] = "updatedAt"
    values[":updatedAt"] = now_iso()
    sets.append("#updatedAt = :updatedAt")
    jobs.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


# ---------------------------------------------------------------------------
# Vector index (Amazon S3 Vectors)
# ---------------------------------------------------------------------------

VECTOR_PUT_BATCH = 100


def put_chunks(job_id: str, filename: str, chunks: list, embeddings: list, model_id: str):
    """Write chunk embeddings to the S3 Vectors index in batches.

    Keys are ``<jobId>#<chunkIdx>`` so reprocessing the same document
    overwrites identical keys, keeping the pipeline idempotent under SQS
    redelivery. Chunk text rides along as metadata for citation snippets.
    """
    items = []
    for chunk, vec in zip(chunks, embeddings):
        items.append({
            "key": f"{job_id}#{chunk['idx']:04d}",
            "data": {"float32": [float(x) for x in vec]},
            "metadata": {
                "jobId": job_id,
                "filename": filename,
                "chunkIdx": chunk["idx"],
                "tokensEst": chunk["tokensEst"],
                "modelId": model_id,
                "text": chunk["text"],
            },
        })
    for i in range(0, len(items), VECTOR_PUT_BATCH):
        _s3v.put_vectors(vectorBucketName=VECTOR_BUCKET, indexName=VECTOR_INDEX,
                         vectors=items[i:i + VECTOR_PUT_BATCH])


# ---------------------------------------------------------------------------
# S3 artifacts
# ---------------------------------------------------------------------------

def download_to(key: str, path: str):
    _s3.download_file(BUCKET, key, path)


def put_text(key: str, body: str, content_type: str = "text/plain; charset=utf-8") -> str:
    _s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode("utf-8"), ContentType=content_type)
    return key


def put_summary(job_id: str, markdown: str) -> str:
    key = f"summaries/{job_id}.md"
    _s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=markdown.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
        CacheControl="public, max-age=3600",
    )
    return key


# ---------------------------------------------------------------------------
# CloudWatch custom metrics (worker namespace)
# ---------------------------------------------------------------------------

_metrics_warned = False


def emit_metric(name: str, value: float, unit: str = "Count"):
    """Best-effort custom metric; the shared task role lacks
    cloudwatch:PutMetricData, so warn once and then stay quiet."""
    global _metrics_warned
    try:
        _cw.put_metric_data(
            Namespace="DocuMind",
            MetricData=[{
                "MetricName": name,
                "Value": value,
                "Unit": unit,
                "Dimensions": [{"Name": "Service", "Value": "worker"}],
            }],
        )
    except Exception as exc:
        if not _metrics_warned:
            _metrics_warned = True
            log.warning("custom metrics unavailable via task role (%s); continuing", exc)
