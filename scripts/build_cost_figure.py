"""Render the computed cost model as a figure for the report.

Replaces the (unavailable) Pricing Calculator public share link with a
screenshot-style image of the cost estimate the report actually quotes:
docs/evidence/cost-ondemand.csv, computed from AWS public price lists.

Output: docs/figures/fig-5-2-cost-model.png

Run: python scripts/build_cost_figure.py
"""
import csv
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "docs", "evidence", "cost-ondemand.csv")
OUT = os.path.join(ROOT, "docs", "figures", "fig-5-2-cost-model.png")
WORK = os.path.join(ROOT, ".build", "costfig")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

SPOT = [("On-demand (quoted in the report)", "16.68"),
        ("50 % Fargate Spot", "11.18"),
        ("70 % Fargate Spot", "8.98")]


def load_rows():
    rows = []
    with open(CSV_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def money(v):
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return v


def main():
    rows = load_rows()
    items = [r for r in rows if r["line_item"] != "TOTAL"]
    total = next((r["monthly_usd"] for r in rows if r["line_item"] == "TOTAL"), "16.68")
    items.sort(key=lambda r: -float(r["monthly_usd"] or 0))

    body = []
    for r in items:
        val = float(r["monthly_usd"] or 0)
        shown = "&lt;0.01" if 0 < val < 0.01 else money(val)
        body.append(
            f"<tr><td class='n'>{r['line_item']}</td>"
            f"<td class='b'>{r['basis']}</td>"
            f"<td class='v'>{shown}</td></tr>"
        )
    body.append(
        f"<tr class='tot'><td class='n'>Total</td><td class='b'>on-demand, ap-southeast-2</td>"
        f"<td class='v'>{money(total)}</td></tr>"
    )

    spot_rows = "".join(
        f"<tr><td class='n'>{label}</td><td class='b'>&nbsp;</td><td class='v'>{v}</td></tr>"
        for label, v in SPOT
    )

    html = f"""<!doctype html><html><head><meta charset='utf-8'>
<style>
  @page {{ size: 900px 620px; margin: 0; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 22px 26px; background: #fff;
          font-family: Arial, Helvetica, sans-serif; color: #000; }}
  h1 {{ font-size: 17px; margin: 0 0 2px; letter-spacing: 0.2px; }}
  .sub {{ font-size: 12px; margin: 0 0 14px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th {{ text-align: left; font-size: 11px; text-transform: uppercase;
        letter-spacing: 0.4px; border-bottom: 2px solid #000; padding: 4px 8px; }}
  td {{ border-bottom: 0.5px solid #999; padding: 4px 8px; vertical-align: top; }}
  td.n {{ width: 34%; }}
  td.b {{ width: 50%; }}
  td.v {{ width: 16%; text-align: right; font-variant-numeric: tabular-nums; }}
  tr.tot td {{ border-top: 2px solid #000; border-bottom: none;
               font-weight: bold; padding-top: 6px; }}
  .spot {{ margin-top: 18px; }}
  .spot h2 {{ font-size: 13px; margin: 0 0 6px; }}
  .spot table {{ font-size: 12px; }}
  .note {{ margin-top: 12px; font-size: 11px; line-height: 1.4; }}
</style></head><body>
<h1>DocuMind AI &mdash; monthly cost estimate</h1>
<p class="sub">Asia Pacific (Sydney) &middot; 50 concurrent users &middot; USD &middot;
rates from the AWS public Price List</p>
<table>
  <tr><th>Service</th><th>Quantity driver</th><th style="text-align:right">US$ / month</th></tr>
  {''.join(body)}
</table>
<div class="spot">
  <h2>Fargate Spot sensitivity (not in the calculator)</h2>
  <table>{spot_rows}</table>
</div>
<p class="note">Computed by scripts/calc_ondemand_cost.py from the AWS Price List bulk files into
docs/evidence/cost-ondemand.csv. The Pricing Calculator cannot price Fargate Spot, so the quoted
total is on-demand.</p>
</body></html>"""

    os.makedirs(WORK, exist_ok=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    html_path = os.path.join(WORK, "costfig.html")
    open(html_path, "w", encoding="utf-8").write(html)

    prof = os.path.join(WORK, "edgeprof")
    os.makedirs(prof, exist_ok=True)

    if os.path.exists(OUT):
        os.remove(OUT)

    subprocess.run(
        [EDGE, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={prof}", "--allow-file-access-from-files",
         "--force-device-scale-factor=2",
         f"--window-size=900,620",
         f"--screenshot={OUT}",
         "file:///" + html_path.replace("\\", "/")],
        check=False, timeout=180,
    )

    if not os.path.exists(OUT):
        print("figure build FAILED")
        return 1
    print(f"figure: {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
