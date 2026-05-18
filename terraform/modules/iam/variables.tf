variable "project_name" {
  description = "Project Name"
  type        = string
}

variable "environment" {
  description = "Environment Name"
  type        = string
}

variable "s3_image_bucket_arn" {
  description = "ARN of the S3 bucket for storing images"
  type        = string
}

# Passed in from the environment rather than hardcoded in the module so the same
# module works across accounts and the ARN (which contains account ID) stays in tfvars.
variable "regula_license_secret_arn" {
  description = "ARN of the Secrets Manager secret containing the base64-encoded Regula license."
  type        = string
}