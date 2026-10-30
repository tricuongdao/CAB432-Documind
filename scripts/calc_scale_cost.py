"""Marginal and scaled cost for the utilisation-to-cost analysis in section 5.3.

Anchors to the authoritative on-demand estimate (calc_ondemand_cost.py ->
docs/evidence/cost-ondemand.csv) rather than rebuilding it, so the numbers agree
with Figure 5.2. All arithmetic happens here.

Run: python scripts/calc_scale_cost.py
"""
import csv
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "docs", "evidence", "cost-ondemand.csv")

# rates verified in calc_ondemand_cost.py (USD, ap-southeast-2)
VCPU_HR = 0.04856
GB_HR = 0.00532
TITAN_PER_M = 0.02
GEMMA_IN_PER_M = 0.0927
GEMMA_OUT_PER_M = 0.2987

WORKER_VCPU, WORKER_GB, DOC_SECONDS = 1.0, 2.0, 12.0
DOC_EMBED_TOKENS = 8_000
SUM_IN, SUM_OUT = 3_500, 220

FIXED_LINES = ("ECS Fargate - controller task", "AWS Secrets Manager", "Amazon CloudWatch")
DOCS_1X, DOCS_10X = 1_000, 10_000


def authoritative_total():
    """Total and fixed floor straight from the CSV the report quotes."""
    total = fixed = 0.0
    with open(CSV_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["line_item"] == "TOTAL":
                total = float(r["monthly_usd"])
            elif r["line_item"] in FIXED_LINES:
                # only the dashboard and alarms are fixed; log ingest scales
                fixed += float(r["monthly_usd"])
    return total, fixed


def marginal_per_document():
    compute = (WORKER_VCPU * VCPU_HR + WORKER_GB * GB_HR) * (DOC_SECONDS / 3600)
    embed = DOC_EMBED_TOKENS * TITAN_PER_M / 1e6
    summary = SUM_IN * GEMMA_IN_PER_M / 1e6 + SUM_OUT * GEMMA_OUT_PER_M / 1e6
    return compute, embed, summary


def main():
    total, fixed_lines = authoritative_total()

    # the CloudWatch line bundles US$0.67 of log ingest, which grows with traffic
    LOG_LINE = 0.67
    fixed = fixed_lines - LOG_LINE
    variable = total - fixed

    compute, embed, summary = marginal_per_document()
    marginal = compute + embed + summary

    total_10x = fixed + 10 * variable

    print("authoritative on-demand estimate (USD/month)")
    print(f"  total at 1x demand (1 000 docs)      {total:8.2f}")
    print(f"  fixed floor                          {fixed:8.2f}")
    print(f"  scales with demand                   {variable:8.2f}")

    print("\nmarginal cost of one more document (USD)")
    print(f"  worker compute  {DOC_SECONDS:.0f} s at 1 vCPU / 2 GB   {compute:.7f}")
    print(f"  embedding       {DOC_EMBED_TOKENS:,} tokens       {embed:.7f}")
    print(f"  summary         {SUM_IN:,} in / {SUM_OUT} out    {summary:.7f}")
    print(f"  TOTAL                                {marginal:.7f}")
    print(f"  (US${marginal*1000:.3f} per 1 000 documents)")

    print("\nat 10x demand (10 000 documents)")
    print(f"  total                                {total_10x:8.2f}")
    print(f"  fixed floor                          {fixed:8.2f}")
    print(f"  scales with demand                   {total_10x - fixed:8.2f}")

    print("\ncost per document (USD)")
    print(f"  at 1x                                {total / DOCS_1X:8.4f}")
    print(f"  at 10x                               {total_10x / DOCS_10X:8.4f}")
    print(f"  improvement factor                   {(total / DOCS_1X) / (total_10x / DOCS_10X):8.2f}x")
    print(f"\n  fixed share of total at 1x           {fixed / total * 100:8.1f}%")
    print(f"  fixed share of total at 10x          {fixed / total_10x * 100:8.1f}%")


if __name__ == "__main__":
    main()
