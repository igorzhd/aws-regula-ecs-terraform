output "alb_sg" {
  description = "ALB SG ID"
  value       = aws_security_group.alb_sg.id
}

output "ecs_sg" {
  description = "ECS SG ID"
  value       = aws_security_group.ecs_sg.id
}

output "rds_sg" {
  description = "RDS SG ID"
  value       = aws_security_group.rds_sg.id
}

output "vpc_endpoint_sg" {
  description = "VPC Endpoint SG ID"
  value       = aws_security_group.vpc_endpoint_sg.id
}