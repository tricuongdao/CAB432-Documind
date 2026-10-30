"""Redraw the four diagrams at a readable print size.

Measured problem: the originals carried labels at about 0.9% of canvas width, so
at 18 cm they printed at 4.3 to 5.6 pt. Legible print needs roughly 2%, which
means a smaller canvas with larger relative text, i.e. fewer nodes per figure.

Every label here is 13 to 15 px on a 720 px canvas, giving about 9 to 10.5 pt at
18 cm. Output is rasterised at 2x through headless Edge.

Run: python scripts/build_diagrams.py
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs", "figures")
WORK = os.path.join(ROOT, ".build", "diagrams")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

W = 720
INK = "#000000"
MUTED = "#444444"
LIGHT = "#e8e8e8"


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Svg:
    def __init__(self, w, h, title, subtitle=""):
        self.w, self.h = w, h
        self.parts = []
        self.parts.append(
            f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>')
        self.parts.append(
            f'<text x="18" y="30" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="19" font-weight="700" fill="{INK}">{esc(title)}</text>')
        if subtitle:
            self.parts.append(
                f'<text x="18" y="50" font-family="Arial, Helvetica, sans-serif" '
                f'font-size="13" fill="{MUTED}">{esc(subtitle)}</text>')

    def box(self, x, y, w, h, label, detail="", bold=True, size=14, fill="#ffffff",
            align="start"):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" ry="6" '
            f'fill="{fill}" stroke="{INK}" stroke-width="1.4"/>')
        if align == "middle":
            cx = x + w / 2
            ty = y + (h / 2 + 5 if not detail else h / 2 - 4)
            self.parts.append(
                f'<text x="{cx}" y="{ty}" text-anchor="middle" '
                f'font-family="Arial, Helvetica, sans-serif" font-size="{size}" '
                f'font-weight="{700 if bold else 400}" fill="{INK}">{esc(label)}</text>')
            if detail:
                self.parts.append(
                    f'<text x="{cx}" y="{ty + 18}" text-anchor="middle" '
                    f'font-family="Arial, Helvetica, sans-serif" font-size="12" '
                    f'fill="{MUTED}">{esc(detail)}</text>')
        else:
            self.parts.append(
                f'<text x="{x + 12}" y="{y + 24}" '
                f'font-family="Arial, Helvetica, sans-serif" font-size="{size}" '
                f'font-weight="{700 if bold else 400}" fill="{INK}">{esc(label)}</text>')
            if detail:
                self.parts.append(
                    f'<text x="{x + 12}" y="{y + 44}" '
                    f'font-family="Arial, Helvetica, sans-serif" font-size="12.5" '
                    f'fill="{MUTED}">{esc(detail)}</text>')

    def arrow(self, x1, y1, x2, y2, dashed=False, label=""):
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{INK}" '
            f'stroke-width="1.5"{dash} marker-end="url(#a)"/>')
        if label:
            self.parts.append(
                f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2 - 6}" text-anchor="middle" '
                f'font-family="Arial, Helvetica, sans-serif" font-size="12" '
                f'fill="{MUTED}">{esc(label)}</text>')

    def step(self, n, y, title, detail, w=672):
        """Numbered row for a sequence diagram."""
        self.parts.append(
            f'<circle cx="32" cy="{y + 20}" r="14" fill="{INK}"/>')
        self.parts.append(
            f'<text x="32" y="{y + 25}" text-anchor="middle" '
            f'font-family="Arial, Helvetica, sans-serif" font-size="13" '
            f'font-weight="700" fill="#ffffff">{n}</text>')
        self.parts.append(
            f'<text x="58" y="{y + 17}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="14" font-weight="700" fill="{INK}">{esc(title)}</text>')
        self.parts.append(
            f'<text x="58" y="{y + 35}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="12.5" fill="{MUTED}">{esc(detail)}</text>')

    def render(self, name):
        defs = (
            '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{INK}"/></marker></defs>')
        body = "\n".join(self.parts)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
               f'height="{self.h}" viewBox="0 0 {self.w} {self.h}">{defs}{body}</svg>')
        return svg


# ---------------------------------------------------------------- figure 2.1
def fig_2_1():
    s = Svg(W, 520, "Vendor-agnostic architecture",
            "Five planes; every box names a service category, not a vendor product")
    bands = [
        ("Interactive plane", "Web client  to  stateless API  to  agent tool endpoint"),
        ("Ingestion plane", "Browser  to  object store  to  event router  to  queue  to  worker pool"),
        ("Data plane", "Object store  +  structured store  +  vector index"),
        ("Control plane", "Backlog controller, metrics, logs, dashboard, alarms"),
        ("Trust plane", "Workload roles, short-lived upload grants, managed secrets"),
    ]
    y = 74
    for name, detail in bands:
        s.box(20, y, 680, 76, name, detail, size=15)
        y += 88
    return s


# ---------------------------------------------------------------- figure 3.1
def fig_3_1():
    s = Svg(W, 560, "DocuMind on AWS, ap-southeast-2",
            "Account 901444280953; all resources tagged purpose=assessment 3")

    # request lane
    s.parts.append(f'<text x="20" y="80" font-family="Arial" font-size="12" '
                   f'font-weight="700" fill="{MUTED}">REQUEST PATH</text>')
    s.box(20, 90, 128, 54, "Web client", "", size=13, align="middle")
    s.box(176, 90, 128, 54, "API Gateway", "HTTP API", size=13, align="middle")
    s.box(332, 90, 128, 54, "Lambda", "FastAPI, 1 GB", size=13, align="middle")
    s.box(488, 90, 212, 54, "Bedrock", "Titan v2 + Gemma 3 12B", size=13, align="middle")
    s.arrow(148, 117, 174, 117)
    s.arrow(304, 117, 330, 117)
    s.arrow(460, 117, 486, 117)

    # ingestion lane
    s.parts.append(f'<text x="20" y="186" font-family="Arial" font-size="12" '
                   f'font-weight="700" fill="{MUTED}">INGESTION PATH</text>')
    s.box(20, 196, 128, 54, "Upload", "presigned PUT", size=13, align="middle")
    s.box(176, 196, 128, 54, "S3 uploads", "ObjectCreated", size=13, align="middle")
    s.box(332, 196, 128, 54, "EventBridge", "prefix rule", size=13, align="middle")
    s.box(488, 196, 128, 54, "SQS", "queue + DLQ", size=13, align="middle")
    s.arrow(148, 223, 174, 223)
    s.arrow(304, 223, 330, 223)
    s.arrow(460, 223, 486, 223)

    # worker pool directly under SQS, wide enough to receive the arrow cleanly
    s.box(380, 296, 320, 54, "Fargate workers", "0 to 6 tasks, pool set by backlog",
          size=13, align="middle")
    s.arrow(552, 250, 552, 294)

    # stores
    s.parts.append(f'<text x="20" y="370" font-family="Arial" font-size="12" '
                   f'font-weight="700" fill="{MUTED}">STORES</text>')
    s.box(20, 380, 208, 54, "S3 storage", "PDFs, chunks, 24 h TTL", size=13, align="middle")
    s.box(248, 380, 208, 54, "S3 Vectors", "1024-dim, cosine", size=13, align="middle")
    s.box(476, 380, 224, 54, "DynamoDB", "jobs + cache, TTL", size=13, align="middle")
    s.parts.append(f'<text x="20" y="458" font-family="Arial" font-size="12.5" '
                   f'fill="{MUTED}">The API and the worker pool both read and write these.</text>')

    # control loop, kept clear of every other box
    s.box(20, 480, 300, 54, "CloudWatch", "backlog + task metrics", size=13, align="middle")
    s.box(368, 480, 332, 54, "Controller task", "60 s tick, sets the pool size",
          size=13, align="middle")
    s.arrow(320, 507, 366, 507)
    return s


# ---------------------------------------------------------------- figure 4.1
def fig_4_1():
    s = Svg(W, 540, "Ingestion sequence",
            "One browser request, then a background chain the status endpoint reports")
    rows = [
        ("Presigned PUT", "browser uploads straight to S3; file bytes never touch the API"),
        ("ObjectCreated event", "S3 emits to EventBridge; no polling"),
        ("Rule matches", "source aws.s3, uploads/ prefix; target is the queue"),
        ("Message on queue", "pointer only: jobId, bucket, key"),
        ("Worker processes", "extract, chunk, embed, summarise; four model calls in flight"),
        ("Artifacts written", "chunk JSON, summary markdown, vector entries, job row"),
        ("Message deleted", "only now; a crash earlier means the visibility timeout replays it"),
    ]
    y = 76
    for i, (t, d) in enumerate(rows, 1):
        s.step(i, y, t, d)
        y += 66
    return s


# ---------------------------------------------------------------- figure 4.2
def fig_4_2():
    s = Svg(W, 600, "Query sequence and the cache decision",
            "A miss goes to origin; the same question again short-circuits at step 1")
    rows = [
        ("Probe L1", "in-process LRU; a hit costs microseconds and no network"),
        ("Probe L2", "shared row cache#hash; a hit backfills L1 and reports HIT-L2"),
        ("Embed question", "same model as ingestion; 1024-dim in about 0.15 s"),
        ("Vector search", "over-fetch twice the top-k because step 5 discards hits"),
        ("Filter live metadata", "drop chunks whose job is not COMPLETED; makes TTL safe"),
        ("Compose prompt", "top 4 to 8 excerpts numbered, citations required"),
        ("Generate", "about 2.5 s; 2 663 ms measured end to end"),
        ("Cache write", "both tiers before responding; a lost reply retries from cache"),
        ("Respond", "answer, sources, tier that served it, elapsed ms"),
    ]
    y = 74
    for i, (t, d) in enumerate(rows, 1):
        s.step(i, y, t, d)
        y += 57
    return s


def main():
    os.makedirs(WORK, exist_ok=True)
    jobs = [
        (fig_2_1(), "fig-2-1-architecture-agnostic"),
        (fig_3_1(), "fig-3-1-architecture-aws"),
        (fig_4_1(), "fig-4-1-sequence-ingestion"),
        (fig_4_2(), "fig-4-2-sequence-query"),
    ]
    for svg_obj, name in jobs:
        svg = svg_obj.render(name)
        w, h = svg_obj.w, svg_obj.h
        html = (f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<style>html,body{{margin:0;padding:0;background:#fff}}"
                f"svg{{display:block}}</style></head><body>{svg}</body></html>")
        hp = os.path.join(WORK, name + ".html")
        open(hp, "w", encoding="utf-8").write(html)
        out = os.path.join(OUT_DIR, name + ".png")
        if os.path.exists(out):
            os.remove(out)
        prof = os.path.join(WORK, "edgeprof")
        os.makedirs(prof, exist_ok=True)
        subprocess.run(
            [EDGE, "--headless", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", f"--user-data-dir={prof}",
             "--allow-file-access-from-files", "--force-device-scale-factor=2",
             f"--window-size={w},{h}", f"--screenshot={out}",
             "file:///" + hp.replace("\\", "/")],
            check=False, timeout=180,
        )
        ok = os.path.exists(out)
        print(f"{name:<36} {'ok' if ok else 'FAILED'}  {w}x{h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
