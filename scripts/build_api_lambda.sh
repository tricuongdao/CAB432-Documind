#!/usr/bin/env bash
# Build the DocuMind API Lambda deployment package.
#
# Dependencies are installed inside a python:3.12-slim linux/amd64 container
# so the wheels match the Lambda runtime exactly (no host-platform surprises).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$ROOT/backend/api"
BUILD="$ROOT/.build"

API_MNT="$(cygpath -w "$API_DIR" 2>/dev/null || echo "$API_DIR")"
BUILD_MNT="$(cygpath -w "$BUILD" 2>/dev/null || echo "$BUILD")"

rm -rf "$BUILD/pkg-api"
mkdir -p "$BUILD/pkg-api"

echo "== installing dependencies in linux/amd64 python:3.12 container =="
docker run --rm --platform linux/amd64 \
  -v "$API_MNT:/src:ro" -v "$BUILD_MNT/pkg-api:/out" \
  python:3.12-slim \
  bash -lc "pip install --no-cache-dir --quiet --target /out -r /src/requirements.txt && echo deps-installed && ls /out | wc -l"

echo "== copying application sources =="
cp "$API_DIR"/*.py "$BUILD/pkg-api/"

# Native-Windows python needs native paths (MSYS conversion is disabled).
python "$(cygpath -w "$ROOT/scripts/zip_dir.py")" "$(cygpath -w "$BUILD/pkg-api")" "$(cygpath -w "$BUILD/documind-api.zip")"
ls -lh "$BUILD/documind-api.zip"
