#!/usr/bin/env python3
"""Build the report PDF: Markdown -> styled HTML -> Edge headless print-to-pdf.

Produces DocuMind-A3-Report.pdf in the repo root and prints the page count
(report body must stay under 30 pages; references/appendices are excluded by
the brief, but we track the total anyway).
"""

import os
import re
import subprocess
import sys

import markdown
from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "REPORT.md")
WORK = os.path.join(ROOT, ".build", "report")
os.makedirs(WORK, exist_ok=True)
HTML = os.path.join(WORK, "report.html")
OUT = os.path.join(ROOT, "DocuMind-A3-Report.pdf")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

CSS = """
@page { size: A4; margin: 16mm 15mm 15mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; }
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10.4pt; line-height: 1.52; color: #1c2438; margin: 0; }
h1 { font-size: 23pt; margin: 2px 0 2px; color: #14213d; }
h2 { font-size: 15.5pt; color: #14213d; border-bottom: 2.2px solid #2f5f9e; padding-bottom: 4px; margin: 24px 0 10px; page-break-after: avoid; }
h3 { font-size: 12.3pt; color: #2f5f9e; margin: 16px 0 6px; page-break-after: avoid; }
p { margin: 6px 0 10px; }
table { border-collapse: collapse; width: 100%; font-size: 9.2pt; margin: 8px 0 14px; page-break-inside: auto; }
th, td { border: 1px solid #c9d4e8; padding: 4px 7px; text-align: left; vertical-align: top; }
th { background: #eef2fa; }
tr { page-break-inside: avoid; }
code { font-family: Consolas, 'Courier New', monospace; font-size: 8.9pt; background: #f2f5fb; padding: 0 3px; border-radius: 3px; }
pre { background: #f6f8fc; border: 1px solid #dbe3f0; border-radius: 6px; padding: 8px 10px; font-size: 8.4pt; white-space: pre-wrap; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
img { max-width: 100%; }
p > img { display: block; margin: 10px auto 4px; border: 1px solid #e2e8f4; border-radius: 4px; page-break-inside: avoid; }
blockquote { border-left: 3px solid #2f5f9e; margin: 8px 0; padding: 2px 12px; color: #33456b; background: #f7f9fd; }
hr { border: none; border-top: 1px solid #c9d4e8; margin: 18px 0; }
li { margin: 2px 0; }
.pb { page-break-before: always; }
.figcap { font-size: 9.4pt; color: #445; font-style: italic; }
"""


def main() -> int:
    text = open(SRC, encoding="utf-8").read()

    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list", "md_in_html"],
    )

    # absolute figure paths so Edge (file://) can load them
    body = body.replace('src="figures/', 'src="file:///C:/Projects/CAB432-DocuMind/docs/figures/')

    # keep the title block on its own page
    body = body.replace("<h2>Executive Summary</h2>", '<h2 class="pb">Executive Summary</h2>', 1)

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>DocuMind AI - CAB432 Assignment 3</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )
    open(HTML, "w", encoding="utf-8").write(html)

    prof = os.path.join(WORK, "edgeprof")
    os.makedirs(prof, exist_ok=True)
    subprocess.run(
        [EDGE, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={prof}", "--allow-file-access-from-files",
         "--no-pdf-header-footer", f"--print-to-pdf={OUT}",
         "file:///" + HTML.replace("\\", "/")],
        check=False, timeout=600,
    )
    if not os.path.exists(OUT):
        print("PDF build FAILED")
        return 1
    n = len(PdfReader(OUT).pages)
    size = os.path.getsize(OUT)
    print(f"PDF: {OUT}")
    print(f"pages: {n}  size: {size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
