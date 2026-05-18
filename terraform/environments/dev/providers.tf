terraform {
  required_version = "~> 1.14"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Replace the bucket name with your own S3 state bucket (see docs/terraform.md → "Remote State").
  # Create it once before the first `terraform init`:
  #   aws s3 mb s3://your-terraform-state-bucket --region us-west-2
  #   aws s3api put-bucket-versioning --bucket your-terraform-state-bucket --versioning-configuration Status=Enabled
  backend "s3" {
    bucket       = "your-terraform-state-bucket"
    key          = "regula-ecs-lab/dev/terraform.tfstate"
    region       = "us-west-2"
    encrypt      = true
    use_lockfile = true
  }
}

# default_tags applies Project/Environment/ManagedBy to every AWS resource automatically,
# including resources that don't expose a tags argument (e.g. route table associations).
# Note: provider blocks can't reference var.*, so these must be literal values per environment.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "regula-ecs-lab"
      Environment = "dev"
      ManagedBy   = "Terraform"
    }
  }
}

# CloudFront ACM certificates must be issued in us-east-1 regardless of the deployment region.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "regula-ecs-lab"
      Environment = "dev"
      ManagedBy   = "Terraform"
    }
  }
}