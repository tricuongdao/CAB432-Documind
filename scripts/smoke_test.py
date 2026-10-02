#!/usr/bin/env python3
"""DocuMind AI end-to-end smoke test against the deployed API.

Flow: health check -> presigned upload -> direct S3 PUT -> status poll
(with a manual worker-task fallback) -> RAG query (miss then hit) -> cache
stats -> MCP handshake and tool call -> admin cache purge.

Writes a JSON evidence file under docs/evidence/. Exit code 0 = all core
checks passed.
"""

import json
import os
import sys
import time

import boto3
import requests

REGION = "ap-southeast-2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_DIR = os.path.join(ROOT, "docs", "evidence")
os.makedirs(EVIDENCE_DIR, exist_ok=True)

RESULTS = []
FAILURES = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + (f" :: {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def discover_endpoint():
    # Preference order: env override -> API Gateway -> Lambda Function URL.
    apigw = boto3.client("apigatewayv2", region_name=REGION)
    for a in apigw.get_apis().get("Items", []):
        if a.get("Name") == "n12136514-documind-api":
            return a["ApiEndpoint"]
    try:
        lam = boto3.client("lambda", region_name=REGION)
        return lam.get_function_url_config(FunctionName="n12136514-documind-api")["FunctionUrl"].rstrip("/")
    except Exception:
        pass
    raise SystemExit("documind api endpoint not found (set DOCUMIND_ENDPOINT)")


def make_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(path, pagesize=A4)
    paras = [
        "DocuMind AI smoke test document. This file verifies the asynchronous ingestion pipeline: object storage receives the upload, an event rule forwards it to a message queue, and container workers extract text and build vector embeddings.",
        "The caching strategy combines a sub-15 millisecond in-process LRU cache with a shared DynamoDB tier, so repeated semantic questions are answered without touching the embedding model or the generator model.",
        "Password protected or corrupted documents are isolated in a dead letter queue after bounded retries, keeping the processing pool healthy. All artifacts expire automatically after twenty four hours via time to live and lifecycle rules.",
    ]
    y = 780
    c.setFont("Helvetica-Bold", 14)
    c.drawString(55, y + 20, "DocuMind AI smoke test")
    c.setFont("Helvetica", 10.5)
    for p in paras * 2:
        words, line = p.split(), ""
        for w in words:
            if len(line) + len(w) > 95:
                c.drawString(55, y, line)
                y -= 13
                line = w
            else:
                line = (line + " " + w).strip()
        if line:
            c.drawString(55, y, line)
            y -= 13
        y -= 8
        if y < 80:
            c.showPage()
            y = 780
            c.setFont("Helvetica", 10.5)
    c.showPage()
    c.save()


def launch_worker():
    ecs = boto3.client("ecs", region_name=REGION)
    resp = ecs.run_task(
        cluster="n12136514-documind-cluster", taskDefinition="n12136514-documind-worker",
        launchType="FARGATE", count=1,
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": ["subnet-075811427d5564cf9"],
            "securityGroups": ["sg-078997505ad1c6bbc"], "assignPublicIp": "ENABLED"}},
        propagateTags="TASK_DEFINITION", startedBy="documind-smoke-fallback")
    return len(resp.get("tasks", []))


def main():
    ep = os.environ.get("DOCUMIND_ENDPOINT") or discover_endpoint()
    print("endpoint:", ep)
    sess = requests.Session()

    # 1) health
    h = sess.get(ep + "/healthz", timeout=60)
    check("healthz 200", h.status_code == 200, f"HTTP {h.status_code}")
    if h.status_code == 200:
        hj = h.json()
        check("healthz models", "ragModel" in hj and "embedModel" in hj,
              f"{hj.get('embedModel')} / {hj.get('ragModel')}")

    # 2) presigned upload
    up = sess.post(ep + "/api/upload", json={"filename": "smoke-test.pdf", "size": 5000}, timeout=60)
    check("upload presign 200", up.status_code == 200, f"HTTP {up.status_code}")
    uj = up.json()
    job_id = uj["jobId"]
    print("job:", job_id)

    # 3) direct S3 PUT to the presigned URL
    pdf_path = os.path.join(os.environ.get("TMPDIR", "."), "documind-smoke-2.pdf")
    make_pdf(pdf_path)
    with open(pdf_path, "rb") as fh:
        payload = fh.read()
    put = sess.put(uj["uploadUrl"], data=payload, headers={"Content-Type": "application/pdf"}, timeout=120)
    check("presigned S3 PUT", put.status_code in (200, 201), f"HTTP {put.status_code}")

    # 4) poll status; fall back to launching a worker if nothing picks it up
    status = None
    transition_log = []
    t0 = time.time()
    launched = False
    while time.time() - t0 < 300:
        try:
            r = sess.get(f"{ep}/api/jobs/{job_id}", timeout=30)
            if r.status_code == 200:
                item = r.json()
                if item.get("status") != status:
                    status = item.get("status")
                    transition_log.append({"t": round(time.time() - t0, 1), "status": status})
                    print(f"  t+{round(time.time()-t0)}s status={status}")
                if status in ("COMPLETED", "FAILED"):
                    break
        except Exception as exc:
            transition_log.append({"t": round(time.time() - t0, 1), "error": str(exc)[:120]})
        if not launched and time.time() - t0 > 45:
            n = launch_worker()
            launched = True
            print(f"  fallback: launched {n} worker task(s)")
        time.sleep(4)
    check("job completed", status == "COMPLETED", f"final status={status}")

    # 5) RAG query: miss then hit
    q = "What caching strategy does the system use?"
    miss = sess.post(ep + "/api/query", json={"question": q}, timeout=120)
    check("query origin 200", miss.status_code == 200, f"HTTP {miss.status_code}")
    mj = miss.json() if miss.status_code == 200 else {}
    tier1 = miss.headers.get("X-Cache-Tier", mj.get("cache", "?"))
    lat1 = mj.get("latencyMs")
    print(f"  first ask: tier={tier1} latencyMs={lat1} sources={len(mj.get('sources', []))}")
    check("query sources", len(mj.get("sources", [])) > 0, f"{len(mj.get('sources', []))} sources")

    time.sleep(1)
    hit = sess.post(ep + "/api/query", json={"question": q}, timeout=120)
    hj = hit.json() if hit.status_code == 200 else {}
    tier2 = hit.headers.get("X-Cache-Tier", hj.get("cache", "?"))
    lat2 = hj.get("latencyMs")
    print(f"  second ask: tier={tier2} latencyMs={lat2}")
    check("second ask cached", tier2.startswith("HIT"), f"tier={tier2}")
    if lat1 and lat2:
        check("cache speedup", lat2 < lat1, f"{lat1} ms -> {lat2} ms")

    # 6) cache stats
    cs = sess.get(ep + "/api/cache/stats", timeout=60)
    check("cache stats 200", cs.status_code == 200, f"HTTP {cs.status_code}")
    stats = cs.json() if cs.status_code == 200 else {}

    # 7) MCP handshake + tool call
    init = sess.post(ep + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                        "params": {}}, timeout=60)
    ok_init = init.status_code == 200 and init.json().get("result", {}).get("serverInfo", {}).get("name") == "documind-rag"
    check("mcp initialize", ok_init, f"HTTP {init.status_code}")
    tools = sess.post(ep + "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, timeout=60)
    n_tools = len(tools.json().get("result", {}).get("tools", [])) if tools.status_code == 200 else 0
    check("mcp tools/list", n_tools == 3, f"{n_tools} tools")
    search = sess.post(ep + "/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                          "params": {"name": "search_documents",
                                                     "arguments": {"query": "dead letter queue", "topK": 2}}},
                       timeout=120)
    ok_search = search.status_code == 200 and not search.json().get("result", {}).get("isError", True)
    check("mcp search_documents", ok_search, f"HTTP {search.status_code}")

    # 8) admin purge (key from Secrets Manager)
    try:
        sm = boto3.client("secretsmanager", region_name=REGION)
        key = json.loads(sm.get_secret_value(SecretId="n12136514-documind-secrets")["SecretString"])["apiAdminKey"]
        purge = sess.delete(ep + "/api/admin/cache", headers={"X-API-Key": key}, json={}, timeout=60)
        check("admin cache purge", purge.status_code == 200, f"HTTP {purge.status_code}")
    except Exception as exc:
        check("admin cache purge", False, str(exc)[:150])

    # 9) summary download if completed
    if status == "COMPLETED":
        sm2 = sess.get(f"{ep}/api/summaries/{job_id}", timeout=60)
        check("summary markdown", sm2.status_code == 200 and len(sm2.content) > 100,
              f"HTTP {sm2.status_code}, {len(sm2.content)} bytes")

    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    out = os.path.join(EVIDENCE_DIR, f"smoke-{ts}.json")
    with open(out, "w") as fh:
        json.dump({
            "endpoint": ep, "jobId": job_id, "transitions": transition_log,
            "firstQuery": {"tier": tier1, "latencyMs": lat1},
            "secondQuery": {"tier": tier2, "latencyMs": lat2},
            "cacheStats": stats, "checks": RESULTS,
            "passed": len(RESULTS) - len(FAILURES), "failed": len(FAILURES),
        }, fh, indent=2, default=str)
    print("\nevidence written:", out)
    print(f"{len(RESULTS) - len(FAILURES)} passed, {len(FAILURES)} failed")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
