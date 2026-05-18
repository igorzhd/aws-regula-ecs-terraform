# ============================================================
# Dockerfile — Python API Container
#
# This file tells Docker how to build a container image for
# the Python API. Think of it as a recipe:
#   1. Start from a base Python image
#   2. Set up the working environment
#   3. Install dependencies
#   4. Copy the application code
#   5. Define how to start the server
#
# WHY MULTI-STAGE BUILD IS NOT USED HERE:
# Multi-stage builds reduce image size by separating the
# build environment from the runtime environment. For a
# Python API this size benefit is minimal, so we keep it
# simple with a single stage for easier debugging.
#
# HOW TO BUILD MANUALLY (you won't need this — docker
# compose builds it automatically):
#   docker build -t python-api .
#
# HOW TO RUN MANUALLY:
#   docker run -p 8001:8001 python-api
# ============================================================

# ----------------------------------------------------------
# Base image
# python:3.12-slim is an official Python image based on
# Debian Linux with only the minimum packages installed.
# "slim" means it's much smaller than the full image
# (~130MB vs ~1GB) while still having everything we need.
# ----------------------------------------------------------
FROM python:3.12-slim

# ----------------------------------------------------------
# Set environment variables for Python behavior inside
# the container.
#
# PYTHONDONTWRITEBYTECODE=1
#   Prevents Python from writing .pyc bytecode cache files
#   to disk. Not needed in a container since we don't reuse
#   the container between runs.
#
# PYTHONUNBUFFERED=1
#   Forces Python to output logs immediately without
#   buffering. Critical in containers — without this, logs
#   might not appear in Docker's log stream for minutes.
# ----------------------------------------------------------
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ----------------------------------------------------------
# Working directory
# All subsequent commands (COPY, RUN, CMD) operate relative
# to this directory inside the container.
# ----------------------------------------------------------
WORKDIR /app

# ----------------------------------------------------------
# Install system dependencies
# Some Python packages need C libraries to compile.
# We install them here before installing Python packages.
#
# libpq-dev: PostgreSQL client library, needed by asyncpg
# gcc: C compiler, needed to compile some Python packages
# curl: useful for health checks and debugging inside container
#
# After installing, we clean up apt cache to keep the
# image size smaller.
# ----------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ----------------------------------------------------------
# Install Python dependencies
# We copy requirements.txt FIRST (before copying app code)
# and install it as a separate Docker layer.
#
# WHY THIS ORDER MATTERS — Docker layer caching:
# Docker caches each instruction as a "layer". If a layer's
# inputs haven't changed, Docker reuses the cached result.
# By copying requirements.txt first, the "pip install" step
# is only re-run when requirements.txt changes. If you only
# change app code (which happens often), Docker skips the
# slow pip install step and rebuilds in seconds instead of
# minutes.
# ----------------------------------------------------------
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ----------------------------------------------------------
# Copy application code
# Now copy everything else. This layer changes every time
# you change code, but it's fast because we already installed
# dependencies in the cached layer above.
#
# Note: .dockerignore (created separately) excludes files
# like .env, __pycache__, local_storage/, etc.
# ----------------------------------------------------------
COPY . .

# ----------------------------------------------------------
# Create the local storage directory
# This is where document crops and JSON files are saved
# in local development mode. In production (S3 mode) this
# folder is not used, but creating it doesn't hurt.
# ----------------------------------------------------------
RUN mkdir -p /app/local_storage

# ----------------------------------------------------------
# Expose port
# Tells Docker that this container listens on port 8001.
# This is documentation only — the actual port mapping
# happens in docker-compose.yml with "ports: 8001:8001".
# ----------------------------------------------------------
EXPOSE 8001

# ----------------------------------------------------------
# Health check
# Docker periodically runs this command to check if the
# container is healthy. If it fails repeatedly, Docker
# marks the container as unhealthy.
# curl calls our /health endpoint — if it returns 200, good.
# --interval: check every 30 seconds
# --timeout: fail if no response within 10 seconds
# --retries: mark unhealthy after 3 consecutive failures
# --start-period: give the app 30 seconds to start up
#                 before health checks begin
# ----------------------------------------------------------
HEALTHCHECK \
    --interval=30s \
    --timeout=10s \
    --retries=3 \
    --start-period=30s \
    CMD curl -f http://localhost:8001/health || exit 1

# ----------------------------------------------------------
# Start command
# This runs when the container starts.
#
# uvicorn: the ASGI web server that runs FastAPI
# app.main:app: the FastAPI app object in app/main.py
# --host 0.0.0.0: listen on all network interfaces inside
#   the container (not just localhost). Required so the
#   container can receive requests from outside.
# --port 8001: the port to listen on
# --reload: watch for code changes and restart automatically
#   IMPORTANT: --reload is for DEVELOPMENT ONLY. Remove it
#   in production for better performance and stability.
# --log-level: controls uvicorn's own log verbosity
# ----------------------------------------------------------
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--log-level", "info"]