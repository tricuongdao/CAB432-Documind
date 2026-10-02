# DocuMind AI — Cost model for 50 concurrent users (CAB432 A3)

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
| Fargate vCPU-hour (on-demand) | ≈$0.0657 | AWS Fargate pricing page (Sydney) |
| Fargate GB-hour (on-demand) | ≈$0.0072 | AWS Fargate pricing page (Sydney) |
| Fargate Spot discount | up to −70 % | AWS Fargate pricing page |
| Titan Embed Text v2 | $0.02 / 1M tokens | AWS Bedrock pricing page |
| Gemma 3 12B (Sydney) | $0.0927 in / $0.2987 out per 1M tokens | AWS Bedrock pricing page |
| Lambda | $0.00001667 / GB-s + free tier requests | AWS Lambda pricing |
| DynamoDB on-demand | ≈$1.35 / M WRU, ≈$0.27 / M RRU, ≈$0.28 / GB-month | AWS DynamoDB pricing (+ Sydney uplift, approximated) |
| SQS | $0.40 / M requests, 1M free | AWS SQS pricing |
| S3 Standard | ≈$0.0255 / GB-month + per-request | AWS S3 pricing (Sydney, rounded) |
| S3 Vectors | usage-based, small at this scale | AWS S3 pricing (vectors section) |
| CloudWatch dashboard | $3.00 / month (first 3: 1 free then $3) | AWS CloudWatch pricing |
| CloudWatch alarm | $0.10 / month | AWS CloudWatch pricing |
| Secrets Manager | $0.40 / secret / month | AWS Secrets Manager pricing |
| API Gateway HTTP | $1.00 / M requests | AWS API Gateway pricing |
| EventBridge | $1.00 / M events | AWS EventBridge pricing |

## 3. Line-by-line monthly estimate

| Service | Math | Cost |
|---------|------|------|
| Fargate workers | 1 000 docs × 12 s ≈ 3.3 vCPU-hr + 6.7 GB-hr = 3.3×$0.0657 + 6.7×$0.0072 | **$0.27** (≈$0.08 with Spot) |
| Fargate controller | 0.25 vCPU / 0.5 GB × 730 h at Spot ≈ $4.40 | **$4.40** |
| Bedrock embeddings | (1 000×8 000 + 900×30) tokens ≈ 8.03 M × $0.02/M | **$0.16** |
| Bedrock generation — summaries | 1 000 × (3 500 in × $0.0927/M + 220 out × $0.2987/M) | **$0.39** |
| Bedrock generation — answers | 900 × (1 800 in × $0.0927/M + 250 out × $0.2987/M) | **$0.22** |
| Lambda (API) | ≈6 300 GB-s × $0.00001667 + requests (mostly free tier) | **$0.11** |
| SQS | ≈8 k requests | **< $0.01** |
| DynamoDB | ≈8 k WRU + ≈120 k RRU + 0.5 GB | **$0.20** |
| S3 (storage + requests) | 2.5 GB + ≈23 k requests | **$0.10** |
| S3 Vectors | ≈8 k vectors + ≈5 k queries | **$0.05** |
| CloudWatch | 1 dashboard $3.00 + 3 alarms $0.30 + log ingest ≈$0.20 | **$3.50** |
| Secrets Manager | 1 secret | **$0.40** |
| API Gateway + EventBridge | ≈24 k requests + 1 k events | **$0.03** |
| **Total** | | **≈ $9.86 / month** |

Fixed vs variable: ≈ $8.30 fixed (controller + observability + secret) + ≈ $1.55 usage-proportional (compute + AI + storage). The fully bursty part of the workload — the document pipeline — costs **under $0.40/month**, because it only bills while actually working.

## 4. Scaling effects (what changes with demand)

- **10× the users (500 concurrent):** documents 10 k/month, answers 9 k/month. AI ≈ $7.7, worker compute ≈ $2.7 (with Spot), fixed costs unchanged → ≈ **$25/month**. Nearly linear in usage, sub-linear in cost per user — the fixed platform floor amortises.
- **Always-on comparison:** keeping one worker task always running adds ≈$58/month on-demand (≈$17 Spot); a hosted search cluster starts at tens-to-hundreds per month **idle**. The scale-to-zero design removes both floors.
- **Cache sensitivity:** at 70 % hit rate, ≈2 100 of 3 000 questions never touch a model. If the hit rate fell to 0 %, AI spend would rise ≈3× (still under $3/month at this scale) and p50 latency would rise from ≈46 ms to ≈2.4 s. The cache is the cheapest reliability *and* cost lever in the system.

## 5. Reproduction in the AWS Pricing Calculator

The brief requires a public share link from the calculator; the estimate above mirrors the calculator line-for-line. To produce the link (≈10 minutes):

1. Open <https://calculator.aws/#/createCalculator>, region **Asia Pacific (Sydney) ap-southeast-2**.
2. Add each service below with the given configuration, then click **Share** (top right) → copy the generated public link.

| Calculator service | Configuration |
|--------------------|----------------|
| Amazon ECS / Fargate | ×2 rows: (a) 3.3 vCPU-hr + 6.7 GB-hr per month, Spot; (b) 0.25 vCPU / 0.5 GB provisioned 24×7, Spot |
| Amazon Bedrock | Titan Text Embeddings V2: 8.03 M input tokens; Gemma 3 12B: 4.4 M input tokens, 0.45 M output tokens |
| AWS Lambda | 6 300 GB-seconds, 24 k requests |
| Amazon SQS | 8 k requests |
| Amazon DynamoDB (on-demand) | 8 k writes, 120 k reads, 0.5 GB storage |
| Amazon S3 Standard | 2.5 GB, 23 k requests |
| Amazon S3 Vectors | 8 k vectors stored, 5 k queries |
| Amazon CloudWatch | 1 dashboard, 3 alarms, 1 GB log ingest |
| AWS Secrets Manager | 1 secret |
| Amazon API Gateway (HTTP) | 24 k requests |
| Amazon EventBridge | 1 k events |

3. Paste the resulting link into the report line *(Section 5.2, "public link")* — search `REPORT.md` for `Pricing Calculator estimate`.

## 6. Sustainability notes

- Compute energy is proportional to documents processed (scale-to-zero; Spot packs bursts onto spare capacity).
- The two-tier cache removes ≈70 % of the highest-intensity work (accelerated model inference), and the 24 h TTL bounds storage growth.
- Right-sized tasks (1 vCPU / 2 GB; measured RSS < 1 GB) and shared managed services amortise physical infrastructure across tenants.
- Honest caveats: the controller task runs 24×7 (≈$4.40/month) for elasticity; no carbon metrics exist at this granularity; the Sydney grid is carbon-heavier than the global average, which strengthens the case for avoiding idle energy rather than minimising region choice.
