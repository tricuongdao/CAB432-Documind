#!/usr/bin/env python3
"""Collect live ARNs for every DocuMind resource and write submission-a3-infra.yml.

Every entry gets a one-line plain-language description, as required by the
Gradescope declaration. Re-runnable: reads current state from AWS each time.
"""

import json
import os

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
ACCOUNT = "901444280953"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "submission-a3-infra.yml")

sqs = boto3.client("sqs", region_name=REGION)
ecs = boto3.client("ecs", region_name=REGION)
sm = boto3.client("secretsmanager", region_name=REGION)
eb = boto3.client("events", region_name=REGION)
ddb = boto3.client("dynamodb", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
ecr = boto3.client("ecr", region_name=REGION)
s3v = boto3.client("s3vectors", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
apigw = boto3.client("apigatewayv2", region_name=REGION)
sched = boto3.client("scheduler", region_name=REGION)


def try_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001 - report and continue
        print(f"  lookup failed: {getattr(fn, '__name__', fn)} -> {exc}")
        return None


def qurl(name):
    return f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{name}"


entries = []


def add(type_, arn, desc):
    entries.append({"type": type_, "arn": str(arn), "description": desc})


# ---- models ----
add("Bedrock model", f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0",
    "Embedding model (1024-dim) used by workers for corpus chunks and by the API for questions.")
add("Bedrock model", f"arn:aws:bedrock:{REGION}::foundation-model/google.gemma-3-12b-it",
    "Generation model used for document summaries and cited RAG answer synthesis.")

# ---- ECS ----
add("ECS cluster", f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/n12136514-documind-cluster",
    "Fargate cluster hosting the ingestion worker service and the autoscaling controller task.")
add("ECS service", f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/n12136514-documind-cluster/n12136514-documind-worker-svc",
    "Elastic worker pool (0-6 Fargate Spot tasks) that pulls documents from SQS and processes them.")
add("ECS service", f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/n12136514-documind-cluster/n12136514-documind-controller-svc",
    "Long-lived controller task implementing metric-based autoscaling from queue backlog.")
for td in ("n12136514-documind-worker", "n12136514-documind-controller"):
    r = try_call(ecs.describe_task_definition, taskDefinition=td)
    if r:
        add("ECS task definition", r["taskDefinition"]["taskDefinitionArn"],
            f"Task definition for the {td.split('-')[-1]} container (image, roles, environment).")

# ---- queues ----
for q, d in ((("n12136514-documind-queue"),
              "Durable work queue: upload events wait here; workers long-poll it; redrive after 3 receives."),
             (("n12136514-documind-dlq"),
              "Dead-letter queue isolating poison documents (corrupt/password-protected) without worker crashes.")):
    r = try_call(sqs.get_queue_attributes, QueueUrl=qurl(q), AttributeNames=["QueueArn"])
    if r:
        add("SQS queue", r["Attributes"]["QueueArn"], d)

# ---- event routing ----
r = try_call(eb.describe_rule, Name="n12136514-documind-upload-rule")
if r:
    add("EventBridge rule", r["Arn"],
        "Routes S3 ObjectCreated events (uploads/ prefix) from the storage bucket to the work queue.")

# ---- storage ----
add("S3 bucket", f"arn:aws:s3:::n12136514-documind-storage",
    "Unstructured store: raw PDF uploads, extracted chunk JSON, summary markdown; 24h lifecycle expiry.")
add("S3 bucket", f"arn:aws:s3:::n12136514-documind-web",
    "Static front-end assets (immutable hashed bundles) served by the API for same-origin delivery.")
r = try_call(s3v.get_vector_bucket, vectorBucketName="n12136514-documind-vectors")
if r and r.get("vectorBucket"):
    add("Vector store", r["vectorBucket"].get("vectorBucketArn", "n12136514-documind-vectors"),
        "Serverless vector index (documind-chunks, 1024-dim cosine) storing chunk embeddings + citation text.")
else:
    add("Vector store", "n12136514-documind-vectors / index documind-chunks",
        "Serverless vector index (1024-dim cosine) storing chunk embeddings with embedded citation text.")

# ---- data ----
r = try_call(ddb.describe_table, TableName="n12136514-documind-jobs")
if r:
    add("DynamoDB table", r["Table"]["TableArn"],
        "Single-table store for job states and cached answers; TTL auto-expires all rows after 24h.")

# ---- compute / api ----
r = try_call(lam.get_function, FunctionName="n12136514-documind-api")
if r:
    cfg = r["Configuration"]
    add("Lambda function", cfg["FunctionArn"],
        "Query API (FastAPI): presigned uploads, job status, cited RAG queries with two-tier cache, MCP tools, static hosting.")
try_call(lam.get_function_url_config, FunctionName="n12136514-documind-api")
add("MCP server", f"https://rqy6cawtyg.execute-api.{REGION}.amazonaws.com/documind/mcp",
    "Model Context Protocol endpoint (JSON-RPC) exposing search_documents, ask_documind and list_documents tools.")
add("API Gateway", f"arn:aws:apigateway:{REGION}::/apis/rqy6cawtyg",
    "Shared course HTTP API; DocuMind mounts additive routes ANY /documind and ANY /documind/{proxy+}.")

# ---- secrets / images ----
r = try_call(sm.describe_secret, SecretId="n12136514-documind-secrets")
if r:
    add("Secrets Manager secret", r["ARN"],
        "Holds the admin API key protecting cache-purge and other mutating operations.")
add("ECR repository", f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/n12136514-documind-worker",
    "Container image registry for the worker/controller image (linux/amd64).")

# ---- observability ----
add("CloudWatch dashboard", f"arn:aws:cloudwatch::{ACCOUNT}:dashboard/n12136514-documind-dashboard",
    "Operational dashboard: queue backlog, task counts, CPU/memory, cache mix, latency, DLQ depth.")
for a, d in (("n12136514-documind-dlq-alarm", "Alerts when any poison document reaches the dead-letter queue."),
             ("n12136514-documind-backlog-alarm", "Alerts on sustained queue backlog above 25 messages."),
             ("n12136514-documind-api-errors-alarm", "Alerts when API errors spike above 5 in 5 minutes.")):
    add("CloudWatch alarm", f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:{a}", d)

for lg in ("/ecs/n12136514-documind-worker", "/ecs/n12136514-documind-controller",
           "/aws/lambda/n12136514-documind-api"):
    add("CloudWatch log group", f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{lg}",
        f"Structured logs for {lg.split('/')[-1]} (7-day retention).")

r = try_call(sched.get_schedule, GroupName="n12136514-documind-schedules", Name="n12136514-documind-scaler-tick")
if r:
    add("EventBridge Scheduler schedule", r["Arn"],
        "One-minute hygiene tick (disabled in steady state; superseded by the controller task).")

yaml_lines = ["cloudInfrastructure:"]
for e in entries:
    yaml_lines.append(f"  - type: {e['type']}")
    yaml_lines.append(f"    arn: {e['arn']}")
    yaml_lines.append(f"    description: {e['description']}")
yaml_lines.append("")
yaml_lines.append("metaData:")
yaml_lines.append("  qutUsernameTag: n12136514@qut.edu.au")
yaml_lines.append("")

text = "\n".join(yaml_lines)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(text)
print(f"wrote {OUT} with {len(entries)} resources")
print(text)
