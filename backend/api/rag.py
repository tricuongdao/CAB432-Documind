"""RAG pipeline: embed the question, retrieve passages from Amazon S3 Vectors,
synthesize a cited answer with Bedrock, and serve everything through a
two-tier result cache (L1 in-process, L2 DynamoDB).

Cache tiers:
  HIT-L1   in-process LRU+TTL, single-digit milliseconds
  HIT-L2   shared DynamoDB item, single-digit-millisecond GetItem
  MISS     full origin path: embed + vector search + LLM synthesis
"""

import hashlib
import json
import time

import boto3
from botocore.config import Config

from cache import get_l1
from config import (
    ANSWER_CACHE_TTL,
    AWS_REGION,
    DEFAULT_TOP_K,
    MAX_TOP_K,
    RAG_MAX_TOKENS,
    RAG_MODEL_ID,
    RETRIEVE_OVERFETCH,
    VECTOR_BUCKET,
    VECTOR_INDEX,
)
import llm
import store

_s3v = boto3.client(
    "s3vectors",
    region_name=AWS_REGION,
    config=Config(retries={"max_attempts": 3, "mode": "standard"},
                  connect_timeout=3, read_timeout=10),
)

PROMPT_TEMPLATE = (
    "You are DocuMind AI, a document question-answering assistant. "
    "Answer the user's question using ONLY the numbered excerpts below. "
    "If the answer is not contained in the excerpts, say so clearly. "
    "Cite the excerpts you use with bracketed numbers like [1] or [2]. "
    "Keep the answer concise (maximum 180 words).\n\n"
    "QUESTION: {question}\n\nEXCERPTS:\n{excerpts}"
)


def cache_key(question: str, top_k: int) -> str:
    norm = " ".join(question.lower().split())
    return hashlib.sha256(f"{norm}|{top_k}|idx1".encode("utf-8")).hexdigest()[:40]


def _live_jobs(job_ids: list) -> set:
    """Subset of job_ids whose job row still exists and is COMPLETED.

    Guards against stale vectors from documents whose metadata has since
    expired via DynamoDB TTL (the artifacts are gone even though the vector
    may briefly remain in the index).
    """
    if not job_ids:
        return set()
    keys = [{"jobId": j} for j in job_ids]
    resp = store.jobs.meta.client.batch_get_item(
        RequestItems={
            store.jobs.name: {
                "Keys": keys,
                "ProjectionExpression": "jobId, #s",
                "ExpressionAttributeNames": {"#s": "status"},
            }
        }
    )
    items = resp.get("Responses", {}).get(store.jobs.name, [])
    return {i["jobId"] for i in items if i.get("status") == "COMPLETED"}


def delete_job_vectors(job_id: str) -> int:
    """Delete every vector belonging to a job. Returns the count removed.

    Enumerates the index rather than deriving keys from the job's chunkCount,
    so vectors left behind by a partially processed job are removed too. The
    index holds one entry per chunk, so a full pass is cheap at this scale.
    """
    keys, token = [], None
    while True:
        kw = dict(vectorBucketName=VECTOR_BUCKET, indexName=VECTOR_INDEX,
                  maxResults=500, returnData=False, returnMetadata=True)
        if token:
            kw["nextToken"] = token
        r = _s3v.list_vectors(**kw)
        for v in r.get("vectors", []):
            if (v.get("metadata") or {}).get("jobId") == job_id:
                keys.append(v["key"])
        token = r.get("nextToken")
        if not token:
            break

    for i in range(0, len(keys), 100):
        _s3v.delete_vectors(vectorBucketName=VECTOR_BUCKET, indexName=VECTOR_INDEX,
                            keys=keys[i:i + 100])
    return len(keys)


def retrieve(question: str, top_k: int):
    """Embed + vector search. Returns (chunks, embed_ms, retrieve_ms)."""
    t0 = time.time()
    qvec = llm.embed_text(question)
    embed_ms = int((time.time() - t0) * 1000)

    t1 = time.time()
    resp = _s3v.query_vectors(
        vectorBucketName=VECTOR_BUCKET,
        indexName=VECTOR_INDEX,
        queryVector={"float32": [float(x) for x in qvec]},
        topK=max(top_k * RETRIEVE_OVERFETCH, top_k),
        returnMetadata=True,
        returnDistance=True,
    )
    retrieve_ms = int((time.time() - t1) * 1000)

    hits = resp.get("vectors", [])
    job_ids = sorted({(h.get("metadata") or {}).get("jobId", "") for h in hits} - {""})
    live = _live_jobs(job_ids)

    chunks = []
    for h in hits:
        md = h.get("metadata") or {}
        if md.get("jobId") not in live:
            continue
        chunks.append({
            "jobId": md.get("jobId"),
            "filename": md.get("filename", ""),
            "chunkIdx": int(md.get("chunkIdx", 0)),
            "text": md.get("text", ""),
            "score": round(1.0 - float(h.get("distance", 1.0)), 4),
        })
        if len(chunks) >= top_k:
            break
    return chunks, embed_ms, retrieve_ms


def synthesize(question: str, chunks: list):
    """Generate the cited answer. Returns (answer, generate_ms)."""
    excerpts = "\n\n".join(
        f"[{i + 1}] (source: {c['filename']}, part {c['chunkIdx'] + 1})\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    prompt = PROMPT_TEMPLATE.format(question=question, excerpts=excerpts)
    t0 = time.time()
    answer = llm.generate_text(RAG_MODEL_ID, prompt, max_tokens=RAG_MAX_TOKENS)
    return answer, int((time.time() - t0) * 1000)


def answer_question(question: str, top_k: int = None):
    """Full cached answering path. Returns (result_dict, tier).

    tier is one of HIT-L1 / HIT-L2 / MISS.
    """
    top_k = min(int(top_k or DEFAULT_TOP_K), MAX_TOP_K)
    key = cache_key(question, top_k)
    l1 = get_l1()

    hit = l1.get(f"q:{key}")
    if hit:
        payload, age_ms = hit
        out = dict(payload)
        out["cache"] = "HIT-L1"
        out["cacheAgeMs"] = age_ms
        return out, "HIT-L1"

    t_l2 = time.time()
    item = store.get_cache_item(key)
    payload = None
    if item:
        if item.get("payloadJson"):
            try:
                payload = json.loads(item["payloadJson"])
            except Exception:
                payload = None
        elif isinstance(item.get("payload"), dict):  # legacy nested-map rows
            payload = item["payload"]
    if isinstance(payload, dict) and float(item.get("ttl", 0)) > time.time():
        l1.set(f"q:{key}", payload)
        out = dict(payload)
        out["cache"] = "HIT-L2"
        out["cacheLookupMs"] = int((time.time() - t_l2) * 1000)
        return out, "HIT-L2"

    chunks, embed_ms, retrieve_ms = retrieve(question, top_k)
    if not chunks:
        # Do not cache "no documents" answers: the corpus may grow a moment
        # later and a stale negative would break the demo experience.
        return {
            "answer": "No matching documents were found. Upload a PDF first, "
                      "then ask a question about its contents.",
            "sources": [],
            "model": RAG_MODEL_ID,
            "cache": "MISS",
            "timings": {"embedMs": embed_ms, "retrieveMs": retrieve_ms, "generateMs": 0},
        }, "MISS"

    answer, gen_ms = synthesize(question, chunks)
    payload = {
        "answer": answer,
        "sources": chunks,
        "model": RAG_MODEL_ID,
        "timings": {"embedMs": embed_ms, "retrieveMs": retrieve_ms, "generateMs": gen_ms},
    }
    l1.set(f"q:{key}", payload)
    store.put_cache_item(key, payload, ANSWER_CACHE_TTL)
    out = dict(payload)
    out["cache"] = "MISS"
    return out, "MISS"
