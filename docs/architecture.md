# Architecture Deep Dive

---

## Network Topology

### VPC Design

The VPC uses a three-tier subnet design spread across two Availability Zones — six subnets total.

```
VPC: 10.0.0.0/16

Public Subnets (2 × AZ):
  10.0.1.0/24  — public_subnet_1 (us-west-2a)
  10.0.2.0/24  — public_subnet_2 (us-west-2b)

Private Subnets (2 × AZ):
  10.0.10.0/24 — private_subnet_1 (us-west-2a)
  10.0.11.0/24 — private_subnet_2 (us-west-2b)

DB Subnets (2 × AZ):
  10.0.20.0/24 — db_subnet_1 (us-west-2a)
  10.0.21.0/24 — db_subnet_2 (us-west-2b)
```

**Why three tiers:**
Each tier has a different trust level and routing requirement.

- **Public subnets** have a default route to the Internet Gateway. Only the ALB lives here — it needs to be reachable from the internet (via CloudFront) and from CloudFront's origin IP ranges.
- **Private subnets** have a default route to the NAT Gateway. ECS tasks live here: they need outbound internet for ECR image pulls and CloudWatch log shipping, but must not be directly reachable from the internet.
- **DB subnets** have no default route. RDS has no outbound internet path. The database is reachable only from the private subnets (ECS tasks), enforced by the RDS security group.

### Routing

| Subnet tier | Outbound route | Inbound access |
|---|---|---|
| Public | Internet Gateway (0.0.0.0/0) | Internet (ALB on ports 80/443 only) |
| Private | NAT Gateway (AZ-local, 0.0.0.0/0) | ALB security group on app port only |
| DB | None (local VPC only) | Private subnet security group on port 5432 only |

**AZ-local NAT routing:**
Each private subnet's route table points to the NAT Gateway in the same Availability Zone. This ensures that if one AZ fails, ECS tasks in the other AZ continue routing through their local NAT Gateway without cross-AZ traffic.

### VPC Endpoints

| Endpoint | Type | Purpose |
|---|---|---|
| S3 Gateway Endpoint | Gateway | Free S3 access from private/DB subnets; avoids NAT Gateway per-GB charges |

The S3 endpoint is attached to both the private route tables and the DB route table. All S3 traffic (ECS writing images and Regula output) stays within the AWS network and does not consume NAT Gateway capacity.

---

## Traffic Flows

### User Request (Document Upload)

```
Browser
  → HTTPS → CloudFront edge node (nearest PoP)
  → HTTPS → ALB (public subnet, port 443)
  → HTTP  → ECS task: python-api container (private subnet, port 8001)
  → HTTP  → regula-docreader container (localhost:8080, same task)
  ← JSON  ← regula-docreader returns recognition result
  → S3    → python-api stores crop images (via S3 Gateway Endpoint)
  → RDS   → python-api stores session record (DB subnet, port 5432)
  ← JSON  ← python-api returns structured response to browser
```

### Static Frontend Request

```
Browser
  → HTTPS → CloudFront edge node
  → (cache hit) ← Served from CloudFront edge cache
  → (cache miss) → S3 bucket (OAC-authenticated request)
```

CloudFront path routing rules:
- `/api/*`, `/health`, `/process*`, `/sessions*` → ALB origin
- Everything else (`/`, `/*.html`, `/*.css`, `/*.js`) → S3 origin

### ECS Task Startup Sequence

```
ECS control plane
  → Instructs Fargate to launch task
  → ECS Execution Role authenticates to ECR (via VPC endpoint or NAT)
  → Pulls python-api image from ECR (private registry)
  → Pulls regula-docreader image from ECR (private registry, ~30 GB)
  → Execution Role fetches /regula/dev/db_username from SSM
  → Execution Role fetches /regula/dev/db_password from SSM (SecureString)
  → Containers start; credentials injected as environment variables
  → python-api connects to RDS on startup (connection pool initialization)
  → Both containers report healthy to ALB target group via /health
```

---

## Security Model

### IAM Roles

Two separate IAM roles are used per ECS task.

**ECS Execution Role** (`ecsTaskExecutionRole`-equivalent):
Used by the ECS agent and Fargate infrastructure. Active before and during container startup. Permissions:
- `ecr:GetAuthorizationToken` — authenticate to ECR
- `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` — pull container images
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` — write to CloudWatch
- `ssm:GetParameters` (scoped to `/regula/dev/*`) — fetch credentials at startup

**ECS Task Role** (application role):
Used by the application code running inside the container. Active at runtime. Permissions:
- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` (scoped to the images bucket) — document image storage

**Why the separation matters:**
The Execution Role has access to credentials and container infrastructure. The Task Role has access to application data. If the application is compromised, the blast radius is limited to the S3 bucket — not to ECR, SSM, or CloudWatch. If the Execution Role were used for application access, a compromised container could potentially read all SSM parameters or push to ECR.

### Security Groups

| Security Group | Inbound | Outbound |
|---|---|---|
| ALB SG | 80, 443 from 0.0.0.0/0 | App port to ECS SG |
| ECS SG | App port from ALB SG | All outbound (to NAT/S3/RDS) |
| RDS SG | 5432 from ECS SG only | None |

The RDS security group accepts connections only from the ECS security group. No other source (including the ALB, or direct developer access) can reach the database.

### Secrets

Database credentials are stored in SSM Parameter Store as `SecureString` (KMS-encrypted). They are referenced by path in the ECS task definition's `secrets` block:

```json
"secrets": [
  {
    "name": "DB_USERNAME",
    "valueFrom": "arn:aws:ssm:us-west-2:<account>:parameter/regula/dev/db_username"
  },
  {
    "name": "DB_PASSWORD",
    "valueFrom": "arn:aws:ssm:us-west-2:<account>:parameter/regula/dev/db_password"
  }
]
```

The actual values are injected by the ECS agent at container startup. They are never stored in Terraform state, application code, or the task definition JSON in ECR.

---

## High Availability Design

| Component | HA mechanism | Failover behavior |
|---|---|---|
| ALB | Deployed across both public subnets; routes only to healthy targets | Unhealthy tasks removed from rotation within 60 seconds (2 health check failures × 30s interval) |
| ECS tasks | `desired_count` tasks distributed across private subnets in both AZs | ECS service scheduler replaces failed tasks automatically; auto scaling adjusts count under load |
| RDS | Multi-AZ standby in second AZ; synchronous replication | Automatic failover in ~60–120 seconds on primary failure; no data loss (synchronous) |
| NAT Gateways | One per AZ with AZ-local routing | AZ failure isolates outbound traffic loss to that AZ only; other AZ unaffected |
| CloudFront | AWS-managed global edge network | No single point of failure; edge failures automatically route to alternate PoPs |

### Auto Scaling

ECS Application Auto Scaling with target tracking policy:
- **Metric:** ECS service CPU utilization
- **Target:** 70% CPU
- **Min tasks:** configurable via `ecs_service_task_min`
- **Max tasks:** configurable via `ecs_service_task_max`
- **Scale-out:** Add tasks when CPU exceeds target for 3 minutes
- **Scale-in:** Remove tasks when CPU drops below target for 15 minutes (cool-down period)

Note: Each task contains both the python-api and regula-docreader containers. Scaling adds full task pairs, not individual containers.

---

## Multi-Region Configuration

Two AWS regions are used:

| Region | Resources |
|---|---|
| `us-west-2` | All compute and data: VPC, ECS, RDS, S3, ALB, ECR, SSM |
| `us-east-1` | CloudFront ACM certificate only (AWS requirement) |

The `us-west-2` ALB has its own ACM certificate (`api.example.com`) in `us-west-2`. CloudFront requires a separate certificate in `us-east-1` for the same domain — these are two distinct certificates validated by the same DNS CNAME record.

Terraform provider aliases handle the multi-region requirement without separate state files. See [`terraform.md`](terraform.md) for configuration details.

---

## Cost Model

Approximate monthly costs for a running dev environment (us-west-2, 2026 pricing):

| Resource | Configuration | Estimated monthly |
|---|---|---|
| ECS Fargate | 2 tasks × 2 vCPU × 8 GB, 24/7 | ~$120 |
| RDS PostgreSQL | `db.t3.micro`, Multi-AZ, 20 GB | ~$30 |
| NAT Gateway | 2 × AZ, ~10 GB/month data | ~$70 |
| ALB | ~10 LCUs | ~$20 |
| CloudFront | ~1 GB/month | ~$5 |
| S3 | 2 buckets, minimal storage | ~$2 |
| ECR | ~35 GB stored | ~$3.50 |
| **Total** | | **~$250/month** |

**Cost management practice:**
Run `terraform destroy` when not actively testing. The S3 backend state and ECR images persist; all compute and data resources are destroyed. Re-deploying from `terraform apply` takes approximately 15–20 minutes (RDS provisioning is the bottleneck).

The largest single-day cost incident was the 100 GB NAT Gateway charge described in [`postmortem-nat-gateway.md`](postmortem-nat-gateway.md).
