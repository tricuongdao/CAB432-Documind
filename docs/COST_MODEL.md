# DocuMind AI, Cost model for 50 concurrent users (CAB432 A3)

Companion to Section 5 of the report. Everything here is reconstructable from AWS list pricing (October 2026) and the measured experiments in `docs/evidence/`. Currency: USD.

## 1. Usage model

| Quantity | Value | Basis |
|----------|-------|-------|
| Concurrent users (average demand) | 50 | Assessment brief |
| Documents / user / month | 20 (2 MB, ≈8 chunks) | Course-scale reading volume |
| Documents / month | 1 000 | 50 × 20 |
| Questions / user / month | 60 | ≈2/day |
| Cache hit rate (modelled) | 70 % | Conservative vs measured (repeat requests: 100 % hits) |
| Origin answers / month | 900 | 50 × 60 × 30 % |
| Worker seconds / document | ≈12 s amortised (≈5.5 s processing + cold-start share) | Measured (pipeline evidence) |
| Storage steady state | ≈2.5 GB | Bounded by the 24 h TTL window |
| Answer size | ≈1 800 in / 250 out tokens | Measured prompts |

## 2. Unit rates used (ap-southeast-2 list, Oct 2026)

| Item | Rate | Source |
|------|------|--------|
| Fargate vCPU-hour (on-demand) | **$0.04856** | AWS Price List bulk file, ap-southeast-2 (verified) |
| Fargate GB-hour (on-demand) | **$0.00532** | AWS Price List bulk file, ap-southeast-2 (verified) |
| Fargate Spot discount | up to −70 % | AWS Fargate pricing page |
| Titan Embed Text v2 | $0.02 / 1M tokens | AWS Bedrock pricing page |
| Gemma 3 12B (Sydney) | $0.0927 in / $0.2987 out per 1M tokens | AWS Bedrock pricing page |
| Lambda | $0.00001667 / GB-s + free tier requests | AWS Lambda pricing |
| DynamoDB on-demand | ≈$1.35 / M WRU, ≈$0.27 / M RRU, ≈$0.28 / GB-month | AWS DynamoDB pricing (+ Sydney uplift, approximated) |
| SQS | $0.40 / M requests, 1M free | AWS SQS pricing |
| S3 Standard | ≈$0.0255 / GB-month + per-request | AWS S3 pricing (Sydney, rounded) |
| S3 Vectors | usage-based, small at this scale | AWS S3 pricing (vectors section) |
| CloudWatch dashboard | $3.00 / month | AWS CloudWatch pricing (published page) |
| CloudWatch log ingestion | **$0.67 / GB** | AWS Price List bulk file, ap-southeast-2 (verified) |
| CloudWatch alarm | $0.10 / month | AWS CloudWatch pricing |
| Secrets Manager | $0.40 / secret / month | AWS Secrets Manager pricing |
| API Gateway HTTP | $1.00 / M requests | AWS API Gateway pricing |
| EventBridge | $1.00 / M events | AWS EventBridge pricing |

## 3. Line-by-line monthly estimate

> **Authoritative source.** These figures are computed, not hand-written: `scripts/calc_ondemand_cost.py` pulls the AWS **public Price List bulk files** for `ap-southeast-2` and writes `docs/evidence/cost-ondemand.csv`. REPORT.md §5.2 quotes that CSV. All values below are **on-demand**, because that is what the AWS Pricing Calculator can price, its Fargate form states that Fargate Spot and Compute Savings Plans are not supported. Spot sensitivity is at §3.1.

| Service | Math | Cost (US$) |
|---------|------|-----------|
| Fargate controller | 0.25 vCPU / 0.5 GB × 730 h: 182.5 × $0.04856 + 365 × $0.00532 | **10.80** |
| CloudWatch | 1 dashboard $3.00 + 3 alarms $0.30 + 1 GB ingest × $0.67 | **3.97** |
| Bedrock generation (Gemma 3 12B) | 5.12 M in × $0.0927/M + 0.445 M out × $0.2987/M | **0.61** |
| Secrets Manager | 1 secret | **0.40** |
| Fargate workers | 3.3 vCPU-hr × $0.04856 + 6.7 GB-hr × $0.00532 | **0.20** |
| S3 Standard | 2.5 GB × $0.025 + 23 k requests | **0.18** |
| DynamoDB | 8 k WRU × $0.71/M + 120 k RRU × $0.1425/M + 0.5 GB × $0.285 | **0.17** |
| Bedrock embeddings (Titan v2) | 8.03 M × $0.02/M | **0.16** |
| Lambda (API) | 6 300 GB-s × $0.0000166667 + 24 k requests × $0.20/M | **0.11** |
| S3 Vectors | 8 k vectors + 5 k queries (published list) | **0.05** |
| API Gateway (HTTP) | 24 k × $1.29/M | **0.03** |
| SQS | 8 k × $0.40/M | **< 0.01** |
| EventBridge | 1 k × $1.00/M | **< 0.01** |
| **Total** | | **16.68** |

Fixed vs variable: **$14.50 fixed** (controller 10.80 + dashboard and alarms 3.30 + secret 0.40) + **≈$2.17 usage-proportional** (compute + AI + storage + requests + the $0.67 of log ingestion that grows with traffic). The fully bursty part of the workload, the document pipeline, costs **under $0.40/month**, because it only bills while actually working.

### 3.1 Spot sensitivity

Fargate Spot is the largest single lever and the calculator cannot express it, so it is quoted separately rather than folded into the total:

| Scenario | Controller | Total |
|----------|-----------|-------|
| On-demand (what the calculator shows) | 10.80 | **16.68** |
| 50 % Spot discount | 5.40 | **11.18** |
| 70 % Spot discount (Fargate's stated maximum) | 3.24 | **8.98** |

Two rates in §2 were materially wrong before this rewrite and are corrected here: Sydney Fargate vCPU is **$0.04856/hr**, not $0.0657, and CloudWatch log ingestion is **$0.67/GB**, not ~$0.20. The first correction lowers the compute lines; the second raises observability.


## 4. Scaling effects (what changes with demand)

- **10× the users (500 concurrent):** documents 10 k/month, answers 9 k/month. AI ≈ $7.7, worker compute ≈ $2.7 (with Spot), fixed costs unchanged → ≈ **$25/month**. Nearly linear in usage, sub-linear in cost per user, the fixed platform floor amortises.
- **Always-on comparison:** keeping one worker task always running adds ≈$58/month on-demand (≈$17 Spot); a hosted search cluster starts at tens-to-hundreds per month **idle**. The scale-to-zero design removes both floors.
- **Cache sensitivity:** at 70 % hit rate, ≈2 100 of 3 000 questions never touch a model. If the hit rate fell to 0 %, AI spend would rise ≈3× (still under $3/month at this scale) and p50 latency would rise from ≈46 ms to ≈2.4 s. The cache is the cheapest reliability *and* cost lever in the system.

## 5. Reproduction in the AWS Pricing Calculator

The brief requires a public share link from the calculator. Reproduce the §3 table with the configuration below (≈10 minutes), then **Share** (top right) → copy the public link.

1. Open <https://calculator.aws/#/createCalculator>, set **Region = Asia Pacific (Sydney)** *before* adding services.
2. Leave **AWS Support** off, it is a large fixed add-on and not part of this estimate.
3. Add each service via **Add service → search → Configure → Save and add service**.

**Build the estimate on-demand.** The calculator's Fargate form states outright: *"Fargate Spot and Compute Savings Plans are currently not supported on the AWS Pricing Calculator."* So the calculator can only produce the on-demand total (US$16.68). Spot is quantified in §3.1 instead, and the report says so explicitly rather than quoting a total the link cannot reproduce.

| Calculator service | Configuration |
|--------------------|----------------|
| AWS Fargate (controller) | 0.25 vCPU, 0.5 GB, 1 task, **730**, units **per month** and **hours** |
| AWS Fargate (workers) | 1 vCPU, 2 GB, 1 task, **3.3**, units **per month** and **hours** |
| Amazon Bedrock, Titan Text Embeddings V2 | input tokens **8 030 000** |
| Amazon Bedrock, Gemma 3 12B | input **5 120 000**, output **445 000** |
| AWS Lambda | x86, 1024 MB, 24 000 requests, 0.26 s |
| Amazon SQS | standard queue, 8 000 requests |
| Amazon DynamoDB (on-demand) | 8 000 writes, 120 000 reads, 0.5 GB |
| Amazon S3 Standard | 2.5 GB, ~8 000 PUT, ~15 000 GET |
| Amazon CloudWatch | 1 dashboard, 3 alarms, 1 GB log ingest |
| AWS Secrets Manager | 1 secret, 30-day duration |
| Amazon API Gateway (HTTP) | 24 000 requests |
| Amazon EventBridge | 1 000 events |
| Amazon S3 Vectors | **not offered by the calculator**, priced at list (~US$0.05) in §3 |

**Three traps that silently produce a wildly wrong total.** Each of these was hit and diagnosed during this build:

1. **Fargate unit dropdowns.** "Number of tasks or pods" defaults to **per day** and "Average duration" defaults to **minutes**. Left alone, a 730-hour controller becomes 730 *minutes* and the task count is extrapolated per day. Set both units explicitly (**per month**, **hours**).
2. **"Amount of vCPU allocated" is below the fold** and defaults to 1 vCPU. A 0.25 vCPU controller priced at 1 vCPU quadruples that line.
3. **Gemma input tokens are 5.12 M, not 4.4 M.** 1 000 summaries × 3 500 + 900 answers × 1 800 = 5 120 000. Using 4.4 M understates the generation line and the total no longer ties to §3.

Expected calculator total: **≈US$16.68**, or slightly less once free tiers apply (Lambda requests, SQS, the first CloudWatch alarms) and the S3 Vectors line is omitted.

## 6. Sustainability notes

- Compute energy is proportional to documents processed (scale-to-zero; Spot packs bursts onto spare capacity).
- The two-tier cache removes ≈70 % of the highest-intensity work (accelerated model inference), and the 24 h TTL bounds storage growth.
- Right-sized tasks (1 vCPU / 2 GB; measured RSS < 1 GB) and shared managed services amortise physical infrastructure across tenants.
- Honest caveats: the controller task runs 24×7 (US$10.80/month on-demand, US$3.24 at 70 % Spot) for elasticity; no carbon metrics exist at this granularity; the Sydney grid is carbon-heavier than the global average, which strengthens the case for avoiding idle energy rather than minimising region choice.
