#Variables for the development environment
variable "aws_region" {
  description = "The AWS region to deploy resources in."
  type        = string
  # No default — must be explicit in tfvars to prevent silent wrong-region deployments.
}

variable "environment" {
  description = "Deployment environment name (dev, staging, prod)."
  type        = string
  # No default — must be explicit in tfvars so a missing value fails loudly.
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "project_name" {
  description = "General Project Name"
  type        = string
}

#Networking module variables
variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
}

variable "public_subnet_cidr" {
  description = "List of CIDR blocks for public subnets"
  type        = list(string)
}

variable "private_subnet_cidr" {
  description = "List of CIDR blocks for private subnets"
  type        = list(string)
}

variable "db_subnet_cidr" {
  description = "List of CIDR blocks for DB subnets"
  type        = list(string)
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
}

#Security module variables
variable "app_port" {
  description = "Port of the application - Python API"
  type        = number
}

#Storage module variables
variable "s3_images_bucket_name" {
  description = "S3 Bucket Name for images"
  type        = string
}

variable "db_name" {
  description = "DB name"
  type        = string
}

variable "db_instance_class" {
  description = "DB instance class"
  type        = string
}

variable "db_multi_az" {
  description = "Enable Multi-AZ for RDS"
  type        = bool
}

variable "db_engine" {
  description = "Database engine"
  type        = string
}

variable "db_engine_version" {
  description = "Database engine version"
  type        = string
}

variable "skip_final_snapshot" {
  description = "Skip final snapshot when deleting RDS instance"
  type        = bool
}

variable "deletion_protection" {
  description = "Enable deletion protection for RDS instance"
  type        = bool
}

variable "db_allocated_storage" {
  description = "Allocated storage for RDS in GB"
  type        = number
}

variable "domain_name" {
  description = "Domain name for the SSL certificate"
  type        = string
}

#ECS module variables
variable "ecs_service_task_min" {
  description = "The minimum number of tasks to run in the ECS service."
  type        = number
  default     = 1
}

variable "ecs_service_task_max" {
  description = "The maximum number of tasks to run in the ECS service."
  type        = number
  default     = 10
}

variable "ecs_service_task_desired" {
  description = "The desired number of tasks to run in the ECS service."
  type        = number
  default     = 1
}

variable "autoscaling_cpu_target" {
  description = "Target CPU utilization percentage for autoscaling"
  type        = number
  default     = 70
}

variable "ecs_service_task_cpu" {
  description = "The amount of CPU units to allocate for each task in the ECS service."
  type        = number
  default     = 256
}

variable "ecs_service_task_memory" {
  description = "The amount of memory (in MiB) to allocate for each task in the ECS service."
  type        = number
  default     = 512
}

# ECR image tags — default to "latest" for dev convenience.
# Override via -var flag in CI/CD to pin to a specific git SHA or semver tag.
variable "python_api_ecr_image_tag" {
  description = "ECR image tag for the Python API container."
  type        = string
  default     = "latest"
}

variable "regula_ecr_image_tag" {
  description = "ECR image tag for the Regula SDK container."
  type        = string
  default     = "latest"
}

# The Secrets Manager ARN has an account-specific random suffix and must live in tfvars.
variable "regula_license_secret_arn" {
  description = "Full ARN of the Secrets Manager secret for the Regula license (base64-encoded)."
  type        = string
}

variable "cloudwatch_retention_days" {
  description = "Number of days to retain ECS CloudWatch logs."
  type        = number
  default     = 14
}

#Frontend module variables
variable "s3_frontend_bucket_name" {
  description = "The name of the S3 bucket to store frontend assets."
  type        = string
}

variable "root_domain_name" {
  description = "Root domain name for Route53 hosted zone"
  type        = string
}