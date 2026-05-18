module "networking" {
  source = "../../modules/networking"

  vpc_cidr            = var.vpc_cidr
  public_subnet_cidr  = var.public_subnet_cidr
  private_subnet_cidr = var.private_subnet_cidr
  db_subnet_cidr      = var.db_subnet_cidr
  availability_zones  = var.availability_zones
}

module "security" {
  source = "../../modules/security"

  vpc_id             = module.networking.vpc_main_vpc_id
  app_port           = var.app_port
  private_subnet_ids = module.networking.private_subnet_ids
  project_name       = var.project_name
  environment        = var.environment
}

#get db credentials from SSM Parameter Store — path uses var.environment so this works in staging/prod unchanged
data "aws_ssm_parameter" "db_username" {
  name = "/regula/${var.environment}/db_username"
}

data "aws_ssm_parameter" "db_password" {
  name            = "/regula/${var.environment}/db_password"
  with_decryption = true
}

module "storage" {
  source = "../../modules/storage"

  project_name          = var.project_name
  environment           = var.environment
  s3_images_bucket_name = local.s3_images_bucket_name
  db_name               = var.db_name
  db_username           = data.aws_ssm_parameter.db_username.value
  db_password           = data.aws_ssm_parameter.db_password.value
  db_instance_class     = var.db_instance_class
  db_subnet_ids         = module.networking.db_subnet_ids
  rds_sg_id             = module.security.rds_sg
  skip_final_snapshot   = var.skip_final_snapshot
  deletion_protection   = var.deletion_protection
  db_multi_az           = var.db_multi_az
  db_engine             = var.db_engine
  db_engine_version     = var.db_engine_version
  db_allocated_storage  = var.db_allocated_storage
}

module "iam" {
  source = "../../modules/iam"

  project_name              = var.project_name
  environment               = var.environment
  s3_image_bucket_arn       = module.storage.s3_images_arn
  regula_license_secret_arn = var.regula_license_secret_arn
}

module "ecs" {
  source = "../../modules/ecs"

  project_name              = var.project_name
  environment               = var.environment
  vpc_id                    = module.networking.vpc_main_vpc_id
  public_subnet_ids         = module.networking.public_subnet_ids
  private_subnet_ids        = module.networking.private_subnet_ids
  ecs_security_group_id     = module.security.ecs_sg
  alb_security_group_id     = module.security.alb_sg
  ecs_execution_role_arn    = module.iam.ecs_execution_role_arn
  ecs_task_role_arn         = module.iam.ecs_task_role_arn
  rds_host                  = module.storage.rds_db_instance_endpoint
  rds_db_name               = var.db_name
  s3_images_bucket_name     = local.s3_images_bucket_name
  app_port                  = var.app_port
  domain_name               = var.domain_name
  root_domain_name          = var.root_domain_name
  ecs_service_task_min      = var.ecs_service_task_min
  ecs_service_task_max      = var.ecs_service_task_max
  ecs_service_task_desired  = var.ecs_service_task_desired
  autoscaling_cpu_target    = var.autoscaling_cpu_target
  ecs_service_task_cpu      = var.ecs_service_task_cpu
  ecs_service_task_memory   = var.ecs_service_task_memory
  python_api_image          = local.python_api_image
  regula_image              = local.regula_image
  regula_license_secret_arn = var.regula_license_secret_arn
  cloudwatch_retention_days = var.cloudwatch_retention_days
  rds_port                  = 5432
}

module "frontend" {
  source = "../../modules/frontend"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  project_name            = var.project_name
  environment             = var.environment
  s3_frontend_bucket_name = local.s3_frontend_bucket_name
  alb_dns_name            = module.ecs.alb_dns_name
  domain_name             = var.domain_name
  root_domain_name        = var.root_domain_name
}

