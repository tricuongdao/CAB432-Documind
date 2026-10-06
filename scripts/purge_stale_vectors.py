#!/usr/bin/env python3
"""Purge orphaned vectors from the S3 Vectors index.

Vectors written for a job linger after its DynamoDB row expires via the 24 h
TTL. The retrieval path filters those stale vectors out at query time (the
"live jobs" guard), but they still consume top-K slots during ranking, which
can drown out freshly uploaded documents for generic questions.

This sweep deletes vectors whose job record is gone or logically expired so
ranking only ever considers live content. Safe to run repeatedly.

Usage:
    python scripts/purge_stale_vectors.py            # sweep expired/orphaned jobs
    python scripts/purge_stale_vectors.py --dry-run  # report only
    python scripts/purge_stale_vectors.py --job job-abc123   # delete one job's vectors
"""
import argparse
import time
from collections import Counter

import boto3

REGION = "ap-southeast-2"
VECTOR_BUCKET = "n12136514-documind-vectors"
VECTOR_INDEX = "documind-chunks"
JOBS_TABLE = "n12136514-documind-jobs"
DELETE_BATCH = 100

s3v = boto3.client("s3vectors", region_name=REGION)
ddb = boto3.client("dynamodb", region_name=REGION)


def list_all_vectors():
    out, tok = [], None
    while True:
        kw = dict(vectorBucketName=VECTOR_BUCKET, indexName=VECTOR_INDEX,
                  maxResults=500, returnData=False, returnMetadata=True)
        if tok:
            kw["nextToken"] = tok
        r = s3v.list_vectors(**kw)
        for v in r.get("vectors", []):
            md = v.get("metadata") or {}
            out.append((v["key"], md.get("jobId", "")))
        tok = r.get("nextToken")
        if not tok:
            return out


def live_status(job_ids):
    """Return {jobId: alive_bool} for the given ids (chunked BatchGetItem)."""
    alive = {}
    ids = sorted(set(job_ids))
    now = int(time.time())
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        resp = ddb.batch_get_item(RequestItems={
            JOBS_TABLE: {"Keys": [{"jobId": {"S": j}} for j in chunk],
                         "ProjectionExpression": "jobId, #s, #t",
                         "ExpressionAttributeNames": {"#s": "status", "#t": "ttl"}}
        })
        items = resp.get("Responses", {}).get(JOBS_TABLE, [])
        seen = set()
        for it in items:
            jid = it["jobId"]["S"]
            seen.add(jid)
            ttl = it.get("ttl", {}).get("N")
            expired = ttl is not None and int(ttl) < now
            alive[jid] = not expired
        for j in chunk:
            if j not in seen:
                alive[j] = False  # row already deleted (or never existed)
    return alive


def delete_keys(keys, dry):
    n = 0
    for i in range(0, len(keys), DELETE_BATCH):
        batch = keys[i:i + DELETE_BATCH]
        if dry:
            print(f"  [dry-run] would delete {len(batch)} vectors")
        else:
            s3v.delete_vectors(vectorBucketName=VECTOR_BUCKET, indexName=VECTOR_INDEX,
                               keys=batch)
            print(f"  deleted {len(batch)} vectors")
        n += len(batch)
        time.sleep(0.2)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--job", help="delete all vectors for this jobId regardless of liveness")
    args = ap.parse_args()

    vectors = list_all_vectors()
    by_job = Counter(j for _, j in vectors)
    print(f"index holds {len(vectors)} vectors across {len(by_job)} jobs")

    if args.job:
        keys = [k for k, j in vectors if j == args.job]
        print(f"targeting job {args.job}: {len(keys)} vectors")
        delete_keys(keys, args.dry_run)
        return

    live = live_status(list(by_job))
    stale_jobs = [j for j, ok in live.items() if not ok]
    stale_keys = [k for k, j in vectors if not live.get(j, False)]
    print(f"live jobs: {sum(1 for ok in live.values() if ok)} | stale jobs: {len(stale_jobs)} "
          f"| stale vectors: {len(stale_keys)}")
    if not stale_keys:
        print("nothing to do")
        return
    print("stale jobs:", ", ".join(sorted(stale_jobs)[:12]) + (" …" if len(stale_jobs) > 12 else ""))
    deleted = delete_keys(stale_keys, args.dry_run)
    print(f"{'would delete' if args.dry_run else 'deleted'} {deleted} vectors "
          f"| remaining: {len(vectors) - deleted}")


if __name__ == "__main__":
    main()
