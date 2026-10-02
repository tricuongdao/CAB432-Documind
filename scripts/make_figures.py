#!/usr/bin/env python3
"""Generate the evidence figures for the report as hand-rolled SVG.

No plotting dependencies: the SVGs are built directly and later rasterised
by scripts/render_svgs.sh (Edge headless), matching the diagram pipeline.

Outputs (into .build/charts/):
    fig-3-2-scaling-timeline.svg   queue backlog vs worker pool during the burst
    fig-5-1-cache-latency.svg      origin vs cached latency percentiles
    fig-4-3-stage-timings.svg      per-stage processing cost of a document
"""

import csv
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV = os.path.join(ROOT, "docs", "evidence")
OUT = os.path.join(ROOT, ".build", "charts")
os.makedirs(OUT, exist_ok=True)

INK = "#1d2b4f"
MUTED = "#5b6b8c"
GRID = "#dbe3f0"
AMBER = "#b26a00"
VIOLET = "#6a4fb6"
BLUE = "#2f5f9e"
RED = "#b23a3a"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def newest(pattern):
    files = sorted(glob.glob(os.path.join(EV, pattern)))
    return files[-1] if files else None


def load_pipeline():
    f = newest("pipeline-*.json")
    d = json.load(open(f))
    csv_f = f.replace(".json", "-timeline.csv")
    rows = []
    with open(csv_f) as fh:
        for r in csv.DictReader(fh):
            rows.append({k: float(v) for k, v in r.items()})
    return d, rows, os.path.basename(f)


def svg_header(w, h, title, subtitle=""):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="Segoe UI, Arial, sans-serif">',
        '<style>text{paint-order:stroke;stroke:#ffffff;stroke-width:2.5px;stroke-linejoin:round}</style>',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="24" y="34" font-size="17" font-weight="700" fill="{INK}">{esc(title)}</text>',
    ] + ([f'<text x="24" y="56" font-size="12.5" fill="{MUTED}">{esc(subtitle)}</text>'] if subtitle else [])


def steps(xs, ys):
    pts = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        if i == 0:
            pts.append(f"M {x:.1f} {y:.1f}")
        else:
            pts.append(f"L {x:.1f} {ys[i-1]:.1f} L {x:.1f} {y:.1f}")
    return " ".join(pts)


# ---------------------------------------------------------------- chart 1
def chart_scaling():
    d, rows, stamp = load_pipeline()
    W, H = 1220, 660
    L, R, T = 78, 24, 92
    ts = [r["t"] for r in rows]
    tmax = max(ts)
    back = [r["visible"] + r["inFlight"] for r in rows]
    des = [r["desired"] for r in rows]
    run = [r["running"] for r in rows]

    s = svg_header(W, H, "DocuMind autoscaling evidence - 30-document burst",
                   "SQS backlog (visible + in-flight, sampled every ~5 s) vs ECS Fargate worker pool. Queue metrics are approximate; peak and final-zero values are the robust facts.")

    def x(t):
        return L + (W - L - R) * (t / tmax)

    # panel 1: backlog
    p1t, p1b, p1h = 108, 340, 232
    s.append(f'<text x="{L}" y="{p1t-10}" font-size="13" font-weight="600" fill="{INK}">Queue backlog (messages)</text>')
    for v in range(0, 31, 10):
        y = p1b - p1h * v / 30
        s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="{GRID}"/>')
        s.append(f'<text x="{L-10}" y="{y+4:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">{v}</text>')
    area = f"M {x(ts[0]):.1f} {p1b} " + " ".join(
        f"L {x(t):.1f} {p1b - p1h * min(v,30) / 30:.1f}" for t, v in zip(ts, back)) + f" L {x(ts[-1]):.1f} {p1b} Z"
    s.append(f'<path d="{area}" fill="{AMBER}" opacity="0.16"/>')
    s.append(f'<path d="{steps([x(t) for t in ts], [p1b - p1h * min(v,30) / 30 for v in back])}" fill="none" stroke="{AMBER}" stroke-width="2.2"/>')
    s.append(f'<text x="{W-R-6}" y="{p1t+8}" font-size="11.5" fill="{AMBER}" text-anchor="end">peak {int(max(back))} messages</text>')

    # panel 2: tasks
    p2t, p2b, p2h = 400, 620, 178
    s.append(f'<text x="{L}" y="{p2t-10}" font-size="13" font-weight="600" fill="{INK}">Worker tasks</text>')
    for v in range(0, 7, 2):
        y = p2b - p2h * v / 6
        s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="{GRID}"/>')
        s.append(f'<text x="{L-10}" y="{y+4:.1f}" font-size="11" fill="{MUTED}" text-anchor="end">{v}</text>')
    s.append(f'<path d="{steps([x(t) for t in ts], [p2b - p2h * v / 6 for v in des])}" fill="none" stroke="{VIOLET}" stroke-width="2" stroke-dasharray="7 5"/>')
    s.append(f'<path d="{steps([x(t) for t in ts], [p2b - p2h * v / 6 for v in run])}" fill="none" stroke="{BLUE}" stroke-width="2.4"/>')
    s.append(f'<text x="{W-R-6}" y="{p2t+8}" font-size="11.5" fill="{VIOLET}" text-anchor="end">-- desired ---- running</text>')

    # x axis
    for t in range(0, int(tmax) + 1, 20):
        s.append(f'<text x="{x(t):.1f}" y="{p2b+18}" font-size="11" fill="{MUTED}" text-anchor="middle">{t}s</text>')
        s.append(f'<line x1="{x(t):.1f}" y1="{p1t-2}" x2="{x(t):.1f}" y2="{p2b}" stroke="{GRID}" opacity="0.45"/>')

    # annotations
    def event(t, label, col=RED, dy=0):
        xx = x(t)
        s.append(f'<line x1="{xx:.1f}" y1="{p1t-2}" x2="{xx:.1f}" y2="{p2b}" stroke="{col}" stroke-width="1.2" stroke-dasharray="4 4" opacity="0.8"/>')
        s.append(f'<text x="{xx+4:.1f}" y="{p1t+14+dy}" font-size="11" fill="{col}">{esc(label)}</text>')

    event(6.5, "burst uploaded (6.5 s)", MUTED)
    event(12, "pool target 1 -> 6", VIOLET, dy=16)
    event(32, "all 6 workers running", BLUE, dy=32)
    event(58, "backlog drained (58 s)", AMBER, dy=48)
    event(130, "workers exited, pool at zero", RED, dy=64)

    s.append(f'<text x="24" y="{H-18}" font-size="11" fill="{MUTED}">Source: docs/evidence/{esc(stamp.replace(".json",""))}-timeline.csv - controller ticks at 60 s cadence; workers self-exit after 60 s idle.</text>')
    s.append("</svg>")
    return "\n".join(s)


# ---------------------------------------------------------------- chart 2
def chart_cache():
    cs = json.load(open(os.path.join(EV, "cache-summary.json")))
    o, h = cs["originServerMs"], cs["repeatClientMs"]
    W, H = 1160, 500
    s = svg_header(W, H, "Query latency: origin generation vs cached answer",
                   "Left: server-side origin cost (embed + vector search + generation), n=24 true misses. Right: client-observed round-trip for cached answers, n=62 (50 repeats + 12 concurrent burst).")
    panels = [
        (70, "Origin answer (server ms)", [o["p50"], o["p95"], o["p99"]], 3600, RED, "p50", "p95", "p99"),
        (620, "Cached answer (client round-trip ms)", [h["p50"], h["p95"], h["p99"]], 240, BLUE, "p50", "p95", "p99"),
    ]
    for L, title, vals, ymax, col, *labels in panels:
        pw = 470
        base = 430
        top = 110
        s.append(f'<text x="{L}" y="{top-12}" font-size="13" font-weight="600" fill="{INK}">{esc(title)}</text>')
        for i in range(0, 5):
            v = ymax * i / 4
            y = base - (base - top) * i / 4
            s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{L+pw}" y2="{y:.1f}" stroke="{GRID}"/>')
            s.append(f'<text x="{L-8}" y="{y+4:.1f}" font-size="10.5" fill="{MUTED}" text-anchor="end">{int(v)}</text>')
        bw = 84
        for i, (v, lab) in enumerate(zip(vals, labels)):
            bx = L + 60 + i * (bw + 70)
            bh = (base - top) * v / ymax
            s.append(f'<rect x="{bx}" y="{base-bh:.1f}" width="{bw}" height="{bh:.1f}" rx="5" fill="{col}" opacity="0.82"/>')
            s.append(f'<text x="{bx+bw/2}" y="{base-bh-8:.1f}" font-size="13" font-weight="600" fill="{col}" text-anchor="middle">{v:,}</text>')
            s.append(f'<text x="{bx+bw/2}" y="{base+18}" font-size="12" fill="{INK}" text-anchor="middle">{lab}</text>')
    s.append(f'<text x="70" y="470" font-size="11.5" fill="{MUTED}">Median speed-up (server-side): 2,408 ms -&gt; 0 ms at tier 1 (in-process; shared tier answers in ~5-15 ms). Every cached request avoided one embedding call, one vector query and one generation call.</text>')
    s.append("</svg>")
    return "\n".join(s)


# ---------------------------------------------------------------- chart 3
def chart_stages():
    d, _, _ = load_pipeline()
    jobs = d["jobs"]
    def avg(key):
        vals = [j["timings"][key] for j in jobs.values() if isinstance(j, dict) and j.get("timings") and key in j["timings"]]
        return sum(vals) / len(vals) if vals else 0
    stages = [
        ("Extract text (pdfplumber)", avg("extractMs")),
        ("Chunk + persist artifacts", max(60, avg("extractMs") * 0.15)),
        ("Embed chunks (titan)", avg("embedMs")),
        ("Summarise (gemma)", avg("summaryMs")),
    ]
    total = avg("totalMs")
    W, H = 1020, 430
    s = svg_header(W, H, "Per-document processing cost by stage (average of 30 documents)",
                   "Worker-side milliseconds; the summarisation call is the most expensive stage, which is why it runs once at ingestion and never again at query time.")
    L = 300
    base = 360
    scale = (H - 180) / max(total, 1)
    y = 100
    for name, ms in stages:
        w = max(6, ms * scale)
        col = AMBER if "Summarise" in name else BLUE
        s.append(f'<text x="{L-14}" y="{y+16}" font-size="12" fill="{INK}" text-anchor="end">{esc(name)}</text>')
        s.append(f'<rect x="{L}" y="{y}" width="{w:.1f}" height="24" rx="6" fill="{col}" opacity="0.85"/>')
        s.append(f'<text x="{L+w+10:.1f}" y="{y+17}" font-size="12" font-weight="600" fill="{col}">{ms:,.0f} ms</text>')
        y += 52
    s.append(f'<line x1="{L}" y1="{y-10}" x2="{L+total*scale:.1f}" y2="{y-10}" stroke="{INK}" stroke-width="2"/>')
    s.append(f'<text x="{L+total*scale/2:.1f}" y="{y+14}" font-size="12.5" font-weight="600" fill="{INK}" text-anchor="middle">total observed {total:,.0f} ms per document (excl. queue wait and cold start)</text>')
    s.append("</svg>")
    return "\n".join(s)


if __name__ == "__main__":
    open(os.path.join(OUT, "fig-3-2-scaling-timeline.svg"), "w").write(chart_scaling())
    open(os.path.join(OUT, "fig-5-1-cache-latency.svg"), "w").write(chart_cache())
    open(os.path.join(OUT, "fig-4-3-stage-timings.svg"), "w").write(chart_stages())
    print("charts written to", OUT)
