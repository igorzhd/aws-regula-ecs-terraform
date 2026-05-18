# ============================================================
# schemas.py — API Request and Response Schemas
#
# This file defines the exact shape of data that goes into
# and comes out of every API endpoint, using Pydantic.
#
# WHAT PYDANTIC DOES FOR YOU AUTOMATICALLY:
#   - Validates incoming request data (wrong type = clear error)
#   - Validates outgoing response data (missing field = caught)
#   - Converts types where possible (e.g. string "123" → int 123)
#   - Generates the API documentation at /docs automatically
#   - Serializes Python objects to JSON for HTTP responses
#
# DIFFERENCE FROM models.py:
#   models.py  = how data is stored in the DATABASE
#   schemas.py = how data travels over HTTP (API interface)
#
# A database row might have 20 columns but you only expose
# 12 of them through the API. Schemas let you control exactly
# what the frontend sees.
#
# HOW SCHEMAS ARE ORGANIZED HERE:
#   1. Small building blocks (source value, comparison, etc.)
#   2. Medium structures (one text field, one quality check)
#   3. Large structures (full text fields result, full response)
#   4. Request schemas (what the API receives)
#   5. Response schemas (what the API sends back)
# ============================================================

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid


# ============================================================
# BUILDING BLOCKS
# Small schemas used as nested components inside larger ones.
# ============================================================

class SourceValue(BaseModel):
    """
    One value for a text field from a specific source.

    A document field like "Document Number" can appear in
    multiple places on a document: in the MRZ (machine
    readable zone), printed visually (VISUAL), or in a
    barcode. Each of those is a separate source with its
    own value and its own validity status.

    Example:
        source="MRZ"    value="E00007929"  validity=1 (FAIL)
        source="VISUAL" value="E00007929"  validity=1 (FAIL)
    """
    source: str = Field(
        description="Where this value came from: MRZ, VISUAL, or BARCODE"
    )
    value: str = Field(
        description="The actual text value from this source"
    )
    validity: Optional[int] = Field(
        default=None,
        description="Validity of this source's value: 0=PASS, 1=FAIL, 2=N/A"
    )
    validity_label: Optional[str] = Field(
        default=None,
        description="Human-readable validity: PASS, FAIL, or N/A"
    )
    page_index: Optional[int] = Field(
        default=0,
        description="Which document page this value came from (0=front, 1=back)"
    )


class ComparisonResult(BaseModel):
    """
    The result of comparing a field's value between two sources.

    When the same field appears in both MRZ and VISUAL,
    Regula compares the two values. If they match, great.
    If they don't match, something suspicious is going on
    and the frontend should highlight this.

    Example (values match):
        source_left="MRZ"  source_right="VISUAL"
        status=0  status_label="PASS"  match=True

    Example (values don't match — highlight on frontend):
        source_left="MRZ"  source_right="VISUAL"
        status=1  status_label="FAIL"  match=False
    """
    source_left: str = Field(
        description="First source being compared (e.g. 'MRZ')"
    )
    source_right: str = Field(
        description="Second source being compared (e.g. 'VISUAL')"
    )
    status: Optional[int] = Field(
        default=None,
        description="Comparison result: 0=match, 1=mismatch, 2=N/A"
    )
    status_label: Optional[str] = Field(
        default=None,
        description="Human-readable result: PASS, FAIL, or N/A"
    )
    match: bool = Field(
        default=False,
        description="True if the two sources agree, False if they differ"
    )


class TextField(BaseModel):
    """
    One document text field with all its sources, validity
    checks, and cross-comparison results.

    This is what the frontend displays for each of the 6
    fields: Document Number, Date of Expiry, Date of Issue,
    Date of Birth, Surname, Given Names.

    For each field the frontend can show:
      - The overall result (pass/fail badge)
      - Each source and its value (MRZ: E00007929 ✓)
      - Whether sources agree (MRZ vs VISUAL: MATCH ✓)
      - If they disagree, what each source said
    """
    field_name: str = Field(
        description="Human-readable field name (e.g. 'Document Number')"
    )
    field_type: int = Field(
        description="Regula field type integer (e.g. 2 for Document Number)"
    )
    overall_status: Optional[int] = Field(
        default=None,
        description="Combined validity + comparison result: 0=PASS, 1=FAIL, 2=N/A"
    )
    overall_label: Optional[str] = Field(
        default=None,
        description="Human-readable overall status: PASS, FAIL, or N/A"
    )
    comparison_status: Optional[int] = Field(
        default=None,
        description="Cross-source comparison result: 0=all match, 1=mismatch, 2=N/A"
    )
    comparison_label: Optional[str] = Field(
        default=None,
        description="Human-readable comparison result"
    )
    validity_status: Optional[int] = Field(
        default=None,
        description="Format/checkdigit validity: 0=PASS, 1=FAIL, 2=N/A"
    )
    validity_label: Optional[str] = Field(
        default=None,
        description="Human-readable validity result"
    )
    sources: list[SourceValue] = Field(
        default=[],
        description="List of values from each source (MRZ, VISUAL, BARCODE)"
    )
    comparisons: list[ComparisonResult] = Field(
        default=[],
        description="Cross-comparison results between sources"
    )


class TextFieldsResult(BaseModel):
    """
    The complete text fields extraction result.
    Contains the overall text check status and all 6 fields.
    """
    overall_status: Optional[int] = Field(
        default=None,
        description="Overall text check result: 0=PASS, 1=FAIL, 2=N/A"
    )
    overall_label: Optional[str] = Field(
        default=None,
        description="Human-readable overall text result"
    )
    fields: list[TextField] = Field(
        default=[],
        description="List of the 6 extracted text fields"
    )


class ImageQualityCheck(BaseModel):
    """
    One individual image quality check result.
    For example: Image Focus, Image Glare, Image Resolution.
    """
    type: int = Field(
        description="Regula check type integer"
    )
    name: str = Field(
        description="Human-readable check name (e.g. 'Image Focus')"
    )
    result: Optional[int] = Field(
        default=None,
        description="Check result: 0=PASS, 1=FAIL, 2=N/A"
    )
    result_label: Optional[str] = Field(
        default=None,
        description="Human-readable result: PASS, FAIL, or N/A"
    )
    probability: Optional[int] = Field(
        default=None,
        description="Confidence probability as percentage (0-100)"
    )
    mean: Optional[float] = Field(
        default=None,
        description="Check mean value (technical detail from Regula)"
    )


class ImageQualityPage(BaseModel):
    """
    All image quality checks for one submitted page.
    One of these per image submitted (front and/or back).
    """
    page: int = Field(
        description="Page index: 0=front, 1=back"
    )
    overall: Optional[int] = Field(
        default=None,
        description="Overall quality result for this page: 0=PASS, 1=FAIL, 2=N/A"
    )
    overall_label: Optional[str] = Field(
        default=None,
        description="Human-readable overall result"
    )
    checks: list[ImageQualityCheck] = Field(
        default=[],
        description="Individual quality check results (focus, glare, etc.)"
    )


class DocumentTypePage(BaseModel):
    """
    Document type information for one submitted page.
    One of these per image submitted (front and/or back).

    Tells you: what kind of document is this? Which country?
    What year was this document type issued?
    """
    page: int = Field(
        description="Page index: 0=front, 1=back"
    )
    name: Optional[str] = Field(
        default=None,
        description="Full document type name (e.g. 'United States - ePassport (2020)')"
    )
    country: Optional[str] = Field(
        default=None,
        description="Issuing country name (e.g. 'United States')"
    )
    icao_code: Optional[str] = Field(
        default=None,
        description="ICAO country code (e.g. 'USA')"
    )
    doc_type: Optional[int] = Field(
        default=None,
        description="Regula document type integer"
    )
    doc_format: Optional[int] = Field(
        default=None,
        description="Regula document format integer"
    )
    doc_year: Optional[str] = Field(
        default=None,
        description="Year this document type was introduced"
    )


class FailureDetail(BaseModel):
    """
    One specific failure reason produced by the parser.

    The frontend renders each item in the Verification Summary
    Details panel as a red bullet (message) with a muted
    italic sub-line (detail).

    category values:
      expiry        — document is past its expiry date
      validity      — a field failed checksum/format validation
      comparison    — field values differ between two sources
      mrz           — MRZ check failed with no field-level cause
      image_quality — an image quality check failed
      security      — optical security check failed (catch-all)
      unknown       — overall FAIL but no specific cause found
    """
    category: str = Field(
        description="Short failure category slug"
    )
    message: str = Field(
        description="One-line human-readable failure statement"
    )
    detail: str = Field(
        description="Additional context: value, source, page, etc."
    )


class OverallStatuses(BaseModel):
    """
    Top-level summary of all verification checks.
    These are the big-picture results shown at the top of
    the frontend — did this document pass overall?
    """
    overall_status: Optional[int] = Field(
        default=None,
        description="Summary of all checks: 0=PASS, 1=FAIL, 2=N/A"
    )
    optical_status: Optional[int] = Field(
        default=None,
        description="Summary of all optical checks: 0=PASS, 1=FAIL, 2=N/A"
    )
    expiry_check: Optional[int] = Field(
        default=None,
        description="Document expiry check: 0=PASS (not expired), 1=FAIL, 2=N/A"
    )
    mrz_check: Optional[int] = Field(
        default=None,
        description="MRZ validity check: 0=PASS, 1=FAIL, 2=N/A"
    )
    text_check: Optional[int] = Field(
        default=None,
        description="Text fields validity and comparison: 0=PASS, 1=FAIL, 2=N/A"
    )
    security_check: Optional[int] = Field(
        default=None,
        description="Document authenticity check: 0=PASS, 1=FAIL, 2=N/A"
    )


class DocumentCropUrls(BaseModel):
    """
    URLs to access the cropped document images.
    In local mode: localhost file server URLs.
    In S3 mode: presigned S3 URLs (expire after 1 hour).

    Keys are page identifiers ("page_0", "page_1").
    """
    page_0: Optional[str] = Field(
        default=None,
        description="URL to the front page document crop image"
    )
    page_1: Optional[str] = Field(
        default=None,
        description="URL to the back page document crop image (if submitted)"
    )


# ============================================================
# REQUEST SCHEMAS
# What the API expects to receive from the frontend.
# ============================================================

# Note: The actual image file upload uses FastAPI's UploadFile
# type directly in main.py (not a Pydantic schema), because
# multipart file uploads work differently from JSON bodies.
# But we define a metadata schema here for documentation.

class ProcessRequest(BaseModel):
    """
    Optional metadata that can accompany the image upload.
    The images themselves are uploaded as multipart form files.

    Currently no extra metadata is required — this is a
    placeholder for future fields like reference IDs or
    custom tags that a client might want to attach.
    """
    # Placeholder for future request metadata
    # e.g. reference_id: Optional[str] = None
    pass


# ============================================================
# RESPONSE SCHEMAS
# What the API sends back to the frontend.
# ============================================================

class ProcessResponse(BaseModel):
    """
    The response returned after successfully processing a
    document. This is what the frontend receives after
    POST /process completes.

    Contains everything needed to display the full result:
    document type, quality checks, text fields with sources
    and comparisons, and URLs to the document images.
    """
    # The session ID in our database (UUID)
    session_id: uuid.UUID = Field(
        description="Internal session ID (our database UUID)"
    )

    # Regula's own transaction identifier
    transaction_id: str = Field(
        description="Regula's transaction UUID"
    )

    # When Regula processed the document
    processed_at: Optional[str] = Field(
        default=None,
        description="Timestamp when Regula processed the document (ISO 8601)"
    )

    # How long processing took
    elapsed_time_ms: Optional[int] = Field(
        default=None,
        description="Processing time in milliseconds"
    )

    # Top-level check summaries
    statuses: OverallStatuses = Field(
        description="Summary of all verification checks"
    )

    # Document type per page
    doc_type: list[DocumentTypePage] = Field(
        default=[],
        description="Document type information for each submitted page"
    )

    # Image quality per page
    image_quality: list[ImageQualityPage] = Field(
        default=[],
        description="Image quality check results for each submitted page"
    )

    # The 6 text fields with sources and comparisons
    text_fields: TextFieldsResult = Field(
        description="Extracted text fields with per-source values and comparisons"
    )

    # URLs to document crop images
    document_images: DocumentCropUrls = Field(
        description="URLs to access the cropped document images"
    )

    # URL to download the full raw Regula JSON
    raw_json_url: Optional[str] = Field(
        default=None,
        description="URL to download the complete Regula JSON response"
    )

    # Structured list of specific failure reasons for the Details panel
    failure_details: list[FailureDetail] = Field(
        default=[],
        description=(
            "Structured failure reasons. Empty when the document passes. "
            "Each item has category, message, and detail."
        )
    )

    # Document-level verdict that excludes image quality from PASS/FAIL.
    # Image quality is a photo-capture issue, not a document authenticity
    # issue.  0=FAIL, 1=PASS (same enum as CheckResult).
    document_verdict: Optional[int] = Field(
        default=None,
        description=(
            "PASS/FAIL based only on document checks (expiry, MRZ, text, "
            "security). Image quality failures do not influence this value."
        )
    )


class SessionListItem(BaseModel):
    """
    A compact summary of one session shown in the history list.
    Used by GET /sessions which returns multiple sessions.

    Intentionally lighter than ProcessResponse — we don't
    want to return full text fields and quality checks for
    every session in a list view.
    """
    session_id: uuid.UUID
    transaction_id: str
    created_at: datetime
    overall_status: Optional[int] = None
    document_name: Optional[str] = Field(
        default=None,
        description="Document type name from the first page"
    )
    country: Optional[str] = Field(
        default=None,
        description="Issuing country from the first page"
    )
    surname: Optional[str] = Field(
        default=None,
        description="Surname extracted from the document"
    )
    given_names: Optional[str] = Field(
        default=None,
        description="Given names extracted from the document"
    )
    document_number: Optional[str] = Field(
        default=None,
        description="Document number extracted from the document"
    )
    thumbnail_url: Optional[str] = Field(
        default=None,
        description="URL to the front page document crop image"
    )


class SessionListResponse(BaseModel):
    """
    The response for GET /sessions.
    A paginated list of sessions.
    """
    total: int = Field(
        description="Total number of sessions in the database"
    )
    page: int = Field(
        default=1,
        description="Current page number (1-based)"
    )
    page_size: int = Field(
        default=20,
        description="Number of sessions per page"
    )
    sessions: list[SessionListItem] = Field(
        description="Sessions on the current page"
    )


class ErrorResponse(BaseModel):
    """
    Standard error response returned when something goes wrong.
    Every error from every endpoint uses this same shape so
    the frontend can handle errors consistently.

    Example:
    {
      "error": "regula_unavailable",
      "message": "Could not connect to document reader",
      "detail": "Connection refused at http://regula:8080"
    }
    """
    error: str = Field(
        description="Short error code (snake_case, no spaces)"
    )
    message: str = Field(
        description="Human-readable error message for display"
    )
    detail: Optional[str] = Field(
        default=None,
        description="Additional technical detail for debugging"
    )


class HealthResponse(BaseModel):
    """
    Response for GET /health — a simple status check.
    Used by AWS load balancers and monitoring tools to verify
    the service is running correctly.
    """
    status: str = Field(
        description="'ok' if the service is healthy"
    )
    version: str = Field(
        description="Current API version"
    )
    regula_url: str = Field(
        description="Configured Regula URL (for debugging)"
    )
    storage_mode: str = Field(
        description="Current storage mode: 'local' or 's3'"
    )