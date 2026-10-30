#!/usr/bin/env python3
"""DocuMind AI worker: long-polls SQS and runs the document pipeline.

Per message: download the PDF from S3 -> extract text -> sentence-aware
chunking -> Bedrock embeddings -> markdown summary -> persist to S3 +
DynamoDB and update job status.

Failure model:
  * Poison inputs (corrupt / password-protected / text-free PDFs, malformed
    messages) are forwarded to the DLQ immediately and the job is marked
    FAILED.
  * Transient errors leave the message on the queue for redelivery; after
    MAX_RECEIVES deliveries the worker forwards it to the DLQ itself (the
    queue's redrive policy remains as a second safety net).
  * SIGTERM (ECS scale-in / task replacement) finishes the in-flight message
    and exits cleanly; an interrupted message is simply redelivered.
"""

import json
import logging
import os
import signal
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from config import (
    AWS_REGION,
    DLQ_URL,
    EMBED_MODEL_ID,
    EMBED_WORKERS,
    JOBS_TABLE,
    MAX_PDF_BYTES,
    QUEUE_URL,
    SUMMARY_INPUT_CHARS,
    SUMMARY_MODEL_ID,
    VECTOR_BUCKET,
    WORKER_IDLE_EXIT_SECONDS,
    now_iso,
    ttl_epoch,
)
import extract
import llm
import store
from extract import PoisonError

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("documind.worker")

MAX_RECEIVES = 3  # then the worker self-routes to the DLQ (queue redrive = backstop)

_shutdown = threading.Event()


def _on_signal(signum, _frame):
    log.info("signal %s received, finishing current message then exiting", signum)
    _shutdown.set()


signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT, _on_signal)

_sqs = boto3.client(
    "sqs",
    region_name=AWS_REGION,
    config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
)


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def parse_event(body: dict):
    """Return (bucket, key, size) from an EventBridge/S3/test message body."""
    if isinstance(body, dict) and "bucket" in body and "key" in body:
        # Direct test payload (used by smoke tests and manual replays).
        return body["bucket"], body["key"], int(body.get("size", 0))
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and "bucket" in detail and "object" in detail:
        # EventBridge "Object Created" envelope.
        obj = detail["object"]
        return detail["bucket"]["name"], obj["key"], int(obj.get("size", 0))
    records = body.get("Records") if isinstance(body, dict) else None
    if records:
        s3o = records[0]["s3"]
        return s3o["bucket"]["name"], s3o["object"]["key"], int(s3o["object"].get("size", 0))
    raise PoisonError(f"unrecognised message body: {str(body)[:200]}")


def job_id_from_key(key: str) -> str:
    """Uploads live at uploads/<jobId>/<filename>."""
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] == "uploads" and parts[1]:
        return parts[1]
    raise PoisonError(f"unexpected object key (want uploads/<jobId>/<file>): {key}")


# ---------------------------------------------------------------------------
# DLQ
# ---------------------------------------------------------------------------

def send_to_dlq(raw_body: str, reason: str, message_id: str = ""):
    if not DLQ_URL:
        log.error("no DLQ configured, dropping poison message: %s", reason)
        return
    try:
        _sqs.send_message(
            QueueUrl=DLQ_URL,
            MessageBody=raw_body,
            MessageAttributes={
                "Reason": {"DataType": "String", "StringValue": reason[:256]},
                "OriginalMessageId": {"DataType": "String", "StringValue": message_id[:64]},
                "FailedAt": {"DataType": "String", "StringValue": now_iso()},
                "Worker": {"DataType": "String", "StringValue": socket.gethostname()[:64]},
            },
        )
        log.warning("routed message to DLQ: %s", reason[:160])
    except Exception as exc:
        log.error("DLQ send failed (%s); message will be redelivered", exc)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def embed_chunks(chunks: list) -> list:
    """Embed chunks concurrently, preserving order."""
    with ThreadPoolExecutor(max_workers=EMBED_WORKERS) as pool:
        futures = [pool.submit(llm.embed_text, c["text"]) for c in chunks]
        return [f.result() for f in futures]


def summary_prompt(text: str, filename: str) -> str:
    body = text[:SUMMARY_INPUT_CHARS]
    return (
        "You are DocuMind, a document analysis assistant. "
        f"Write a concise executive summary of the document '{filename}' in 120 to 180 words. "
        "Cover its topic, purpose and the most important points. "
        "Use short paragraphs or bullet points. Output markdown only, with no preamble.\n\n"
        "DOCUMENT TEXT:\n" + body
    )


def build_summary_markdown(job_id, filename, summary, meta) -> str:
    lines = [
        f"# DocuMind summary: {filename}",
        "",
        summary.strip(),
        "",
        "---",
        "",
        f"* Job ID: `{job_id}`",
        f"* Pages: {meta['pages']} | Chunks: {meta['chunks']} | Embedding model: `{EMBED_MODEL_ID}`",
        f"* Summary model: `{SUMMARY_MODEL_ID}`",
        f"* Generated: {now_iso()} (artifacts auto-expire after {meta['ttlHours']}h)",
    ]
    return "\n".join(lines) + "\n"


def process_message(msg: dict) -> bool:
    """Handle one SQS message. Returns True when it may be deleted."""
    receive_count = int((msg.get("Attributes") or {}).get("ApproximateReceiveCount", "1"))
    raw_body = msg.get("Body", "")
    job_id = None
    try:
        body = json.loads(raw_body)
    except Exception:
        send_to_dlq(raw_body, "invalid JSON body", msg.get("MessageId", ""))
        return True

    try:
        bucket, key, size = parse_event(body)
        if not key.lower().startswith("uploads/"):
            # Defensive: the EventBridge rule filters to uploads/, but never
            # let a stray event (e.g. a worker-written artifact) be retried.
            log.info("ignoring non-upload object: %s", key)
            return True
        job_id = job_id_from_key(key)
        filename = key.rsplit("/", 1)[-1]

        if not key.lower().endswith(".pdf"):
            raise PoisonError(f"not a PDF upload: {key}")
        if size <= 0:
            raise PoisonError("empty upload (0 bytes)")
        if size > MAX_PDF_BYTES:
            raise PoisonError(f"upload too large ({size} bytes > {MAX_PDF_BYTES})")

        # The notification may outlive the object: deleting a document removes
        # the upload but cannot recall an S3 event already on the queue. If the
        # object is gone there is nothing to process, so discard the message
        # instead of creating a phantom job that can only end as FAILED.
        if not store.object_exists(key):
            log.warning("upload no longer present, discarding stale notification: %s", key)
            store.emit_metric("StaleUploadDiscarded", 1)
            return True

        store.ensure_job(job_id, bucket, key, size)
        t0 = time.time()
        store.update_job(job_id, status="PROCESSING", receiveCount=receive_count,
                         worker=socket.gethostname())
        log.info("job %s PROCESSING (%s, %s bytes, receive %s)", job_id, key, size, receive_count)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.close()
        try:
            try:
                store.download_to(key, tmp.name)
            except ClientError as exc:
                # Deleted between the existence check and the download.
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in ("404", "NoSuchKey", "NotFound"):
                    log.warning("upload vanished mid-flight, discarding: %s", key)
                    store.emit_metric("StaleUploadDiscarded", 1)
                    return True
                raise
            extracted = extract.extract_text(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        extract_ms = int((time.time() - t0) * 1000)

        chunks = chunk_text_safe(extracted["text"])
        if not chunks:
            raise PoisonError("no chunks produced from extracted text")

        store.put_text(f"processed/{job_id}/extracted.txt", extracted["text"])
        store.put_text(
            f"processed/{job_id}/chunks.json",
            json.dumps({
                "jobId": job_id,
                "filename": filename,
                "chunkCount": len(chunks),
                "chunks": [{"idx": c["idx"], "chars": c["chars"], "tokensEst": c["tokensEst"],
                            "overlapChars": c["overlapChars"], "text": c["text"]} for c in chunks],
            }, indent=2),
        )
        store.update_job(job_id, status="EMBEDDING", chunkCount=len(chunks),
                         pageCount=extracted["pages"], extractMs=extract_ms)

        t1 = time.time()
        embeddings = embed_chunks(chunks)
        store.put_chunks(job_id, filename, chunks, embeddings, EMBED_MODEL_ID)
        embed_ms = int((time.time() - t1) * 1000)
        log.info("job %s embedded %s chunks in %sms", job_id, len(chunks), embed_ms)

        t2 = time.time()
        summary = llm.generate_text(
            SUMMARY_MODEL_ID, summary_prompt(extracted["text"], filename), max_tokens=600)
        summary_key = store.put_summary(job_id, build_summary_markdown(
            job_id, filename, summary,
            {"pages": extracted["pages"], "chunks": len(chunks),
             "ttlHours": store_job_ttl_hours()}))
        summary_ms = int((time.time() - t2) * 1000)

        total_ms = int((time.time() - t0) * 1000)
        store.update_job(
            job_id,
            status="COMPLETED",
            completedAt=now_iso(),
            summaryKey=summary_key,
            embeddingDim=len(embeddings[0]),
            timings={"extractMs": extract_ms, "embedMs": embed_ms, "summaryMs": summary_ms,
                     "totalMs": total_ms},
            ttl=ttl_epoch(),
        )
        store.emit_metric("DocumentsCompleted", 1)
        store.emit_metric("DocumentProcessingMs", total_ms, "Milliseconds")
        log.info("job %s COMPLETED in %sms (extract=%s embed=%s summary=%s)",
                 job_id, total_ms, extract_ms, embed_ms, summary_ms)
        return True

    except PoisonError as exc:
        log.warning("poison message (job=%s): %s", job_id, exc)
        send_to_dlq(raw_body, f"poison: {exc}", msg.get("MessageId", ""))
        if job_id:
            _safe_fail(job_id, str(exc))
        return True

    except Exception as exc:  # transient: network, throttling, OOM-adjacent, ...
        log.exception("transient failure on job=%s (receive %s): %s", job_id, receive_count, exc)
        if receive_count >= MAX_RECEIVES:
            send_to_dlq(raw_body, f"retries exhausted after {receive_count} receives: {exc}",
                        msg.get("MessageId", ""))
            if job_id:
                _safe_fail(job_id, f"retries exhausted: {exc}")
            return True
        if job_id:
            try:
                store.update_job(job_id, attempts=receive_count, lastError=str(exc)[:512])
            except Exception:
                pass
        return False  # leave on the queue for redelivery


def chunk_text_safe(text: str) -> list:
    from chunker import chunk_text
    return chunk_text(text)


def store_job_ttl_hours() -> int:
    from config import ARTIFACT_TTL_HOURS
    return ARTIFACT_TTL_HOURS


def _safe_fail(job_id: str, reason: str):
    try:
        store.update_job(job_id, status="FAILED", error=reason[:512], failedAt=now_iso())
    except Exception as exc:
        log.error("could not mark job %s FAILED: %s", job_id, exc)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> int:
    missing = [name for name, val in [
        ("SQS_QUEUE_URL", QUEUE_URL), ("S3_BUCKET", os.environ.get("S3_BUCKET", "")),
        ("JOBS_TABLE", JOBS_TABLE), ("VECTOR_BUCKET", VECTOR_BUCKET),
    ] if not val]
    if missing:
        log.error("missing required configuration: %s", ", ".join(missing))
        return 2

    idle_exit = WORKER_IDLE_EXIT_SECONDS
    mode = f"standalone (idle exit {idle_exit}s)" if idle_exit > 0 else "service (run forever)"
    log.info("DocuMind worker starting: region=%s queue=%s bucket=%s host=%s pid=%s mode=%s",
             AWS_REGION, QUEUE_URL, os.environ.get("S3_BUCKET", ""), socket.gethostname(),
             os.getpid(), mode)

    last_activity = time.time()
    while not _shutdown.is_set():
        try:
            resp = _sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=300,
                AttributeNames=["All"],
            )
        except Exception as exc:
            log.error("receive_message failed: %s", exc)
            time.sleep(5)
            continue

        messages = resp.get("Messages", [])
        if not messages:
            if idle_exit > 0 and (time.time() - last_activity) >= idle_exit:
                log.info("no work for %ss, exiting (standalone task done, backlog empty)", idle_exit)
                store.emit_metric("WorkerIdleExit", 1)
                break
            continue

        msg = messages[0]
        ok = process_message(msg)
        last_activity = time.time()
        if ok and not _shutdown.is_set():
            try:
                _sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
            except Exception as exc:
                log.error("delete_message failed (message will be redelivered): %s", exc)

    log.info("DocuMind worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
