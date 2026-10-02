#!/usr/bin/env bash
# DocuMind AI - one-shot infrastructure deployment (CAB432 A3, n12136514).
#
# Idempotent: safe to re-run; each step checks before creating. Encodes the
# CAB432 sandbox rules that shaped this project:
#   * region ap-southeast-2, account 901444280953
#   * every resource tagged purpose=assessment 3 + qut-username=<student>
#   * shared course roles are used (custom IAM role creation is not permitted)
#   * the shared course API gateway is extended ADDITIVELY with /documind/*
#   * CloudFront/ElastiCache/managed autoscaling are unavailable in this
#     account; the equivalents live in this repo (two-tier cache, controller).
#
# Prerequisites: aws CLI (SSO profile logged in), docker, python3 + boto3,
# node/npm. Run from the repository root: bash scripts/deploy-infra.sh
set -euo pipefail

REGION=ap-southeast-2
ACCOUNT=901444280953
STUDENT=n12136514
QUT_TAG="n12136514@qut.edu.au"
PURPOSE="assessment 3"
TAGS="Key=purpose,Value=$PURPOSE Key=qut-username,Value=$QUT_TAG"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

say() { printf '\n== %s\n' "$*"; }
exists() { aws "$@" >/dev/null 2>&1; }

say "environment checks"
aws sts get-caller-identity --query Account --output text | grep -q "^$ACCOUNT$" \
  || { echo "not logged into account $ACCOUNT (run: aws sso login)"; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker daemon not running"; exit 1; }

say "S3 buckets (storage + web)"
for b in n12136514-documind-storage n12136514-documind-web; do
  if ! exists s3api head-bucket --bucket "$b"; then
    aws s3api create-bucket --bucket "$b" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
    aws s3api put-public-access-block --bucket "$b" \
      --public-access-block-configuration \
      "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  fi
  aws s3api put-bucket-tagging --bucket "$b" --tagging "TagSet=[{Key=purpose,Value=$PURPOSE},{Key=qut-username,Value=$QUT_TAG}]"
done
aws s3api put-bucket-encryption --bucket n12136514-documind-storage \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# CORS so browsers can PUT presigned uploads (matches the SPA's XHR headers)
aws s3api put-bucket-cors --bucket n12136514-documind-storage --cors-configuration '{
  "CORSRules": [{"AllowedHeaders":["*"],"AllowedMethods":["PUT","GET","HEAD"],
  "AllowedOrigins":["*"],"ExposeHeaders":["ETag"],"MaxAgeSeconds":3600}]}'

# 24h artifact garbage collection (TTL rows expire the metadata; this expires the blobs)
aws s3api put-bucket-lifecycle-configuration --bucket n12136514-documind-storage \
  --lifecycle-configuration '{"Rules":[{"ID":"documind-24h-artifact-gc","Status":"Enabled",
  "Filter":{"Prefix":"uploads/"},"Expiration":{"Days":1}},
  {"ID":"documind-24h-chunks","Status":"Enabled","Filter":{"Prefix":"chunks/"},"Expiration":{"Days":1}},
  {"ID":"documind-24h-summaries","Status":"Enabled","Filter":{"Prefix":"summaries/"},"Expiration":{"Days":1}}]}'

say "SQS queue + dead-letter queue"
DLQ_URL=$(aws sqs create-queue --queue-name n12136514-documind-dlq \
  --attributes MessageRetentionPeriod=1209600 --tags "purpose=$PURPOSE,qut-username=$QUT_TAG" \
  --query QueueUrl --output text)
DLQ_ARN=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)
QUEUE_URL=$(aws sqs create-queue --queue-name n12136514-documind-queue \
  --attributes "VisibilityTimeout=300,MessageRetentionPeriod=345600,ReceiveMessageWaitTimeSeconds=20,RedrivePolicy={\"deadLetterTargetArn\":\"$DLQ_ARN\",\"maxReceiveCount\":\"3\"}" \
  --tags "purpose=$PURPOSE,qut-username=$QUT_TAG" --query QueueUrl --output text)
QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)
echo "queue: $QUEUE_URL"

say "DynamoDB jobs table (jobs + shared cache, TTL garbage collection)"
if ! exists dynamodb describe-table --table-name n12136514-documind-jobs; then
  aws dynamodb create-table --table-name n12136514-documind-jobs \
    --attribute-definitions AttributeName=jobId,AttributeType=S AttributeName=listKey,AttributeType=S AttributeName=createdAt,AttributeType=S \
    --key-schema AttributeName=jobId,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --global-secondary-indexes '[{"IndexName":"byCreated","KeySchema":[{"AttributeName":"listKey","KeyType":"HASH"},{"AttributeName":"createdAt","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}]' \
    --tags "$TAGS"
fi
aws dynamodb update-time-to-live --table-name n12136514-documind-jobs \
  --time-to-live-specification "Enabled=true,AttributeName=ttl"

say "S3 Vectors bucket + index"
exists s3vectors get-vector-bucket --vector-bucket-name n12136514-documind-vectors \
  || aws s3vectors create-vector-bucket --vector-bucket-name n12136514-documind-vectors \
       --tags "purpose=$PURPOSE,qut-username=$QUT_TAG"
exists s3vectors get-index --vector-bucket-name n12136514-documind-vectors --index-name documind-chunks \
  || aws s3vectors create-index --vector-bucket-name n12136514-documind-vectors \
       --index-name documind-chunks --data-type float32 --dimension 1024 --distance-metric cosine \
       --tags "purpose=$PURPOSE,qut-username=$QUT_TAG"

say "ECR, Secrets Manager, log groups"
exists ecr describe-repositories --repository-names n12136514-documind-worker \
  || aws ecr create-repository --repository-name n12136514-documind-worker --tags "$TAGS"
exists secretsmanager describe-secret --secret-id n12136514-documind-secrets \
  || aws secretsmanager create-secret --name n12136514-documind-secrets \
       --secret-string "{\"apiAdminKey\":\"$(python -c 'import secrets;print(secrets.token_urlsafe(24))')\"}" --tags "$TAGS"
for lg in /ecs/n12136514-documind-worker /ecs/n12136514-documind-controller /aws/lambda/n12136514-documind-api; do
  aws logs create-log-group --log-group-name "$lg" 2>/dev/null || true
  aws logs put-retention-policy --log-group-name "$lg" --retention-in-days 7
done

say "ECS cluster (reused course cluster) + worker/controller task definitions"
exists ecs describe-clusters --clusters n12136514-documind-cluster \
  || aws ecs create-cluster --cluster-name n12136514-documind-cluster \
       --settings name=containerInsights,value=enabled --tags "$TAGS"
aws ecs register-task-definition --cli-input-json file://infra/taskdef-worker.json --tags "$TAGS" >/dev/null
aws ecs register-task-definition --cli-input-json file://infra/taskdef-controller.json --tags "$TAGS" >/dev/null
echo "note: taskdefs reference the shared roles Task-Role-CAB432-ECS / Execution-Role-CAB432-ECS"

say "build + push the worker image (linux/amd64)"
bash scripts/build_worker_image.sh

say "ECS services (worker scales 0..6 via the controller; controller runs 1)"
if ! exists ecs describe-services --cluster n12136514-documind-cluster --services n12136514-documind-worker-svc; then
  aws ecs create-service --cluster n12136514-documind-cluster --service-name n12136514-documind-worker-svc \
    --task-definition n12136514-documind-worker --desired-count 0 --launch-type FARGATE \
    --platform-version LATEST --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
    --network-configuration 'awsvpcConfiguration={subnets=[subnet-075811427d5564cf9,subnet-05a3b8177138c8b14,subnet-04ca053dcbe5f49cc],securityGroups=[sg-078997505ad1c6bbc],assignPublicIp=ENABLED}' \
    --tags "$TAGS"
fi
if ! exists ecs describe-services --cluster n12136514-documind-cluster --services n12136514-documind-controller-svc; then
  aws ecs create-service --cluster n12136514-documind-cluster --service-name n12136514-documind-controller-svc \
    --task-definition n12136514-documind-controller --desired-count 1 --launch-type FARGATE \
    --platform-version LATEST --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
    --network-configuration 'awsvpcConfiguration={subnets=[subnet-075811427d5564cf9,subnet-05a3b8177138c8b14,subnet-04ca053dcbe5f49cc],securityGroups=[sg-078997505ad1c6bbc],assignPublicIp=ENABLED}' \
    --tags "$TAGS"
fi

say "S3 -> EventBridge -> SQS wiring"
aws sqs set-queue-attributes --queue-url "$QUEUE_URL" --attributes "Policy={\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"events.amazonaws.com\"},\"Action\":\"sqs:SendMessage\",\"Resource\":\"$QUEUE_ARN\"}]}"
RULE_ARN=$(aws events put-rule --name n12136514-documind-upload-rule \
  --event-pattern "{\"source\":[\"aws.s3\"],\"detail-type\":[\"Object Created\"],\"detail\":{\"bucket\":{\"name\":[\"n12136514-documind-storage\"]},\"object\":{\"key\":[{\"prefix\":\"uploads/\"}]}}}" \
  --query RuleArn --output text)
aws events tag-resource --resource-arn "$RULE_ARN" --tags "$TAGS" 2>/dev/null || true
aws events put-targets --rule n12136514-documind-upload-rule --targets "Id=documind-queue,Arn=$QUEUE_ARN"
aws s3api put-bucket-notification-configuration --bucket n12136514-documind-storage \
  --notification-configuration '{"EventBridgeConfiguration":{}}'

say "API Lambda + shared-gateway route (additive) + function URL"
bash scripts/build_api_lambda.sh
python scripts/deploy_api_lambda.py

say "frontend build + deploy"
bash scripts/deploy_frontend.sh

say "observability (dashboard, alarms) + infrastructure declaration"
python scripts/create_observability.py
python scripts/collect_infra.py

cat <<'DONE'

== deploy complete

Next steps that are intentionally manual:
  1. AWS Pricing Calculator share link  -> docs/COST_MODEL.md section 5
  2. Record the demo video              -> docs/VIDEO_SCRIPT.md
  3. Submit: report PDF + video (Canvas), source + submission-a3-infra.yml (Gradescope)

Smoke test:  python scripts/smoke_test.py
Load test:   python scripts/load_test.py pipeline --count 30
DONE
