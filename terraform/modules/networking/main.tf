#Import parameters
data "aws_region" "current" {}

#Create VPC
resource "aws_vpc" "main_vpc" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = {
    Name = "main_vpc"
  }
}

#Create Subnets
resource "aws_subnet" "public_subnet" {
  count                   = length(var.public_subnet_cidr)
  vpc_id                  = aws_vpc.main_vpc.id
  cidr_block              = var.public_subnet_cidr[count.index]
  availability_zone       = var.availability_zones[count.index % length(var.availability_zones)]
  map_public_ip_on_launch = true
  tags = {
    Name = "public_subnet_${count.index + 1}"
  }
}

resource "aws_subnet" "private_subnet" {
  count                   = length(var.private_subnet_cidr)
  vpc_id                  = aws_vpc.main_vpc.id
  cidr_block              = var.private_subnet_cidr[count.index]
  availability_zone       = var.availability_zones[count.index % length(var.availability_zones)]
  map_public_ip_on_launch = false
  tags = {
    Name = "private_subnet_${count.index + 1}"
  }
}

resource "aws_subnet" "db_subnet" {
  count                   = length(var.db_subnet_cidr)
  vpc_id                  = aws_vpc.main_vpc.id
  cidr_block              = var.db_subnet_cidr[count.index]
  availability_zone       = var.availability_zones[count.index % length(var.availability_zones)]
  map_public_ip_on_launch = false
  tags = {
    Name = "db_subnet_${count.index + 1}"
  }
}

#Create Gateways & Endpoints

#Create Internet Gateway
resource "aws_internet_gateway" "main_igw" {
  vpc_id = aws_vpc.main_vpc.id
  tags = {
    Name = "main_igw"
  }
}

#Create Elastic IPs for NAT Gateways (one per AZ)
resource "aws_eip" "nat_eip" {
  count  = length(var.availability_zones)
  domain = "vpc"
  tags = {
    Name = "nat_eip_${var.availability_zones[count.index]}"
  }
}

#Create NAT Gateways in public subnets (one per AZ) to allow private subnet egress to the internet
resource "aws_nat_gateway" "nat_gw" {
  count         = length(var.availability_zones)
  allocation_id = aws_eip.nat_eip[count.index].id
  subnet_id     = aws_subnet.public_subnet[count.index].id
  depends_on    = [aws_internet_gateway.main_igw]
  tags = {
    Name = "nat_gw_${var.availability_zones[count.index]}"
  }
}

#Create VPC Endpoint for S3
resource "aws_vpc_endpoint" "main_vpc_endpoint" {
  vpc_id       = aws_vpc.main_vpc.id
  service_name = "com.amazonaws.${data.aws_region.current.region}.s3"
  route_table_ids = concat(
    aws_route_table.private_rt[*].id,
    [aws_route_table.db_rt.id]
  )
  tags = {
    Name = "main_vpc_endpoint"
  }
}

#Create Public Route Tables & Associate with Public Subnets
resource "aws_route_table" "public_rt" {
  vpc_id = aws_vpc.main_vpc.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main_igw.id
  }
  tags = {
    Name = "public_rt"
  }
}

resource "aws_route_table_association" "public_rta" {
  count          = length(var.public_subnet_cidr)
  subnet_id      = aws_subnet.public_subnet[count.index].id
  route_table_id = aws_route_table.public_rt.id
}

#Create Private Route Tables & Associate with Private Subnets
resource "aws_route_table" "private_rt" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main_vpc.id
  tags = {
    Name = "private_rt_${var.availability_zones[count.index]}"
  }
}

resource "aws_route_table_association" "private_rta" {
  count          = length(var.private_subnet_cidr)
  subnet_id      = aws_subnet.private_subnet[count.index].id
  route_table_id = aws_route_table.private_rt[count.index].id
}

#Route all private subnet egress traffic through the AZ-local NAT Gateway
resource "aws_route" "private_nat_route" {
  count                  = length(var.availability_zones)
  route_table_id         = aws_route_table.private_rt[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.nat_gw[count.index].id
}

#Create DB Route Table & Associate with DB Subnets
resource "aws_route_table" "db_rt" {
  vpc_id = aws_vpc.main_vpc.id
  tags = {
    Name = "db_rt"
  }
}

resource "aws_route_table_association" "db_rta" {
  count          = length(var.db_subnet_cidr)
  subnet_id      = aws_subnet.db_subnet[count.index].id
  route_table_id = aws_route_table.db_rt.id
}