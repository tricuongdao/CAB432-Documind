# DocuMind AI — Demonstration video script (4–5 minutes)

**Goal:** "look, it works" evidence for (1) the working system, (2) caching with hard numbers, (3) auto-scaling under real load, (4) fault isolation. The report carries the explanations; the video shows the behaviour. Target duration ≈4:30.

**Recording setup (before you hit record)**

| # | Prepare | Detail |
|---|---------|--------|
| 1 | Browser tab 1 | `https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/` — logged-in desktop Chrome/Edge, **DevTools → Network** open, filter `query`, window zoomed to ~125 % (readable text) |
| 2 | Browser tab 2 | AWS console → CloudWatch → Dashboards → `n12136514-documind-dashboard` (zoom ~125 %) |
| 3 | Terminal 1 | Repo root; `python scripts/load_test.py pipeline --count 24 --timeout 900 --scale-in-watch 120` typed and ready but NOT entered |
| 4 | Terminal 2 | `aws sqs get-queue-attributes --queue-url <dlq-url> --attribute-names ApproximateNumberOfMessages` ready (or the CloudWatch DLQ widget) |
| 5 | Files | 3–4 small PDFs handy for the live upload moment |
| 6 | Recorder | OBS Studio (or Canvas *Record media*), 1080p, capture full screen; mic on, speak at demo pace |

**Timing cheat-sheet:** upload+ask ≈ 0:30 · caching proof ≈ 1:15 · load test + scaling ≈ 1:30 · DLQ ≈ 0:40 · wrap ≈ 0:15. If the pipeline test drains faster than expected, slow talking beats dead air — edit two takes together.

---

## 0:00 — 0:45 · Problem in one breath + the app at rest

**Do:** Show the UI hero (header shows `API online · google.gemma-3-12b-it · ap-southeast-2`).

**Say (≈short):** *"DocuMind turns uploaded PDFs into a searchable, question-answering knowledge base — and deletes everything automatically after 24 hours. It's built for bursty use: a study group dumps a semester of readings at once, asks questions for a while, then stops."*

**Show:** scroll the pipeline list — Ready documents, chunk counts, pipeline timings, the two **Failed** rows (encrypted/corrupt poison from the fault-isolation test) with their exact error reasons. Mention they're deliberate test casualties (setting up the 4:00 segment).

*This proves: the system is real, documents processed, and the failure surface is visible rather than hidden.*

## 0:45 — 1:15 · Live upload through the pipeline

**Do:** drag your 3–4 PDFs onto the drop zone. Point at the per-file progress bar (**direct-to-S3 presigned upload** — the API never sees the bytes). Then let the pipeline card show moving states.

**Say:** *"Uploads go browser → S3 directly. S3 raises an event, EventBridge routes it to the queue, and a Fargate worker pool picks them up — watch a row go Queued → Worker processing → Embedding → Ready. The pool is at zero right now; it will start workers because there's work."*

**Show:** within ~30 s, states advance on screen (leave the pointer highlighting the badge changes). If a worker is mid-start, narrate the cold start: *"there's the scale-up — a worker task just started because the queue had messages."*

## 1:15 — 2:20 · Caching, proven with DevTools (the required evidence)

**Do:** in the chat, ask a real question, e.g. **"What is the role of the dead letter queue in the pipeline?"** wait for the answer.

**Show:** the answer's **source pills** ([1] cloud-briefing…, part n, score) and the amber badge **“🔍 Origin generation · 2.xx s”** with the timing row (embed / retrieve / generate ms). In DevTools → Network, click the `query` request → show **Response Headers → X-Cache: MISS**.

**Say:** *"First ask hits the origin path: embed the question, vector search, filter expired documents, then generate. Two and a half seconds, and most of that is the model call."*

**Do:** ask the **identical question** again.

**Show:** instant answer, green badge **“⚡ Cache hit (L1 memory) · 0 ms”**; DevTools now shows **X-Cache: HIT-L1** and a ~40–50 ms total round trip (network included). Optionally run a third ask from a different tab for L2, or show `/api/cache/stats` numbers in the card below.

**Say:** *"Same question: the cache answers in zero server milliseconds — no embedding call, no vector search, no generation call. That's the cost story too: the cache removes most of the model spend."*

## 2:20 — 4:00 · Auto-scaling under load (the required evidence)

**Do:** switch to Terminal 1, hit enter on the pipeline load test (24 documents). Say what it does: *"This uploads two dozen documents through the real presigned flow in seconds."*

**Show:** terminal progress lines as documents complete. Switch to the CloudWatch dashboard and **narrate the chart live**:

- *"Queue backlog spikes — that's the amber line, up to ~20+ messages."*
- *"And the worker count — from 1 — jumps to 6. That's the metric-based scaling controller: it aims for two queue messages per worker, checked every 60 seconds."*
- Point at CPU/memory widgets: *"each worker is 1 vCPU, 2 GB — sized to the job."*

**Say while it drains:** *"The drain rate scales roughly with workers — that's the point of the exercise. The queue, not the users, absorbs the burst — nothing failed, it just queued."*

**Show the wind-down:** *"When the backlog hits zero the controller scales back to zero workers — no idle compute, no idle cost. That's where most of the cost saving lives."* (Dashboard's running/desired widgets return to 0, typically within ~2–3 minutes; edit the wait if needed, but keep the scale-to-zero moment in — it's the assignment's favourite bit.)

## 4:00 — 4:45 · Fault isolation: poison documents → DLQ

**Do:** switch to the pipeline card (or Terminal 2 / DLQ widget).

**Show:** the three **Failed** rows again, zoomed: `password-protected PDF (PdfminerException)`, `corrupt or unreadable PDF (No /Root object)`, `no extractable text (0 chars…)`.

**Say:** *"Deliberately poisoned uploads: a password-protected PDF, a corrupt file, a blank scan. Each one got bounded retries, was routed to the dead-letter queue with a classified reason, and its job was marked failed — the workers kept draining the healthy documents the whole time. One bad file, one bad file's problem."*

**Show (optional, 10 s):** the DLQ alarm in the console (`n12136514-documind-dlq-alarm`), or the DLQ depth widget on the dashboard.

## 4:45 — 5:00 · Wrap

**Say:** *"So: event-driven ingestion, a worker pool that scales itself and sleeps at zero, a two-tier cache that turns repeat questions into milliseconds, dead-letter isolation for bad input, and automatic 24-hour cleanup everywhere. The report has the architecture, the cost model and the methodology."*

---

**Editing notes**

- Cut dead air aggressively: the load test needs ~2 minutes of real time; a 4× speed-ramp on the drain portion is fine, keep the scale-out and scale-to-zero moments at 1×.
- If the calculator link is asked about in the video (it isn't required), skip it — it's report material.
- Keep cursor movements slow around the cache badges; markers rewind to check those numbers.
- Export at 1080p; Canvas upload + **Submit** (students routinely forget the Submit click).
- This file doubles as the shot list: record segments 2/3/4 as separate takes if the pipeline timing fights you, then join.
