# Terraform Reference

---

## Module Structure

```
terraform/
├── environments/
│   └── dev/
│       ├── main.tf           # Module instantiation and wiring
│       ├── variables.tf      # Variable declarations
│       ├── terraform.tfvars  # Actual values for this environment
│       ├── outputs.tf        # Outputs consumed by CI/CD
│       └── providers.tf      # AWS provider + us-east-1 alias
└── modules/
    ├── networking/           # VPC, subnets, gateways, route tables, S3 endpoint
    ├── security/             # Security groups and rules
    ├── iam/                  # ECS execution role, task role, policies
    ├── storage/              # RDS PostgreSQL instance, S3 buckets
    ├── ecs/                  # ECS cluster, task definition, service, ALB, ACM, autoscaling
    └── frontend/             # CloudFront distribution, OAC, Route 53 records
```

Each module owns a distinct AWS resource domain and exposes outputs used by other modules through the environment entry point. Modules do not call each other directly — all cross-module references go through `environments/dev/main.tf`.

---

## Module Responsibilities

### `networking`
- VPC with DNS support and hostnames enabled
- Public subnets (×2, one per AZ) — ALB placement
- Private subnets (×2, one per AZ) — ECS task placement
- DB subnets (×2, one per AZ) — RDS placement
- Internet Gateway
- NAT Gateways (×2, one per AZ) with Elastic IPs
- Route tables: public (→ IGW), private (→ AZ-local NAT), DB (local only)
- S3 VPC Gateway Endpoint (free, attached to private and DB route tables)

**Key outputs:** `vpc_main_vpc_id`, `public_subnet_ids`, `private_subnet_ids`, `db_subnet_ids`

### `security`
- ALB security group: inbound 80/443 from internet, outbound to ECS
- ECS security group: inbound app port from ALB, outbound all
- RDS security group: inbound 5432 from ECS SG only

Uses `aws_security_group_rule` resources (not inline rules) to avoid circular dependency between ALB and ECS security groups. See [challenges-and-decisions.md](challenges-and-decisions.md#53-terraform-module-organization).

**Key outputs:** `alb_sg`, `ecs_sg`, `rds_sg`

### `iam`
- ECS Execution Role with policies for: ECR image pull, CloudWatch Logs write, SSM parameter read
- ECS Task Role with policies for: S3 object read/write on the images bucket
- IAM role-policy attachments

**Key outputs:** `ecs_execution_role_arn`, `ecs_task_role_arn`

### `storage`
- RDS PostgreSQL instance (Multi-AZ, encrypted at rest)
- RDS subnet group using DB subnets
- S3 bucket for document crop images (private, no public access)
- S3 bucket for frontend static files (private, served via CloudFront OAC)

**Key outputs:** `rds_db_instance_endpoint`, `rds_db_instance_address`, `s3_images_arn`, `s3_frontend_bucket_name`

### `ecs`
- ECS cluster
- ECS task definition: two containers (`python-api` + `regula-docreader`) in the same task
- ECS service with placement across both private subnets
- Application Load Balancer in public subnets
- ALB target group with health checks on `/health`
- ALB HTTP listener (redirects to HTTPS)
- ALB HTTPS listener with ACM certificate
- ACM certificate for `api.<domain>` with DNS validation
- Route 53 records for ACM validation
- Application Auto Scaling: target tracking on CPU utilization

**Key outputs:** `alb_dns_name`, `alb_arn`

### `frontend`
- CloudFront distribution with two origins: S3 (frontend) and ALB (API)
- CloudFront OAC (Origin Access Control) for private S3 access
- S3 bucket policy allowing CloudFront OAC only
- Route 53 A record (alias) pointing domain to CloudFront

Uses the `aws.us_east_1` provider alias for the ACM certificate resource (CloudFront requires certs in us-east-1).

**Key outputs:** `cloudfront_distribution_id`, `cloudfront_domain_name`

---

## Remote State

Terraform state is stored remotely in S3 with native state locking (Terraform AWS provider v6+).

```hcl
terraform {
  backend "s3" {
    bucket       = "your-terraform-state-bucket"
    key          = "regula-ecs-lab/dev/terraform.tfstate"
    region       = "us-west-2"
    encrypt      = true
    use_lockfile = true
  }
}
```

`use_lockfile = true` uses S3's native locking mechanism introduced in provider v6.0. This replaces the previous requirement for a DynamoDB table for lock management. The lock is stored as a `.tflock` file in the same S3 bucket.

The state bucket itself must be created before the first `terraform init`. It is not managed by this Terraform configuration (bootstrapping problem — Terraform cannot manage the bucket it stores its own state in).

---

## Multi-Region Providers

```hcl
# providers.tf
provider "aws" {
  region = "us-west-2"
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}
```

The `frontend` module requires both providers because CloudFront ACM certificates must be in `us-east-1`. The module is called with explicit provider assignments:

```hcl
module "frontend" {
  source = "../../modules/frontend"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }
  # ...
}
```

Inside `modules/frontend/providers.tf`:
```hcl
terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1]
    }
  }
}
```

---

## Deployment Prerequisites

Before running `terraform apply` for the first time:

### 1. Create S3 state bucket (one-time)
```bash
aws s3 mb s3://your-terraform-state-bucket --region us-west-2
aws s3api put-bucket-versioning \
  --bucket your-terraform-state-bucket \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption \
  --bucket your-terraform-state-bucket \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

### 2. Store database credentials in SSM
```bash
aws ssm put-parameter \
  --name "/regula/dev/db_username" \
  --value "your_db_username" \
  --type String \
  --region us-west-2

aws ssm put-parameter \
  --name "/regula/dev/db_password" \
  --value "your_db_password" \
  --type SecureString \
  --region us-west-2
```

### 3. Mirror Regula image to ECR
The Regula DocReader image must be in a private ECR repository before ECS can pull it. ECR repositories are created by Terraform; the image must be pushed after the ECR resource is created.

```bash
# Apply only the ECR repository resource first
cd terraform/environments/dev
terraform apply -target=module.ecs.aws_ecr_repository.docreader

# Authenticate Docker to ECR
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin \
  <account-id>.dkr.ecr.us-west-2.amazonaws.com

# Pull from Docker Hub (requires ~30 GB disk space and time)
docker pull regulaforensics/docreader:latest

# Tag and push to ECR
docker tag regulaforensics/docreader:latest \
  <account-id>.dkr.ecr.us-west-2.amazonaws.com/regula-ecs-lab-dev-docreader:latest
docker push \
  <account-id>.dkr.ecr.us-west-2.amazonaws.com/regula-ecs-lab-dev-docreader:latest
```

Update `terraform.tfvars`:
```hcl
regula_docreader_image = "<account-id>.dkr.ecr.us-west-2.amazonaws.com/regula-ecs-lab-dev-docreader:latest"
```

### 4. Deploy via GitHub Actions (recommended)

Push your code to GitHub, then:
1. **Actions → Plan → Run workflow** — validates and runs `terraform plan`, posts the full diff to the job summary
2. Copy the Run ID from the Plan run URL (the number at the end, e.g. `12345678`)
3. Review the plan output in the job summary
4. **Actions → Apply → Run workflow** — paste the Run ID, click Run
5. Apply downloads the exact saved plan and applies it; then deploys frontend and checks health

**Manual deploy (local):**
```bash
cd terraform/environments/dev
terraform init
terraform plan -out=tfplan
terraform show tfplan        # review
terraform apply tfplan
```

---

## Key Variables

| Variable | Description | Example value |
|---|---|---|
| `project_name` | Used in all resource name prefixes | `regula-ecs-lab` |
| `environment` | Environment tag | `dev` |
| `vpc_cidr` | VPC CIDR block | `10.0.0.0/16` |
| `availability_zones` | AZs to deploy into | `["us-west-2a", "us-west-2b"]` |
| `domain_name` | Full domain for API (e.g. `api.example.com`) | `api.example.com` |
| `root_domain_name` | Root domain for Route 53 zone | `example.com` |
| `regula_docreader_image` | ECR URI for Regula image | `<account>.dkr.ecr.us-west-2.amazonaws.com/...:latest` |
| `ecs_service_task_memory` | Memory per task in MB | `16384` |
| `ecs_service_task_cpu` | CPU units per task (1024 = 1 vCPU) | `4096` |
| `db_instance_class` | RDS instance type | `db.t3.medium` |
| `db_multi_az` | Enable RDS Multi-AZ | `true` |
| `skip_final_snapshot` | Skip final snapshot on RDS destroy | `true` (dev only) |

---

## Teardown

**Preferred: GitHub Actions destroy workflow** — go to Actions → Destroy → Run workflow. Type `DESTROY` to confirm. The workflow runs `terraform plan -destroy` first so you can see exactly what will be deleted before approving.

**Manual teardown:**
```bash
cd terraform/environments/dev
terraform destroy
```

Notes:
- ECR images are preserved after destroy (not managed by Terraform in this project)
- S3 state bucket and SSM parameters are not managed by this config — they persist
- RDS: `skip_final_snapshot = true` in `terraform.tfvars` — no automatic backup taken on destroy. Take a manual snapshot first if you need the data.

---

## Operational Notes

**Tainted resources:**
If Terraform marks a resource as tainted (visible in `terraform plan` as `-/+ destroy and then create replacement`), verify the resource state in AWS console before accepting the plan. For long-provisioning resources like RDS, a taint usually means the resource is healthy but Terraform's local record marked it as failed. Use `terraform untaint <resource-address>` to clear the taint without destroying the resource.

**Import existing resources:**
If resources were created outside Terraform and you want Terraform to manage them:
```bash
terraform import module.ecs.aws_ecr_repository.main <repository-arn>
```

**Partial applies:**
Use `-target` to apply or destroy specific resources during debugging:
```bash
terraform apply -target=module.ecs.aws_ecs_service.main
terraform destroy -target=module.storage.aws_db_instance.main
```

**Recovering from a failed GitHub Actions apply:**
If the Apply workflow fails partway through, some resources may have been created and written to state while others were not. Do not re-run Apply with the same plan — the state has changed and you will get "Saved plan is stale."

Recovery steps:
1. Run the **Plan** workflow — Terraform reads the current state and only plans what is still missing
2. Review the new plan (it will be smaller — already-created resources won't appear)
3. Run **Apply** with the new plan Run ID
