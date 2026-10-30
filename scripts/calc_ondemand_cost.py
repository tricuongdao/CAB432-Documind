"""Compute DocuMind's on-demand monthly cost from AWS's public price lists.

Uses the public Price List bulk files (no credentials required) for
ap-southeast-2 (Sydney). Every rate is either pulled from AWS's own file
(rate_source="price-list") or, where AWS does not publish the SKU in the
bulk file, taken from the published pricing page (rate_source="published").

All arithmetic happens in this script, and the CSV it writes is the file the
report quotes from — no hand-arithmetic anywhere.

Run: python scripts/calc_ondemand_cost.py [--refresh]
"""
import csv
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".build", "pricing")
OUT_CSV = os.path.join(ROOT, "docs", "evidence", "cost-ondemand.csv")
REGION = "ap-southeast-2"
HOURS = 730.0

# ---- usage model (docs/COST_MODEL.md §1) ----
EMBED_TOKENS = 8_030_000
GEMMA_IN, GEMMA_OUT = 5_120_000, 445_000
TITAN_PER_M, GEMMA_IN_PER_M, GEMMA_OUT_PER_M = 0.02, 0.0927, 0.2987
LAMBDA_GB_S, LAMBDA_REQUESTS = 6300, 24_000
SQS_REQUESTS = 8_000
DDB_WRU, DDB_RRU, DDB_GB = 8_000, 120_000, 0.5
S3_GB, S3_REQ = 2.5, 23_000
CW_DASH, CW_ALARM, CW_LOG_GB = 1, 3, 1.0
SECRETS = 1
APIGW_REQUESTS = 24_000
EVENTS = 1_000
CTRL_VCPU, CTRL_GB, CTRL_H = 0.25, 0.5, HOURS
WORK_VCPU_H, WORK_GB_H = 3.3, 6.7          # already in vCPU-hours / GB-hours
S3V_COST = 0.05

# ---- published-page fallbacks, used only when the bulk file has no SKU ----
PUBLISHED = {
    "cw_dashboard": (3.00, "USD/dashboard-month (published pricing page)"),
    "ddb_gb": (0.285, "USD/GB-month (published DynamoDB pricing, Sydney)"),
    "s3_put": (0.0050 / 1000, "USD/request (published S3 pricing, Sydney)"),
    "s3_get": (0.0004 / 1000, "USD/request (published S3 pricing, Sydney)"),
    "eventbridge": (1.00 / 1_000_000, "USD/event (published EventBridge pricing)"),
}


def fetch(offer):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{offer}-{REGION}.json")
    if os.path.exists(path) and os.path.getsize(path) > 1000 and "--refresh" not in sys.argv:
        return json.load(open(path, encoding="utf-8"))
    url = f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{offer}/current/{REGION}/index.json"
    with urllib.request.urlopen(url, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    json.dump(data, open(path, "w", encoding="utf-8"))
    return data


def price(doc, sku):
    for term in doc.get("terms", {}).get("OnDemand", {}).get(sku, {}).values():
        for dim in term.get("priceDimensions", {}).values():
            try:
                return float(dim["pricePerUnit"]["USD"])
            except (KeyError, ValueError):
                pass
    return None


def by_usage(doc, *usagetypes):
    """Exact usagetype match, first hit."""
    wanted = set(usagetypes)
    for sku, prod in doc.get("products", {}).items():
        if prod.get("attributes", {}).get("usagetype") in wanted:
            p = price(doc, sku)
            if p is not None:
                return p
    return None


def by_predicate(doc, pred):
    for sku, prod in doc.get("products", {}).items():
        a = prod.get("attributes", {})
        if pred(a, prod.get("productFamily", "")):
            p = price(doc, sku)
            if p is not None:
                return p
    return None


def main():
    rates, sources, notes = {}, {}, []

    def put(key, value, source):
        if value is None:
            fb = PUBLISHED.get(key)
            if fb:
                rates[key], sources[key] = fb[0], "published"
                notes.append(f"{key}: no SKU in bulk file, used published rate")
            else:
                notes.append(f"{key}: NOT FOUND and no fallback")
            return
        rates[key], sources[key] = value, source

    ecs = fetch("AmazonECS")
    put("fargate_vcpu_hr", by_usage(ecs, "APS2-Fargate-vCPU-Hours:perCPU"), "price-list")
    put("fargate_gb_hr", by_usage(ecs, "APS2-Fargate-GB-Hours"), "price-list")

    lam = fetch("AWSLambda")
    put("lambda_gb_s", by_usage(lam, "APS2-Lambda-GB-Second"), "price-list")
    put("lambda_request", by_usage(lam, "APS2-Request"), "price-list")

    ddb = fetch("AmazonDynamoDB")
    put("ddb_wru", by_usage(ddb, "APS2-WriteRequestUnits"), "price-list")
    put("ddb_rru", by_usage(ddb, "APS2-ReadRequestUnits"), "price-list")
    put("ddb_gb", None, "published")

    sqs = fetch("AWSQueueService")
    put("sqs_request", by_usage(sqs, "APS2-Requests-Tier1"), "price-list")

    apigw = fetch("AmazonApiGateway")
    put("apigw_http", by_usage(apigw, "APS2-ApiGatewayHttpRequest"), "price-list")

    cw = fetch("AmazonCloudWatch")
    put("cw_alarm", by_usage(cw, "APS2-CW:AlarmMonitorUsage"), "price-list")
    put("cw_log_gb", by_usage(cw, "APS2-DataProcessing-Bytes"), "price-list")
    put("cw_dashboard", None, "published")

    sm = fetch("AWSSecretsManager")
    put("secret", by_predicate(sm, lambda a, pf: "secret" in a.get("usagetype", "").lower()
                               and "rotation" not in a.get("usagetype", "").lower()), "price-list")

    s3 = fetch("AmazonS3")
    put("s3_gb", by_predicate(s3, lambda a, pf: pf == "Storage" and a.get("volumeType") == "Standard"), "price-list")
    put("s3_put", None, "published")
    put("s3_get", None, "published")
    put("eventbridge", None, "published")

    # ---------------- compute ----------------
    rows = []

    def add(name, basis, usd):
        rows.append({"line_item": name, "basis": basis, "monthly_usd": round(usd, 4)})

    ctrl = CTRL_VCPU * CTRL_H * rates["fargate_vcpu_hr"] + CTRL_GB * CTRL_H * rates["fargate_gb_hr"]
    work = WORK_VCPU_H * rates["fargate_vcpu_hr"] + WORK_GB_H * rates["fargate_gb_hr"]
    add("ECS Fargate - controller task", "0.25 vCPU / 0.5 GB x 730 h (on-demand)", ctrl)
    add("ECS Fargate - worker pool", "3.3 vCPU-h + 6.7 GB-h burst", work)
    add("Amazon Bedrock - Titan Text Embeddings V2", f"{EMBED_TOKENS/1e6:.2f}M input tokens",
        EMBED_TOKENS / 1e6 * TITAN_PER_M)
    add("Amazon Bedrock - Gemma 3 12B generation", f"{GEMMA_IN/1e6:.2f}M in / {GEMMA_OUT/1e6:.3f}M out",
        GEMMA_IN / 1e6 * GEMMA_IN_PER_M + GEMMA_OUT / 1e6 * GEMMA_OUT_PER_M)
    add("AWS Lambda", f"{LAMBDA_GB_S} GB-s + {LAMBDA_REQUESTS} requests",
        LAMBDA_GB_S * rates["lambda_gb_s"] + LAMBDA_REQUESTS * rates["lambda_request"])
    add("Amazon SQS", f"{SQS_REQUESTS} requests", SQS_REQUESTS * rates["sqs_request"])
    add("Amazon DynamoDB", f"{DDB_WRU} WRU + {DDB_RRU} RRU + {DDB_GB} GB",
        DDB_WRU * rates["ddb_wru"] + DDB_RRU * rates["ddb_rru"] + DDB_GB * rates["ddb_gb"])
    add("Amazon S3 Standard", f"{S3_GB} GB + {S3_REQ} requests",
        S3_GB * rates["s3_gb"] + S3_REQ * rates["s3_put"])
    add("Amazon S3 Vectors", "8k vectors + 5k queries (published list price)", S3V_COST)
    add("Amazon CloudWatch", f"{CW_DASH} dashboard + {CW_ALARM} alarms + {CW_LOG_GB} GB logs",
        CW_DASH * rates["cw_dashboard"] + CW_ALARM * rates["cw_alarm"] + CW_LOG_GB * rates["cw_log_gb"])
    add("AWS Secrets Manager", f"{SECRETS} secret", SECRETS * rates["secret"])
    add("Amazon API Gateway (HTTP)", f"{APIGW_REQUESTS} requests", APIGW_REQUESTS * rates["apigw_http"])
    add("Amazon EventBridge", f"{EVENTS} events", EVENTS * rates["eventbridge"])

    total = sum(r["monthly_usd"] for r in rows)
    fixed = ctrl + CW_DASH * rates["cw_dashboard"] + CW_ALARM * rates["cw_alarm"] + rates["secret"]

    # Spot sensitivity: the calculator cannot express Fargate Spot, so quantify it here.
    # FARGATE_SPOT is up to ~70% cheaper; use 70% and 50% to bracket.
    spot_rows = []
    for pct in (0.50, 0.70):
        ctrl_spot = ctrl * (1 - pct)
        work_spot = work * (1 - pct)
        spot_total = total - ctrl - work + ctrl_spot + work_spot
        spot_rows.append((pct, ctrl_spot, spot_total))

    w = 46
    print("=" * 96)
    print("DocuMind AI - on-demand monthly cost, Asia Pacific (Sydney), USD")
    print("rates: AWS public Price List bulk files (+ published page where no bulk SKU exists)")
    print("=" * 96)
    for r in sorted(rows, key=lambda x: -x["monthly_usd"]):
        print(f'  {r["line_item"]:<{w}} {r["basis"]:<42} ${r["monthly_usd"]:>7.2f}')
    print("-" * 96)
    print(f'  {"TOTAL":<{w}} {"":<42} ${total:>7.2f}')
    print(f'  {"fixed floor (controller + observability + secret)":<{w}} {"":<42} ${fixed:>7.2f}')
    print(f'  {"usage-proportional":<{w}} {"":<42} ${total - fixed:>7.2f}')
    print("=" * 96)
    print("\nSpot sensitivity (Fargate lines only; calculator cannot model this):")
    for pct, ctrl_spot, spot_total in spot_rows:
        print(f'  {int(pct*100)}% Spot discount -> controller ${ctrl_spot:>6.2f}, '
              f'total ${spot_total:>6.2f}')

    print("\nrates (USD) and source:")
    for k in sorted(rates):
        print(f"  {k:<18} {rates[k]:<16g} {sources.get(k,'?')}")
    if notes:
        print("\nnotes:")
        for n in notes:
            print("  -", n)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["line_item", "basis", "monthly_usd"])
        wr.writeheader()
        wr.writerows(rows)
        wr.writerow({"line_item": "TOTAL", "basis": "on-demand ap-southeast-2", "monthly_usd": round(total, 2)})
    print(f"\nwrote {OUT_CSV}")


if __name__ == "__main__":
    main()
