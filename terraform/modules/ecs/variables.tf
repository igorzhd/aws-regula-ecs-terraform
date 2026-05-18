variable "ecs_service_task_min" {
  description = "The minimum number of tasks to run in the ECS service."
  type        = number
  default     = 2
}

variable "ecs_service_task_max" {
  description = "The maximum number of tasks to run in the ECS service."
  type        = number
  default     = 4
}

variable "ecs_service_task_desired" {
  description = "The desired number of tasks to run in the ECS service."
  type        = number
  default     = 2
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

variable "ecs_execution_role_arn" {
  description = "The ARN of the IAM role that the ECS service tasks will use for execution."
  type        = string
}

variable "ecs_task_role_arn" {
  description = "The ARN of the IAM role that the ECS service tasks will use for task-level permissions."
  type        = string
}

variable "vpc_id" {
  description = "The ID of the VPC where the ECS service will be deployed."
  type        = string
}

variable "public_subnet_ids" {
  description = "A list of subnet IDs where the ALB will be deployed."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "A list of subnet IDs where the ECS service tasks will be deployed."
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "The ID of the security group to associate with the ALB"
  type        = string
}

variable "ecs_security_group_id" {
  description = "The ID of the security group to associate with the ECS service tasks."
  type        = string
}

variable "app_port" {
  description = "The port on which the application will listen inside the ECS service tasks."
  type        = number
  default     = 8000
}

variable "rds_host" {
  description = "The hostname of the RDS database that the ECS service tasks will connect to."
  type        = string
}

variable "rds_port" {
  description = "The port number of the RDS database that the ECS service tasks will connect to."
  type        = number
  default     = 5432
}

variable "rds_db_name" {
  description = "The name of the RDS database that the ECS service tasks will connect to."
  type        = string
}

variable "s3_images_bucket_name" {
  description = "The name of the S3 bucket where the ECS service tasks will store images."
  type        = string
}

variable "project_name" {
  description = "The name of the project."
  type        = string
}

variable "environment" {
  description = "The deployment environment (e.g., dev, staging, prod)."
  type        = string
}

variable "domain_name" {
  description = "Domain name for the SSL certificate"
  type        = string
}

variable "root_domain_name" {
  description = "Root domain name for the Route53 hosted zone (e.g. example.com)"
  type        = string
}

variable "cloudwatch_retention_days" {
  description = "The number of days to retain CloudWatch logs."
  type        = number
  default     = 14
}

# Full ECR image URIs are built in the environment's locals.tf (account ID + region + tag)
# and passed in here so no account IDs are hardcoded in module code.
variable "python_api_image" {
  description = "Full ECR image URI for the Python API container, including tag."
  type        = string
}

variable "regula_image" {
  description = "Full ECR image URI for the Regula SDK container, including tag."
  type        = string
}

# The Secrets Manager ARN contains an account-specific random suffix so it can't be
# constructed programmatically — it lives in tfvars and is passed in here.
variable "regula_license_secret_arn" {
  description = "ARN of the Secrets Manager secret containing the base64-encoded Regula license."
  type        = string
}