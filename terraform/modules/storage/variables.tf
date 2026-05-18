#General variables
variable "project_name" {
  description = "Project Name"
  type        = string
}

variable "environment" {
  description = "Environment Name"
  type        = string
}

#S3 variables
variable "s3_images_bucket_name" {
  description = "S3 Bucket Name for images"
  type        = string
}

# --- Database variables ---

variable "db_name" {
  description = "DB name"
  type        = string
}

variable "db_username" {
  description = "DB username"
  type        = string
}

variable "db_password" {
  description = "DB password"
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "DB instance class"
  type        = string
}

variable "db_subnet_ids" {
  description = "List of DB subnet IDs for RDS subnet group"
  type        = list(string)
}

variable "rds_sg_id" {
  description = "RDS security group ID"
  type        = string
}

variable "db_multi_az" {
  description = "Enable Multi-AZ for RDS"
  type        = bool
}

#what database engine & version to use
variable "db_engine" {
  description = "Database engine"
  type        = string
  default     = "postgres"
}

variable "db_engine_version" {
  description = "Database engine version"
  type        = string
  default     = "16"
}

#Snapshot & protention settings
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
  default     = 20
}

# RDS Enhanced Monitoring — sends OS-level metrics (CPU steal, swap, I/O) to CloudWatch
# every monitoring_interval seconds. 0 = disabled. Use 60 for production.
variable "monitoring_interval" {
  description = "RDS Enhanced Monitoring interval in seconds (0 disables it). Use 60 for production."
  type        = number
  default     = 60
}