data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

#Create an Application Load Balancer
resource "aws_lb" "main_alb" {
  name               = "${var.project_name}-${var.environment}-alb"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [var.alb_security_group_id]
  subnets            = var.public_subnet_ids

  # Lab only — set to true in production to prevent accidental deletion via the
  # console or a mistaken terraform destroy. Keeping it false here so the lab
  # environment can be torn down cleanly without extra steps.
  enable_deletion_protection = false

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

#Create ALB target group for ECS service tasks & configure health checks
resource "aws_lb_target_group" "alb_target_group" {
  name        = "${var.project_name}-${var.environment}-alb-tg"
  port        = var.app_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip" #ECS tasks will register with the target group using their IP addresses

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
    matcher             = "200-299"
    port                = var.app_port
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

#Redirect ALB HTTP traffic to HTTPS
resource "aws_lb_listener" "alb_http_listener" {
  load_balancer_arn = aws_lb.main_alb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301"
    }
  }
}

#Forward ALB HTTPS traffic to the ALB target group
resource "aws_lb_listener" "alb_https_listener" {
  load_balancer_arn = aws_lb.main_alb.arn
  port              = 443
  protocol          = "HTTPS"

  ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn = aws_acm_certificate_validation.main_cert.certificate_arn #reference the validation resource, not the cert directly — this blocks listener creation until the cert is ISSUED

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.alb_target_group.arn
  }
}

#Create ACM certificate for the ALB
resource "aws_acm_certificate" "main_cert" {
  domain_name       = "api.${var.domain_name}"
  validation_method = "DNS"

  tags = {
    Name = "${var.project_name}-${var.environment}-cert"
  }

  lifecycle {
    create_before_destroy = true
  }
}

#Wait for ACM to finish validating the cert before anything tries to use it.
#aws_acm_certificate just requests the cert and returns immediately (status: PENDING_VALIDATION).
#This resource polls ACM until the cert reaches ISSUED — Terraform blocks here until that happens.
#The HTTPS listener references this resource's certificate_arn, not the cert directly,
#which creates a real data dependency that depends_on can't give you.
#DNS validation record to be added manually in DNS provider.
resource "aws_acm_certificate_validation" "main_cert" {
  certificate_arn = aws_acm_certificate.main_cert.arn
}

#Create CloudWatch log group for ECS service tasks with a retention policy to manage log storage costs
resource "aws_cloudwatch_log_group" "ecs_log_group" {
  name              = "/aws/ecs/${var.project_name}-${var.environment}"
  retention_in_days = var.cloudwatch_retention_days #Keep logs for the specified number of days to balance troubleshooting needs with storage costs

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

#Create ECS cluster to run the Python API & Regula applications
resource "aws_ecs_cluster" "main_ecs_cluster" {
  name = "${var.project_name}-${var.environment}-ecs-cluster"

  # Container Insights enables per-task CPU, memory, and network metrics in CloudWatch.
  # Without this, you only get cluster-level aggregates, which are not enough for
  # debugging runaway tasks or sizing individual services.
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

#Create ECS task definition for the application, including environment variables for RDS and S3 connectivity, and configure logging to CloudWatch
resource "aws_ecs_task_definition" "ecs_task_definition" {
  family                   = "${var.project_name}-${var.environment}-task-def"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.ecs_service_task_cpu
  memory                   = var.ecs_service_task_memory
  execution_role_arn       = var.ecs_execution_role_arn
  task_role_arn            = var.ecs_task_role_arn

  container_definitions = jsonencode([
    {
      name = "python-api-container"
      # Image URI is built in the environment's locals.tf from account ID + region + tag.
      # Override the tag via -var in CI/CD; never hardcode the account ID in module code.
      image = var.python_api_image

      portMappings = [
        {
          containerPort = var.app_port
          protocol      = "tcp"
        }
      ]

      environment = [
        # Database — assembled into DATABASE_URL by config.py
        {
          name  = "RDS_HOST"
          value = var.rds_host
        },
        {
          name  = "RDS_PORT"
          value = tostring(var.rds_port)
        },
        {
          name  = "RDS_DB_NAME"
          value = var.rds_db_name
        },
        # Regula — containers share localhost inside the same ECS task
        {
          name  = "REGULA_URL"
          value = "http://localhost:8080"
        },
        # Storage
        {
          name  = "STORAGE_MODE"
          value = "s3"
        },
        {
          name  = "S3_BUCKET_NAME"
          value = var.s3_images_bucket_name
        },
        {
          name  = "AWS_REGION"
          value = data.aws_region.current.id
        }
      ]

      # SSM ARNs are constructed from data sources so no account IDs or regions are
      # hardcoded here. The parameter path uses var.environment so the same module
      # works for dev, staging, and prod without any code changes.
      secrets = [
        {
          name      = "RDS_DB_USERNAME"
          valueFrom = "arn:aws:ssm:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:parameter/regula/${var.environment}/db_username"
        },
        {
          name      = "RDS_DB_PASSWORD"
          valueFrom = "arn:aws:ssm:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:parameter/regula/${var.environment}/db_password"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs_log_group.name
          "awslogs-region"        = data.aws_region.current.id
          "awslogs-stream-prefix" = "${var.project_name}-${var.environment}"
        }
      }
    },
    {
      name = "regula-container"
      # Same pattern as python-api — URI built from locals, tag overridable in CI/CD.
      image = var.regula_image

      entryPoint = ["/bin/sh", "-c", "echo \"$REGULA_LICENSE_B64\" | base64 -d > /app/extBin/unix/regula.license && exec /app/entrypoint.sh"]

      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        }
      ]

      # The Secrets Manager ARN has an account-specific random suffix and is passed
      # in from tfvars via the environment — not constructable from data sources.
      secrets = [
        {
          name      = "REGULA_LICENSE_B64"
          valueFrom = var.regula_license_secret_arn
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs_log_group.name
          "awslogs-region"        = data.aws_region.current.id
          "awslogs-stream-prefix" = "${var.project_name}-${var.environment}-regula"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "main_ecs_service" {
  name            = "${var.project_name}-${var.environment}-ecs-service"
  cluster         = aws_ecs_cluster.main_ecs_cluster.id
  task_definition = aws_ecs_task_definition.ecs_task_definition.arn
  desired_count   = var.ecs_service_task_desired
  launch_type     = "FARGATE"

  #The ECS service only references the target group ARN, so Terraform sees no data dependency on the listeners
  #and will try to create the service in parallel — before the listeners attach the target group to the ALB.
  #depends_on forces the listeners to exist first, which is what actually makes the association happen in AWS.
  depends_on = [
    aws_lb_listener.alb_http_listener,
    aws_lb_listener.alb_https_listener,
  ]

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.alb_target_group.arn
    container_name   = "python-api-container"
    container_port   = var.app_port
  }

  # If a new task definition fails to start (bad image, OOM, crash-loop), ECS will
  # automatically stop the deployment and roll back to the last healthy version.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # CI/CD manages the running image by registering new task definition revisions directly.
  # Without ignore_changes, every `terraform apply` would revert the service to the image
  # tag that was current when Terraform last ran — overwriting the CI-deployed version.
  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

#Configure Auto Scaling for the ECS service based on average CPU utilization to ensure the application can handle varying traffic loads while optimizing costs
resource "aws_appautoscaling_target" "ecs_target" {
  max_capacity       = var.ecs_service_task_max
  min_capacity       = var.ecs_service_task_min
  resource_id        = "service/${aws_ecs_cluster.main_ecs_cluster.name}/${aws_ecs_service.main_ecs_service.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

#Create an Auto Scaling policy to scale the ECS service in and out based on average CPU utilization, with cooldown periods to prevent rapid scaling actions that could lead to instability
resource "aws_appautoscaling_policy" "ecs_policy" {
  name               = "${var.project_name}-${var.environment}-autoscaling-policy"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs_target.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs_target.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs_target.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = var.autoscaling_cpu_target
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_out_cooldown = 60  # 1 min — scale out quickly under sudden load spikes
    scale_in_cooldown  = 300 # 5 min — scale in slowly to avoid task thrashing on bursty traffic
  }
}

#Route53 alias record for the ALB — keeps api. DNS in sync with the ALB across every destroy/apply cycle
data "aws_route53_zone" "main" {
  name = var.root_domain_name
}

resource "aws_route53_record" "api_alb_alias" {
  zone_id         = data.aws_route53_zone.main.zone_id
  name            = "api.${var.domain_name}"
  type            = "A"
  allow_overwrite = true

  alias {
    name                   = aws_lb.main_alb.dns_name
    zone_id                = aws_lb.main_alb.zone_id
    evaluate_target_health = true
  }
}