# DocuMind AI — CAB432 Assignment 3 (n12136514)

Serverless document intelligence: upload PDFs, ask questions, get cited answers. Event-driven ingestion on a queue, a Fargate worker pool that scales itself by metric and sleeps at zero, a two-tier cache in front of the RAG path, dead-letter isolation for bad input, and automatic 24-hour cleanup on every artifact.

**Live deployment:** <https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/>
(fallback: Lambda function URL — see `submission-a3-infra.yml`)

## The system in one diagram

Client → (presigned PUT) → S3 → EventBridge → SQS → ECS Fargate workers (0–6, Spot) → Bedrock (Titan embeddings + Gemma answers) → S3 Vectors + DynamoDB. Query API = Lambda (FastAPI) with L1 in-process + L2 DynamoDB cache and an MCP tool endpoint. A controller task reads queue backlog every 60 s and drives the pool: `desired = 0 if backlog = 0 else clamp(ceil(backlog/2), 1, 6)`.

Full reasoning: **`DocuMind-A3-Report.pdf`** (source: `REPORT.md`). Operations & handoff: **`docs/HANDOFF.md`** (state, playbook, sandbox constraints, open items).

## Repository layout

```
backend/
  api/        FastAPI service (Mangum) — presign, jobs, query, MCP, static, cache
  worker/     SQS worker — extract → chunk → embed → summarise → store (+ tests)
  scaler/     controller task + control law (metric-based autoscaling)
  Dockerfile  worker/controller image (linux/amd64)
frontend/     React SPA (dropzone, pipeline monitor, cited chat, cache stats)
infra/        task definitions (JSON)
scripts/      deploy-infra.sh (one-shot), load_test.py, smoke_test.py,
              make_figures.py, capture_ui.mjs, build_report_pdf.py, …
docs/         REPORT assets: diagrams (SVG), figures (PNG), evidence (JSON/CSV),
              VIDEO_SCRIPT.md, COST_MODEL.md
submission-a3-infra.yml   Gradescope infrastructure declaration (26 resources)
REPORT.md / DocuMind-A3-Report.pdf
```

## Quickstart (local)

```bash
cd backend/worker && python test_local.py        # 8/8 unit tests, no AWS needed
# deployed-system checks (no AWS creds needed — talks to the live API):
python scripts/smoke_test.py                     # 14-check end-to-end suite
python scripts/load_test.py pipeline --count 30  # auto-scaling evidence run
python scripts/load_test.py cache --queries 50   # caching evidence run
python scripts/load_test.py dlq                  # fault-isolation run
```

## Deploy (account 901444280953, ap-southeast-2)

```bash
aws sso login
bash scripts/deploy-infra.sh    # idempotent; builds images, services, routes
```

Sandbox notes that shaped the build (see report §3.8): single shared API gateway (ours mounts `/documind/*` additively), no CloudFront / ElastiCache / managed scaling APIs → custom controller + DB-backed L2 cache; Bedrock allow-list = Titan Embed v2 + Gemma 3 12B; every resource tagged `purpose=assessment 3` + `qut-username=n12136514@qut.edu.au`.

## Evidence

`docs/evidence/` holds every number in the report: pipeline timeline CSV/JSON (1→6 workers, 58 s drain, scale back to zero), cache battery (24 misses p50 2 408 ms; 62 hits p50 46 ms round-trip, 0 ms in-process), DLQ run (3 poison classes isolated), smoke runs (14/14).

## Manual steps before submission

1. **AWS Pricing Calculator public link** — reproduce from `docs/COST_MODEL.md` §5 (~10 min), paste the Share link into the report (search for “Pricing Calculator estimate”).
2. **Demo video (4–5 min)** — follow `docs/VIDEO_SCRIPT.md`; upload to Canvas and click **Submit**.
3. Gradescope: upload this source tree + `submission-a3-infra.yml`.
4. Canvas: upload `DocuMind-A3-Report.pdf` + the video.

Remember to delete resources when the unit finishes (report §3.8 lists everything): drop the two ECS services, the rule, the queues, both buckets, the table, the vector bucket, the secret and the Lambda.
