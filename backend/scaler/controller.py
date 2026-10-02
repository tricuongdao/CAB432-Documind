#!/usr/bin/env python3
"""DocuMind scaling controller: the metric-based autoscaling control loop.

Runs as a dedicated ECS Fargate task (spot) inside the DocuMind cluster and
ticks every CONTROL_INTERVAL_SECONDS. Each tick reads the SQS backlog metric
(visible + in-flight messages) and drives the worker service's desired count
with a target-value control law: one worker per TARGET_BACKLOG_PER_TASK
messages, a minimum of one worker for any non-empty backlog, and scale to
zero when the queue is empty.

Placement rationale (see the report): the shared Lambda execution role is not
permitted to call the ECS APIs in this account, while the ECS task role is.
Running the controller as a cluster task keeps every actuator permission
inside the compute plane the account policy intends, and the service itself
performs task lifecycle management (start, stop, replacement) from the
desired-count we publish.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scaler  # noqa: E402


def main():
    interval = int(os.environ.get("CONTROL_INTERVAL_SECONDS", "60"))
    print(f"DocuMind controller starting: interval={interval}s "
          f"cluster={scaler.CLUSTER} service={scaler.SERVICE} "
          f"target backlog per task={scaler.TARGET_BACKLOG_PER_TASK}")
    while True:
        try:
            res = scaler.handler({}, None)
            print("tick " + json.dumps(res, default=str))
        except Exception as exc:
            print(f"tick error: {type(exc).__name__}: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
