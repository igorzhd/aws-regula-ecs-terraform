# ============================================================
# config.py — Application Configuration
#
# This file is the single source of truth for all settings
# that the application needs to run. Nothing is hardcoded
# here — every value comes from environment variables or a
# .env file, so the same code works in local development
# and in production on AWS without any changes.
#
# HOW IT WORKS:
#   1. Create a .env file in services/python-api/
#   2. When the app starts, this file reads that .env file
#   3. Every other file imports `settings` from here:
#      from app.config import settings
#   4. Then uses it like: settings.REGULA_URL
#
# If a required variable is missing, the app will refuse to
# start and tell you exactly which variable is missing.
# This is intentional — it's better to fail loudly at
# startup than to fail silently in production.
# ============================================================

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
from typing import Literal, Optional


class Settings(BaseSettings):
    """
    All application settings in one place.

    BaseSettings is a Pydantic class that automatically reads
    values from environment variables. Each field defined here
    corresponds to one environment variable with the same name.

    Field(...) means the variable is REQUIRED — the app will
    not start without it.
    Field("default") means the variable is OPTIONAL and will
    use the default value if not set.
    """

    # ----------------------------------------------------------
    # Regula Document Reader
    # URL of the Regula container. In local development this
    # points to the Docker service name defined in
    # docker-compose.yml. In production on ECS, both containers
    # run in the same task so it's still localhost.
    # ----------------------------------------------------------
    REGULA_URL: str = Field(
        default="http://regula:8080",
        description="Base URL of the Regula Document Reader service"
    )

    # Regula API endpoint for document processing.
    # This is appended to REGULA_URL when making requests.
    # Full URL becomes: http://regula:8080/api/process
    REGULA_PROCESS_PATH: str = Field(
        default="/api/process",
        description="Regula processing endpoint path"
    )

    # How many seconds to wait for Regula to respond before
    # giving up. Regula can take a few seconds for complex
    # documents, especially with FullProcess scenario.
    REGULA_TIMEOUT_SECONDS: int = Field(
        default=20,
        description="Timeout in seconds for Regula API calls"
    )

    # The processing scenario to use. FullProcess runs all
    # available checks: OCR, MRZ, barcode, authenticity, etc.
    # Other options: Mrz, Ocr, DocType, Barcode, etc.
    REGULA_SCENARIO: str = Field(
        default="FullProcess",
        description="Regula processing scenario"
    )

    # ----------------------------------------------------------
    # Database (PostgreSQL)
    # Connection string format:
    #   postgresql+asyncpg://user:password@host:port/dbname
    # We use +asyncpg because SQLAlchemy needs an async driver
    # to work with FastAPI's non-blocking architecture.
    #
    # Local development example:
    #   postgresql+asyncpg://postgres:postgres@localhost:5432/docverify
    # AWS RDS example:
    #   postgresql+asyncpg://admin:secret@rds-host.amazonaws.com:5432/docverify
    # ----------------------------------------------------------
    # Can be set directly (local dev) or assembled from
    # RDS_HOST / RDS_PORT / RDS_DB_NAME / RDS_DB_USERNAME /
    # RDS_DB_PASSWORD (ECS production).
    DATABASE_URL: Optional[str] = Field(
        default=None,
        description="Full PostgreSQL connection string. If absent, assembled from RDS_* vars."
    )

    # Individual RDS components passed by the ECS task definition
    RDS_HOST: Optional[str] = Field(default=None)
    RDS_PORT: int = Field(default=5432)
    RDS_DB_NAME: Optional[str] = Field(default=None)
    RDS_DB_USERNAME: Optional[str] = Field(default=None)
    RDS_DB_PASSWORD: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def assemble_database_url(self) -> "Settings":
        if not self.DATABASE_URL:
            if not all([self.RDS_HOST, self.RDS_DB_NAME, self.RDS_DB_USERNAME, self.RDS_DB_PASSWORD]):
                raise ValueError(
                    "Provide DATABASE_URL or all of: RDS_HOST, RDS_DB_NAME, RDS_DB_USERNAME, RDS_DB_PASSWORD"
                )
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{self.RDS_DB_USERNAME}:{self.RDS_DB_PASSWORD}"
                f"@{self.RDS_HOST}:{self.RDS_PORT}/{self.RDS_DB_NAME}"
            )
        return self

    # How many database connections to keep open in the pool.
    # The pool means we don't open a new connection for every
    # request — we reuse existing ones, which is much faster.
    DATABASE_POOL_SIZE: int = Field(
        default=5,
        description="Number of connections in the database pool"
    )

    DATABASE_MAX_OVERFLOW: int = Field(
        default=10,
        description="Extra connections allowed beyond pool size during spikes"
    )

    # ----------------------------------------------------------
    # Storage Mode
    # Controls where document crops and raw JSON are saved.
    #
    # "local" — saves files to LOCAL_STORAGE_PATH on disk.
    #   Use this in development. No AWS credentials needed.
    #
    # "s3" — uploads files to S3_BUCKET_NAME on AWS S3.
    #   Use this in production (or when testing S3 locally
    #   via LocalStack).
    # ----------------------------------------------------------
    STORAGE_MODE: Literal["local", "s3"] = Field(
        default="local",
        description="Where to save files: 'local' for development, 's3' for production"
    )

    # ----------------------------------------------------------
    # Local Storage (used when STORAGE_MODE = "local")
    # The folder on disk where document crops and raw JSON
    # will be saved during local development.
    # Each session gets its own subfolder:
    #   ./local_storage/{transaction_id}/page_0_crop.jpg
    #   ./local_storage/{transaction_id}/page_1_crop.jpg
    #   ./local_storage/{transaction_id}/raw_response.json
    # ----------------------------------------------------------
    LOCAL_STORAGE_PATH: str = Field(
        default="./local_storage",
        description="Local folder for file storage in development mode"
    )

    # ----------------------------------------------------------
    # S3 Storage (used when STORAGE_MODE = "s3")
    # These are only required when STORAGE_MODE is "s3".
    # In local mode, they can be left empty or unset.
    #
    # S3_BUCKET_NAME: the name of your S3 bucket
    # AWS_REGION: the AWS region where the bucket lives
    #
    # Credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    # are NOT defined here. In production on ECS, the task
    # role handles authentication automatically — no keys
    # needed. Locally, they are read from your AWS CLI
    # configuration or from environment variables that boto3
    # picks up on its own.
    # ----------------------------------------------------------
    S3_BUCKET_NAME: str = Field(
        default="",
        description="S3 bucket name (required when STORAGE_MODE=s3)"
    )

    AWS_REGION: str = Field(
        default="us-east-1",
        description="AWS region for S3"
    )

    # How long presigned download URLs for document images and
    # raw JSON are valid. After this time the URL expires and
    # the frontend must request a new one.
    S3_PRESIGNED_URL_EXPIRY_SECONDS: int = Field(
        default=3600,
        description="Presigned URL expiry time in seconds (default: 1 hour)"
    )

    # ----------------------------------------------------------
    # API Settings
    # ----------------------------------------------------------

    # The port the FastAPI server listens on.
    # We use 8001 locally so it doesn't clash with Regula
    # which already uses 8080.
    API_PORT: int = Field(
        default=8001,
        description="Port the API server listens on"
    )

    # App title and version shown in the auto-generated
    # API documentation at http://localhost:8001/docs
    APP_TITLE: str = Field(
        default="Document Verification API",
        description="API title shown in /docs"
    )

    APP_VERSION: str = Field(
        default="0.1.0",
        description="API version"
    )

    # Controls how much detail is logged. Options:
    # "DEBUG"   — everything, very verbose, good for development
    # "INFO"    — normal operations, good for production
    # "WARNING" — only problems
    # "ERROR"   — only errors
    LOG_LEVEL: str = Field(
        default="DEBUG",
        description="Logging level: DEBUG, INFO, WARNING, ERROR"
    )

    # ----------------------------------------------------------
    # Pydantic-settings configuration
    # This inner class tells pydantic-settings WHERE to look
    # for the values defined above.
    #
    # env_file: look for a .env file in the current directory
    # env_file_encoding: the file is UTF-8 text
    # case_sensitive: DATABASE_URL ≠ database_url (exact match)
    # extra: "ignore" means unknown variables in .env are
    #        silently skipped instead of causing an error
    # ----------------------------------------------------------
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    @property
    def regula_process_url(self) -> str:
        """
        A convenience property that returns the full Regula
        processing URL by combining REGULA_URL and
        REGULA_PROCESS_PATH. Other files use this instead of
        building the URL manually each time.

        Example: "http://regula:8080/api/process"
        """
        return f"{self.REGULA_URL}{self.REGULA_PROCESS_PATH}"

    @property
    def is_local_storage(self) -> bool:
        """Returns True if we are saving files to local disk."""
        return self.STORAGE_MODE == "local"

    @property
    def is_s3_storage(self) -> bool:
        """Returns True if we are saving files to S3."""
        return self.STORAGE_MODE == "s3"


# ----------------------------------------------------------
# Create a single shared instance of Settings.
#
# This is the object that every other file in the project
# imports. Python's module system ensures this is only
# created once — every import gets the same object.
#
# Usage in any other file:
#   from app.config import settings
#   print(settings.REGULA_URL)
#   print(settings.regula_process_url)
# ----------------------------------------------------------
settings = Settings()


# ============================================================
# EXAMPLE .env FILE
# Create this file at services/python-api/.env
# It is already in .gitignore so secrets are never committed.
# ============================================================
#
# # Regula
# REGULA_URL=http://regula:8080
# REGULA_TIMEOUT_SECONDS=60
# REGULA_SCENARIO=FullProcess
#
# # Database (local Docker PostgreSQL)
# DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/docverify
#
# # Storage (local development)
# STORAGE_MODE=local
# LOCAL_STORAGE_PATH=./local_storage
#
# # Storage (production S3 — uncomment when deploying)
# # STORAGE_MODE=s3
# # S3_BUCKET_NAME=your-bucket-name
# # AWS_REGION=us-west-2
#
# # API
# API_PORT=8001
# LOG_LEVEL=DEBUG
# ============================================================