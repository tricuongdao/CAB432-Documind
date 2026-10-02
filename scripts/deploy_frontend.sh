#!/usr/bin/env bash
# Build the DocuMind SPA and deploy it to the web bucket. The API Lambda
# serves these objects for HTTPS + same-origin delivery from one URL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"

BUCKET=n12136514-documind-web
REGION=ap-southeast-2

echo "== npm install =="
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
fi

echo "== vite build =="
npm run build

echo "== sync hashed assets (immutable, 1 year) =="
aws s3 sync dist/assets "s3://$BUCKET/assets" \
  --cache-control "public, max-age=31536000, immutable" --region "$REGION"

echo "== sync remaining files =="
aws s3 sync dist "s3://$BUCKET" --exclude "assets/*" --delete \
  --cache-control "public, max-age=300" --region "$REGION"

echo "== deployed to s3://$BUCKET (served via the API URL) =="
