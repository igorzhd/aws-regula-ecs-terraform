#General variables
project_name = "regula-ecs-lab"
environment  = "dev"
aws_region   = "us-west-2"

#Variables for Networking module
vpc_cidr            = "10.0.0.0/16"
public_subnet_cidr  = ["10.0.1.0/24", "10.0.2.0/24"]
private_subnet_cidr = ["10.0.3.0/24", "10.0.4.0/24"]
db_subnet_cidr      = ["10.0.5.0/24", "10.0.6.0/24"]
availability_zones  = ["us-west-2a", "us-west-2b"]

#Variables for Security module
app_port = 8001

#Variables for Storage module
# Base name only — AWS account ID is appended automatically via locals.tf
s3_images_bucket_name = "regula-document-verification-images-dev"

db_name              = "regula_images_db"
db_instance_class    = "db.t3.micro"
db_engine            = "postgres"
db_engine_version    = "16"
db_allocated_storage = 20

db_multi_az         = true
skip_final_snapshot = true
deletion_protection = false

root_domain_name = "your-api-domain.com" #change to your domain name
domain_name      = "regula-ecs-lab.your-api-domain.com" #change to your subdomain

#Variables for ECS module
ecs_service_task_min      = 2
ecs_service_task_max      = 6
ecs_service_task_desired  = 2
autoscaling_cpu_target    = 70
ecs_service_task_cpu      = 2048
ecs_service_task_memory   = 8192
cloudwatch_retention_days = 14

# ECR image tags — override via -var in CI/CD to deploy a specific version.
# "latest" is acceptable for dev; pin to a git SHA or semver tag in staging/prod.
python_api_ecr_image_tag = "latest"
regula_ecr_image_tag     = "latest"

# Create the secret with: aws secretsmanager create-secret --name Regula_license --secret-string file://regula.license --region us-west-2
# The ARN is printed in the output; copy it here. The random suffix (e.g. -1Yxcg0) is assigned by AWS at creation time.
regula_license_secret_arn = "arn:aws:secretsmanager:us-west-2:<YOUR_ACCOUNT_ID>:secret:Regula_license-XXXXXX"

# Base name only — AWS account ID is appended automatically via locals.tf
s3_frontend_bucket_name = "regula-ecs-lab-frontend-dev"
