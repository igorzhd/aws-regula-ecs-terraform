data "aws_caller_identity" "current" {}

locals {
  # S3 bucket names get the account ID appended to guarantee global uniqueness.
  # The base name comes from tfvars; the suffix is resolved at plan time.
  s3_images_bucket_name   = "${var.s3_images_bucket_name}-${data.aws_caller_identity.current.account_id}"
  s3_frontend_bucket_name = "${var.s3_frontend_bucket_name}-${data.aws_caller_identity.current.account_id}"

  # ECR registry URL built from account + region so account IDs never
  # appear in module code — only in this environment-level file.
  ecr_registry     = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
  python_api_image = "${local.ecr_registry}/${var.project_name}/python-api:${var.python_api_ecr_image_tag}"
  regula_image     = "${local.ecr_registry}/${var.project_name}/regula-sdk:${var.regula_ecr_image_tag}"
}
