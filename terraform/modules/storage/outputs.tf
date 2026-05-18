output "s3_images_arn" {
  description = "ARN of S3 images bucket"
  value       = aws_s3_bucket.s3_images.arn
}

output "rds_db_instance_endpoint" {
  description = "Endpoint of the RDS database instance"
  value       = aws_db_instance.rds_db_instance.address
}