# RUNBOOK — trying DocuMind yourself

Three ways, from zero-effort to full-nerd. Everything below works against the **live deployment**; nothing here needs Docker.

Live app: **https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/**

---

## 0) 30-second option — just use it (no setup at all)

1. Open the live app in a browser.
2. **Drag 1–3 of your own PDFs** onto the drop zone. Each file uploads straight to S3 (watch the progress bar), then a worker picks it up: status goes `Queued → Worker processing → Embedding → Ready` (first one takes ~30 s; the worker pool cold-starts itself because of the queue).
3. In the chat, **ask a question about what you uploaded**. You get an answer with source pills `[1] cloud-briefing… part 2`, and an amber badge: `🔍 Origin generation · 2.x s` (that's the real RAG path: embed → vector search → LLM).
4. **Ask the exact same question again.** This time: green badge `⚡ Cache hit (L1 memory) · 0 ms`. That's the two-tier cache the report is all about.
5. Scroll down: **Cache statistics** (hits/misses), and in the pipeline list, a **failed** password-protected/corrupt PDF (poison documents are isolated in the dead-letter queue — deliberate test casualties from the fault-isolation run).
6. Click **"View AI summary"** on any completed document — the worker's markdown digest.

## 1) One-time setup (for the test scripts)

You need Python 3 with pip. From the repo root:

```bash
python -m pip install -r scripts/requirements-test.txt     # requests + boto3
```

For the load tests (they read queue/ECS metrics while they run) you also need AWS access:

```bash
aws sso login        # browser flow; expire ~hourly, just re-run when it says expired
```

## 2) Automated checks (the evidence pipeline)

**Smoke test — 14 end-to-end checks, no AWS credentials needed (~2 min):**

```bash
DOCUMIND_ENDPOINT="https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind" python scripts/smoke_test.py
```

Expected: a chain of `PASS …` lines and `14 passed, 0 failed`; a JSON record lands in `docs/evidence/`.

**Worker unit tests — fully offline, no AWS at all:**

```bash
cd backend/worker && python test_local.py      # 8/8 green, exercises chunking/extraction logic
```

**The three experiment runs** (each needs `aws sso login`; each writes its own evidence file):

```bash
python scripts/load_test.py pipeline --count 30   # auto-scaling: 1→6 workers, drain, back to 0
python scripts/load_test.py cache    --queries 50 # origin vs cached latency percentiles
python scripts/load_test.py dlq                   # poison docs isolated in the DLQ
```

**While a load test runs, watch it live** — CloudWatch dashboard (queue backlog + worker count):

https://ap-southeast-2.console.aws.amazon.com/cloudwatch/home?region=ap-southeast-2#dashboards:name=n12136514-documind-dashboard

The party trick: kick off `pipeline`, open the dashboard, and watch backlog spike then `desired/running` tasks go **1 → 6**, drain, and fall back to **0**. That's the metric-based autoscaling (controller ticks every 60 s, target = 2 queue messages per worker).

Other console spots: SQS queues (`n12136514-documind-queue`, `…-dlq`), ECS cluster `n12136514-documind-cluster` (worker + controller services), the DLQ alarm `n12136514-documind-dlq-alarm`.

## 3) Where the results land

- `docs/evidence/` — every JSON/CSV the experiments produced (trend timeline CSV, cache percentiles, DLQ reasons, smoke runs).
- Rebuild the report charts: `python scripts/make_figures.py` (SVG → PNG via `scripts/render_svgs.sh`).
- Rebuild the report PDF: `python scripts/build_report_pdf.py` → `DocuMind-A3-Report.pdf`.

## 4) Common nits

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: requests/boto3/pypdf` | step 1: `python -m pip install -r scripts/requirements-test.txt` (add `pypdf markdown pyyaml` if rebuilding the report) |
| `The SSO session … has expired` | `aws sso login` (only needed for load tests / AWS console work) |
| Smoke test picks the "wrong" URL | pass `DOCUMIND_ENDPOINT="…/documind"` explicitly (as above) — it defaults to the Lambda Function URL |
| First query after idle feels slow | that's a Lambda cold start + the very first worker boot; repeats are instant (cache) |

Uploads expire automatically 24 h after submission — there is nothing to clean up.

## 5) From-zero rebuild (optional, for the brave)

```bash
aws sso login
bash scripts/deploy-infra.sh     # idempotent: buckets, queues, table, vectors, ECS, event rule, Lambda, frontend, dashboard
```

Needs Docker (for the linux/amd64 image), Node (frontend build) and the AWS SSO profile. It's the same orchestrator the project was deployed with; every resource carries `purpose=assessment 3` + `qut-username=n12136514@qut.edu.au`.
