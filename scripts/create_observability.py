#!/usr/bin/env python3
"""Create the DocuMind observability surface (idempotent):

  * log-based metric filters (documents completed, scale actions)
  * three CloudWatch alarms (DLQ depth, queue backlog, API errors)
  * one CloudWatch dashboard with the full operational picture
"""

import json

import boto3
from botocore.exceptions import ClientError

REGION = "ap-southeast-2"
QUEUE = "n12136514-documind-queue"
DLQ = "n12136514-documind-dlq"
API_FN = "n12136514-documind-api"
API_ID = "rqy6cawtyg"
CLUSTER = "n12136514-documind-cluster"
WORKER_SVC = "n12136514-documind-worker-svc"
CONTROLLER_SVC = "n12136514-documind-controller-svc"
DASH = "n12136514-documind-dashboard"
TAGS = [{"Key": "purpose", "Value": "assessment 3"},
        {"Key": "qut-username", "Value": "n12136514@qut.edu.au"}]

cw = boto3.client("cloudwatch", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)


def metric_filters():
    specs = [
        ("/ecs/n12136514-documind-worker", "DocuMindDocsCompleted",
         '"COMPLETED in"', "DocuMind", [{"Name": "Service", "Value": "worker"}]),
        ("/ecs/n12136514-documind-controller", "DocuMindScalingActions",
         '"scaling worker service"', "DocuMind", [{"Name": "Service", "Value": "controller"}]),
    ]
    for group, name, pattern, ns, dims in specs:
        try:
            logs.put_metric_filter(
                logGroupName=group, filterName=name, filterPattern=pattern,
                metricTransformations=[{
                    "metricNamespace": ns, "metricName": name.replace("DocuMind", ""),
                    "metricValue": "1", "defaultValue": 0,
                    "dimensions": {d["Name"]: d["Value"] for d in dims},
                }],
            )
            print("metric filter ok:", name)
        except ClientError as exc:
            print("metric filter failed:", name, exc.response["Error"]["Code"])


def alarms():
    spec = [
        dict(AlarmName="n12136514-documind-dlq-alarm",
             AlarmDescription="DocuMind: messages in the dead-letter queue indicate poison documents",
             Namespace="AWS/SQS", MetricName="ApproximateNumberOfMessagesVisible",
             Dimensions=[{"Name": "QueueName", "Value": DLQ}],
             Statistic="Maximum", Period=300, EvaluationPeriods=1,
             Threshold=0.0, ComparisonOperator="GreaterThanThreshold",
             TreatMissingData="notBreaching", Tags=TAGS),
        dict(AlarmName="n12136514-documind-backlog-alarm",
             AlarmDescription="DocuMind: sustained queue backlog above 25 messages",
             Namespace="AWS/SQS", MetricName="ApproximateNumberOfMessagesVisible",
             Dimensions=[{"Name": "QueueName", "Value": QUEUE}],
             Statistic="Maximum", Period=60, EvaluationPeriods=3,
             Threshold=25.0, ComparisonOperator="GreaterThanThreshold",
             TreatMissingData="notBreaching", Tags=TAGS),
        dict(AlarmName="n12136514-documind-api-errors-alarm",
             AlarmDescription="DocuMind: API Lambda error rate elevated",
             Namespace="AWS/Lambda", MetricName="Errors",
             Dimensions=[{"Name": "FunctionName", "Value": API_FN}],
             Statistic="Sum", Period=300, EvaluationPeriods=1,
             Threshold=5.0, ComparisonOperator="GreaterThanThreshold",
             TreatMissingData="notBreaching", Tags=TAGS),
    ]
    for a in spec:
        try:
            cw.put_metric_alarm(**a)
            print("alarm ok:", a["AlarmName"])
        except ClientError as exc:
            print("alarm failed:", a["AlarmName"], exc.response["Error"]["Code"])


def dashboard():
    def text_widget(x, y, w, h, text):
        return {"type": "text", "x": x, "y": y, "width": w, "height": h,
                "properties": {"markdown": text}}

    def metric(x, y, w, h, title, metrics, stat="Sum", period=60):
        return {"type": "metric", "x": x, "y": y, "width": w, "height": h,
                "properties": {"title": title, "region": REGION, "stat": stat,
                               "period": period, "metrics": metrics, "view": "timeSeries"}}

    body = {"widgets": [
        text_widget(0, 0, 24, 2,
                    "# DocuMind AI · operations (CAB432 A3 · n12136514)\n"
                    "Event-driven ingestion (S3 → EventBridge → SQS → ECS workers), "
                    "metric-based autoscaling controller, two-tier cache, Bedrock RAG."),
        metric(0, 2, 12, 6, "Queue backlog (visible + in-flight)", [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", QUEUE, {"label": "visible"}],
            ["AWS/SQS", "ApproximateNumberOfMessagesNotVisible", "QueueName", QUEUE, {"label": "in-flight"}],
        ]),
        metric(12, 2, 12, 6, "Processing pipeline health", [
            ["DocuMind", "DocumentsCompleted", "Service", "worker", {"label": "documents completed"}],
            ["DocuMind", "ScalingActions", "Service", "controller", {"label": "scale actions"}],
        ]),
        metric(0, 8, 12, 6, "Worker service (ECS)", [
            ["AWS/ECS", "CPUUtilization", "ClusterName", CLUSTER, "ServiceName", WORKER_SVC, {"label": "CPU %"}],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", CLUSTER, "ServiceName", WORKER_SVC, {"label": "Memory %"}],
        ]),
        metric(12, 8, 12, 6, "Workers running / desired (Container Insights)", [
            ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", CLUSTER, "ServiceName", WORKER_SVC, {"label": "running"}],
            ["ECS/ContainerInsights", "DesiredTaskCount", "ClusterName", CLUSTER, "ServiceName", WORKER_SVC, {"label": "desired"}],
        ]),
        metric(0, 14, 12, 6, "API Lambda", [
            ["AWS/Lambda", "Invocations", "FunctionName", API_FN, {"label": "invocations"}],
            ["AWS/Lambda", "Errors", "FunctionName", API_FN, {"label": "errors"}],
        ]),
        metric(12, 14, 12, 6, "Query cache (EMF from API)", [
            ["DocuMind", "CacheHitsL1", "Service", "api", {"label": "L1 hits"}],
            ["DocuMind", "CacheHitsL2", "Service", "api", {"label": "L2 hits"}],
            ["DocuMind", "CacheMisses", "Service", "api", {"label": "misses"}],
        ]),
        metric(0, 20, 12, 6, "Query latency (origin path)", [
            ["DocuMind", "QueryOriginLatencyMs", "Service", "api", {"label": "origin latency ms", "stat": "Average"}],
        ], stat="Average"),
        metric(12, 20, 12, 6, "Dead-letter queue depth", [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", DLQ, {"label": "DLQ messages"}],
        ]),
    ]}
    try:
        cw.put_dashboard(DashboardName=DASH, DashboardBody=json.dumps(body))
        print("dashboard ok:", DASH)
    except ClientError as exc:
        print("dashboard failed:", exc.response["Error"]["Code"])


if __name__ == "__main__":
    metric_filters()
    alarms()
    dashboard()
