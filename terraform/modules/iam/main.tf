data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# --- ECS Execution ---

#Create IAM role for ECS execution
resource "aws_iam_role" "ecs_execution_role" {
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
  name = "${var.project_name}-${var.environment}-ecs-execution-role"
  tags = {
    Name = "${var.project_name}-${var.environment}-ecs-execution-role"
  }
}

#Attaching default ECS execution role policy (allows ECR & CloudWatch access)
resource "aws_iam_role_policy_attachment" "ecs_execution_ecr-cloudwatch_access_attachment" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Scoped SSM policy — grants read access only to this project's parameters.
# AmazonSSMReadOnlyAccess (the managed policy) grants access to ALL parameters in the account,
# which violates least-privilege and would expose parameters from other projects or environments.
resource "aws_iam_policy" "ecs_execution_ssm_policy" {
  name        = "${var.project_name}-${var.environment}-ecs-execution-ssm-policy"
  description = "Allows ECS execution role to read only DB credentials from SSM for this environment."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:GetParameters"]
      Resource = "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/regula/${var.environment}/*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_ssm_access_attachment" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = aws_iam_policy.ecs_execution_ssm_policy.arn
}

# --- ECS Task ---

# Create IAM role for ECS task
resource "aws_iam_role" "ecs_task_role" {
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
  name = "${var.project_name}-${var.environment}-ecs-task-role"
  tags = {
    Name = "${var.project_name}-${var.environment}-ecs-task-role"
  }
}

#Create policy for ECS task role to allow access to S3 bucket for images
resource "aws_iam_policy" "ecs_task_s3_access_policy" {
  name        = "${var.project_name}-${var.environment}-ecs-task-s3-access-policy"
  description = "Policy to allow ECS tasks to access S3 bucket for images"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          var.s3_image_bucket_arn,
          "${var.s3_image_bucket_arn}/*"
        ]
      }
    ]
  })
}

#Attach the S3 access policy to the ECS task role
resource "aws_iam_role_policy_attachment" "ecs_task_s3_access_attachment" {
  role       = aws_iam_role.ecs_task_role.name
  policy_arn = aws_iam_policy.ecs_task_s3_access_policy.arn
}

#For ECS Task access to RDS we use SSM, not policy

# Scoped Secrets Manager policy — grants access only to the Regula license secret.
# The ARN is passed in from the environment (tfvars) rather than hardcoded here
# so the module works across accounts without modification.
resource "aws_iam_policy" "ecs_execution_secretsmanager_policy" {
  name        = "${var.project_name}-${var.environment}-ecs-execution-secretsmanager-policy"
  description = "Allows ECS execution role to read the Regula license secret from Secrets Manager."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.regula_license_secret_arn]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_secretsmanager_attachment" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = aws_iam_policy.ecs_execution_secretsmanager_policy.arn
}