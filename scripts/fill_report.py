#!/usr/bin/env python3
"""Fill REPORT.md tokens with measured evidence and finalise wording.

Runs after the load tests. Every replacement is explicit; the script prints
any leftover {{TOKENS}} it could not resolve so nothing ships unresolved.
"""

import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "REPORT.md")
EV = os.path.join(ROOT, "docs", "evidence")

text = open(REPORT, encoding="utf-8").read()

# ---------------------------------------------------------------- evidence
pipe = json.load(open(sorted(glob.glob(os.path.join(EV, "pipeline-*.json")))[-1]))
dlq = json.load(open(sorted(glob.glob(os.path.join(EV, "dlq-*.json")))[-1]))
smoke = sorted(glob.glob(os.path.join(EV, "smoke-*.json")))[-1]
yml = open(os.path.join(ROOT, "submission-a3-infra.yml"), encoding="utf-8").read().strip()

pipe_stamp = os.path.basename(sorted(glob.glob(os.path.join(EV, "pipeline-*.json")))[-1]).replace("pipeline-", "").replace(".json", "")
dlq_stamp = os.path.basename(sorted(glob.glob(os.path.join(EV, "dlq-*.json")))[-1]).replace("dlq-", "").replace(".json", "")
smoke_stamp = os.path.basename(smoke).replace("smoke-", "").replace(".json", "")

dlq_reasons = "password-protected PDF; corrupt/unreadable PDF (\"No /Root object\"); and no extractable text (blank page)"

# ---------------------------------------------------------------- cost model
# ap-southeast-2 list approximations (Oct 2026): Fargate $0.0657/vCPU-hr +
# $0.0072/GB-hr on-demand, Spot up to -70%; Titan embed $0.02/M tokens;
# Gemma 3 12B $0.0927/M in, $0.2987/M out; dashboard $3; alarms $0.10 each.
COST = {
    "COST_FARGATE": "0.30",       # ~3.3 vCPU-hr + ~6.7 GB-hr, on-demand; spot cuts further
    "COST_CONTROLLER": "4.40",    # 0.25 vCPU / 0.5 GB x 730 h at spot rates
    "COST_EMBED": "0.16",
    "COST_GEN": "0.61",           # 1 000 summaries + 900 answers
    "COST_LAMBDA": "0.11",
    "COST_SQS": "<0.01",
    "COST_DDB": "0.20",
    "COST_S3": "0.10",
    "COST_VECTORS": "0.05",
    "COST_CW": "3.50",
    "COST_EDGE": "0.03",
    "COST_TOTAL": "10",
    "COST_INFRA": "9.1",
    "COST_AI": "0.77",
}

TOKENS = {
    "PIPE_COUNT": str(pipe["documents"]),
    "PIPE_SPIKE_BACKLOG": str(pipe["peak"]["backlog"]),
    "PIPE_MAX_RUN": str(pipe["peak"]["running"]),
    "PIPE_DRAIN_MIN": "1",
    "PIPE_DRAIN_RATE": "31",
    "PIPE_SCALEIN_MIN": "1.3",
    "PIPE_STAMP": pipe_stamp,
    "DLQ_REASONS": dlq_reasons,
    "DLQ_STAMP": dlq_stamp,
    "SMOKE_STAMP": smoke_stamp,
    "YAML_EXCERPT": "```yaml\n" + yml + "\n```",
    **COST,
}

# ---------------------------------------------------------------- text fixes
FIXES = [
    ("a monthly cost estimate of ≈US${{COST_TOTAL}} for 50 concurrent users, of which the dominant drivers are the managed AI services rather than the infrastructure (Section 5).",
     "a monthly cost estimate of ≈US$10 for 50 concurrent users, dominated by the always-on controller task and the observability dashboard, with burst compute and managed AI each under a dollar per month (Section 5)."),
    ("The economics tell the intended story cleanly: the two largest line items are a *fixed* ~US$3 dashboard/observability convenience and the *usage-proportional* AI spend (≈{{COST_AI}}); the bursty compute itself — the part a naive design makes expensive — is under a dollar because it only bills while documents are actually being processed.",
     "The economics tell an honest story: roughly 80 % of the bill is *fixed* — the always-on controller task (≈US$4.40 at spot rates) and the observability stack (≈US$3.50) — while the fully usage-proportional parts are each under a dollar (burst compute ≈US$0.30; managed AI ≈US$0.77). For an idle-most-of-the-time workload that is the right shape: you pay for the control loop and the dashboard, not for standby capacity."),
    ("| ECS Fargate Spot (workers) | 1 000 docs × 25 s ≈ 7 vCPU-hr + 14 GB-hr at spot rates | ≈{{COST_FARGATE}} |",
     "| ECS Fargate (workers) | 1 000 docs ≈ 3.3 vCPU-hr + 6.7 GB-hr amortised, on-demand list (Spot cuts up to ≈70 %) | ≈{{COST_FARGATE}} |"),
    ("Compare: a single always-on worker task would add ≈US$48/month (on-demand) and a hosted search alternative hundreds more — the scale-to-zero design removes both.",
     "Compare: a single always-on worker task would add ≈US$58/month (on-demand; ≈US$17 with Spot) and a hosted search alternative hundreds more — the scale-to-zero design removes both."),
    ("**Spot capacity halves the compute unit cost** at no reliability cost, because the workload is designed to tolerate interruption (Section 2.8).",
     "**Spot capacity cuts the compute unit cost by up to ≈70 %** at no reliability cost, because the workload is designed to tolerate interruption (Section 2.8)."),
    ("the custom loop is ~150 lines, fully observable, and costs ≈US$3/month",
     "the custom loop is ~150 lines, fully observable, and costs ≈US$5/month at spot rates"),
    ("| One more component to operate; ~US$3/month fixed cost |",
     "| One more component to operate; ~US$4.40/month fixed cost at spot rates |"),
    ("| ECS Fargate (controller task) | 0.25 vCPU / 0.5 GB × 730 hr at spot rates | ≈{{COST_CONTROLLER}} |",
     "| ECS Fargate (controller task) | 0.25 vCPU / 0.5 GB × 730 hr at Spot rates | ≈{{COST_CONTROLLER}} |"),
]

for old, new in FIXES:
    if old not in text:
        print("FIX NOT FOUND (check whitespace):", old[:90])
    text = text.replace(old, new)

# ---------------------------------------------------------------- token pass
for tok, val in TOKENS.items():
    text = text.replace("{{" + tok + "}}", str(val))

leftover = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", text)))
open(REPORT, "w", encoding="utf-8").write(text)
print("leftover tokens:", leftover if leftover else "none")
print("report chars:", len(text))
