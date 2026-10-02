"""DocuMind AI HTTP API (FastAPI, deployed on AWS Lambda via Mangum).

Endpoints
    POST /api/upload            presigned S3 upload + job registration
    GET  /api/jobs              recent jobs (cached)
    GET  /api/jobs/{jobId}      live processing status (cached 3s)
    POST /api/query             cited RAG answer (two-tier result cache)
    GET  /api/cache/stats       cache hit/miss statistics
    GET  /api/summaries/{id}    generated markdown summary download
    DELETE /api/admin/cache     purge cache (X-API-Key from Secrets Manager)
    POST /mcp                   MCP server (JSON-RPC 2.0)
    GET  /, /assets/*, ...      the built SPA, proxied from the web bucket for
                                HTTPS + same-origin delivery from one URL
"""

import json
import re
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse as _JSONResponseBase


class JSONResponse(_JSONResponseBase):
    """JSONResponse that tolerates DynamoDB ``Decimal`` values.

    Items read from DynamoDB carry ``decimal.Decimal`` numbers, which the
    stdlib json encoder refuses; convert Decimals to floats (and anything
    else exotic to str) so every endpoint serialises cleanly.
    """

    def render(self, content) -> bytes:
        import decimal
        import json as _json

        def _default(o):
            if isinstance(o, decimal.Decimal):
                return float(o)
            return str(o)

        return _json.dumps(content, ensure_ascii=False, allow_nan=False,
                           default=_default, separators=(",", ":")).encode("utf-8")

import cache as cache_mod
import mcp as mcp_mod
import rag
import store
from config import (
    ADMIN_SECRET_ARN,
    APP_VERSION,
    AWS_REGION,
    EMBED_MODEL_ID,
    LIST_CACHE_TTL,
    MAX_UPLOAD_BYTES,
    RAG_MODEL_ID,
    SECRET_TTL,
    STATIC_CACHE_TTL,
    STATUS_CACHE_TTL,
    SUMMARY_CACHE_TTL,
    UPLOAD_URL_TTL,
    VECTOR_BUCKET,
    VECTOR_INDEX,
)

app = FastAPI(title="DocuMind AI API", version=APP_VERSION,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

# The same app is reachable directly (Lambda Function URL, no prefix) and
# behind the course API Gateway route prefix (/documind/*). Strip the prefix
# when present so both entrypoints share one implementation.
ROUTE_PREFIX = "/documind"


@app.middleware("http")
async def strip_route_prefix(request: Request, call_next):
    import os as _os
    from fastapi.responses import RedirectResponse
    prefix = _os.environ.get("ROUTE_PREFIX", ROUTE_PREFIX)
    path = request.scope.get("path", "")
    if prefix and path == prefix:
        # Normalise /documind -> /documind/ so relative asset URLs resolve.
        return RedirectResponse(prefix + "/", status_code=308)
    if prefix and path.startswith(prefix):
        request.scope["path"] = path[len(prefix):] or "/"
        raw = request.scope.get("raw_path")
        if raw:
            request.scope["raw_path"] = raw[len(prefix):] or b"/"
    return await call_next(request)

_req_re = re.compile(r"[^A-Za-z0-9._-]")


def emit_metric(name: str, value: float, unit: str = "Count"):
    """Emit a CloudWatch EMF metric through the Lambda log stream."""
    doc = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "DocuMind",
                "Dimensions": [["Service"]],
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        "Service": "api",
        name: value,
    }
    print(json.dumps(doc))


@app.middleware("http")
async def timing_and_request_id(request: Request, call_next):
    t0 = time.time()
    response = await call_next(request)
    response.headers["X-Response-Time-Ms"] = str(int((time.time() - t0) * 1000))
    response.headers["X-Request-Id"] = uuid.uuid4().hex[:12]
    return response


# ---------------------------------------------------------------------------
# Health / config
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    l1 = cache_mod.get_l1()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "region": AWS_REGION,
        "embedModel": EMBED_MODEL_ID,
        "ragModel": RAG_MODEL_ID,
        "vectorStore": f"{VECTOR_BUCKET}/{VECTOR_INDEX}",
        "cacheL1": l1.stats(),
        "ts": store.now_iso(),
    }


@app.get("/api/config")
def api_config():
    return {
        "region": AWS_REGION,
        "embedModel": EMBED_MODEL_ID,
        "ragModel": RAG_MODEL_ID,
        "artifactTtlHours": 24,
        "uploadUrlTtlSeconds": UPLOAD_URL_TTL,
        "maxUploadBytes": MAX_UPLOAD_BYTES,
    }


# ---------------------------------------------------------------------------
# Uploads + jobs
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def api_upload(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    filename = str(body.get("filename", "")).strip()
    size = int(body.get("size") or 0)
    if not filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "only .pdf uploads are supported"}, status_code=400)
    if size and size > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": f"file exceeds {MAX_UPLOAD_BYTES} bytes"}, status_code=400)

    safe = _req_re.sub("_", filename)[:80] or "document.pdf"
    job_id = "job-" + uuid.uuid4().hex[:10]
    key = f"uploads/{job_id}/{safe}"
    store.put_new_job(job_id, safe, size)
    url = store.presign_put(key)
    emit_metric("UploadPresigns", 1)
    return {
        "jobId": job_id,
        "s3Key": key,
        "uploadUrl": url,
        "expiresIn": UPLOAD_URL_TTL,
        "status": "CREATED",
    }


@app.get("/api/jobs")
def api_jobs(limit: int = 25):
    limit = max(1, min(limit, 100))
    l1 = cache_mod.get_l1()
    hit = l1.get(f"jobs:list:{limit}")
    if hit:
        payload, _ = hit
        return JSONResponse(payload, headers={"X-Cache": "HIT", "X-Cache-Tier": "L1"})
    items = store.list_jobs(limit)
    for it in items:
        it.pop("listKey", None)
        if it.get("status") == "COMPLETED":
            it["summaryUrl"] = f"/api/summaries/{it['jobId']}"
    l1.set(f"jobs:list:{limit}", items, LIST_CACHE_TTL)
    return JSONResponse(items, headers={"X-Cache": "MISS", "X-Cache-Tier": "L1"})


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    l1 = cache_mod.get_l1()
    hit = l1.get(f"job:{job_id}")
    if hit:
        payload, _ = hit
        return JSONResponse(payload, headers={"X-Cache": "HIT", "X-Cache-Tier": "L1"})
    item = store.get_job(job_id)
    if not item:
        return JSONResponse({"error": "job not found"}, status_code=404)
    item.pop("listKey", None)
    if item.get("status") == "COMPLETED":
        item["summaryUrl"] = f"/api/summaries/{job_id}"
    l1.set(f"job:{job_id}", item, STATUS_CACHE_TTL)
    return JSONResponse(item, headers={"X-Cache": "MISS", "X-Cache-Tier": "L1"})


# ---------------------------------------------------------------------------
# RAG query
# ---------------------------------------------------------------------------

@app.post("/api/query")
async def api_query(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    question = str(body.get("question", "")).strip()
    if not question:
        return JSONResponse({"error": "question is required"}, status_code=400)
    if len(question) > 500:
        return JSONResponse({"error": "question too long (max 500 chars)"}, status_code=400)
    top_k = body.get("topK")
    try:
        top_k = int(top_k) if top_k is not None else None
    except (TypeError, ValueError):
        top_k = None

    t0 = time.time()
    result, tier = rag.answer_question(question, top_k)
    total_ms = int((time.time() - t0) * 1000)
    result["question"] = question
    result["latencyMs"] = total_ms

    emit_metric("Queries", 1)
    if tier == "HIT-L1":
        emit_metric("CacheHitsL1", 1)
    elif tier == "HIT-L2":
        emit_metric("CacheHitsL2", 1)
    else:
        emit_metric("CacheMisses", 1)
        emit_metric("QueryOriginLatencyMs", total_ms, "Milliseconds")

    headers = {
        "X-Cache": "HIT" if tier.startswith("HIT") else "MISS",
        "X-Cache-Tier": tier,
        "X-Model": result.get("model", RAG_MODEL_ID),
        "X-Query-Latency-Ms": str(total_ms),
    }
    return JSONResponse(result, headers=headers)


@app.get("/api/cache/stats")
def api_cache_stats():
    l1 = cache_mod.get_l1()
    s = l1.stats()
    return {
        "backend": "memory-l1+dynamodb-l2",
        "l1": s,
        "l2": {
            "store": "DynamoDB items (cache#<hash>) in the jobs table",
            "ttlSeconds": 3600,
        },
    }


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

@app.get("/api/summaries/{job_id}")
def api_summary(job_id: str):
    l1 = cache_mod.get_l1()
    hit = l1.get(f"sum:{job_id}")
    if hit:
        data, _ = hit
        return Response(data, media_type="text/markdown; charset=utf-8",
                        headers={"X-Cache": "HIT", "X-Cache-Tier": "L1"})
    data = store.get_summary_bytes(job_id)
    if data is None:
        return JSONResponse({"error": "summary not available yet"}, status_code=404)
    l1.set(f"sum:{job_id}", data, SUMMARY_CACHE_TTL)
    return Response(data, media_type="text/markdown; charset=utf-8",
                    headers={"X-Cache": "MISS", "X-Cache-Tier": "L1"})


# ---------------------------------------------------------------------------
# Admin (X-API-Key from Secrets Manager)
# ---------------------------------------------------------------------------

def _admin_key():
    l1 = cache_mod.get_l1()
    hit = l1.get("adminKey")
    if hit:
        return hit[0]
    key = store.get_admin_key()
    if key:
        l1.set("adminKey", key, SECRET_TTL)
    return key


@app.delete("/api/admin/cache")
async def api_admin_cache(request: Request):
    if not ADMIN_SECRET_ARN:
        return JSONResponse({"error": "admin API disabled"}, status_code=404)
    supplied = request.headers.get("x-api-key", "")
    expected = _admin_key()
    if not expected or supplied != expected:
        return JSONResponse({"error": "invalid or missing X-API-Key"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    l1 = cache_mod.get_l1()
    key = str(body.get("key", "")).strip()
    if key:
        purged = l1.purge(f"q:{key}")
        l2 = store.delete_cache_item(key)
        return {"purgedL1": purged, "purgedL2": l2, "key": key}
    purged = l1.purge("")
    return {"purgedL1": purged, "note": "L1 fully purged; L2 items expire by TTL"}


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error"}},
                            status_code=400)
    status, resp = mcp_mod.handle_rpc(body)
    if resp is None:
        return Response(status_code=202)
    return JSONResponse(resp, status_code=status)


@app.get("/mcp")
def mcp_get():
    return JSONResponse({"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32000,
                                   "message": "MCP Streamable HTTP requires POST"}},
                        status_code=405)


# ---------------------------------------------------------------------------
# Static SPA (served from the web bucket for HTTPS + same-origin)
# ---------------------------------------------------------------------------

def _serve_asset(path: str):
    l1 = cache_mod.get_l1()
    hit = l1.get(f"static:{path}")
    if hit:
        data, ctype = hit[0]
        return Response(data, media_type=ctype,
                        headers={"X-Cache": "HIT",
                                 "Cache-Control": _cache_control(path)})
    data, ctype = store.get_web_asset(path)
    if data is None:
        return None
    l1.set(f"static:{path}", (data, ctype or "application/octet-stream"), STATIC_CACHE_TTL)
    return Response(data, media_type=ctype,
                    headers={"X-Cache": "MISS", "Cache-Control": _cache_control(path)})


def _cache_control(path: str) -> str:
    if path.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return "public, max-age=300"


@app.get("/")
def root():
    return _serve_asset("index.html") or JSONResponse({"error": "frontend not deployed"}, status_code=404)


@app.get("/index.html")
def index_html():
    return _serve_asset("index.html") or JSONResponse({"error": "frontend not deployed"}, status_code=404)


@app.get("/favicon.svg")
def favicon():
    return _serve_asset("favicon.svg") or Response(status_code=404)


@app.get("/{full_path:path}")
def static_catchall(full_path: str):
    if full_path.startswith("api/") or full_path == "api" or full_path.startswith("mcp"):
        return JSONResponse({"error": "not found"}, status_code=404)
    resp = _serve_asset(full_path)
    if resp is not None:
        return resp
    # SPA fallback for extension-less routes.
    if "." not in full_path.rsplit("/", 1)[-1]:
        resp = _serve_asset("index.html")
        if resp is not None:
            return resp
    return JSONResponse({"error": "not found", "path": full_path}, status_code=404)
