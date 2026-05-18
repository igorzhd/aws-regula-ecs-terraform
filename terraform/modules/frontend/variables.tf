variable "project_name" {
  description = "The name of the project."
  type        = string
}

variable "environment" {
  description = "The environment (e.g., dev, staging, prod)."
  type        = string
}

variable "s3_frontend_bucket_name" {
  description = "The name of the S3 bucket to store frontend assets."
  type        = string
}

variable "alb_dns_name" {
  description = "The DNS name of the Application Load Balancer."
  type        = string
}

variable "default_ttl" {
  description = "The default TTL for CloudFront distributions."
  type        = number
  default     = 3600
}

variable "max_ttl" {
  description = "The maximum TTL for CloudFront distributions."
  type        = number
  default     = 86400
}

variable "min_ttl" {
  description = "The minimum TTL for CloudFront distributions."
  type        = number
  default     = 0
}

variable "price_class" {
  description = "The price class for CloudFront distributions."
  type        = string
  default     = "PriceClass_100"
}

variable "domain_name" {
  description = "The domain name for the frontend application."
  type        = string
}

variable "root_domain_name" {
  description = "Root domain name for Route53 hosted zone"
  type        = string
}