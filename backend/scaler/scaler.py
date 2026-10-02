#!/usr/bin/env python3
"""DocuMind AI scaler: metric-based autoscaling controller (CAB432 A3).

Invoked every minute by EventBridge Scheduler. Reads the SQS backlog metric
(visible + in-flight messages), applies a target-value control law with a
target backlog of two messages per worker, and launches standalone Fargate
worker tasks when more capacity is needed. Workers retire themselves after
an idle period (WORKER_IDLE_EXIT_SECONDS), so the pool drains back to zero
when the backlog clears.

This controller is the sandbox-compatible equivalent of Application Auto
Scaling target tracking: the shared account's IAM policy blocks the native
auto-scaling APIs, so the same control law is implemented explicitly and
observably (see the report for the trade-off discussion).
"""

import json
import math
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
CLUSTER = os.environ.get("CLUSTER", "n12136514-documind-cluster")
SERVICE = os.environ.get("SERVICE", "n12136514-documind-worker-svc")
TASK_DEFINITION = os.environ.get("TASK_DEFINITION", "n12136514-documind-worker")
QUEUE_URL = os.environ.get(
    "SQS_QUEUE_URL",
    "https://sqs.ap-southeast-2.amazonaws.com/901444280953/n12136514-documind-queue",
)
SUBNETS = [s for s in os.environ.get(
    "SUBNETS",
    "subnet-075811427d5564cf9,subnet-05a3b8177138c8b14,subnet-04ca053dcbe5f49cc",
).split(",") if s]
SECURITY_GROUP = os.environ.get("SECURITY_GROUP", "sg-078997505ad1c6bbc")
TARGET_BACKLOG_PER_TASK = float(os.environ.get("TARGET_BACKLOG_PER_TASK", "2"))
MAX_TASKS = int(os.environ.get("MAX_TASKS", "6"))

_clients = {}


def _c(name):
    if name not in _clients:
        _clients[name] = boto3.client(
            name, region_name=AWS_REGION,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _clients[name]


_metric_warned = False


def emit(name, value, unit="Count"):
    """Best-effort metric; the task role lacks PutMetricData, so warn once."""
    global _metric_warned
    try:
        _c("cloudwatch").put_metric_data(
            Namespace="DocuMind",
            MetricData=[{
                "MetricName": name, "Value": value, "Unit": unit,
                "Dimensions": [{"Name": "Service", "Value": "scaler"}],
            }],
        )
    except Exception:
        if not _metric_warned:
            _metric_warned = True
            print("custom metrics unavailable via this role; continuing without them")


def handler(event, context):
    sqs, ecs = _c("sqs"), _c("ecs")

    attrs = sqs.get_queue_attributes(
        QueueUrl=QUEUE_URL,
        AttributeNames=["ApproximateNumberOfMessages",
                        "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    visible = int(attrs.get("ApproximateNumberOfMessages", 0))
    inflight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))
    backlog = visible + inflight

    # Target-value control law: one worker per TARGET_BACKLOG_PER_TASK
    # messages; a minimum of one worker for any non-empty backlog; scale to
    # zero when the queue is empty.
    desired = 0 if backlog == 0 else min(MAX_TASKS, max(1, math.ceil(backlog / TARGET_BACKLOG_PER_TASK)))
    forced = int((event or {}).get("force") or 0)
    if forced:
        desired = forced

    current = None
    action = "none"
    error = None
    try:
        svc = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
        current = int(svc.get("desiredCount", 0))
    except ClientError as exc:
        error = exc.response["Error"]["Code"]
        print(f"describe_services not permitted: {error}")

    if current is None:
        # Fallback: manage a standalone task pool (RunTask) when the service
        # API is unavailable to the caller.
        running = None
        try:
            running = ecs.list_tasks(cluster=CLUSTER, family=TASK_DEFINITION,
                                     desiredStatus="RUNNING").get("taskArns", [])
        except ClientError as exc:
            print(f"list_tasks not permitted ({exc.response['Error']['Code']}), using in-flight estimate")
        estimate = len(running) if running is not None else (inflight if backlog > 0 else 0)
        to_launch = max(0, min(desired - estimate, MAX_TASKS))
        if to_launch > 0:
            try:
                resp = ecs.run_task(
                    cluster=CLUSTER, taskDefinition=TASK_DEFINITION, launchType="FARGATE",
                    count=to_launch,
                    networkConfiguration={"awsvpcConfiguration": {
                        "subnets": SUBNETS, "securityGroups": [SECURITY_GROUP],
                        "assignPublicIp": "ENABLED"}},
                    propagateTags="TASK_DEFINITION",
                    startedBy="documind-scaler",
                )
                action = f"run_task x{to_launch}"
                for f in resp.get("failures", []):
                    print("run_task failure:", json.dumps(f, default=str))
            except ClientError as exc:
                error = exc.response["Error"]["Code"]
                print(f"run_task not permitted: {error}")
    elif desired != current:
        try:
            ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=desired)
            action = f"update_service {current} -> {desired}"
            print(f"scaling worker service: {current} -> {desired} (backlog={backlog})")
        except ClientError as exc:
            error = exc.response["Error"]["Code"]
            print(f"update_service not permitted: {error}")

    result = {
        "backlog": backlog, "visible": visible, "inFlight": inflight,
        "currentDesired": current, "desired": desired, "action": action,
        "maxTasks": MAX_TASKS, "forceUsed": forced or None, "error": error,
        "targetBacklogPerTask": TARGET_BACKLOG_PER_TASK,
        "ts": int(time.time()),
    }
    print(json.dumps(result))
    emit("BacklogMessages", backlog)
    emit("CurrentDesiredTasks", current if current is not None else -1)
    emit("DesiredTasks", desired)
    return result
