variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
}

variable "public_subnet_cidr" {
  description = "List of CIDR blocks for public subnets."
  type        = list(string)
}

variable "private_subnet_cidr" {
  description = "List of CIDR blocks for private subnets."
  type        = list(string)
}

variable "db_subnet_cidr" {
  description = "List of CIDR blocks for db subnets."
  type        = list(string)
}

variable "availability_zones" {
  description = "List of availability zones to use for subnets."
  type        = list(string)
}