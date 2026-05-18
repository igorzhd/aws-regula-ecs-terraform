output "cloudfront_domain_name" {
  description = "CloudFront domain - add as CNAME for domain"
  value       = module.frontend.cloudfront_domain_name
}

output "cloudfront_cert_validation" {
  description = "Add this CNAME for CloudFront cert validation"
  value       = module.frontend.cloudfront_cert_validation_options
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID - used by CI to invalidate cache after frontend deploy"
  value       = module.frontend.cloudfront_distribution_id
}

output "s3_frontend_bucket_name" {
  description = "S3 bucket name for frontend assets - used by CI to sync files after terraform apply"
  value       = module.frontend.s3_frontend_bucket_name
}

output "s3_images_bucket_name" {
  description = "S3 bucket name for document images - used by CI or ops scripts if needed"
  value       = local.s3_images_bucket_name
}