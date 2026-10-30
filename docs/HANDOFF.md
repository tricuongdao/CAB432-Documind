# HANDOFF, DocuMind AI · CAB432 Assessment 3

**Student:** Tri Cuong Dao, n12136514@qut.edu.au · **Account:** 901444280953 · **Region:** ap-southeast-2 (Sydney)
**Repo:** the submitted source tree (git `master`)
**Last updated:** 28 October 2026 · **Status:** deployed and verified, smoke **14/14** and 73/73 UI assertions against the live URL on 28 Oct; document deletion shipped (`DELETE /api/jobs/{jobId}`); tags corrected on all declared resources. One user action outstanding (demo video) plus the pricing-calculator link (Section 8).

The one-paragraph version: DocuMind AI is a serverless document-intelligence system built for QUT CAB432 (Cloud Computing) Assessment 3. Users upload PDFs; an event-driven pipeline extracts, chunks, embeds (Bedrock Titan v2) and summarises (Gemma 3 12B) them into an Amazon S3 Vectors index via SQS-driven ECS Fargate workers (metric-based autoscaling, scale-to-zero, and a custom controller so the scaling law stays explicit and observable); a Lambda + FastAPI service answers cited RAG questions through a two-tier cache (in-process L1 + DynamoDB L2) and also serves an MCP tool endpoint for agents. It runs live behind the shared course API gateway under `/documind/*`. Every deliverable, report, submission YAML, evidence bundle, video script, cost model, runbook, is in this repo. Every resource carries `purpose=assessment 3` and `qut-username=n12136514@qut.edu.au`.

---

## 1. Entry points & links

| What | Where |
|---|---|
| Live app (UI + API) | https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/ |
| API fallback (Function URL) | https://6q3jq7pfogqaxz77xtw5k5dxbq0spnpt.lambda-url.ap-southeast-2.on.aws |
| CloudWatch dashboard | console → CloudWatch → dasboards → `n12136514-documind-dashboard` |
| Worker logs | `/ecs/n12136514-documind-worker` (CloudWatch Logs, 7-day retention) |
| Report (PDF) | `DocuMind-A3-Report.pdf` (32 pp total; 27 body pages + refs/appendices) |
| Report (source) | `REPORT.md` |
| Submission manifest | `submission-a3-infra.yml` (26 resources, strict-YAML valid) |
| Operator guide | `docs/RUNBOOK.md` |
| Video storyboard | `docs/VIDEO_SCRIPT.md` |
| Cost model | `docs/COST_MODEL.md` |
| Evidence bundle | `docs/evidence/*.json` + `-timeline.csv` |
| Figures & diagrams | `docs/figures/*.png`, sources in `docs/diagrams/` + `.build/charts/` |

## 2. Deployed resources (the inventory)

| Resource | Name / ARN shorthand | Key config |
|---|---|---|
| ECS cluster | `n12136514-documind-cluster` | Container Insights on |
| Worker service | `n12136514-documind-worker-svc` (task def `n12136514-documind-worker`) | FARGATE_SPOT, 1 vCPU / 2 GB, idle-exit after 60 s, pool 0–6 |
| Controller service | `n12136514-documind-controller-svc` (task def `n12136514-documind-controller`) | 0.25 vCPU / 0.5 GB, always-on 60 s control loop |
| SQS queue | `n12136514-documind-queue` | visibility 300 s |
| SQS DLQ | `n12136514-documind-dlq` | redrive after 3 receives |
| EventBridge rule | `n12136514-documind-upload-rule` | S3 ObjectCreated (prefix `uploads/`) → queue (queue policy conditioned on rule ARN) |
| Storage bucket | `n12136514-documind-storage` | `uploads/`, `chunks/`, `summaries/`; public blocked; 24 h lifecycle |
| Web bucket | `n12136514-documind-web` | SPA assets, served via API origin |
| Vector index | S3 Vectors bucket `n12136514-documind-vectors`, index `documind-chunks` | 1024-dim cosine; keys `jobId#chunkIdx` |
| Jobs table | `n12136514-documind-jobs` | PK `jobId`; GSI `byCreated` (`listKey`+`createdAt`); TTL attr `ttl` = +24 h; cache rows `cache#<sha>` (payload stored as JSON **string**, see gotchas) |
| API | Lambda `n12136514-documind-api` (Python 3.12, FastAPI+Mangum) | 1024 MB, 29 s timeout (gateway ceiling); REST + MCP + static serving |
| Gateway routes | shared HTTP API `rqy6cawtyg` → `/documind`, `/documind/{proxy+}` | **Additive only, A2 routes must never be touched** |
| Secrets | `n12136514-documind-secrets` | `apiAdminKey` (admin cache purge) |
| Registry | ECR `n12136514-documind-worker:latest` | linux/amd64, multi-stage |
| Observability | dashboard + 3 alarms (DLQ depth, backlog, API errors) via log-metric filters | ECS task-role `cloudwatch:PutMetricData` is denied; log-based metrics are the supported path |
| Scheduler group | `n12136514-documind-schedules` | hygiene tick **disabled** (superseded by the controller); intentionally not declared in the submission YAML |

Bedrock models (account allow-list): `amazon.titan-embed-text-v2:0`, `google.gemma-3-12b-it`. Shared roles: `CAB432-Lambda-Role`, `Task-Role-CAB432-ECS`, `Execution-Role-CAB432-ECS`.

## 3. How it works (60 seconds)

- **Ingestion:** browser → presigned S3 PUT (`uploads/…`) → EventBridge → SQS → worker (long-poll) → pdfplumber extract → sentence-aware chunks (~1 000 chars, 180 overlap) → Titan embeddings → `put_vectors` + DynamoDB status → summary (Gemma) → message delete. Poison docs self-route to the DLQ after bounded receives.
- **Query:** `POST /api/query` → L1 (in-process, ms) → L2 (DynamoDB, ~5–15 ms) → origin (embed → vector search → Gemma synthesis, ~2–3 s). Responses carry `X-Cache-Tier: HIT-L1 | HIT-L2 | MISS`. Negative results are deliberately **not** cached.
- **Scaling law (custom controller):** every 60 s: `desired = 0 if backlog == 0 else clamp(ceil(backlog/2), 1, 6)`; workers exit after 60 s idle → true scale-to-zero. Migration path to managed Applications Auto Scaling if ever allowed is documented in the report (a config change).
- **Reliability:** at-least-once queue semantics; deterministic vector keys make reprocessing idempotent; stale-vector guard filters hits whose job row expired via TTL.

## 4. Operations playbook

```bash
# First-time / after token expiry (tokens last ~1 h):
aws sso login                       # default profile, browser approval

# Test scripts' Python deps (one-time; keep standard lib out of scope):
python -m pip install -r scripts/requirements-test.txt

# Smoke test, 14 checks, NO AWS credentials needed (~3–12 min; it waits
# for the asynchronous vector index build before checking citations):
python scripts/smoke_test.py        # -> "14 passed, 0 failed", evidence JSON

# Experiments (need aws sso login):
python scripts/load_test.py pipeline --count 30   # scaling: 1→6 workers, drain, →0
python scripts/load_test.py cache    --queries 50 # cache latency percentiles
python scripts/load_test.py dlq                   # poison isolation
cd backend/worker && python test_local.py         # 8/8 offline unit tests

# Deploys:
bash scripts/deploy-infra.sh                      # idempotent from-zero orchestrator (needs docker+node)
bash scripts/build_worker_image.sh                # rebuild+push worker image (linux/amd64)
bash scripts/build_api_lambda.sh && python scripts/deploy_api_lambda.py
bash scripts/deploy_frontend.sh                   # vite build + S3 sync
python scripts/create_observability.py            # dashboard + alarms (idempotent)

# Report pipeline:
python scripts/fill_report.py                     # (already applied; token filler)
python scripts/make_figures.py                    # chart SVGs -> .build/charts
bash scripts/render_svgs.sh                       # SVG -> PNG (Edge headless, 2x)
python scripts/build_report_pdf.py                # REPORT.md -> DocuMind-A3-Report.pdf

# Corpus hygiene (removes vectors whose job rows expired, so fresh documents
# rank cleanly for generic questions; safe + idempotent):
python scripts/purge_stale_vectors.py            # run before demos
```

Before taking screenshots/recording: `node scripts/capture_narrow.mjs` etc. (Edge via debug port; see script headers).

## 5. Verification status (evidence-backed)

| Check | Result | Evidence |
|---|---|---|
| Full smoke (14 checks) | **14/14** re-verified 7 Oct after the stale-vector purge (job 46 s; origin MISS 2 175 ms / 4 sources → HIT-L1 0 ms) | `docs/evidence/smoke-20261006-223730.json` (+ 2 Oct runs) |
| Pipeline burst, 30 docs | upload 6.5 s · backlog peak 28 · pool 1→6 in ~25 s · drain 58.2 s · 30/30 COMPLETED · pool → 0 | `docs/evidence/pipeline-20261002-143846.json` + `-timeline.csv` |
| Cache, 50 queries | origin server p50 2 408 ms (n=24) vs cached 0 ms L1 (n=62); 62/62 repeats hit | `cache-bench-20261002-144249.json`, `cache-summary.json` |
| Fault isolation | password-protected / corrupt / blank → FAILED + DLQ depth 3, zero worker crashes | `dlq-20261002-145114.json` |
| Worker unit tests | 8/8 green | `backend/worker/test_local.py` |
| Vector index health | Fresh-chunk visibility directly measured: **3 s** from job completion to searchable; queries return cited hits (direct + via API) | `docs/evidence/index-visibility-20261007.json`; probes 4 + 7 Oct |

**Known behaviours (not bugs, document them, don't "fix" them):**
1. **Fresh vectors are searchable within seconds** (directly measured 7 Oct: visible **3 s after job completion**, probe `job-a18d543f20`, evidence `docs/evidence/index-visibility-20261007.json`). Earlier "no matching documents" windows came from a different effect: vectors of **TTL-expired jobs remain in the index** and are filtered only *after* top-K ranking, a small new document can be out-ranked for *generic* questions. Document-specific questions surface fresh content immediately; the smoke test keeps a wait-loop as a safety margin. **Sweep dead vectors with `scripts/purge_stale_vectors.py` before demos** (first sweep 7 Oct: 185 stale vectors removed, only live content remained).
2. **24 h TTL expiry.** Job rows expire daily (by design); their vectors are then filtered by the stale-vector guard, so an old demo corpus legitimately "goes quiet". Re-seed fresh PDFs before demos/recording.
3. **Worker pool sits at 0 when idle**, that's the scale-to-zero target state, not an outage.

## 6. Sandbox constraints & the workarounds built around them

| Constraint | Workaround shipped |
|---|---|
| One API Gateway per student | Additive `/documind` routes on the shared `rqy6cawtyg` gateway (**never modify existing A2 routes**) |
| CloudFront creation denied | In-app static serving from S3 via the API; CDN documented as target state in the report |
| Managed Application Auto Scaling denied | Custom ECS controller task (see scaling law) |
| `tag_resources` (RGTA) denied; S3 buckets are ABAC-enabled | Tag via per-service APIs; S3 via `s3control.tag_resource` |
| Bedrock allow-list = Titan v2 + Gemma 3 only | Prompts/format tuned to those models (Gemma uses OpenAI-style `messages`); Claude/Nova code paths removed |
| Gateway hard 29 s integration timeout | Lambda timeout 29 s; generation budget tuned under it |
| Task role lacks `cloudwatch:PutMetricData` | Log-based metric filters + alarms (supported path) |
| EventBridge Scheduler APIs can't tag/verify in this account | Hygiene tick disabled; controller is the real scaler; schedule not declared in YAML |

## 7. Hard-won gotchas (for the next operator)

- **Presigned PUT requires `Content-Type: application/pdf`** exactly, omit it and S3 returns 403 (the lag-probe script tripped on this once).
- **Presigned URLs must use SigV4 + the regional endpoint** (`s3.ap-southeast-2.amazonaws.com`). The global endpoint 307-redirects and browsers fail with a generic CORS/network error while curl succeeds. `backend/api/store.py` forces both.
- **boto3 DynamoDB rejects Python floats**, L2 cache payloads are stored as a JSON **string** (`payloadJson`). Do not "improve" this back to a map.
- **SSO expires ≈hourly**; smoke test works without credentials (it degrades to the controller cold-start and skips the admin-key check); load tests need `aws sso login`.
- **Local test dependencies** are not installed automatically, run `pip install -r scripts/requirements-test.txt` (plus `pypdf markdown pyyaml` for report rebuilds).
- **Report figure rules:** screenshots captured at 590 px CSS viewport (≈12 pt print rule); charts authored 17–20 px fonts on ≤850 px canvases; **body must stay ≤30 pages**, verify with `pypdf` after every rebuild.
- Windows/MSYS quirks: `curl -o /dev/null` exits 23 (use bash redirects); native tools need `C:/…` forward-slash paths; Edge headless needs an isolated `--user-data-dir` + `cygpath -w` absolute paths.

## 8. Open items

1. **Record the demo video** per `docs/VIDEO_SCRIPT.md` (≈5 min). Re-seed fresh PDFs ~5 min before recording (TTL + index settling). The system is confirmed working for live demos.
2. **AWS Pricing Calculator share link** (spec requirement): `docs/COST_MODEL.md` §5 lists the 11 line items to enter; paste the public link into `REPORT.md` §5.2 and rebuild the PDF with `scripts/build_report_pdf.py` (~10 min of clicking).

**Completed:**

- ~~Shared gateway tag~~ **Done (28 Oct):** `rqy6cawtyg` now carries `purpose=assessment 3`; all 51 routes untouched, including the A2 ones.
- ~~Quantify the S3 Vectors visibility lag~~ **Done (7 Oct):** measured **3 s** from job completion to searchable; RUNBOOK + Section 5 updated.
- ~~Log-group retention~~ **Not achievable:** `logs:PutRetentionPolicy` is not authorised for the student role, so retention stays at the CloudWatch never-expire default. Recorded as a limitation in the report rather than claimed as configured.

**Housekeeping (optional):**

- Split the two architecture + two sequence diagrams into focused, larger-type diagrams (the only remaining legibility gap vs the ~12 pt guidance).
- `scripts/build_worker_image.sh` builds from the repo root, but `backend/Dockerfile` needs the `backend/` context. Build with `docker build -f backend/Dockerfile -t <repo>:latest backend`.

## 9. Cost snapshot (50 concurrent users, average demand)

≈ **US$16.68/month on-demand** (≈US$8.98 with Fargate Spot), dominated by the always-on controller (US$10.80 on-demand) plus the observability stack; the worker pool costs ~nothing idle (scale-to-zero) and cents per burst. Computed from the AWS public Price List files by `scripts/calc_ondemand_cost.py` into `docs/evidence/cost-ondemand.csv`. Full breakdown, assumptions and sustainability notes: `docs/COST_MODEL.md` and REPORT.md §5.

## 10. File map (top level)

```
backend/            api/ (FastAPI app, store, rag, cache, mcp) · worker/ (poll, extract, chunk, embed, store)
backend/scaler/     controller.py (the autoscaler) · scaler.py
frontend/           React+Vite SPA (upload zone, pipeline monitor, chat, stats)
infra/              ECS task definitions (worker, controller)
scripts/            deploy-infra.sh · load_test.py · smoke_test.py · create_observability.py
                    build_* · deploy_* · make_figures.py · render_svgs.sh · build_report_pdf.py
                    capture_*.mjs (screenshot harnesses) · collect_infra.py · lag pin: .build/lag_probe.py
docs/               RUNBOOK.md · VIDEO_SCRIPT.md · COST_MODEL.md · HANDOFF.md (this file)
                    diagrams/ (SVG) · figures/ (PNG) · evidence/ (JSON/CSV)
REPORT.md · DocuMind-A3-Report.pdf · SUBMISSION: submission-a3-infra.yml · README.md
```

**Non-negotiables for anyone touching this:** keep every resource tagged `purpose=assessment 3` + `qut-username=n12136514@qut.edu.au`, keep all names prefixed `n12136514-`, stay in `ap-southeast-2`, and **do not modify Assignment 2 resources** (cluster `n12136514-cluster`, strobe service/buckets, `n12136514-ddb-users`, `n12136514-lambda-auth`, `n12136514-mcp-task`, existing gateway routes).
