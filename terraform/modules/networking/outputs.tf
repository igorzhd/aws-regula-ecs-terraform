output "vpc_main_vpc_id" {
  description = "ID of the main VPC."
  value       = aws_vpc.main_vpc.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets."
  value       = aws_subnet.public_subnet[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets."
  value       = aws_subnet.private_subnet[*].id
}

output "db_subnet_ids" {
  description = "IDs of the db subnets."
  value       = aws_subnet.db_subnet[*].id
}

output "internet_gateway_id" {
  description = "ID of the Internet Gateway."
  value       = aws_internet_gateway.main_igw.id
}