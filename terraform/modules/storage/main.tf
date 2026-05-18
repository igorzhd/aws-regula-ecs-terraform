# --- S3 Bucket for images ---

#Creating S3 bucket
resource "aws_s3_bucket" "s3_images" {
  bucket = var.s3_images_bucket_name

  # force_destroy = true allows terraform destroy to delete the bucket even when it
  # contains objects. Required for lab teardown. Set to false in production to prevent
  # accidental data loss — AWS will reject the delete if the bucket is non-empty.
  force_destroy = true
}

#Blocking public access to the S3 bucket
resource "aws_s3_bucket_public_access_block" "s3_images_public_access_block" {
  bucket = aws_s3_bucket.s3_images.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

#S3 Lifecycle configuration to expire objects after 90 days
resource "aws_s3_bucket_lifecycle_configuration" "s3_images_lifecycle_configuration" {
  bucket = aws_s3_bucket.s3_images.id

  rule {
    id     = "ExpireOldImages"
    status = "Enabled"

    expiration {
      days = 90
    }
  }
}

# --- RDS Enhanced Monitoring role ---

# RDS Enhanced Monitoring sends OS-level metrics (CPU steal, swap, I/O wait) to CloudWatch.
# The monitoring agent runs inside the DB host and needs its own IAM role — separate from
# the task role — because it calls CloudWatch on behalf of the RDS service, not your application.
resource "aws_iam_role" "rds_monitoring_role" {
  name = "${var.project_name}-${var.environment}-rds-monitoring-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring_attachment" {
  role       = aws_iam_role.rds_monitoring_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# --- RDS database ---

#Creating RDS subnet group
resource "aws_db_subnet_group" "main_db_subnet_group" {
  name       = "${var.project_name}-${var.environment}-db-subnet-group"
  subnet_ids = var.db_subnet_ids

  tags = {
    Name = "${var.project_name}-${var.environment}-db-subnet-group"
  }
}

#Creating RDS database instance
resource "aws_db_instance" "rds_db_instance" {
  allocated_storage   = var.db_allocated_storage
  db_name             = var.db_name
  engine              = var.db_engine
  engine_version      = var.db_engine_version
  instance_class      = var.db_instance_class
  username            = var.db_username
  password            = var.db_password
  skip_final_snapshot = var.skip_final_snapshot
  deletion_protection = var.deletion_protection

  # Network
  db_subnet_group_name   = aws_db_subnet_group.main_db_subnet_group.name
  vpc_security_group_ids = [var.rds_sg_id]

  # High availability
  multi_az = var.db_multi_az

  # Security
  storage_encrypted          = true
  auto_minor_version_upgrade = true # Apply minor engine patches automatically during the maintenance window.

  # Backups
  backup_retention_period = 7
  copy_tags_to_snapshot   = true # Snapshot inherits the same Project/Environment tags for cost attribution.

  # Observability — Enhanced Monitoring (OS-level metrics: CPU steal, I/O wait, swap).
  # monitoring_interval = 0 disables it; 60 means a datapoint every 60 seconds.
  monitoring_interval = var.monitoring_interval
  monitoring_role_arn = aws_iam_role.rds_monitoring_role.arn

  # Performance Insights — query-level metrics (top SQL, wait events).
  # Free for db.t3 instances (7-day retention); invaluable for diagnosing slow queries.
  performance_insights_enabled = true

  tags = {
    Name = "${var.project_name}-${var.environment}-rds"
  }
}