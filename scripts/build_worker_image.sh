#!/usr/bin/env bash
# Build and push the worker/controller container image (linux/amd64) to ECR.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REGION=ap-southeast-2
ACCOUNT=901444280953
REPO=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/n12136514-documind-worker

echo "== docker login to ECR =="
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

echo "== build (linux/amd64; Fargate runtime platform) =="
docker build --platform linux/amd64 -f backend/Dockerfile -t "$REPO:latest" .

echo "== push =="
docker push "$REPO:latest"
echo "pushed: $REPO:latest"
