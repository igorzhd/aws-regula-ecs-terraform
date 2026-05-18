variable "vpc_id" {
  description = "ID of the VPC where security groups will be created."
  type        = string
}

variable "app_port" {
  description = "Port of the application"
  type        = number
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for VPC Interface Endpoints."
  type        = list(string)
}

# Required for namespacing security group names to prevent collisions when multiple
# environments (dev, staging, prod) are deployed into the same AWS account.
variable "project_name" {
  description = "Project name — prepended to all security group names."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod) — included in security group names."
  type        = string
}