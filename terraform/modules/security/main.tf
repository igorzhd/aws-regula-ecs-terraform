#Security groups for ALB
resource "aws_security_group" "alb_sg" {
  # Namespaced with project+env to prevent name collision if multiple environments
  # (dev, staging, prod) are deployed into the same AWS account.
  name        = "${var.project_name}-${var.environment}-alb-sg"
  description = "Security group for ALB"
  vpc_id      = var.vpc_id
  tags = {
    Name = "${var.project_name}-${var.environment}-alb-sg"
  }
}

#ALB inbound traffic from HTTP from anywhere
resource "aws_security_group_rule" "alb_sg_http_ingress" {
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.alb_sg.id
}

#ALB inbound traffic from HTTPS from anywhere
resource "aws_security_group_rule" "alb_sg_https_ingress" {
  type              = "ingress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.alb_sg.id
}

#ALB outbound traffic to ECS SG & Application Port only
resource "aws_security_group_rule" "alb_sg_ecs_egress" {
  type                     = "egress"
  from_port                = var.app_port
  to_port                  = var.app_port
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs_sg.id
  security_group_id        = aws_security_group.alb_sg.id
}

#Security group for ECS tasks
resource "aws_security_group" "ecs_sg" {
  name        = "${var.project_name}-${var.environment}-ecs-sg"
  description = "Security group for ECS tasks"
  vpc_id      = var.vpc_id
  tags = {
    Name = "${var.project_name}-${var.environment}-ecs-sg"
  }
}

#ECS inbound traffic from ALB SG only and app_port only
resource "aws_security_group_rule" "ecs_sg_alb_ingress" {
  type                     = "ingress"
  from_port                = var.app_port
  to_port                  = var.app_port
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb_sg.id
  security_group_id        = aws_security_group.ecs_sg.id
}

#ECS outbound traffic to RDS SG only and PostgreSQL port only
resource "aws_security_group_rule" "ecs_sg_rds_egress" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.rds_sg.id
  security_group_id        = aws_security_group.ecs_sg.id
}

#ECS outbound HTTPS to AWS services — traffic is routed to VPC Interface Endpoints, not the internet
resource "aws_security_group_rule" "ecs_sg_https_egress" {
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.ecs_sg.id
}

data "aws_region" "current" {}

#Security group for VPC Interface Endpoints
resource "aws_security_group" "vpc_endpoint_sg" {
  name        = "${var.project_name}-${var.environment}-vpc-endpoint-sg"
  description = "Security group for VPC Interface Endpoints (ECR, SSM, CloudWatch Logs)"
  vpc_id      = var.vpc_id
  tags = {
    Name = "${var.project_name}-${var.environment}-vpc-endpoint-sg"
  }
}

#Allow HTTPS inbound from ECS tasks only
resource "aws_security_group_rule" "vpc_endpoint_sg_https_ingress" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs_sg.id
  security_group_id        = aws_security_group.vpc_endpoint_sg.id
}

#VPC Interface Endpoint — ECR API (authentication before image pull)
resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoint_sg.id]
  private_dns_enabled = true
  tags = {
    Name = "ecr-api-endpoint"
  }
}

#VPC Interface Endpoint — ECR DKR (image layer pulls)
resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoint_sg.id]
  private_dns_enabled = true
  tags = {
    Name = "ecr-dkr-endpoint"
  }
}

#VPC Interface Endpoint — SSM (Parameter Store for DB credentials at task startup)
resource "aws_vpc_endpoint" "ssm" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.ssm"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoint_sg.id]
  private_dns_enabled = true
  tags = {
    Name = "ssm-endpoint"
  }
}

#VPC Interface Endpoint — SSM Messages (required alongside SSM for full parameter store functionality)
resource "aws_vpc_endpoint" "ssmmessages" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.ssmmessages"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoint_sg.id]
  private_dns_enabled = true
  tags = {
    Name = "ssmmessages-endpoint"
  }
}

#VPC Interface Endpoint — CloudWatch Logs (ECS awslogs log driver)
resource "aws_vpc_endpoint" "logs" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoint_sg.id]
  private_dns_enabled = true
  tags = {
    Name = "logs-endpoint"
  }
}

#Security group for RDS instance
resource "aws_security_group" "rds_sg" {
  name        = "${var.project_name}-${var.environment}-rds-sg"
  description = "Security group for RDS instance"
  vpc_id      = var.vpc_id
  tags = {
    Name = "${var.project_name}-${var.environment}-rds-sg"
  }
}

#RDS inbound traffic from ECS SG only and PostgreSQL port only
resource "aws_security_group_rule" "rds_sg_ecs_ingress" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs_sg.id
  security_group_id        = aws_security_group.rds_sg.id
}