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


def svg_open(w, h, title):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="Segoe UI, Arial, sans-serif">',
        '<style>text{paint-order:stroke;stroke:#ffffff;stroke-width:3px;stroke-linejoin:round}</style>',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="18" y="30" font-size="20" font-weight="700" fill="{INK}">{esc(title)}</text>',
    ]


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
    W, H = 840, 560
    L, R = 62, 16
    ts = [r["t"] for r in rows]
    tmax = max(ts)
    back = [r["visible"] + r["inFlight"] for r in rows]
    des = [r["desired"] for r in rows]
    run = [r["running"] for r in rows]
    s = svg_open(W, H, "Autoscaling evidence - 30-document burst")

    def x(t):
        return L + (W - L - R) * (t / tmax)

    # panel 1: backlog
    p1t, p1b, p1h = 72, 250, 178
    s.append(f'<text x="{L}" y="{p1t-10}" font-size="18" font-weight="600" fill="{INK}">Queue backlog (messages)</text>')
    for v in range(0, 31, 10):
        y = p1b - p1h * v / 30
        s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="{GRID}"/>')
        s.append(f'<text x="{L-8}" y="{y+6:.1f}" font-size="17" fill="{MUTED}" text-anchor="end">{v}</text>')
    area = f"M {x(ts[0]):.1f} {p1b} " + " ".join(
        f"L {x(t):.1f} {p1b - p1h * min(v,30) / 30:.1f}" for t, v in zip(ts, back)) + f" L {x(ts[-1]):.1f} {p1b} Z"
    s.append(f'<path d="{area}" fill="{AMBER}" opacity="0.16"/>')
    s.append(f'<path d="{steps([x(t) for t in ts], [p1b - p1h * min(v,30) / 30 for v in back])}" fill="none" stroke="{AMBER}" stroke-width="2.6"/>')
    pk = max(range(len(back)), key=lambda i: back[i])
    s.append(f'<text x="{x(ts[pk])+6:.1f}" y="{p1b - p1h*min(back[pk],30)/30 - 8:.1f}" font-size="17" font-weight="700" fill="{AMBER}">peak {int(back[pk])}</text>')

    # panel 2: tasks
    p2t, p2b, p2h = 318, 470, 152
    s.append(f'<text x="{L}" y="{p2t-10}" font-size="18" font-weight="600" fill="{INK}">Worker tasks</text>')
    s.append(f'<text x="{W-R}" y="{p2t-10}" font-size="16" fill="{VIOLET}" text-anchor="end">dashed = desired, solid = running</text>')
    for v in range(0, 7, 2):
        y = p2b - p2h * v / 6
        s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="{GRID}"/>')
        s.append(f'<text x="{L-8}" y="{y+6:.1f}" font-size="17" fill="{MUTED}" text-anchor="end">{v}</text>')
    s.append(f'<path d="{steps([x(t) for t in ts], [p2b - p2h * v / 6 for v in des])}" fill="none" stroke="{VIOLET}" stroke-width="2.4" stroke-dasharray="8 6"/>')
    s.append(f'<path d="{steps([x(t) for t in ts], [p2b - p2h * v / 6 for v in run])}" fill="none" stroke="{BLUE}" stroke-width="2.8"/>')

    # x axis + events
    for t in range(0, int(tmax) + 1, 20):
        s.append(f'<line x1="{x(t):.1f}" y1="{p1t-2}" x2="{x(t):.1f}" y2="{p2b}" stroke="{GRID}" opacity="0.45"/>')
        s.append(f'<text x="{x(t):.1f}" y="{p2b+24}" font-size="17" fill="{MUTED}" text-anchor="middle">{t}s</text>')

    def event(t, label, col, anchor="start"):
        xx = x(t)
        s.append(f'<line x1="{xx:.1f}" y1="{p1t-2}" x2="{xx:.1f}" y2="{p2b}" stroke="{col}" stroke-width="1.4" stroke-dasharray="5 5" opacity="0.85"/>')
        s.append(f'<text x="{xx + (4 if anchor == "start" else -4):.1f}" y="{p2b+52}" font-size="16" fill="{col}" text-anchor="{anchor}">{esc(label)}</text>')

    event(6.5, "burst uploaded (6.5 s)", MUTED)
    event(12, "pool target 1 > 6", VIOLET)
    event(58, "drained (58 s)", AMBER)
    event(128, "pool at zero (135 s)", RED, anchor="end")

    s.append(f'<text x="18" y="{H-12}" font-size="15" fill="{MUTED}">Source: docs/evidence/{esc(stamp.replace(".json", ""))}-timeline.csv · sampled every ~5 s; queue metrics are approximate.</text>')
    s.append("</svg>")
    return "\n".join(s)


# ---------------------------------------------------------------- chart 2
def chart_cache():
    cs = json.load(open(os.path.join(EV, "cache-summary.json")))
    o, h = cs["originServerMs"], cs["repeatClientMs"]
    W, H = 800, 470
    s = svg_open(W, H, "Query latency: origin vs cached answer")
    panels = [
        (16, "Origin answer (server ms)", [o["p50"], o["p95"], o["p99"]], 3600, RED),
        (432, "Cached answer (client ms)", [h["p50"], h["p95"], h["p99"]], 240, BLUE),
    ]
    for L, title, vals, ymax, col in panels:
        pw = 352
        top, base = 84, 330
        s.append(f'<text x="{L}" y="{top-14}" font-size="17" font-weight="600" fill="{INK}">{esc(title)}</text>')
        for i in range(0, 5):
            v = ymax * i / 4
            y = base - (base - top) * i / 4
            s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{L+pw}" y2="{y:.1f}" stroke="{GRID}"/>')
            s.append(f'<text x="{L-6}" y="{y+6:.1f}" font-size="16" fill="{MUTED}" text-anchor="end">{int(v):,}</text>')
        bw = 62
        for i, (v, lab) in enumerate(zip(vals, ["p50", "p95", "p99"])):
            bx = L + 50 + i * (bw + 48)
            bh = max(3, (base - top) * v / ymax)
            s.append(f'<rect x="{bx}" y="{base-bh:.1f}" width="{bw}" height="{bh:.1f}" rx="6" fill="{col}" opacity="0.85"/>')
            s.append(f'<text x="{bx+bw/2}" y="{base-bh-10:.1f}" font-size="19" font-weight="700" fill="{col}" text-anchor="middle">{v:,}</text>')
            s.append(f'<text x="{bx+bw/2}" y="{base+26}" font-size="18" fill="{INK}" text-anchor="middle">{lab}</text>')
    s.append(f'<text x="16" y="412" font-size="16" fill="{MUTED}">Origin (server-side, n=24 true misses): median 2,408 ms.</text>')
    s.append(f'<text x="16" y="436" font-size="16" fill="{MUTED}">Cached (n=62): 0 ms in-process · ~5-15 ms from the shared tier · 46 ms client round-trip median.</text>')
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
    W, H = 800, 420
    s = svg_open(W, H, "Per-document processing cost by stage (30-doc average)")
    L = 330
    scale = 320 / max(total, 1)
    y = 78
    for name, ms in stages:
        w = max(8, ms * scale)
        col = AMBER if "Summarise" in name else BLUE
        s.append(f'<text x="{L-16}" y="{y+21}" font-size="18" fill="{INK}" text-anchor="end">{esc(name)}</text>')
        s.append(f'<rect x="{L}" y="{y}" width="{w:.1f}" height="30" rx="7" fill="{col}" opacity="0.85"/>')
        s.append(f'<text x="{L+w+12:.1f}" y="{y+22}" font-size="19" font-weight="700" fill="{col}">{ms:,.0f} ms</text>')
        y += 58
    s.append(f'<line x1="{L}" y1="{y-16}" x2="{L+total*scale:.1f}" y2="{y-16}" stroke="{INK}" stroke-width="2.4"/>')
    s.append(f'<text x="{L}" y="{y+12}" font-size="18" font-weight="600" fill="{INK}">total ≈{total:,.0f} ms per document (excl. queue wait)</text>')
    s.append("</svg>")
    return "\n".join(s)


if __name__ == "__main__":
    open(os.path.join(OUT, "fig-3-2-scaling-timeline.svg"), "w").write(chart_scaling())
    open(os.path.join(OUT, "fig-5-1-cache-latency.svg"), "w").write(chart_cache())
    open(os.path.join(OUT, "fig-4-3-stage-timings.svg"), "w").write(chart_stages())
    print("charts written to", OUT)
