#!/usr/bin/env python3
"""DocuMind AI empirical evidence generator (CAB432 A3, n12136514).

Modes
    pipeline   Upload N synthetic PDFs through the real presigned flow while
               sampling queue depth and ECS task counts; wait for the pool to
               drain and (optionally) for scale-in to zero. Produces the
               auto-scaling evidence timeline.
    cache      Ask a battery of unique questions twice (origin then cached)
               plus a concurrent-repeat burst. Produces the cache latency
               percentiles (p50/p95/p99) evidence.
    dlq        Inject poison documents (password-protected, corrupt, blank)
               and verify dead-letter routing and job failure marking.

Outputs land in docs/evidence/ as JSON (+CSV for the pipeline timeline).
"""

import argparse
import csv
import json
import os
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
import requests

REGION = "ap-southeast-2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(ROOT, "docs", "evidence")
os.makedirs(EVIDENCE, exist_ok=True)
TMP = os.environ.get("TMPDIR", ".")

QUEUE_URL = "https://sqs.ap-southeast-2.amazonaws.com/901444280953/n12136514-documind-queue"
DLQ_URL = "https://sqs.ap-southeast-2.amazonaws.com/901444280953/n12136514-documind-dlq"
CLUSTER = "n12136514-documind-cluster"
WORKER_SVC = "n12136514-documind-worker-svc"


def discover_endpoint():
    ep = os.environ.get("DOCUMIND_ENDPOINT")
    if ep:
        return ep.rstrip("/")
    apigw = boto3.client("apigatewayv2", region_name=REGION)
    for a in apigw.get_apis().get("Items", []):
        if a.get("Name") == "n12136514-strobe-api":
            return a["ApiEndpoint"].rstrip("/") + "/documind"
    lam = boto3.client("lambda", region_name=REGION)
    return lam.get_function_url_config(FunctionName="n12136514-documind-api")["FunctionUrl"].rstrip("/")


def ts():
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def pct(data, p):
    if not data:
        return None
    data = sorted(data)
    k = min(len(data) - 1, max(0, int(round((p / 100.0) * (len(data) - 1)))))
    return data[k]


TOPICS = [
    ("Vector Search and Semantic Retrieval",
     ["Embeddings map text into a high dimensional space where semantic similarity becomes geometric proximity. Cosine similarity compares the angle between vectors, so documents about the same topic cluster together regardless of wording.",
      "The retrieval pipeline embeds the incoming question with the same model used for the corpus, then queries the vector index for the nearest neighbours. Top-k results are filtered against live job metadata so expired documents never leak into answers.",
      "Operators tune k and the similarity threshold to balance recall against prompt size. Larger contexts improve completeness but increase latency and token cost, so production systems often over-fetch and rerank."]),
    ("Queue Based Work Distribution",
     ["A message queue decouples producers from consumers. Uploads land in object storage, an event reaches the queue, and a pool of workers pulls messages at their own pace. Backpressure is implicit: the queue absorbs bursts while the pool stays saturated.",
      "Visibility timeouts guard against worker crashes: a message that is not deleted within the timeout becomes visible again for redelivery. Poison messages that repeatedly fail are routed to a dead letter queue after a bounded number of attempts.",
      "Queue depth is the natural scaling signal. Comparing visible plus in-flight messages against a target backlog per worker gives a direct, observable control loop for pool size."]),
    ("Caching Layers and Latency Budgets",
     ["An in-process LRU cache with TTL expiry answers repeated queries in single digit milliseconds because no network hop is involved. A shared second tier survives container recycling and lets every warm execution environment reuse results.",
      "Cache keys must capture everything that changes an answer: normalized question text, retrieval parameters, and a corpus version marker. Without the version marker, stale answers outlive the documents they reference.",
      "Hit ratio and hit latency percentiles are the operational metrics that matter. A cache that is fast but rarely hit does not reduce cost; a cache that hits often but serves stale data destroys trust."]),
    ("Dead Letter Queues and Fault Isolation",
     ["Fault isolation means one bad input never blocks the pipeline. Bounded retries with exponential backoff separate transient failures, such as throttling, from permanent ones, such as a password-protected file that can never be parsed.",
      "Poison messages are forwarded to a dead letter queue with failure metadata: reason, original message identifier, and the time of failure. Operators inspect the dead letter queue to find input classes that deserve first-class validation.",
      "Alarming on dead letter queue depth converts a silent failure into a visible signal. The queue stops growing only when the underlying input problem is fixed."]),
    ("Container Scaling Control Loops",
     ["A metric based autoscaler reads an operational signal, compares it with a target, and adjusts capacity. With a target backlog of two messages per worker, a thirty message burst requests six workers until the queue drains.",
      "Scale to zero is the strongest cost optimisation: when the backlog is empty the desired count drops to zero and the compute bill follows. Cold start latency is the price paid, measured in tens of seconds for container tasks.",
      "Control loops should be idempotent and hysteresis aware. Comparing desired against current capacity on every tick prevents flapping, and cooldowns stop a noisy metric from thrashing the pool."]),
    ("Security and Least Privilege",
     ["Pre-signed URLs move upload bandwidth off the API while keeping the storage bucket private. The signer controls the exact key, content type, and expiry, so the browser never holds long lived credentials.",
      "Execution roles should grant exactly what a workload needs. A document processor needs read access to the upload prefix, write access to the processed prefix, permission to poll one queue, and nothing else.",
      "Secrets belong in a managed store with rotation and access logging. Configuration that is not secret belongs in environment variables, where it is easier to inspect and change."]),
]


def make_pdf(path, topic_idx, paragraphs, pages=3):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    title, paras = TOPICS[topic_idx % len(TOPICS)]
    c = Canvas(path, pagesize=A4)
    y = 800
    for page in range(pages):
        c.setFont("Helvetica-Bold", 15)
        c.drawString(55, y, f"{title} (part {page + 1})")
        y -= 26
        c.setFont("Helvetica", 10.5)
        pool = paras + [f"Supplemental note {i}: " + random.choice(paras) for i in range(2)]
        for para in random.sample(pool, min(len(pool), paragraphs)):
            words, line = para.split(), ""
            for w in words:
                if len(line) + len(w) > 96:
                    c.drawString(55, y, line)
                    y -= 13
                    line = w
                else:
                    line = (line + " " + w).strip()
            if line:
                c.drawString(55, y, line)
                y -= 13
            y -= 7
            if y < 70:
                break
        c.showPage()
        y = 800
    c.save()


def upload_doc(sess, ep, path, name):
    up = sess.post(ep + "/api/upload", json={"filename": name, "size": os.path.getsize(path)}, timeout=60)
    up.raise_for_status()
    uj = up.json()
    with open(path, "rb") as fh:
        put = sess.put(uj["uploadUrl"], data=fh.read(),
                       headers={"Content-Type": "application/pdf"}, timeout=180)
    put.raise_for_status()
    return uj["jobId"], time.time()


def mode_pipeline(args):
    ep = discover_endpoint()
    print("endpoint:", ep)
    sqs = boto3.client("sqs", region_name=REGION)
    ecs = boto3.client("ecs", region_name=REGION)

    samples = []
    stop = threading.Event()

    def sampler():
        t0 = time.time()
        while not stop.is_set():
            try:
                attrs = sqs.get_queue_attributes(QueueUrl=QUEUE_URL, AttributeNames=[
                    "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
                svc = ecs.describe_services(cluster=CLUSTER, services=[WORKER_SVC])["services"][0]
                samples.append({
                    "t": round(time.time() - t0, 1),
                    "visible": int(attrs["ApproximateNumberOfMessages"]),
                    "inFlight": int(attrs["ApproximateNumberOfMessagesNotVisible"]),
                    "desired": int(svc["desiredCount"]),
                    "running": int(svc["runningCount"]),
                    "pending": int(svc["pendingCount"]),
                })
            except Exception as exc:
                print("sampler err:", str(exc)[:120])
            stop.wait(5)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()

    n = args.count
    docs = []
    print(f"generating {n} PDFs...")
    for i in range(n):
        path = os.path.join(TMP, f"documind-load-{i:02d}.pdf")
        pages = random.randint(2, 5)
        make_pdf(path, i, random.randint(2, 4), pages=pages)
        docs.append((path, f"cloud-briefing-{i:02d}.pdf"))

    print("uploading (8 parallel)...")
    t_up0 = time.time()
    jobs = {}
    sess = requests.Session()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(upload_doc, requests.Session(), ep, p, name) for p, name in docs]
        for f in futs:
            job_id, t1 = f.result()
            jobs[job_id] = {"uploadedAt": t1}
    upload_secs = round(time.time() - t_up0, 1)
    print(f"all {n} uploads done in {upload_secs}s; polling for completion...")

    t0 = time.time()
    done = {}
    while time.time() - t0 < args.timeout:
        for jid in list(jobs.keys()):
            if jid in done:
                continue
            try:
                r = sess.get(f"{ep}/api/jobs/{jid}", timeout=30)
                if r.status_code == 200:
                    item = r.json()
                    if item.get("status") in ("COMPLETED", "FAILED"):
                        done[jid] = item
                        jobs[jid]["status"] = item.get("status")
                        jobs[jid]["completedAfterSec"] = round(time.time() - jobs[jid]["uploadedAt"], 1)
                        jobs[jid]["chunkCount"] = item.get("chunkCount")
                        jobs[jid]["timings"] = item.get("timings")
            except Exception:
                pass
        if len(done) >= len(jobs):
            break
        left = len(jobs) - len(done)
        print(f"  .. {left} remaining (t+{round(time.time()-t0)}s, backlog sample: {samples[-1] if samples else '?'})")
        time.sleep(10)

    drain_secs = round(time.time() - t0, 1)
    peak = {
        "backlog": max((s["visible"] + s["inFlight"] for s in samples), default=0),
        "desired": max((s["desired"] for s in samples), default=0),
        "running": max((s["running"] for s in samples), default=0),
    }
    print("watching scale-in...")
    watch_until = time.time() + args.scale_in_watch
    while time.time() < watch_until:
        if samples and samples[-1]["desired"] == 0 and samples[-1]["running"] == 0:
            break
        time.sleep(10)
    stop.set()
    th.join(timeout=10)

    statuses = {}
    for j in jobs.values():
        statuses[j.get("status", "MISSING")] = statuses.get(j.get("status", "MISSING"), 0) + 1
    out = {
        "mode": "pipeline", "endpoint": ep, "documents": n,
        "uploadSeconds": upload_secs, "drainSeconds": drain_secs,
        "statuses": statuses, "peak": peak,
        "finalSample": samples[-1] if samples else None,
        "jobs": {k: v for k, v in jobs.items()},
        "samples": samples,
    }
    path = os.path.join(EVIDENCE, f"pipeline-{ts()}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    csv_path = os.path.join(EVIDENCE, f"pipeline-{ts()}-timeline.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["t", "visible", "inFlight", "desired", "running", "pending"])
        w.writeheader()
        w.writerows(samples)
    print(f"statuses: {statuses} | peaks: {peak} | upload {upload_secs}s | drain {drain_secs}s")
    print("evidence:", path, "|", csv_path)
    return out


QUESTIONS = [
    "How does the system handle password protected documents?",
    "What happens to messages that repeatedly fail processing?",
    "Why is a visibility timeout important for queue workers?",
    "How does the in-process cache achieve sub-fifteen-millisecond responses?",
    "What is the second cache tier and why does it exist?",
    "How does the autoscaler decide how many workers to run?",
    "What is scale to zero and what does it cost?",
    "Why do cache keys include a corpus version marker?",
    "How do pre-signed uploads keep the storage bucket private?",
    "What does the dead letter queue preserve for operators?",
    "How are expired documents excluded from answers?",
    "What observations feed the scaling control loop?",
    "How is cosine similarity used to find relevant passages?",
    "Why over-fetch and rerank instead of relying on top-k alone?",
    "What roles does least privilege assign to the document processor?",
    "How does bounded retry with backoff separate failure classes?",
    "Why alarm on dead letter queue depth?",
    "What happens when the queue backlog is empty?",
    "How do workers self-terminate in the standalone task pool?",
    "What happens to artifacts after twenty four hours?",
    "How does the summary export work?",
    "What is stored in the vector index metadata?",
    "How does the API flag cache hits to clients?",
    "Why compare desired against current capacity every tick?",
    "What latency tradeoffs come with cold container starts?",
]


def mode_cache(args):
    ep = discover_endpoint()
    print("endpoint:", ep)
    sess = requests.Session()
    n = args.queries
    qs = [QUESTIONS[i % len(QUESTIONS)] for i in range(n)]
    results = []
    print(f"running {n} origin+cached question pairs...")
    t_all = time.time()
    for i, q in enumerate(qs):
        row = {"q": q}
        for phase in ("origin", "repeat"):
            t0 = time.time()
            try:
                r = sess.post(ep + "/api/query", json={"question": q}, timeout=180)
                row[phase] = {"status": r.status_code,
                              "latencyMs": int((time.time() - t0) * 1000),
                              "tier": r.headers.get("X-Cache-Tier"),
                              "serverMs": r.json().get("latencyMs") if r.status_code == 200 else None}
            except Exception as exc:
                row[phase] = {"error": str(exc)[:120]}
        results.append(row)
        if (i + 1) % 10 == 0:
            print(f"  .. {i+1}/{n} pairs done ({round(time.time()-t_all)}s)")
    # concurrent repeat burst on one hot question
    hot_q = "How does the autoscaler decide how many workers to run?"
    burst = []
    def ask(_):
        t0 = time.time()
        try:
            r = sess.post(ep + "/api/query", json={"question": hot_q}, timeout=120)
            return {"ms": int((time.time() - t0) * 1000), "tier": r.headers.get("X-Cache-Tier")}
        except Exception as exc:
            return {"error": str(exc)[:80]}
    print("concurrent burst: 12 parallel repeats of one hot question...")
    with ThreadPoolExecutor(max_workers=12) as pool:
        burst = list(pool.map(ask, range(12)))

    origin = [r["origin"]["latencyMs"] for r in results if r.get("origin", {}).get("latencyMs")]
    cached = [r["repeat"]["latencyMs"] for r in results if r.get("repeat", {}).get("latencyMs")]
    tiers = {}
    for r in results:
        t = (r.get("repeat") or {}).get("tier", "?")
        tiers[t] = tiers.get(t, 0) + 1
    stats = {
        "origin": {"n": len(origin), "p50": pct(origin, 50), "p95": pct(origin, 95),
                   "p99": pct(origin, 99), "mean": round(statistics.mean(origin), 1) if origin else None},
        "cached": {"n": len(cached), "p50": pct(cached, 50), "p95": pct(cached, 95),
                   "p99": pct(cached, 99), "mean": round(statistics.mean(cached), 1) if cached else None},
        "repeatTiers": tiers,
        "burst": burst,
    }
    if origin and cached:
        stats["speedupP50"] = round(statistics.median(origin) / max(1, statistics.median(cached)), 1)
    out = {"mode": "cache", "endpoint": ep, "stats": stats,
           "results": [{k: v for k, v in r.items() if k != "text"} for r in results]}
    path = os.path.join(EVIDENCE, f"cache-bench-{ts()}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(json.dumps(stats, indent=2))
    print("evidence:", path)
    return out


def mode_dlq(args):
    ep = discover_endpoint()
    print("endpoint:", ep)
    sess = requests.Session()
    files = []

    # 1) password-protected
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    from pypdf import PdfWriter
    plain = os.path.join(TMP, "poison-plain.pdf")
    c = Canvas(plain, pagesize=A4)
    c.drawString(60, 760, "Secret agenda content " * 30)
    c.showPage()
    c.save()
    enc = os.path.join(TMP, "poison-encrypted.pdf")
    w = PdfWriter(clone_from=plain)
    w.encrypt("n12136514")
    with open(enc, "wb") as fh:
        w.write(fh)
    files.append((enc, "poison-encrypted.pdf"))

    # 2) corrupt
    cor = os.path.join(TMP, "poison-corrupt.pdf")
    with open(cor, "wb") as fh:
        fh.write(b"%PDF-1.4 broken bytes " + os.urandom(400))
    files.append((cor, "poison-corrupt.pdf"))

    # 3) blank (no text layer)
    blank = os.path.join(TMP, "poison-blank.pdf")
    c2 = Canvas(blank, pagesize=A4)
    c2.showPage()
    c2.save()
    files.append((blank, "poison-blank.pdf"))

    jobs = {}
    for path, name in files:
        job_id, _ = upload_doc(sess, ep, path, name)
        jobs[job_id] = name
        print("uploaded poison:", name, "->", job_id)

    print("waiting for workers to isolate poison messages...")
    t0 = time.time()
    final = {}
    while time.time() - t0 < 300:
        for jid in jobs:
            if jid in final:
                continue
            r = sess.get(f"{ep}/api/jobs/{jid}", timeout=30)
            if r.status_code == 200 and r.json().get("status") in ("FAILED", "COMPLETED"):
                final[jid] = r.json()
                print(f"  {jobs[jid]}: {r.json().get('status')} :: {str(r.json().get('error'))[:110]}")
        if len(final) >= len(jobs):
            break
        time.sleep(8)

    dlq = boto3.client("sqs", region_name=REGION)
    attrs = dlq.get_queue_attributes(QueueUrl=DLQ_URL, AttributeNames=["ApproximateNumberOfMessages"])["Attributes"]
    depth = int(attrs.get("ApproximateNumberOfMessages", 0))
    peeked = []
    if depth > 0:
        msgs = dlq.receive_message(QueueUrl=DLQ_URL, MaxNumberOfMessages=min(5, depth),
                                   MessageAttributeNames=["All"], WaitTimeSeconds=5).get("Messages", [])
        for m in msgs:
            peeked.append({
                "messageId": m["MessageId"],
                "reason": (m.get("MessageAttributes", {}).get("Reason", {}).get("StringValue") or "")[:200],
                "worker": m.get("MessageAttributes", {}).get("Worker", {}).get("StringValue"),
                "bodyHead": m["Body"][:160],
            })
        # leave messages in place; visibility timeout returns them shortly

    out = {"mode": "dlq", "endpoint": ep,
           "injected": list(jobs.values()),
           "finalStatuses": {jobs[k]: {"status": v.get("status"), "error": v.get("error")} for k, v in final.items()},
           "dlqDepth": depth, "peek": peeked}
    path = os.path.join(EVIDENCE, f"dlq-{ts()}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"dlq depth: {depth}; failures recorded: {sum(1 for v in final.values() if v.get('status') == 'FAILED')}")
    print("evidence:", path)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["pipeline", "cache", "dlq"])
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--scale-in-watch", type=int, default=600)
    args = ap.parse_args()
    if args.mode == "pipeline":
        mode_pipeline(args)
    elif args.mode == "cache":
        mode_cache(args)
    else:
        mode_dlq(args)


if __name__ == "__main__":
    main()
