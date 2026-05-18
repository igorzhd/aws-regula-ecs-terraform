# Scaling to Production: 1 Million Users per Day

This document describes what changes — and why — when this platform grows from a lab environment to a production workload at significant scale. It is not a feature backlog; it is an architectural discussion of bottlenecks, trade-offs, and the decisions that would need to be made at each growth phase.

**Current lab configuration:** 2 ECS tasks, RDS `db.t3.micro` Multi-AZ, synchronous request processing, single region (us-west-2).

**Target scale:** 1,000,000 document verifications per day.

---

## Baseline Math

| Metric | Value |
|---|---|
| Requests per day | 1,000,000 |
| Average requests per second | ~11.6 req/s |
| Peak (10× average, typical burst ratio) | ~100–120 req/s |
| Regula processing time per document | < 1 second |
| Database writes per day | ~1,000,000 (one session per verification) |
| Average writes per second | ~11.6/s |
| Database rows at end of year | ~365 million |

**Key insight:** the numbers are more manageable than they look. 12 writes/second is not a challenging database workload. The challenge is burst handling, not sustained throughput.

---

## What Breaks First

### 1. The synchronous request model

**Current flow:** user uploads image → ALB → python-api → Regula → response returned within 1 second.

At 100 req/s peak, you need ~100 ECS tasks available immediately. ECS autoscaling takes 60–120 seconds to provision new tasks. During that gap, requests pile up, ALB returns 503s, and users see errors.

This is the primary bottleneck. Everything else — database, S3, CloudFront — scales without structural changes.

### 2. ECS autoscaling is CPU-based and too slow

The current autoscaling policy tracks CPU utilization with a symmetric 60-second cooldown. For a burst workload:
- Scale-out needs to be fast (add tasks in 30 seconds)
- Scale-in can be slow (wait 5 minutes before removing tasks)
- The metric should reflect actual work waiting, not CPU on currently running tasks

### 3. Database reads at year-scale

365M rows in the sessions table is not a write problem — it's a read problem. Session history queries without proper indexing and partitioning become full table scans. This is manageable but requires planning from the start, not a crisis fix later.

---

## Phase 1: Async Processing with SQS

This is the highest-impact change and the prerequisite for everything else.

### The trade-off: latency vs. resilience

This decision has a genuine cost. Regula processes documents in under 1 second, which makes the synchronous model tempting to keep.

| Approach | Response time | Burst handling | Retry on failure |
|---|---|---|---|
| Synchronous (current) | ~1 second | No buffer — errors during bursts | No automatic retry |
| Async + SQS | ~2–3 seconds (poll-based) | Queue absorbs any burst size | Automatic — message reappears after visibility timeout |

**Recommendation:** Async + SQS is correct for 1M/day, even though it adds ~1 second perceived latency. The UX impact is manageable — a 2-second result return with a progress indicator is acceptable for document verification. The alternative (synchronous errors during bursts) is not. If a hard <1 second SLA is required, that forces keeping synchronous processing with much more aggressive pre-warming of ECS tasks — expensive and less reliable.

### New request flow

```
User                 Frontend              S3 (uploads)       SQS              ECS Worker          S3 (outputs)      DB
 |                      |                       |               |                    |                    |             |
 |--- upload image ----->|                       |               |                    |                    |             |
 |                      |-- request presigned URL (GET /upload-url)                  |                    |             |
 |                      |<-- presigned PUT URL --                |               (python-api)             |             |
 |                      |                       |               |                    |                    |             |
 |                      |-- PUT image directly to S3 ---------->|               |                    |             |
 |                      |                       |-- S3 event notification ---------->|                    |             |
 |                      |<-- transaction_id ----                |               |                    |             |
 |                      |                       |               |-- pick up message ->|                    |             |
 |                      |                       |               |                    |-- fetch image ------>|             |
 |                      |                       |               |                    |-- send to Regula (localhost:8080)  |
 |                      |                       |               |                    |-- store crop images ->|             |
 |                      |                       |               |                    |-- delete source image->|            |
 |                      |                       |               |                    |-- write session record ------------>|
 |                      |                       |               |                    |-- delete SQS message               |
 |                      |-- poll GET /sessions/{id} every 500ms (python-api queries DB)                                   |
 |<--- result returned when session record exists ----------------------------------------------------------------        |
```

### Standard SQS, not FIFO

Use Standard SQS. FIFO is limited to 3,000 messages/second (with batching) and guarantees ordering and exactly-once delivery — neither of which is needed here. There is no meaningful ordering requirement between different users' documents.

**On the double-processing concern:** Standard SQS delivers messages at-least-once. If a worker crashes mid-processing, the message becomes visible again after the visibility timeout (set this to 90–120 seconds, longer than Regula's processing time). The fix is an idempotent worker: before processing, check if a session record already exists for the `transaction_id`. If it does, skip processing and delete the message. This is the standard pattern and adds minimal complexity.

### S3 bucket strategy

```
regula-uploads-dev (temporary)
  └── pending/{transaction_id}/document.jpg
      → S3 lifecycle rule: delete after 24 hours (safety net)
      → Worker deletes immediately after successful processing

regula-images-dev (permanent, existing)
  └── sessions/{transaction_id}/
      page_0_crop.jpg
      page_1_crop.jpg
      raw_response.json
```

Two buckets serve distinct purposes. The uploads bucket is a transit buffer — files should not accumulate there. The images bucket is the permanent record. Keeping them separate makes lifecycle management, access policies, and cost attribution straightforward.

### Revised ECS autoscaling policy

Replace CPU target tracking with SQS queue depth tracking:

| Parameter | Current | At scale |
|---|---|---|
| Scaling metric | ECS CPU utilization | SQS `ApproximateNumberOfMessagesVisible` |
| Target | 70% CPU | 10 messages per running task |
| Scale-out cooldown | 60 seconds | **30 seconds** — react to bursts fast |
| Scale-in cooldown | 60 seconds | **300 seconds** — don't remove tasks too quickly |
| Min tasks | 2 | 5 (pre-warm for baseline load) |
| Max tasks | 6 | 50–100 (depends on burst profile and Regula licensing tier) |

**Why asymmetric cooldowns:** scale-out aggressiveness prevents queue buildup during bursts. Scale-in conservatism prevents yo-yo behavior — adding 20 tasks, then removing them, then adding them again as load oscillates. Keep tasks running a bit longer than strictly necessary.

### Regula licensing at scale

Regula's licensing model is **transaction-based, not per-instance**. Each processed document counts against the license transaction quota. This is important: you can run 50 or 100 ECS tasks without multiplying license cost by that factor. License validation and transaction counting require outbound internet access — this is why NAT Gateways are retained in the architecture even though they add cost. At scale, negotiate a volume transaction tier with Regula Forensics rather than a fixed allocation.

---

## Phase 2: Database Scaling

### Write load reality check

At 1M writes/day (11.6/s average), **the write load is not the bottleneck**. Standard PostgreSQL handles hundreds of writes per second. Aurora PostgreSQL handles tens of thousands. The session record insert — one row per verification — is a small, fast operation.

The real database concern is:
1. **Read performance on a growing table** (365M rows/year)
2. **Multi-region read access** (covered in Phase 3)

### Table partitioning from day one

Partition the `sessions` table by `created_at` month. This keeps query plans efficient as data grows — a query for "sessions from this week" scans one partition, not 365M rows.

```sql
-- Partitioned sessions table (range by month)
CREATE TABLE sessions (
    transaction_id UUID PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL,
    -- ... other columns
) PARTITION BY RANGE (created_at);

CREATE TABLE sessions_2026_01 PARTITION OF sessions
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
```

Old partitions (>90 days) can be archived to S3 and queried via Athena — keeping the operational database lean and fast while preserving full audit history cheaply.

### Migrate from RDS to Aurora Serverless v2

**Aurora Serverless v2** is the right target for production:
- Compute scales automatically in fine-grained increments (measured in Aurora Capacity Units — ACUs)
- You set a min and max ACU range; Aurora adjusts within that range per second based on load
- No instance type to choose or resize — it is effectively Fargate for databases
- Compatible with Aurora PostgreSQL — the application connection string and schema are identical to what the lab uses now
- Storage scales automatically from 10 GB to 128 TB
- Works with Aurora Global Database for multi-region (covered below)

**For this lab:** keep RDS `db.t3.micro`. The schema, queries, and application code do not change between RDS PostgreSQL and Aurora PostgreSQL. Migrating to Aurora is an infrastructure change, not an application change — the correct time to do it is when the workload justifies the cost, not during development.

| Concern | RDS Multi-AZ (current lab) | Aurora Serverless v2 (production) |
|---|---|---|
| Instance sizing | Manual (`db.t3.micro`) | Automatic (ACU range) |
| Storage scaling | Manual (allocated GB) | Automatic (up to 128 TB) |
| Read replicas | Up to 5, manual | Up to 15, Aurora Replicas |
| Failover time | ~60–120 seconds | ~30 seconds |
| Multi-region | Not supported | Aurora Global Database |
| Cost model | Fixed hourly | Per-second ACU consumption |

---

## Phase 3: Multi-Region Deployment

### When multi-region becomes necessary

Multi-region is not a day-one requirement. It becomes necessary when:
- Users in other regions experience >200ms latency on API responses
- A regional outage (like the 2021 us-east-1 event) is an unacceptable business risk
- Regulatory requirements mandate data residency in specific geographies

### Architecture

```
Route 53 (latency-based routing)
  ├── us-west-2  → CloudFront US → Regional ALB → ECS cluster → Aurora Global (primary)
  ├── eu-west-1  → CloudFront EU → Regional ALB → ECS cluster → Aurora Global (reader)
  └── ap-northeast-1 → CloudFront APAC → Regional ALB → ECS cluster → Aurora Global (reader)
```

**Aurora Global Database:**
- One primary region handles all writes (strong consistency)
- Up to 5 read regions with <1 second replication lag
- Regional failover: promote any reader to primary in <1 minute
- For document verification history: eventual consistency (<1s lag) is acceptable — a user checking their submission from a different country a few seconds later gets their result

**S3 Cross-Region Replication:**
- Enable on the images bucket: processed document crops and raw Regula JSON replicated to each active region
- ECS workers write to their local S3 endpoint; CRR propagates to other regions within seconds
- S3 replication is one-directional by default — all writes originate in the processing region and replicate out

**CloudFront is already multi-region by nature** — it distributes frontend files to edge locations globally. No changes needed here.

### Multi-region write routing

All document processing writes go to the Aurora Global primary (one region). With Regula processing in <1 second and Aurora's sub-5ms write latency, cross-region write latency (us-west-2 → eu-west-1 = ~130ms) adds to each response time in the EU region. This is the primary cost of a single-primary model.

Alternatives that are more complex:
- **Multi-primary (active-active):** Write to the nearest region, merge eventual consistency. Extremely complex for relational data with conflict resolution. Not recommended for this workload.
- **Sharding by region:** Users are permanently assigned to a region's database. Simpler than active-active, but cross-region user data is siloed. Works if regional data residency is required anyway.

For a global document verification platform with no data residency requirements, single-primary Aurora Global with regional reads is the right starting point.

---

## Component-by-Component Summary

| Component | Current (lab) | At 1M/day | Change needed |
|---|---|---|---|
| **Frontend** | S3 + CloudFront | S3 + CloudFront | None — scales automatically |
| **ALB** | Single region | Single per region | Provision per regional deployment |
| **Request model** | Synchronous | Async + SQS | Full re-architecture of upload flow |
| **ECS tasks** | 2–6 tasks, CPU scaling | 5–100 tasks, SQS depth scaling | New autoscaling policy, higher max |
| **ECS scaling speed** | 60s symmetric cooldown | 30s scale-out, 300s scale-in | Policy update |
| **Regula licensing** | Transaction-based | Transaction-based (volume tier) | Negotiate volume tier with vendor |
| **S3 (uploads)** | N/A | Temporary upload bucket | New bucket, lifecycle policy, presigned URLs |
| **S3 (images)** | Single region | Multi-region with CRR | Enable Cross-Region Replication |
| **Database** | RDS PostgreSQL `t3.micro` | Aurora Serverless v2 | Migration (no schema changes) |
| **DB scaling** | Multi-AZ standby | Aurora Global + Serverless v2 | Instance → serverless migration |
| **DB partitioning** | None | Monthly range partitioning | Schema migration |
| **DNS routing** | Single region | Route 53 latency-based | Multi-region Route 53 policy |

---

## What This Lab Intentionally Omits

The lab makes several deliberate simplifications that would need to change in production:

- **No WAF on CloudFront or ALB.** Production would require AWS WAF to block malicious uploads, rate-limit abusive clients, and meet compliance requirements.
- **No authentication.** Every API endpoint is public. Production would add Amazon Cognito or an API key system.
- **No dead-letter queue (DLQ).** Messages that fail processing repeatedly should go to a DLQ for manual inspection, not silently drop.
- **CloudWatch logs only.** Production would aggregate logs to a SIEM and set alerts on error rates, queue depth, and processing latency.
- **Single Terraform environment.** Production requires at minimum dev → staging → prod promotion with separate state files and AWS accounts per environment.
