#Create S3 bucket for website
resource "aws_s3_bucket" "s3_frontend_bucket" {
  bucket = var.s3_frontend_bucket_name

  # force_destroy = true allows terraform destroy to delete the bucket even when it contains
  # frontend build artifacts. Required for lab teardown. Set to false in production.
  force_destroy = true

  tags = {
    Name        = "Frontend Bucket"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_policy" "s3_frontend_bucket_policy" {
  bucket = aws_s3_bucket.s3_frontend_bucket.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.s3_frontend_bucket.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.frontend_distribution.arn
          }
        }
      }
    ]
  })
}

resource "aws_s3_bucket_public_access_block" "s3_frontend_bucket_public_access_block" {
  bucket = aws_s3_bucket.s3_frontend_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "main_oac" {
  name                              = "${var.project_name}-${var.environment}-oac"
  description                       = "OAC for S3 frontend bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

#Create ACM certificate for CloudFront (separate, as us-east-1 region is required for CloudFront)
resource "aws_acm_certificate" "cloudfront_cert" {
  provider          = aws.us_east_1
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-cloudfront-cert"
  }
}

#This resource polls ACM until the cert reaches ISSUED — Terraform blocks here until that happens.
#The CloudFront distribution references this resource's certificate_arn, not the cert directly,
resource "aws_acm_certificate_validation" "cloudfront_cert" {
  provider        = aws.us_east_1
  certificate_arn = aws_acm_certificate.cloudfront_cert.arn
}

# AWS managed cache policies — look up by name so IDs are not hardcoded.
# Using managed policies is the current recommended approach; the older forwarded_values
# inline syntax is deprecated and will be removed in a future provider version.
data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized" # Compresses objects, respects Cache-Control from S3.
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled" # Passes every request through — required for API paths.
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  # Forwards all viewer headers, cookies, and query strings to the ALB origin,
  # but strips the Host header so CloudFront sets its own (the ALB domain).
  name = "Managed-AllViewerExceptHostHeader"
}

#Create CloudFront distribution for frontend (2 origins: S3 bucket for static assets and ALB for API)
resource "aws_cloudfront_distribution" "frontend_distribution" {
  origin {
    domain_name = aws_s3_bucket.s3_frontend_bucket.bucket_regional_domain_name
    origin_id   = "S3CloudFrontOrigin"

    origin_access_control_id = aws_cloudfront_origin_access_control.main_oac.id
  }

  origin {
    domain_name = "api.${var.domain_name}"
    origin_id   = "ALBOrigin"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  enabled             = true
  is_ipv6_enabled     = true
  aliases             = [var.domain_name]
  comment             = "${var.project_name} ${var.environment} Frontend Distribution"
  default_root_object = "index.html"

  default_cache_behavior {
    target_origin_id       = "S3CloudFrontOrigin"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = ["GET", "HEAD", "OPTIONS"]
    cached_methods  = ["GET", "HEAD"]

    # Managed-CachingOptimized: compresses objects and respects Cache-Control / ETag
    # headers returned by S3, giving good cache hit rates for static assets.
    cache_policy_id = data.aws_cloudfront_cache_policy.caching_optimized.id
  }

  # All ALB-routed paths share the same pattern: no caching + forward everything.
  # Managed-CachingDisabled ensures every request reaches the API.
  # Managed-AllViewerExceptHostHeader forwards all headers/cookies/query strings
  # but lets CloudFront set the Host header (ALB requires this for its own routing).

  ordered_cache_behavior {
    path_pattern           = "/api/*"
    target_origin_id       = "ALBOrigin"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD"]

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  ordered_cache_behavior {
    path_pattern           = "/health"
    target_origin_id       = "ALBOrigin"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = ["GET", "HEAD"]
    cached_methods  = ["GET", "HEAD"]

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  ordered_cache_behavior {
    path_pattern           = "/process"
    target_origin_id       = "ALBOrigin"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD"]

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  ordered_cache_behavior {
    path_pattern           = "/sessions*"
    target_origin_id       = "ALBOrigin"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD"]

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  price_class = var.price_class
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # Reference the validation resource, not the cert directly — this guarantees
    # the cert is in ISSUED state before CloudFront tries to attach it.
    # Using aws_acm_certificate.arn here would let CloudFront be created with a
    # PENDING_VALIDATION cert, causing an immediate deployment failure.
    acm_certificate_arn      = aws_acm_certificate_validation.cloudfront_cert.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = {
    Name        = "${var.project_name}-${var.environment}-frontend-distribution"
    Environment = var.environment
  }

  depends_on = [
    aws_acm_certificate_validation.cloudfront_cert #Ensure distribution is created after cert is validated, or it will fail due to cert not being ready.
  ]
}

#Adding A alieas record in Route53 for the CloudFront distribution
data "aws_route53_zone" "main" {
  name = var.root_domain_name
}

resource "aws_route53_record" "cloudfront_alias" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.frontend_distribution.domain_name
    zone_id                = aws_cloudfront_distribution.frontend_distribution.hosted_zone_id
    evaluate_target_health = false
  }
}