# Challenges & Architectural Decisions

A chronological log of every significant incident, design decision, and hard-won lesson from building this platform. Written to be useful to anyone hitting the same problems — and as an honest record of what production AWS deployments actually involve.

---

## Table of Contents

1. [Infrastructure & Terraform](#1-infrastructure--terraform)
2. [ECS & Containers](#2-ecs--containers)
3. [Networking & DNS](#3-networking--dns)
4. [Application Layer](#4-application-layer)
5. [Architectural Decisions](#5-architectural-decisions)

---

## 1. Infrastructure & Terraform

### 1.1 RDS Tainted State After Connection Timeout During Apply

**What happened:**
A `terraform apply` ran successfully for most resources but timed out while waiting for the RDS instance to become available (RDS Multi-AZ provisioning takes 10–15 minutes). Terraform marked the RDS resource as "tainted," meaning it would destroy and recreate the instance on the next apply.

**Root cause:**
Terraform's apply timeout hit before RDS finished provisioning. The resource was created successfully in AWS, but Terraform's local state marked it as failed/tainted because it never received the completion signal.

**Resolution:**
Verified the RDS instance was healthy in the AWS console (status: `available`), then ran `terraform untaint module.storage.aws_db_instance.main` to clear the taint without destroying the instance. Subsequent `terraform apply` ran cleanly.

**Lesson:**
Tainted state does not always mean the resource is broken — always verify actual AWS resource state before accepting a destroy/recreate. For long-provisioning resources like RDS Multi-AZ, consider using `-parallelism` flags or splitting applies.

---

### 1.2 Target Group Port Mismatch (8000 vs 8001)

**What happened:**
All ALB health checks were failing immediately after deployment. The ECS service was running tasks but the load balancer marked every target as unhealthy, and no traffic was reaching the application.

**Root cause:**
The ALB target group `port` and the ECS task definition `containerPort` used different values. The `app_port` variable was set to `8000` in one place and `8001` in another. The Python API listens on `8001`; the ALB was health-checking port `8000`.

**Resolution:**
Audited all references to the port variable across `terraform.tfvars`, the ECS module, and the ALB target group configuration. Standardized to `8001` everywhere and re-applied.

**Lesson:**
Use a single `app_port` variable threaded through all modules (ALB target group, ECS task definition, ECS container port mapping, health check configuration). Never hardcode port numbers in module logic — they should all reference the same variable.

---

### 1.3 ALB Target Group Deletion Blocked by Listener Dependency

**What happened:**
`terraform destroy` (or a plan that replaced the target group) failed with: `TargetGroupNotEmpty: The target group is currently in use by a listener`. Terraform attempted to delete the target group before deleting the listener that referenced it.

**Root cause:**
Terraform's dependency graph did not automatically infer that the listener must be destroyed before the target group. This happens when the dependency is implicit (the ARN is referenced in the listener) but the graph resolution doesn't order operations correctly in all cases.

**Resolution:**
Added an explicit `depends_on` between the listener and target group resources in the ECS module to force correct destruction order. In some cases, the listener was manually deleted via the AWS console to unblock the Terraform operation.

**Lesson:**
Terraform's implicit dependency resolution works for creation order but can fail for destruction order when resources reference each other via ARNs. Use explicit `depends_on` for resources that have strict teardown ordering requirements.

---

### 1.4 CloudFront `CNAMEAlreadyExists` Error

**What happened:**
A re-run of `terraform apply` after a prior destroy failed with `CNAMEAlreadyExists: One or more of the CNAMEs you provided are already associated with a different resource`. The CloudFront distribution creation was blocked.

**Root cause:**
A previous `terraform apply` had created a CloudFront distribution with the custom domain CNAME. The `terraform destroy` removed the Terraform-managed distribution, but a stale distribution from an earlier manual test (created outside Terraform) still held the CNAME association. CloudFront CNAMEs must be globally unique across all AWS accounts in a region.

**Resolution:**
Listed all CloudFront distributions in the account via `aws cloudfront list-distributions`, identified the orphaned distribution, and deleted it manually. The subsequent `terraform apply` succeeded.

**Lesson:**
Always clean up manually-created AWS resources before switching to Terraform management. CloudFront CNAME conflicts are particularly painful because they span the global CloudFront namespace, not just your account or region.

---

### 1.5 ACM Certificate Validation Stuck

**What happened:**
ACM certificate status remained `Pending validation` for over an hour. The Terraform `aws_acm_certificate_validation` resource was waiting indefinitely, blocking the ALB listener creation and making the entire HTTPS stack unavailable.

**Root cause:**
DNS validation requires adding a specific CNAME record to the domain's DNS zone. The CNAME record existed in Namecheap's DNS panel but was not propagating. Investigation revealed that Namecheap's DNS management interface was intermittently freezing and failing to save changes, silently discarding the record.

**Resolution:**
Migrated the domain's DNS management from Namecheap's nameservers to Route 53 hosted zones. Updated the Namecheap domain settings to use Route 53 nameservers. Re-added the ACM validation CNAME record in Route 53. Certificate validated within minutes.

**Additional discovery:**
A single ACM DNS validation CNAME record can validate the same domain certificate across multiple AWS regions simultaneously. The cert in `us-west-2` (for the ALB) and the cert in `us-east-1` (for CloudFront) share the same validation CNAME — only one DNS record is needed.

**Lesson:**
For any production AWS deployment involving custom domains, use Route 53 for DNS management. Third-party DNS panels introduce reliability risks for time-sensitive operations like certificate validation. The Route 53 migration was necessary work, not optional optimization.

---

### 1.6 ECR Repository Conflict

**What happened:**
`terraform apply` failed on the ECR repository resource with a conflict error. A repository with the same name already existed in the account.

**Root cause:**
The ECR repository had been created manually via AWS CLI before Terraform was introduced to manage it. Terraform attempted to create a new repository but the name was already taken.

**Resolution:**
Imported the existing repository into Terraform state using `terraform import module.ecs.aws_ecr_repository.main <repository-arn>`. Terraform then managed it without conflict.

**Lesson:**
When adopting Terraform for resources that already exist, use `terraform import` rather than destroying and recreating. This is especially important for registries and databases that may already contain data.

---

### 1.7 Terraform State Management — S3 Backend with Native Locking

**Decision:**
Remote Terraform state stored in an S3 bucket with server-side encryption. State locking uses S3's native locking mechanism introduced in Terraform AWS provider v6.0, replacing the previous requirement for a DynamoDB table.

**Why this matters:**
Local state is not suitable for any environment where multiple people or CI/CD pipelines run Terraform. Without remote state, two concurrent `terraform apply` operations can corrupt the state file. S3 native locking (v6+) eliminates the need to provision and manage a separate DynamoDB table for lock records — the lock is stored directly in S3.

**Configuration:**
```hcl
terraform {
  backend "s3" {
    bucket       = "your-terraform-state-bucket"
    key          = "regula-ecs-lab/dev/terraform.tfstate"
    region       = "us-west-2"
    encrypt      = true
    use_lockfile = true   # S3 native locking (provider v6+)
  }
}
```

---

## 2. ECS & Containers

### 2.1 Regula Container OOM — Exit Code 137

**What happened:**
ECS tasks were starting and immediately crashing with exit code 137 (out of memory killed by the OS). The ECS service entered a crash loop, restarting tasks continuously.

**Root cause:**
Regula Document Reader requires significantly more RAM than a typical application container. The process loads document recognition templates and supporting data at startup before it can serve any requests. The initial task definition allocated far too little memory.

**Resolution path:**
- `512 MB` → immediate OOM crash (exit code 137)
- `4096 MB` (4 GB) → still crashing
- `8192 MB` (8 GB) → stable startup and operation

Final configuration: `memory = 8192` (8 GB), `cpu = 2048` (2 vCPU) — set in `terraform.tfvars`.

**Lesson:**
Image size and runtime memory requirement are different numbers. Check vendor documentation or test locally with `docker stats` before writing a Fargate task definition. The ECS task definition memory value is a hard ceiling — the container is killed the moment it exceeds it.

**Connection to cost incident:**
While investigating this, the task crash loop was generating Docker Hub pull traffic through the NAT Gateway. Each OOM restart triggered a full image pull from Docker Hub through NAT. The total NAT egress accumulated from the frequency of restarts across the 3-hour window — not from an unusually large image, but from many repeated pulls with no VPC endpoints in place. See [section 2.5](#25-nat-gateway-cost-incident--ecs-crash-loop-and-unexpected-data-transfer) for the full incident writeup.

---

### 2.2 Python API Startup Failure — RDS Endpoint Format

**What happened:**
The Python API container started but immediately failed with a database connection error. The error message referenced an incorrect hostname format.

**Root cause:**
Terraform's `aws_db_instance` resource exposes two different attributes:
- `endpoint` — includes the port (e.g., `mydb.xxxx.us-west-2.rds.amazonaws.com:5432`)
- `address` — hostname only (e.g., `mydb.xxxx.us-west-2.rds.amazonaws.com`)

The ECS task definition was passing `module.storage.rds_db_instance_endpoint` (which includes the port) as the `DB_HOST` environment variable. SQLAlchemy's connection string builder then appended the port again, resulting in a double-port in the connection string that PostgreSQL rejected.

**Resolution:**
Changed the Terraform output to use `.address` instead of `.endpoint` for the hostname variable passed to ECS. The port is specified separately in the connection string.

**Lesson:**
Read Terraform resource documentation carefully for attributes that look similar. `endpoint` and `address` are different outputs from `aws_db_instance`. When constructing database connection strings programmatically, use the hostname-only attribute.

---

### 2.3 Secrets Manager / SSM Access Denied for ECS Execution Role

**What happened:**
ECS tasks were failing to start with an access denied error when attempting to fetch database credentials from SSM Parameter Store. The tasks could not proceed past the credential injection phase.

**Root cause:**
The ECS Task Execution Role (the role ECS infrastructure uses to pull images and inject secrets) was missing the `ssm:GetParameters` and `ssm:GetParameter` permissions for the specific SSM parameter paths used by the application.

There is an important distinction between two ECS IAM roles:
- **Execution Role** — used by the ECS agent and Fargate infrastructure to: pull images from ECR, write logs to CloudWatch, and fetch secrets from SSM/Secrets Manager before the container starts. This role must have SSM permissions.
- **Task Role** — used by the application code running inside the container at runtime (e.g., to read/write S3). This role must have S3 permissions.

The Execution Role had ECR and CloudWatch permissions but was missing SSM access. The SSM permissions were incorrectly assigned only to the Task Role.

**Resolution:**
Added an IAM policy attachment to the Execution Role granting `ssm:GetParameters` scoped to the specific parameter path prefix (`/regula/dev/*`).

**Lesson:**
The execution role vs. task role distinction is fundamental to ECS security architecture. Before container startup: Execution Role. After container startup (runtime): Task Role. The ECS documentation describes this clearly, but it's easy to mix up when first configuring task definitions.

---

### 2.4 Two-Container Task Design — Python API and Regula on localhost

**Decision:**
The ECS Task Definition runs two containers in the same task: `python-api` and `regula-docreader`. Within a single ECS task, all containers share a network namespace, meaning they can communicate via `localhost`.

**Why this matters:**
The Python API forwards document images to Regula via HTTP (`http://localhost:8080`). If these were separate ECS services with separate task definitions, the API would need to discover and call Regula via the ALB or service discovery, introducing latency, additional network hops, and a more complex security group configuration.

Running them in the same task keeps the Regula service completely private: it is never exposed to the ALB, never registered with a target group, and never reachable from outside the ECS task. The only client is the Python API container in the same task.

**Trade-off:**
The two containers scale together. If you needed to scale the Python API independently from Regula, or run multiple API instances against a single Regula pool, you would need separate services. For this workload, same-task colocation is the right choice.

---

### 2.5 NAT Gateway Cost Incident — ECS Crash Loop and Unexpected Data Transfer

**What happened:**
Immediately after `terraform apply`, ECS launched 2 Fargate tasks with `desired_count = 2`. The tasks crashed with exit code 137 (OOM) almost immediately because the task definition allocated only 512 MB — far below what Regula DocReader requires at runtime. ECS's default restart policy kept relaunching the tasks automatically. Each restart triggered a full pull of `regulaforensics/docreader:latest` from Docker Hub through the NAT Gateway. Two tasks restarting continuously over ~3 hours accumulated approximately 100 GB of NAT data transfer on an environment with zero user traffic.

**Timeline:**

| Time | Event |
|---|---|
| T+0 | `terraform apply` completes. ECS launches 2 tasks immediately with `desired_count = 2`. |
| T+~5m | Each task pulls `regulaforensics/docreader:latest` (~1 GB) from Docker Hub through NAT. |
| T+~10m | Both tasks OOM-crash. ECS restarts automatically. |
| T+ongoing | Restart loop: OOM crash → ECS restart → full Docker Hub image re-pull → OOM crash. Repeating. |
| T+3h | `terraform destroy` halts the environment. |
| T+24h | AWS bill shows ~100 GB NAT Gateway data processing — a few dollars actual cost, but a clear signal that something was badly wrong. |

**Root causes:**

1. **`regulaforensics/docreader:latest` pulled from Docker Hub** — not from ECR. Fargate has no local image cache; every task restart pulls the full image. The ~1 GB image itself is not large, but frequency (dozens of restarts) created significant volume.
2. **Task memory severely under-provisioned** — `512 MB` caused immediate OOM crash. Required memory: 8 GB. The crash loop turned a one-time pull cost into a continuous one.
3. **No VPC Interface Endpoints** — all AWS API traffic (ECR auth, SSM, CloudWatch Logs, ECS agent heartbeats) routed through NAT in addition to the Docker Hub pulls.
4. **"No user traffic" ≠ "no data transfer"** — AWS infrastructure generates significant traffic automatically at task start and continuously (health check logs, ECS agent heartbeats, SSM credential fetches on every restart).

**Resolution:**
- Pushed the Regula image to private ECR. Task definition changed to reference ECR URL via `var.regula_docreader_image`.
- Set `memory = 8192`, `cpu = 2048` in `terraform.tfvars` — eliminates the crash loop.
- Added VPC Interface Endpoints for `ecr.api`, `ecr.dkr`, `ssm`, `ssmmessages`, `logs`.
- Removed private subnet default route to NAT Gateway. Private subnets now have local-only routing.
- NAT Gateways retained for Regula license validation (DocReader requires outbound internet access at container startup for its license check).

**Lessons:**
- **`desired_count` is immediate.** Tasks launch at apply time and pull images right away. Infrastructure cost begins at first `terraform apply`, not first user request.
- **Crash loops amplify one-time costs into continuous costs.** A single image pull becomes dozens when OOM restarts are not bounded by correct memory sizing.
- **Enable AWS Cost Anomaly Detection before deploying.** A $10 alert threshold would have triggered within the first hour.
- **Never reference a public registry image in a task definition.** Mirror to ECR first. See [section 5.8](#58-nat-gateway-vs-vpc-interface-endpoints) for the architectural decision that followed.

---

## 3. Networking & DNS

### 3.1 Namecheap DNS Panel Freezing — Migration to Route 53

**What happened:**
Multiple attempts to add CNAME records to Namecheap's DNS panel for ACM certificate validation were failing silently. The UI would appear to save the record, but the record was not present when verified via `dig` or third-party DNS lookup tools. The Namecheap panel was also intermittently freezing during the record-add workflow.

**Resolution:**
Created a Route 53 hosted zone for the domain. Updated the Namecheap domain's nameserver settings to point to Route 53's nameservers (four NS records). From that point forward, all DNS changes (ACM validation records, A records for CloudFront, CNAME for the API subdomain) were managed in Route 53.

**Lesson:**
Route 53 is the correct DNS provider for AWS-hosted applications. The operational reliability difference matters most when you are waiting for time-sensitive DNS propagation (like certificate validation). Third-party DNS panels are fine for static personal domains; for production AWS infrastructure, use Route 53.

---

### 3.2 CloudFront Path Routing — Missing API Routes

**What happened:**
After deploying CloudFront, the frontend loaded correctly but API calls were failing with 403 or 404 errors. The `/api/*` path was routing to the ALB, but `/health`, `/process`, and `/sessions` paths were not.

**Root cause:**
The CloudFront distribution was configured with a behavior for `/api/*` path pattern routing to the ALB origin, with all other paths falling back to the S3 frontend origin. The Python API's routes (`/health`, `/process`, `/sessions`) were not prefixed with `/api/` — they were defined at the root path.

Two options: add `/api/` prefix to all API routes in the application code, or update the CloudFront path patterns to include `/health`, `/process*`, and `/sessions*` as additional behaviors pointing to the ALB origin.

**Resolution:**
Updated the CloudFront distribution to include explicit path-based behaviors for each API route pattern pointing to the ALB origin. The S3 origin serves only the frontend files.

**Lesson:**
CloudFront path-based routing requires exhaustive pattern definition. Unlike an ALB where you can define a catch-all rule, CloudFront's default behavior is the S3 origin, and every API route must be explicitly listed. Design the API path structure with CloudFront routing in mind from the start.

---

### 3.3 ACM Certificate Covering Wrong Domain

**What happened:**
The ALB HTTPS listener was created with a certificate, but browsers were showing SSL warnings. Investigation revealed the certificate was issued for the root domain (`example.com`) instead of the API subdomain (`api.example.com`).

**Root cause:**
The initial certificate resource in Terraform used `var.root_domain_name` as the `domain_name` argument. CloudFront requires a separate certificate that covers `api.example.com` (the subdomain used as a CloudFront CNAME), not the root domain.

**Resolution:**
Updated the ACM certificate resource to use `"api.${var.domain_name}"` as the domain name. Re-ran `terraform apply` to issue a new certificate and re-attach it to the ALB listener.

**Lesson:**
Map out all domains and subdomains before creating certificates. `example.com` and `api.example.com` are different domains from ACM's perspective. A wildcard cert (`*.example.com`) would have covered both, but DNS validation for wildcard certs has additional requirements.

---

### 3.4 HTTPS Between CloudFront and ALB

**What happened:**
CloudFront was serving HTTPS to browsers correctly, but the connection from CloudFront to the ALB origin was using HTTP, creating a mixed-security path.

**Root cause:**
CloudFront's origin protocol policy was set to `http-only` for the ALB origin, meaning CloudFront would accept HTTPS from viewers but communicate with the ALB over HTTP.

**Resolution:**
Updated the CloudFront origin to use `https-only` protocol policy for the ALB origin. This required the ALB to have a valid certificate (which it did, for `api.example.com`). Configured the origin's `ssl_protocols` to match the TLS policy on the ALB listener.

**Lesson:**
Full end-to-end HTTPS requires configuring both the viewer-facing protocol (CloudFront → browser) and the origin-facing protocol (CloudFront → ALB). These are independent settings and both default to HTTP unless explicitly configured.

---

### 3.5 Dual NAT Gateway Design for AZ Independence

**Decision:**
Two NAT Gateways are deployed — one per Availability Zone — with AZ-local routing (each private subnet routes outbound traffic through the NAT Gateway in the same AZ).

**Why not one NAT Gateway:**
A single NAT Gateway creates an AZ dependency. If the NAT Gateway's AZ becomes unavailable, all private subnet tasks in the other AZ lose outbound internet access. With dual NAT Gateways, an AZ failure only affects traffic from that AZ — the other AZ's tasks continue operating.

**Cost consideration:**
Two NAT Gateways at ~$32/month each = ~$64/month baseline. This is the cost of true AZ independence for outbound traffic. The alternative (single NAT) saves $32/month but introduces a hidden availability dependency.

---

## 4. Application Layer

### 4.1 Frontend `config.js` Pointing to localhost in Production

**What happened:**
After deploying the full stack, the frontend UI loaded via CloudFront but all API calls were failing. Browser developer tools showed requests going to `http://localhost:8001` instead of `https://api.example.com`.

**Root cause:**
The `services/frontend/config.js` file contained a hardcoded `API_BASE_URL = "http://localhost:8001"` that was committed and deployed as-is to S3. The production deployment pipeline was copying the development configuration to the production bucket.

**Resolution:**
Updated `config.js` to use the production API domain. Added a CloudFront cache invalidation step to the CI/CD pipeline to force CloudFront to fetch the updated file from S3 on the next request.

**Lesson:**
Configuration files that differ between environments should never be hardcoded for the development environment and committed to version control. Use environment-specific files (`.env`, injected at deploy time) or build-time substitution. For a static site, at minimum, the CI/CD pipeline should update or verify the config before syncing to S3.

---

### 4.2 CloudFront Cache Serving Stale `config.js`

**What happened:**
After fixing `config.js` and syncing the new version to S3, the frontend was still loading the old (localhost) configuration. Clearing browser cache had no effect — the stale file was being served by CloudFront's edge cache.

**Root cause:**
CloudFront caches responses from its origin (S3) at edge locations. Updating the file in S3 does not automatically evict cached copies from CloudFront's edge nodes. The default cache TTL meant the old file would be served for hours or until the cache expired.

**Resolution:**
Ran a CloudFront cache invalidation via AWS CLI:
```bash
aws cloudfront create-invalidation \
  --distribution-id <distribution-id> \
  --paths "/*"
```

Added this step permanently to the GitHub Actions deployment pipeline, running after every S3 sync operation.

**Lesson:**
Any deployment pipeline that syncs files to an S3 origin behind CloudFront must include a cache invalidation step. Without it, deployments appear successful but users continue receiving stale content. The invalidation step is not optional — it is part of the definition of a complete deployment.

---

## 5. Architectural Decisions

### 5.1 Multi-AZ Architecture Design

**Decision:**
Full Multi-AZ deployment across two availability zones: two public subnets (ALB), two private subnets (ECS tasks), two DB subnets (RDS standby), two NAT Gateways.

**What this required understanding:**
The difference between RDS Multi-AZ and RDS read replicas is commonly misunderstood. Multi-AZ deploys a synchronous standby replica in a second AZ for automatic failover — it is a high availability mechanism, not a read scaling mechanism. The standby is not readable; it only becomes the primary if the primary fails. Read replicas are asynchronous, manually promoted, and designed for read scale-out. For a lab environment prioritizing HA over read throughput, Multi-AZ is the correct choice.

---

### 5.2 ECS Fargate vs EKS

**Decision:** ECS Fargate.

**Rationale:**
EKS provides more control and is appropriate for large-scale microservice deployments with complex scheduling requirements. For a two-service workload (Python API + Regula), the Kubernetes control plane overhead — node groups, CoreDNS, kube-proxy, API server, etcd — is not justified. ECS Fargate eliminates the control plane entirely: no nodes to patch, no cluster infrastructure to manage. Task definitions are simpler than Pod specs for straightforward workloads.

**What Fargate does not provide:**
Custom kernel parameters, DaemonSets, privileged containers, or granular node-level scheduling. None of these were required here.

---

### 5.3 Terraform Module Organization

**Decision:**
Five reusable modules — `networking`, `security`, `iam`, `storage`, `ecs`, `frontend` — each owning a clear domain boundary, called from a single environment entry point.

**Challenge encountered:**
Circular dependency in security group rules. The ALB security group needs to allow traffic to the ECS security group; the ECS security group needs to accept traffic from the ALB security group. Defining these as inline rules within the `aws_security_group` resource creates a circular reference Terraform cannot resolve.

**Resolution:**
Separated security group creation from rule definition. Created `aws_security_group` resources (with no inline rules) in one step, then attached rules via separate `aws_security_group_rule` resources. This allows the groups to be created first, then cross-referencing rules added after both groups exist.

**Additional decision:**
Used the enterprise pattern of `variables.tf` + `terraform.tfvars` for environment configuration rather than using `locals` throughout. This is more verbose but makes it explicit which values can differ per environment, and it matches the pattern used in real-world team environments.

---

### 5.4 Multi-Region Provider Configuration

**Challenge:**
CloudFront distributions require ACM certificates in `us-east-1` (hardcoded AWS requirement). All other resources in this project are in `us-west-2`. A single-region Terraform configuration cannot create resources in two regions.

**Solution:**
Terraform provider aliases. The `providers.tf` in the environment entry point defines two AWS provider instances:
```hcl
provider "aws" {
  region = "us-west-2"   # default
}
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"   # for CloudFront ACM cert
}
```

The `frontend` module accepts both providers and uses the `us_east_1` alias specifically for the ACM certificate resource. The `module` block in `main.tf` passes both providers explicitly:
```hcl
module "frontend" {
  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }
}
```

**Lesson:**
Multi-region Terraform is common in real AWS deployments (CloudFront, Route 53, and IAM are always `us-east-1`). Provider aliases are the correct Terraform pattern — not separate state files for this use case.

---

### 5.5 Secrets Management

**Decision:**
Database credentials stored in SSM Parameter Store as `SecureString` type. ECS task definitions reference the parameters via the `secrets` block, which instructs the ECS agent to fetch and inject the values as environment variables at container startup. Credentials never appear in Terraform state, code, or `.tfvars` files.

**Why SSM over Secrets Manager:**
AWS Secrets Manager supports automatic rotation and is the recommended choice for production systems requiring credential lifecycle management. SSM Parameter Store with `SecureString` is simpler and lower-cost for a lab environment where credential rotation is not required. The ECS injection pattern is identical for both services.

**Why not environment variables in the task definition:**
Plaintext environment variables in ECS task definitions are visible in the AWS console and in Terraform state. Any secret passed as a plaintext env var is effectively stored in multiple places. The `secrets` block in the task definition stores only a reference to the SSM parameter path — the actual value is injected by the ECS agent at runtime and is never persisted.

---

### 5.6 VPC Gateway Endpoint vs NAT for S3

**Decision:**
S3 access from private subnets uses a VPC Gateway Endpoint (free), not the NAT Gateway ($0.045/GB).

**Why this is the right default:**
The S3 Gateway Endpoint routes traffic to S3 through the AWS internal network, bypassing the NAT Gateway entirely. It is free (no hourly charge, no per-GB charge), and there is no reason not to use it for any workload that accesses S3 from private subnets. This was one of the few architectural choices that had no trade-off.

The ECS tasks write document crop images and Regula output to S3. Without the endpoint, every image write and every Regula JSON write would have been billed at $0.045/GB through the NAT Gateway. For an application processing document images at scale, this cost would be significant.

---

### 5.7 CloudFront Origin Access Control (OAC)

**Decision:**
CloudFront uses Origin Access Control (OAC) instead of the legacy Origin Access Identity (OAI) to access the S3 frontend bucket.

**Why:**
OAI was CloudFront's original mechanism for private S3 access, but AWS deprecated it in favor of OAC. OAC supports additional S3 features (SSE-KMS, all S3 operations), uses SigV4 signing, and is the current AWS-recommended approach. The S3 bucket policy allows `s3:GetObject` only when the request comes from the specific CloudFront distribution, blocking all direct S3 URL access.

---

### 5.8 NAT Gateway vs VPC Interface Endpoints

**Context:**
The NAT Gateway cost incident ([section 2.5](#25-nat-gateway-cost-incident--ecs-crash-loop-and-unexpected-data-transfer)) drove an architectural review of whether NAT Gateways should be replaced with VPC Interface Endpoints for all private-to-AWS-service traffic.

**Options considered:**

**Option A — Keep NAT Gateway (original architecture)**
- Simple, flexible: private subnets can reach any internet endpoint
- ECS can pull images from Docker Hub or any public registry
- Risk: uncontrolled outbound data paths, difficult to audit what leaves the VPC

**Option B — Replace NAT with VPC Interface Endpoints + push images to ECR**
- Private subnets have zero path to the public internet
- All AWS service traffic stays within the AWS network
- Third-party images (DocReader) mirrored to private ECR
- Added operational responsibility: image update lifecycle must be managed

**Option C — Hybrid: keep NAT, add VPC endpoints alongside**
- Reduces per-GB data cost for AWS services
- Still maintains internet egress path (Docker Hub, etc.)
- More expensive: NAT hourly charge + endpoint hourly charge running simultaneously

**Cost analysis:**

| Configuration | Baseline (monthly) | Per-GB cost |
|---|---|---|
| 2 × NAT Gateway | ~$65/mo | $0.045/GB |
| 5 endpoints × 2 AZs | ~$72/mo | $0.01/GB |
| Hybrid (both) | ~$137/mo | $0.045/GB NAT, $0.01/GB endpoints |

VPC endpoints become cheaper than NAT at approximately 200 GB/month, where the per-GB savings offset the ~$7/month endpoint premium. For dev/lab volumes, the primary driver is security architecture, not cost.

**Decision: Option B**

1. **Security posture** — Private subnets with no internet egress is the correct enterprise default. Any outbound internet path is a potential data exfiltration vector and must be explicitly justified.
2. **Supply chain security** — Pulling from Docker Hub at runtime introduces availability risk (rate limits, outages) and supply chain attack surface (compromised upstream images). Images mirrored to private ECR are scanned on push (`scan_on_push = true`) and pinned to a specific digest.
3. **Enterprise standard** — No production environment pulls images from public registries at runtime. Images are pulled once by a pipeline, scanned, pushed to a private registry, and referenced by digest.
4. **Architecture coherence** — The S3 Gateway Endpoint was already in place (free, keeps S3 traffic off NAT). Adding interface endpoints for ECR, SSM, and CloudWatch Logs completes the pattern consistently.

**Trade-offs accepted:**

| Trade-off | Mitigation |
|---|---|
| Image update lifecycle is now manual or CI/CD-managed | Automate: pull → scan → push to ECR as part of the deployment pipeline |
| VPC endpoints add ~$72/month vs ~$65/month for NAT at low volume | Justified by security benefit; cost advantage reverses at scale |
| Private subnets cannot reach arbitrary internet endpoints | Use public subnets for debug tooling; keep private subnet isolation |

**Note:** NAT Gateways were retained in the final architecture because Regula DocReader requires outbound internet access for license validation at container startup. The primary cost controls are: mirroring the image to ECR (eliminates Docker Hub pulls through NAT) and correct memory sizing (eliminates the crash loop).
