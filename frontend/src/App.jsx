import { useCallback, useEffect, useRef, useState } from 'react'
import { apiJson, uploadWithProgress } from './api'

// The four states the pipeline actually reports. Keep in sync with the worker's
// status vocabulary — the progress bar is determinate at exactly this
// granularity and claims nothing finer.
const STAGES = ['Queued', 'Worker processing', 'Embedding', 'Ready']
const STATUS_STEP = { CREATED: 0, QUEUED: 0, PROCESSING: 1, EMBEDDING: 2, COMPLETED: 3, FAILED: -1 }

const STAGGER_MS = 40
const STAGGER_MAX = 5

function statusBadge(job) {
  switch (job.status) {
    case 'COMPLETED': return <span className="status-badge ok">Ready</span>
    case 'FAILED': return <span className="status-badge err">Failed</span>
    case 'EMBEDDING': return <span className="status-badge embed">Embedding</span>
    case 'PROCESSING': return <span className="status-badge busy">Processing</span>
    default: return <span className="status-badge">Queued</span>
  }
}

// Staged progress: four segments, filled only by real status transitions.
// There is no completion percentage anywhere in the API, so this deliberately
// never invents one — it is determinate at stage granularity and silent below
// that, with an indeterminate sweep inside the active segment.
function StageProgress({ status }) {
  const failed = status === 'FAILED'
  const idx = STATUS_STEP[status] ?? 0
  const stage = failed ? 'Failed' : STAGES[idx]
  const step = failed ? 0 : idx + 1

  return (
    <div className="progress">
      <div
        className="track"
        role="progressbar"
        aria-valuemin={1}
        aria-valuemax={4}
        aria-valuenow={failed ? 0 : step}
        aria-valuetext={failed ? 'Failed' : `${stage} (stage ${step} of 4)`}
      >
        {STAGES.map((s, i) => {
          let state = 'todo'
          if (failed) state = i === 0 ? 'failed' : 'todo'
          else if (status === 'COMPLETED') state = 'done'
          else if (i < idx) state = 'done'
          else if (i === idx) state = 'active'
          return <span key={s} className={'seg ' + state} />
        })}
      </div>
      <div className="progress-label">
        <span className="stage">{stage}</span>
        {status === 'COMPLETED' ? <span>complete</span> : <span>{step} / 4</span>}
      </div>
    </div>
  )
}

function timeAgo(iso) {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (!t) return ''
  const s = Math.max(0, Math.round((Date.now() - t) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}

function mdLite(text) {
  const esc = String(text || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  let html = esc
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br/>')}</p>`).join('')
  return { __html: html }
}

function UploadZone({ onUploaded, onError }) {
  const [busy, setBusy] = useState(null)
  const [drag, setDrag] = useState(false)
  const inputRef = useRef(null)

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || [])
    for (const f of files) {
      if (!f.name.toLowerCase().endsWith('.pdf')) {
        onError(`${f.name}: only PDF files are supported`)
        continue
      }
      if (f.size > 25 * 1024 * 1024) {
        onError(`${f.name}: file is larger than the 25 MB limit`)
        continue
      }
      setBusy({ name: f.name, progress: 0 })
      try {
        const { data } = await apiJson('/api/upload', {
          method: 'POST',
          body: JSON.stringify({ filename: f.name, size: f.size }),
        })
        await uploadWithProgress(data.uploadUrl, f, (p) => setBusy({ name: f.name, progress: p }))
        onUploaded(data)
      } catch (err) {
        onError(`${f.name}: ${err.message}`)
      } finally {
        setBusy(null)
      }
    }
  }

  const openPicker = () => inputRef.current && inputRef.current.click()

  return (
    <section className="card panel-upload">
      <header><h2>Upload documents</h2></header>
      {/* A real keyboard-reachable control, not a div with onClick. */}
      <div
        className={'dropzone' + (drag ? ' drag' : '')}
        role="button"
        tabIndex={0}
        aria-label="Upload PDF documents — drop files here, or press Enter to browse"
        onClick={openPicker}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPicker() }
        }}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files) }}
      >
        <div className="big">Drop PDFs here or <b>browse</b></div>
        <div className="small">Direct-to-S3 upload via presigned URLs · max 25 MB per file</div>
        <input
          ref={inputRef} type="file" accept="application/pdf,.pdf" multiple hidden
          onChange={(e) => { handleFiles(e.target.files); e.target.value = '' }}
        />
      </div>
      {busy && (
        <div className="upload-progress">
          <div className="meta"><span>{busy.name}</span><span>{busy.progress}%</span></div>
          <div className="bar"><span style={{ transform: `scaleX(${busy.progress / 100})` }} /></div>
        </div>
      )}
    </section>
  )
}

function PipelineMonitor({ jobs, statusMeta, onDelete }) {
  // Animate only rows that are genuinely new. The list re-renders every 2.5 s,
  // so animating on render would replay the entrance constantly.
  const seen = useRef(new Set())
  const [fresh, setFresh] = useState(() => new Set())
  const [confirming, setConfirming] = useState(null)
  const [deleting, setDeleting] = useState(null)

  useEffect(() => {
    const added = jobs.filter((j) => !seen.current.has(j.jobId))
    if (added.length === 0) return undefined
    added.forEach((j) => seen.current.add(j.jobId))
    setFresh(new Set(added.map((j) => j.jobId)))
    const t = setTimeout(() => setFresh(new Set()), 700)
    return () => clearTimeout(t)
  }, [jobs])

  // Deleting a document is destructive and must be a deliberate second click,
  // not a stray one on the row.
  const remove = async (jobId) => {
    setDeleting(jobId)
    try {
      await onDelete(jobId)
      setConfirming(null)
    } finally {
      setDeleting(null)
    }
  }

  return (
    <section className="card panel-pipe">
      <header>
        <h2>
          Live pipeline
          <span className="sub">
            {' '}· auto-refresh 2.5 s{statusMeta ? ` · status served from cache: ${statusMeta.cache} (${statusMeta.ms} ms)` : ''}
          </span>
        </h2>
        <div className="legend">
          {STAGES.map((s, i) => (
            <span key={s}>{i > 0 ? ' › ' : ''}{s}</span>
          ))}
        </div>
      </header>
      <div className="body">
        {jobs.length === 0 && (
          <div className="empty">No documents yet. Upload a PDF to watch it move through the pipeline.</div>
        )}
        {jobs.map((j, i) => {
          const isNew = fresh.has(j.jobId)
          const isConfirming = confirming === j.jobId
          return (
            <div
              className={'job' + (isNew ? ' is-new' : '')}
              key={j.jobId}
              style={isNew ? { animationDelay: `${Math.min(i, STAGGER_MAX) * STAGGER_MS}ms` } : undefined}
            >
              <div className="row1">
                <span className="name" title={j.filename}>{j.filename || j.jobId}</span>
                {statusBadge(j)}
                {isConfirming ? (
                  <span className="confirm">
                    <button
                      className="btn-mini danger"
                      onClick={() => remove(j.jobId)}
                      disabled={deleting === j.jobId}
                    >
                      {deleting === j.jobId ? 'Deleting…' : 'Delete'}
                    </button>
                    <button
                      className="btn-mini"
                      onClick={() => setConfirming(null)}
                      disabled={deleting === j.jobId}
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    className="job-del"
                    onClick={() => setConfirming(j.jobId)}
                    aria-label={`Delete ${j.filename || j.jobId}`}
                    title="Delete this document"
                  >
                    ×
                  </button>
                )}
              </div>
              <StageProgress status={j.status} />
              <div className="meta">
                <span><code>{j.jobId}</code></span>
                {j.chunkCount != null && <span>{j.chunkCount} chunks</span>}
                {j.pageCount != null && <span>{j.pageCount} pages</span>}
                {j.timings && j.timings.totalMs != null && (
                  <span>pipeline {(j.timings.totalMs / 1000).toFixed(1)} s</span>
                )}
                <span>{timeAgo(j.createdAt || j.completedAt)}</span>
                {j.status === 'COMPLETED' && (
                  <a className="summary-link" href={`${typeof window !== 'undefined' && window.location.pathname.startsWith('/documind') ? '/documind' : ''}/api/summaries/${j.jobId}`} target="_blank" rel="noreferrer">
                    View AI summary
                  </a>
                )}
              </div>
              {j.error && <div className="job-error">{j.error}</div>}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function Chat() {
  const [msgs, setMsgs] = useState([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef(null)

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [msgs, busy])

  const send = async () => {
    const question = q.trim()
    if (!question || busy) return
    setMsgs((m) => [...m, { role: 'user', text: question }])
    setQ('')
    setBusy(true)
    try {
      const { data } = await apiJson('/api/query', {
        method: 'POST',
        body: JSON.stringify({ question }),
      })
      setMsgs((m) => [...m, { role: 'assistant', ...data }])
    } catch (err) {
      setMsgs((m) => [...m, { role: 'assistant', error: err.message }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card panel-chat">
      <header>
        <h2>Ask your documents <span className="sub">· RAG over the indexed corpus</span></h2>
      </header>
      <div className="chat-log" ref={logRef} aria-busy={busy}>
        {msgs.length === 0 && (
          <div className="empty">
            Ask a question about your uploaded PDFs. Try the same question twice: the second
            response is served from the cache in milliseconds instead of seconds.
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role} ${m.error ? 'error' : ''}`}>
            {m.role === 'user' ? (
              <p>{m.text}</p>
            ) : m.error ? (
              <p>{m.error}</p>
            ) : (
              <>
                <div dangerouslySetInnerHTML={mdLite(m.answer)} />
                {Array.isArray(m.sources) && m.sources.length > 0 && (
                  <div className="sources">
                    {m.sources.map((s, si) => (
                      <span className="source-pill" key={si} title={s.text}>
                        [{si + 1}] {s.filename} · part {s.chunkIdx + 1} · {s.score}
                      </span>
                    ))}
                  </div>
                )}
                <div className="cache-line">
                  {m.cache === 'HIT-L1' || m.cache === 'HIT-L2' ? (
                    <span className="cache-hit">
                      ⚡ Cache hit ({m.cache === 'HIT-L1' ? 'L1 memory' : 'L2 shared'}) · {m.latencyMs} ms
                    </span>
                  ) : (
                    <span className="cache-miss">
                      🔍 Origin generation · {(m.latencyMs / 1000).toFixed(2)} s
                    </span>
                  )}
                  {m.timings && (
                    <span className="timings">
                      embed {m.timings.embedMs} ms · retrieve {m.timings.retrieveMs} ms · generate {m.timings.generateMs} ms
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        ))}
        {busy && (
          <div className="thinking">
            <span className="dots" aria-hidden="true"><i /><i /><i /></span>
            Generating answer…
          </div>
        )}
      </div>
      <div className="chat-input">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send() }}
          placeholder="Ask a question about your uploaded documents"
          aria-label="Ask a question about your uploaded documents"
        />
        <button className="btn" onClick={send} disabled={busy || !q.trim()}>{busy ? '…' : 'Ask'}</button>
      </div>
    </section>
  )
}

function CacheStats() {
  const [s, setS] = useState(null)
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const { data } = await apiJson('/api/cache/stats')
        if (alive) setS(data)
      } catch (e) { /* ignore */ }
    }
    load()
    const t = setInterval(load, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])
  const l1 = s && s.l1
  const pct = l1 ? Math.round((l1.hitRate || 0) * 100) : 0
  return (
    <section className="card panel-stats">
      <header>
        <h2>Cache statistics <span className="sub">· {s ? s.backend : 'loading'}</span></h2>
      </header>
      {!l1 ? (
        <div className="empty">Loading…</div>
      ) : (
        <>
          <div className="stat-strip">
            <div className="stat"><div className="v">{l1.hits}</div><div className="k">hits</div></div>
            <div className="stat"><div className="v">{l1.misses}</div><div className="k">misses</div></div>
            <div className="stat"><div className="v">{(l1.hitRate * 100).toFixed(0)}%</div><div className="k">hit rate</div></div>
            <div className="stat"><div className="v">{l1.entries}</div><div className="k">entries</div></div>
          </div>
          <div className="hitrate">
            <div className="bar"><span style={{ transform: `scaleX(${pct / 100})` }} /></div>
            <div className="hitrate-note">
              L1 {l1.entries}/{l1.capacity} entries · {l1.evictions} evictions · counters reset when this container is recycled
            </div>
          </div>
        </>
      )}
    </section>
  )
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [jobs, setJobs] = useState([])
  const [statusMeta, setStatusMeta] = useState(null)
  const [toast, setToast] = useState(null)
  const [toastLeaving, setToastLeaving] = useState(false)
  const toastTimer = useRef(null)

  const loadHealth = useCallback(async () => {
    try {
      const { data } = await apiJson('/healthz')
      setHealth(data)
    } catch (e) {
      setHealth(null)
    }
  }, [])

  const loadJobs = useCallback(async () => {
    try {
      const { data, headers } = await apiJson('/api/jobs?limit=30')
      setJobs(data)
      setStatusMeta({
        cache: headers.get('X-Cache') || '-',
        ms: headers.get('X-Response-Time-Ms') || '-',
      })
    } catch (e) { /* keep last known list */ }
  }, [])

  useEffect(() => {
    loadHealth()
    loadJobs()
    const t1 = setInterval(loadJobs, 2500)
    const t2 = setInterval(loadHealth, 30000)
    return () => { clearInterval(t1); clearInterval(t2) }
  }, [loadHealth, loadJobs])

  useEffect(() => () => clearTimeout(toastTimer.current), [])

  // Exit is animated before unmount, so the toast doesn't just blink out.
  const showError = useCallback((msg) => {
    clearTimeout(toastTimer.current)
    setToast(msg)
    setToastLeaving(false)
    toastTimer.current = setTimeout(() => {
      setToastLeaving(true)
      toastTimer.current = setTimeout(() => {
        setToast(null)
        setToastLeaving(false)
      }, 170)
    }, 5200)
  }, [])

  // Delete a document: vectors, S3 artifacts, job row and the answer cache.
  // Declared after showError — a const is in its temporal dead zone above its
  // own declaration, so referencing it earlier would throw at render.
  const deleteJob = useCallback(async (jobId) => {
    try {
      await apiJson(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
      await loadJobs()
    } catch (err) {
      showError(`Could not delete ${jobId}: ${err.message}`)
    }
  }, [loadJobs, showError])

  return (
    <div className="shell">
      <header className="top">
        <div className="top-inner">
          <img className="logo" src="favicon.svg" alt="" />
          <div className="brand">
            <h1>DocuMind AI</h1>
            <p>CAB432 Assessment 3 · serverless document intelligence · student n12136514</p>
          </div>
          <div className="top-spacer" />
          <span className="pill"><span className={'dot ' + (health ? 'ok' : '')} />{health ? 'API online' : 'API unreachable'}</span>
          {health && <span className="pill">{health.ragModel}</span>}
          {health && <span className="pill">{health.region}</span>}
        </div>
      </header>

      <main className="bento">
        <UploadZone onUploaded={() => loadJobs()} onError={showError} />
        <PipelineMonitor jobs={jobs} statusMeta={statusMeta} onDelete={deleteJob} />
        <Chat />
        <CacheStats />
      </main>

      <footer>
        Event-driven pipeline: S3 → EventBridge → SQS → ECS Fargate workers · Bedrock embeddings + generation
        · two-tier cache (in-memory L1 + DynamoDB L2) · MCP endpoint at <code>POST /mcp</code>
      </footer>

      {toast && (
        <div className={'toast' + (toastLeaving ? ' leaving' : '')} role="status" aria-live="polite">
          {toast}
        </div>
      )}
    </div>
  )
}
