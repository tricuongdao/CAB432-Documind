# DocuMind AI, demonstration video script

**Target 4:25** (hard limit 5:00). Screen capture only, a phone recording is not accepted.

**You only touch two things on camera: the AWS web console and the DocuMind web app.** No terminal, no CLI, no DevTools. The load that proves scaling comes from dropping a batch of PDFs into the app itself, which is stronger evidence anyway, it's your product doing the work.

The assignment asks this video to show the system **works**, plus **asynchronous behaviour**, **caching** and **scaling**. Each is one clean act below.

| Act | Time | Evidence |
|---|---|---|
| 1 | 0:00 – 0:20 | App at rest |
| 2 | 0:20 – 1:20 | **Caching**, origin 2.5 s, then cache hit 0 ms |
| 3 | 1:20 – 2:20 | **Asynchronous**, a batch moves through the pipeline on its own |
| 4 | 2:20 – 3:40 | **Scaling**, queue backlog drives 0 → 6 workers, then back to 0 |
| 5 | 3:40 – 4:10 | Batch finished; delete a document |
| 6 | 4:10 – 4:25 | Close |

**Speaking rule:** say what's on screen, then stop. Anything about *why* it's built that way belongs in the report.

---

## 1. Before you hit record

Everything here is off camera and needs no terminal.

1. **Log into the AWS console** in your browser (the SSO access portal, this is the console session, not the CLI).
2. Open the app: <https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/>
3. **Upload 3–4 real PDFs** and wait until every row says **Ready**. This is your reference corpus.
4. **Open one of those PDFs** and note **one fact you can point at**. You'll ask for it in Act 2, if the answer isn't in these files, the app will reply *"no matching documents"*.
5. **Make a batch of ~24 PDFs.** In File Explorer, select your PDFs, press **Ctrl+C**, then tap **Ctrl+V** seven or eight times, you'll get `file - Copy`, `file - Copy (2)`… all in one folder. You'll drag the whole lot in a single drop.
6. **Open two console tabs:**
   - SQS → Queues → **`n12136514-documind-queue`**
   - ECS → Clusters → **`n12136514-documind-cluster`** → Services tab
7. **Zoom the browser to 125 % (Ctrl +)** in every tab. Check the text is readable at arm's length.
8. Start OBS (1080p) or Canvas *Record media*. Mic on.

**Check the starting point:** the app shows your 3–4 Ready documents, and the ECS Services list shows the worker service at **0 running**. That zero is what makes the scale-up in Act 4 land. It's at 0 now, and it returns to 0 about two minutes after work stops, so if you've just been testing, wait a couple of minutes.

---

## 2. The script

**DO** = what to click. **SAY** = the only words you speak. **POINT AT** = hold the cursor there a beat.

### Act 1 · 0:00 – 0:20 · App at rest

**POINT AT** the header pills.

**SAY:** *"This is DocuMind, you upload PDFs, then ask questions about them. Up here it shows the API is online, the model it's using, and the region."*

**POINT AT** the pipeline rows, all Ready.

**SAY:** *"I've got four documents in here already, each showing how long it took to process."*

---

### Act 2 · 0:20 – 1:20 · Caching

**DO** type your prepared question and press Ask.

**POINT AT** the source pills under the answer, then the amber badge.

**SAY:** *"It answers using those documents, and cites the passages it found. The amber badge means this came from origin generation, about two and a half seconds."*

**DO** ask the **exact same question again**.

**POINT AT** the green badge.

**SAY:** *"Same question again. Green badge, cache hit, zero milliseconds, same answer."*

**POINT AT** the Cache statistics panel (top right).

**SAY:** *"And the cache panel is counting the hits and misses as they happen."*

> A badge reading **L2 shared** instead of **L1 memory** is still a correct cache hit, a different server answered it from the shared cache. Don't re-record.

---

### Act 3 · 1:20 – 2:20 · Asynchronous processing

**DO** switch to File Explorer, select all ~24 PDFs, drag them onto the drop zone in one go.

**SAY:** *"Now I'll drop twenty-four documents in at once. Each one uploads straight from the browser to S3."*

**POINT AT** a row's progress bar as its segments fill.

**SAY:** *"Each row moves through queued, worker processing, embedding, then ready, on its own. I'm not clicking anything; the page just picks up the new state."*

**SAY:** *"The progress bar fills one segment per stage."*

*Stay on this until several rows are visibly at different stages, that's the asynchronous evidence.*

---

### Act 4 · 2:20 – 3:40 · Scaling

**DO** switch to the **SQS tab**. Hit the refresh button. **POINT AT** **Messages available**.

**SAY:** *"Here's the queue behind it. Twenty-odd messages are waiting to be processed."*

**DO** switch to the **ECS tab** → **Services**. **POINT AT** the worker service **Running** count. Hit refresh if it hasn't updated.

**SAY:** *"And the worker pool is scaling up. It was at zero, now it's bringing tasks online, up to six."*

**POINT AT** the running count once it has climbed.

**SAY:** *"Six tasks running. Each one is one vCPU and two gigabytes."*

**DO** wait, refreshing occasionally, as the numbers move.

**SAY:** *"As the queue drains, the pool shrinks back down."*

**POINT AT** the count going to **0**.

**SAY:** *"And back to zero. No work, no workers."*

> The pool scales in 60-second steps and tasks take ~30 s to start, so this act is mostly watching numbers change, let it breathe and narrate only when a number moves. If you'd rather also show a chart, the CloudWatch dashboard `n12136514-documind-dashboard` has both, but its metrics lag one to two minutes, so the SQS and ECS pages above show the change sooner.

---

### Act 5 · 3:40 – 4:10 · Batch finished, and delete

**DO** switch back to the app.

**POINT AT** the list, most rows now Ready.

**SAY:** *"And the batch is through, twenty-four more documents indexed."*

**DO** click the **×** on one row, then **Delete**.

**SAY:** *"A document can be removed too, it deletes the file and its search index along with it."*

---

### Act 6 · 4:10 – 4:25 · Close

**SAY:** *"So that's upload, asynchronous processing, caching and scaling. The report covers the architecture and the cost model."*

**DO** stop recording.

---

## 3. Editing notes

- The batch takes ~60 s to queue and ~2 min to drain, and the pool scales in 60 s steps. **Record Acts 3–4 as one take** so the numbers stay consistent, then trim the boring stretches to fit the time.
- Speed-ramp the drain, but keep the **scale-up** and the **return to zero** at 1×, those are the two moments the scaling mark rests on.
- Cut dead air. Silence reads as "it broke".
- Slow the cursor down when it's over the cache badges; markers rewind to check `0 ms`.
- Export 1080p and confirm the text is legible before submitting.

## 4. Submit checklist

1. **Upload to Canvas and click Submit.** The brief calls this out specifically because people forget it.
2. Confirm the duration reads **4:00–5:00**, not 5:01.
3. Report PDF → Canvas. Source archive + `submission-a3-infra.yml` → Gradescope.

## 5. If something goes wrong

| Symptom | Fix |
|---|---|
| Answer says "no matching documents" | The 24 h cleanup removed your corpus. Re-upload a few PDFs, wait for **Ready**, retake Act 2. |
| Batch rows sit at **Queued** and don't move | The pool is cold-starting. Give it a minute; the ECS tab should show tasks appearing. |
| ECS still shows **0 running** after a minute | The controller polls every 60 s, wait one more cycle. If the queue is empty, the batch had already drained; redo Act 3 with more files. |
| Queue shows 0 immediately | You refreshed too late, the workers were fast. Drag another batch and cut straight to the console. |
| Badge says **L2 shared** | Fine, keep it, it's still a cache hit. |
| Batch drains too fast to narrate | Use more files (30+) next take; the backlog lasts longer. |
| Console session expired | Log into the console again in the browser, no CLI needed. |
