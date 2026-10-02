#!/usr/bin/env bash
# Render the hand-authored report SVGs to crisp PNGs using Edge headless.
# Each SVG is wrapped in a minimal HTML page at its intrinsic size, then
# screenshotted at 2x device scale for print-quality raster.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
DIAG="$ROOT/docs/diagrams"
FIG="$ROOT/docs/figures"
mkdir -p "$FIG" "$ROOT/.build/render"

render() {
  local svg="$1" out="$2" w="$3" h="$4"
  local html="$ROOT/.build/render/$(basename "$svg" .svg).html"
  {
    printf '<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:#fff}svg{display:block;width:%spx;height:%spx}</style></head><body>\n' "$w" "$h"
    cat "$svg"
    printf '\n</body></html>\n'
  } > "$html"
  rm -f "$out"
  "$EDGE" --headless --disable-gpu --no-first-run --no-default-browser-check \
    --user-data-dir="$(cygpath -w "$ROOT/.build/render/edgeprof")" --allow-file-access-from-files \
    --hide-scrollbars --force-device-scale-factor=2 --window-size="$w,$h" \
    --screenshot="$(cygpath -w "$out")" "file:///$(cygpath -m "$html")" 2>/dev/null || true
  if [ -f "$out" ]; then echo "rendered: $out ($(stat -c%s "$out" 2>/dev/null || echo '?') bytes)"; else echo "FAILED: $out"; fi
}

render "$DIAG/architecture-vendor-agnostic.svg" "$FIG/fig-2-1-architecture-agnostic.png" 1180 780
render "$DIAG/architecture-aws.svg"              "$FIG/fig-3-1-architecture-aws.png"      1206 838
render "$DIAG/sequence-ingestion.svg"            "$FIG/fig-4-1-sequence-ingestion.png"     1200 860
render "$DIAG/sequence-query.svg"                "$FIG/fig-4-2-sequence-query.png"         1200 790
echo "done"
