output "alb_dns_name" {
  description = "The DNS name of the Application Load Balancer"
  value       = aws_lb.main_alb.dns_name
}

output "acm_domain_validation_options" {
  description = "The domain validation options for the ACM certificate"
  value       = aws_acm_certificate.main_cert.domain_validation_options
}

output "ecs_cluster_name" {
  description = "The name of the ECS cluster"
  value       = aws_ecs_cluster.main_ecs_cluster.name
}