# ============================================================
# models.py — Database Table Definitions
#
# This file defines the structure of the database using
# SQLAlchemy, which is a Python library that lets you work
# with databases using Python classes instead of raw SQL.
#
# HOW IT WORKS:
#   - Each class here represents one database table
#   - Each class attribute represents one column in that table
#   - SQLAlchemy translates Python operations into SQL:
#       saving an object  →  INSERT INTO sessions ...
#       reading an object →  SELECT * FROM sessions ...
#       updating a field  →  UPDATE sessions SET ...
#
# THIS FILE DOES NOT CREATE THE TABLE.
# The table is created by Alembic migrations. This file just
# describes what the table should look like.
#
# OTHER FILES THAT USE THIS:
#   - main.py uses Session to save and retrieve records
#   - Alembic reads this to generate migration SQL scripts
# ============================================================

from sqlalchemy import (
    Column,          # defines a table column
    String,          # text column (VARCHAR)
    Integer,         # integer column
    SmallInteger,    # small integer (good for status codes 0/1/2)
    DateTime,        # date + time column
    Text,            # unlimited length text
    func,            # SQL functions like NOW()
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
# UUID  — PostgreSQL's native UUID type (for primary keys)
# JSONB — PostgreSQL's binary JSON type (faster than JSON,
#          supports indexing and querying with -> operator)

from sqlalchemy.orm import DeclarativeBase
import uuid


# ----------------------------------------------------------
# Base class
# All SQLAlchemy models must inherit from a Base class.
# DeclarativeBase is the modern SQLAlchemy 2.0 way to do
# this. It keeps track of all models so Alembic can find
# them when generating migrations.
# ----------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ----------------------------------------------------------
# Session Model
# Represents the "sessions" table in PostgreSQL.
# One row = one document processing request.
#
# A "session" in this context means: a user uploaded one
# or two document images, Regula processed them, and we
# stored the full result. Everything about that processing
# event lives in one row of this table.
# ----------------------------------------------------------
class Session(Base):

    # The name of the actual table in PostgreSQL
    __tablename__ = "sessions"

    # ----------------------------------------------------------
    # PRIMARY KEY
    # A UUID is a randomly generated unique identifier like:
    # "550e8400-e29b-41d4-a716-446655440000"
    # We generate it in Python (not the database) so we know
    # the ID before we even save the record. This is useful
    # when you need to reference the ID somewhere else first
    # (like in an S3 path) before the database write happens.
    # ----------------------------------------------------------
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,   # auto-generate a new UUID for each row
        nullable=False,
        comment="Internal primary key, auto-generated UUID"
    )

    # ----------------------------------------------------------
    # TRANSACTION ID (from Regula)
    # Regula assigns its own unique ID to every processing
    # request. We store it here so we can:
    #   1. Link our record back to Regula's logs if needed
    #   2. Use it as the folder name in S3
    # unique=True ensures no two sessions share the same ID.
    # index=True makes lookups by transaction_id fast.
    # ----------------------------------------------------------
    transaction_id = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        comment="Regula's transaction UUID, used as S3 folder name"
    )

    # ----------------------------------------------------------
    # TIMESTAMPS
    # created_at: when this session was created in our system.
    #   server_default=func.now() means PostgreSQL sets this
    #   automatically when the row is inserted — we don't have
    #   to set it manually in Python.
    #
    # processed_at: the exact timestamp from Regula's response
    #   (TransactionInfo.DateTime). This is when Regula
    #   actually processed the document, which may be slightly
    #   different from when we saved it to the database.
    # ----------------------------------------------------------
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,         # index for fast sorting by date
        comment="When this session was created in our system"
    )

    processed_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp from Regula (TransactionInfo.DateTime)"
    )

    # ----------------------------------------------------------
    # PROCESSING METADATA
    # How long Regula took to process the document (ms) and
    # which version of Regula SDK was used. Useful for
    # debugging performance issues and tracking SDK upgrades.
    # ----------------------------------------------------------
    elapsed_time_ms = Column(
        Integer,
        nullable=True,
        comment="Processing time in milliseconds (from Regula)"
    )

    regula_version = Column(
        String(50),
        nullable=True,
        comment="Regula SDK version (e.g. '9.4.319820.2195')"
    )

    # ----------------------------------------------------------
    # OVERALL STATUS CHECKS
    # These are the top-level check results from result_type 33
    # (the Status container in Regula's response).
    #
    # SmallInteger is used because these values are always
    # one of three possibilities:
    #   0 = PASS  (check passed)
    #   1 = FAIL  (check failed)
    #   2 = N/A   (check was not performed / not applicable)
    # ----------------------------------------------------------
    overall_status = Column(
        SmallInteger,
        nullable=True,
        comment="Overall result of all checks: 0=pass, 1=fail, 2=n/a"
    )

    optical_status = Column(
        SmallInteger,
        nullable=True,
        comment="Overall optical checks result: 0=pass, 1=fail, 2=n/a"
    )

    expiry_check = Column(
        SmallInteger,
        nullable=True,
        comment="Document expiry date check: 0=pass, 1=fail, 2=n/a"
    )

    mrz_check = Column(
        SmallInteger,
        nullable=True,
        comment="MRZ validity check: 0=pass, 1=fail, 2=n/a"
    )

    text_check = Column(
        SmallInteger,
        nullable=True,
        comment="Text fields validity and comparison: 0=pass, 1=fail, 2=n/a"
    )

    security_check = Column(
        SmallInteger,
        nullable=True,
        comment="Document authenticity/security check: 0=pass, 1=fail, 2=n/a"
    )

    # ----------------------------------------------------------
    # DOCUMENT TYPE — stored as JSONB
    # One entry per page submitted. Stored as JSON because
    # the number of pages varies (1 or 2) and each page has
    # multiple fields.
    #
    # Structure:
    # [
    #   {
    #     "page": 0,
    #     "name": "United States - ePassport (2020)",
    #     "country": "United States",
    #     "icao_code": "USA",
    #     "doc_type": 11,
    #     "doc_format": 2,
    #     "doc_year": "2020"
    #   },
    #   {
    #     "page": 1,
    #     "name": "United States - ePassport (2020) Page 3",
    #     ...
    #   }
    # ]
    # ----------------------------------------------------------
    doc_type = Column(
        JSONB,
        nullable=True,
        comment="Document type info per page from result_type 9 (OneCandidate)"
    )

    # ----------------------------------------------------------
    # IMAGE QUALITY — stored as JSONB
    # One entry per page. Each entry contains the overall
    # quality result and individual check results (focus,
    # glare, resolution, perspective angle, bounds, portrait).
    #
    # Structure:
    # [
    #   {
    #     "page": 0,
    #     "overall": 1,
    #     "checks": [
    #       {"name": "Image Focus", "result": 1, "probability": 99},
    #       {"name": "Image Glare", "result": 0, "probability": 5},
    #       {"name": "Image Resolution", "result": 0, "probability": 0},
    #       {"name": "Perspective Angle", "result": 0, "probability": 0},
    #       {"name": "Bounds Valid", "result": 0, "probability": 0},
    #       {"name": "Portrait", "result": 2, "probability": 0}
    #     ]
    #   }
    # ]
    # ----------------------------------------------------------
    image_quality = Column(
        JSONB,
        nullable=True,
        comment="Image quality checks per page from result_type 30"
    )

    # ----------------------------------------------------------
    # TEXT FIELDS — stored as JSONB
    # The 6 document fields the frontend needs to display,
    # with full detail: value per source, validity per source,
    # and cross-comparison between sources.
    #
    # Stored as JSONB (not flat columns) because each field
    # can have multiple sources (MRZ, VISUAL, BARCODE) and
    # flattening that into columns like
    # "doc_number_mrz_value", "doc_number_visual_value" etc.
    # would be very messy and hard to extend.
    #
    # Structure (see parser.py for full detail):
    # {
    #   "overall_status": 0,
    #   "fields": [
    #     {
    #       "field_name": "Document Number",
    #       "field_type": 2,
    #       "overall_status": 0,
    #       "comparison_status": 0,
    #       "validity_status": 1,
    #       "sources": [
    #         {"source": "MRZ",    "value": "E00007929", "validity": 1},
    #         {"source": "VISUAL", "value": "E00007929", "validity": 1}
    #       ],
    #       "comparisons": [
    #         {"source_left": "MRZ", "source_right": "VISUAL", "status": 0}
    #       ]
    #     },
    #     ... (5 more fields)
    #   ]
    # }
    # ----------------------------------------------------------
    text_fields = Column(
        JSONB,
        nullable=True,
        comment="Parsed text fields with per-source values and comparisons"
    )

    # ----------------------------------------------------------
    # S3 FILE REFERENCES
    # Paths to files stored in S3 (or local disk in dev mode).
    # Stored as JSONB because the number of pages varies.
    #
    # s3_doc_crops: paths to the cropped document images
    #   (fieldType 207 from result_type 37, one per page)
    # Structure:
    # {
    #   "page_0": "sessions/uuid/page_0_crop.jpg",
    #   "page_1": "sessions/uuid/page_1_crop.jpg"
    # }
    #
    # s3_raw_json: path to the full Regula JSON response
    # Structure: "sessions/uuid/raw_response.json"
    # ----------------------------------------------------------
    s3_doc_crops = Column(
        JSONB,
        nullable=True,
        comment="S3 keys for document crop images, keyed by page number"
    )

    s3_raw_json = Column(
        String(500),
        nullable=True,
        comment="S3 key for the full raw Regula JSON response"
    )

    # ----------------------------------------------------------
    # FULL RAW RESPONSE — stored as JSONB
    # The complete, unmodified JSON response from Regula.
    # We store this so that:
    #   1. The user can download it from the frontend
    #   2. We can re-parse it later if we add new fields
    #   3. We can debug issues without re-processing the document
    #
    # This is nullable=False because we always want to keep
    # the raw response — it is the source of truth.
    # ----------------------------------------------------------
    raw_response = Column(
        JSONB,
        nullable=False,
        comment="Full unmodified Regula JSON response"
    )

    # ----------------------------------------------------------
    # Python representation
    # This is optional but very useful during development.
    # When you print() a Session object in the terminal, Python
    # shows this string instead of something like:
    # <app.models.Session object at 0x7f1234abcd>
    # ----------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"<Session "
            f"id={self.id} "
            f"transaction_id={self.transaction_id} "
            f"overall_status={self.overall_status} "
            f"created_at={self.created_at}>"
        )