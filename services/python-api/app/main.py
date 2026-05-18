# ============================================================
# main.py — FastAPI Application Entry Point
#
# This is the main file that runs the entire API. It:
#   1. Creates the FastAPI application
#   2. Sets up the database connection pool
#   3. Defines all HTTP routes (endpoints)
#   4. Orchestrates regula.py, parser.py, storage.py, models.py
#
# ROUTE OVERVIEW:
#   POST /process              — upload image(s), process document
#   GET  /sessions             — list all processed sessions
#   GET  /sessions/{id}        — get full details of one session
#   GET  /sessions/{id}/download — download raw Regula JSON
#   GET  /sessions/{id}/image/{page} — get document crop image URL
#   GET  /health               — service health check
#
# HOW DEPENDENCY INJECTION WORKS:
#   Routes that need a database connection declare:
#     db: AsyncSession = Depends(get_db)
#   FastAPI automatically creates a DB connection, passes it
#   to the function, and closes it when the function finishes
#   — even if an error occurs. This is called "dependency
#   injection" and is much safer than managing connections
#   manually.
#
# HOW ERROR HANDLING WORKS:
#   Each route wraps its logic in try/except blocks.
#   Known errors (RegulaConnectionError, etc.) return clean
#   JSON error responses using the ErrorResponse schema.
#   Unknown errors are caught by a global exception handler
#   at the bottom and return a generic 500 error.
# ============================================================

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (
    FastAPI,
    Request,
    Depends,
    HTTPException,
    Query,
)
from starlette.datastructures import UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy import select, func

from app.config import settings
from app.models import Base, Session as SessionModel
from app.schemas import (
    ProcessResponse,
    SessionListResponse,
    SessionListItem,
    OverallStatuses,
    DocumentTypePage,
    ImageQualityPage,
    ImageQualityCheck,
    TextFieldsResult,
    TextField,
    SourceValue,
    ComparisonResult,
    DocumentCropUrls,
    FailureDetail,
    ErrorResponse,
    HealthResponse,
)
from app.regula import (
    process_document,
    RegulaConnectionError,
    RegulaTimeoutError,
    RegulaProcessingError,
)
from app.parser import parse_regula_response, _build_failure_details
from app.storage import save_session_files, get_file_url, StorageError
import app.models as models

# ----------------------------------------------------------
# Logging setup
# Configure logging for the entire application.
# Every file uses logger = logging.getLogger(__name__) and
# this configuration applies to all of them.
# LOG_LEVEL from settings controls verbosity:
#   DEBUG   → shows everything, good for development
#   INFO    → normal operations
#   WARNING → only problems
# ----------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.DEBUG),
    format="%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# DATABASE SETUP
#
# create_async_engine creates a connection pool — a set of
# reusable database connections. Instead of opening a new
# connection for every request (slow), connections are kept
# open and reused (fast).
#
# async_sessionmaker creates a factory for database sessions.
# Each request gets its own session (think of a session as
# a temporary workspace for database operations). When the
# session is committed, changes are written to the database.
# When it's closed, the connection returns to the pool.
# ============================================================
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    # echo=True logs every SQL query — very useful for debugging
    # but very noisy in production. Only enable in DEBUG mode.
    echo=(settings.LOG_LEVEL.upper() == "DEBUG"),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    # expire_on_commit=False means after committing a session,
    # the objects in that session are still accessible. Without
    # this, accessing an attribute after commit would trigger
    # another database query, which causes errors in async code.
)


# ============================================================
# LIFESPAN — Startup and Shutdown Logic
#
# The lifespan context manager runs code when the app starts
# and when it shuts down. We use it to:
#   - Create database tables on startup (if they don't exist)
#   - Create the local storage folder if needed
#   - Close the database connection pool on shutdown
#
# "async with lifespan(app)" is handled automatically by
# FastAPI — you just define it and FastAPI calls it.
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ----------------------------------------------------------
    # STARTUP
    # ----------------------------------------------------------
    logger.info(f"Starting {settings.APP_TITLE} v{settings.APP_VERSION}")
    logger.info(f"Storage mode: {settings.STORAGE_MODE}")
    logger.info(f"Regula URL: {settings.regula_process_url}")

    # Create all database tables that don't exist yet.
    # In production you would use Alembic migrations instead,
    # but this is convenient for development — it creates the
    # tables automatically on first run.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified/created")

    # Create the local storage folder if in local mode
    if settings.is_local_storage:
        import os
        os.makedirs(settings.LOCAL_STORAGE_PATH, exist_ok=True)
        logger.info(
            f"Local storage folder ready: {settings.LOCAL_STORAGE_PATH}"
        )

    logger.info("API ready to accept requests")

    # "yield" is where the app runs. Everything before yield
    # is startup, everything after yield is shutdown.
    yield

    # ----------------------------------------------------------
    # SHUTDOWN
    # ----------------------------------------------------------
    logger.info("Shutting down, closing database connections...")
    await engine.dispose()
    logger.info("Database connections closed. Goodbye.")


# ============================================================
# FASTAPI APPLICATION
#
# This creates the FastAPI app instance. Everything else
# (routes, middleware, etc.) is registered on this object.
# ============================================================
app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description=(
        "Document verification API. Processes passport and "
        "identity document images using Regula Document Reader "
        "and returns structured verification results."
    ),
    lifespan=lifespan,
)


# ----------------------------------------------------------
# CORS Middleware
#
# CORS (Cross-Origin Resource Sharing) controls which domains
# are allowed to call this API from a browser. Without this,
# your frontend (running on localhost:3000) would be blocked
# from calling the API (running on localhost:8001) because
# they are on different ports — browsers treat that as a
# "different origin" and block the request.
#
# allow_origins=["*"] allows ALL origins. This is fine for
# development but should be restricted to your frontend's
# actual domain in production.
# ----------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # TODO: restrict to frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],       # allow GET, POST, etc.
    allow_headers=["*"],
)


# ----------------------------------------------------------
# Static Files (Local Development Only)
#
# In local mode, the document crop images are saved to disk.
# This mounts the LOCAL_STORAGE_PATH folder as a static file
# server at /files/, so the frontend can access images at:
#   http://localhost:8001/files/sessions/{uuid}/page_0_crop.jpg
#
# In production (S3 mode), images are served via presigned
# S3 URLs and this is not needed.
# ----------------------------------------------------------
if settings.is_local_storage:
    import os
    os.makedirs(settings.LOCAL_STORAGE_PATH, exist_ok=True)
    app.mount(
        "/files",
        StaticFiles(directory=settings.LOCAL_STORAGE_PATH),
        name="files",
    )
    logger.info(f"Static files mounted at /files → {settings.LOCAL_STORAGE_PATH}")


# ============================================================
# DEPENDENCY — Database Session
#
# This function is used as a dependency in routes that need
# a database connection. FastAPI calls it automatically and
# passes the resulting session to the route function.
#
# "yield" makes it a context manager — the session is open
# while the route runs, then closed in the finally block
# no matter what happens (success or error).
#
# Usage in a route:
#   async def my_route(db: AsyncSession = Depends(get_db)):
#       result = await db.execute(...)
# ============================================================
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ============================================================
# ROUTES
# ============================================================

# ----------------------------------------------------------
# GET /health
# Simple health check endpoint.
# Called by AWS load balancers to verify the service is up.
# Also useful to quickly check if the API is running.
# ----------------------------------------------------------
@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check",
)
async def health_check():
    """
    Returns the current health status of the API.
    Always returns 200 OK if the service is running.
    """
    return HealthResponse(
        status="ok",
        version=settings.APP_VERSION,
        regula_url=settings.regula_process_url,
        storage_mode=settings.STORAGE_MODE,
    )


# ----------------------------------------------------------
# POST /process
# The main endpoint. Receives document image(s), runs the
# full processing pipeline, and returns the result.
#
# Accepts multipart form data with:
#   - image_front: required, the front of the document
#   - image_back:  optional, the back of the document
#
# Both files are read directly from request.form() to avoid
# any conflict between FastAPI's File() parameter parser and
# raw form access. Swagger UI sends image_back="" when no
# file is selected; the isinstance(UploadFile) check handles
# that gracefully.
#
# Returns: ProcessResponse with all verification results
# ----------------------------------------------------------
@app.post(
    "/process",
    response_model=ProcessResponse,
    tags=["Document Processing"],
    summary="Process a document image",
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "required": ["image_front"],
                        "properties": {
                            "image_front": {
                                "type": "string",
                                "format": "binary",
                                "description": "Front of the document (passport, ID card, driver's license)",
                            },
                            "image_back": {
                                "type": "string",
                                "format": "binary",
                                "description": "Back of the document (optional, for ID cards and driver's licenses)",
                            },
                        }
                    }
                }
            }
        }
    },
    responses={
        200: {"description": "Document processed successfully"},
        400: {"model": ErrorResponse, "description": "Invalid image or request"},
        503: {"model": ErrorResponse, "description": "Regula service unavailable"},
        504: {"model": ErrorResponse, "description": "Regula service timed out"},
    }
)
async def process(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Upload one or two document images for verification.

    The API will:
    1. Send the image(s) to Regula for processing
    2. Extract document type, image quality, text fields
    3. Save document crop images to storage
    4. Save the session to the database
    5. Return the full structured result
    """

    # ----------------------------------------------------------
    # Step 1: Read uploaded image bytes from the multipart form.
    # Both files are read here so that FastAPI's own form parser
    # does not interfere with our raw form access.
    # ----------------------------------------------------------
    form = await request.form()
    front_val = form.get("image_front")
    back_val  = form.get("image_back")

    # image_front is required
    if not isinstance(front_val, UploadFile):
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error="invalid_image",
                message="image_front is required and must be a file upload",
            ).model_dump()
        )

    image_front: UploadFile = front_val
    # Swagger UI sends "" for unselected optional files — treat non-UploadFile as None
    image_back: Optional[UploadFile] = back_val if isinstance(back_val, UploadFile) else None

    logger.info(
        f"Received processing request. "
        f"front={image_front.filename} "
        f"back={image_back.filename if image_back else 'none'}"
    )

    images: list[bytes] = []

    front_bytes = await image_front.read()
    if not front_bytes:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error="invalid_image",
                message="The front image file is empty",
            ).model_dump()
        )
    images.append(front_bytes)

    # Add the back image if provided
    if image_back:
        back_bytes = await image_back.read()
        if back_bytes:
            images.append(back_bytes)

    # ----------------------------------------------------------
    # Step 2: Send to Regula and get raw JSON response
    # ----------------------------------------------------------
    try:
        logger.info(f"Sending {len(images)} image(s) to Regula...")
        raw_response = await process_document(images)

    except RegulaConnectionError as e:
        logger.error(f"Regula connection failed: {e}")
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error="regula_unavailable",
                message="Document reader service is not available",
                detail=str(e),
            ).model_dump()
        )

    except RegulaTimeoutError as e:
        logger.error(f"Regula timed out: {e}")
        return JSONResponse(
            status_code=504,
            content=ErrorResponse(
                error="regula_timeout",
                message="Document processing timed out. Please try again.",
                detail=str(e),
            ).model_dump()
        )

    except RegulaProcessingError as e:
        logger.error(f"Regula processing error: {e}")
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error="processing_failed",
                message="Could not process the document image",
                detail=str(e),
            ).model_dump()
        )

    # ----------------------------------------------------------
    # Step 3: Parse the Regula response
    # Extract document type, quality, text fields, crops
    # ----------------------------------------------------------
    logger.info("Parsing Regula response...")
    parsed = parse_regula_response(raw_response)

    tx_info          = parsed["transaction_info"]
    statuses         = parsed["statuses"]
    doc_type         = parsed["doc_type"]
    img_quality      = parsed["image_quality"]
    text_fields      = parsed["text_fields"]
    doc_crops        = parsed["document_crops"]
    failure_details  = parsed["failure_details"]
    document_verdict = parsed["document_verdict"]

    transaction_id = tx_info.get("transaction_id")

    # ----------------------------------------------------------
    # Step 4: Save files to storage (local or S3)
    # ----------------------------------------------------------
    try:
        logger.info("Saving files to storage...")
        storage_result = await save_session_files(
            transaction_id=transaction_id,
            document_crops=doc_crops,
            raw_response=raw_response,
        )
    except StorageError as e:
        logger.error(f"Storage failed: {e}")
        # Storage failure is not fatal — we still save to DB
        # and return results. Just log the error and continue
        # with empty storage keys.
        storage_result = {"doc_crops": {}, "raw_json": None}

    # ----------------------------------------------------------
    # Step 5: Save session to the database
    # ----------------------------------------------------------
    logger.info("Saving session to database...")

    # Parse the processed_at timestamp string to a datetime
    # object for the database column
    processed_at = None
    if tx_info.get("processed_at"):
        from datetime import datetime, timezone
        try:
            processed_at = datetime.fromisoformat(
                tx_info["processed_at"].replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            processed_at = None

    # Create the database record
    session_record = SessionModel(
        transaction_id  = transaction_id,
        processed_at    = processed_at,
        elapsed_time_ms = tx_info.get("elapsed_time_ms"),
        regula_version  = tx_info.get("regula_version"),

        # Overall statuses (individual columns)
        overall_status  = statuses.get("overall_status"),
        optical_status  = statuses.get("optical_status"),
        expiry_check    = statuses.get("expiry_check"),
        mrz_check       = statuses.get("mrz_check"),
        text_check      = statuses.get("text_check"),
        security_check  = statuses.get("security_check"),

        # Structured data (JSONB columns)
        doc_type        = doc_type,
        image_quality   = img_quality,
        text_fields     = text_fields,

        # Storage references
        s3_doc_crops    = storage_result.get("doc_crops", {}),
        s3_raw_json     = storage_result.get("raw_json"),

        # Full raw response
        raw_response    = raw_response,
    )

    db.add(session_record)
    await db.commit()
    await db.refresh(session_record)

    logger.info(
        f"Session saved. "
        f"id={session_record.id} "
        f"transaction_id={transaction_id}"
    )

    # ----------------------------------------------------------
    # Step 6: Build URLs for document crops
    # In local mode: http://localhost:8001/files/sessions/...
    # In S3 mode: presigned S3 URLs
    # ----------------------------------------------------------
    crop_keys = storage_result.get("doc_crops", {})
    raw_json_key = storage_result.get("raw_json")

    page_0_url = None
    page_1_url = None

    if crop_keys.get("page_0"):
        page_0_url = await get_file_url(crop_keys["page_0"])
    if crop_keys.get("page_1"):
        page_1_url = await get_file_url(crop_keys["page_1"])

    raw_json_url = None
    if raw_json_key:
        raw_json_url = await get_file_url(
            raw_json_key,
            filename=f"regula_{transaction_id}.json"
        )

    # ----------------------------------------------------------
    # Step 7: Build and return the response
    # Convert our parsed data into the schema structures
    # that FastAPI will serialize to JSON for the frontend.
    # ----------------------------------------------------------
    return ProcessResponse(
        session_id      = session_record.id,
        transaction_id  = transaction_id,
        processed_at    = tx_info.get("processed_at"),
        elapsed_time_ms = tx_info.get("elapsed_time_ms"),

        statuses = OverallStatuses(**statuses),

        doc_type = [
            DocumentTypePage(**page) for page in doc_type
        ],

        image_quality = [
            ImageQualityPage(
                page          = page["page"],
                overall       = page.get("overall"),
                overall_label = page.get("overall_label"),
                checks        = [
                    ImageQualityCheck(**check)
                    for check in page.get("checks", [])
                ]
            )
            for page in img_quality
        ],

        text_fields = TextFieldsResult(
            overall_status = text_fields.get("overall_status"),
            overall_label  = text_fields.get("overall_label"),
            fields         = [
                TextField(
                    field_name        = f["field_name"],
                    field_type        = f["field_type"],
                    overall_status    = f.get("overall_status"),
                    overall_label     = f.get("overall_label"),
                    comparison_status = f.get("comparison_status"),
                    comparison_label  = f.get("comparison_label"),
                    validity_status   = f.get("validity_status"),
                    validity_label    = f.get("validity_label"),
                    sources           = [
                        SourceValue(**s) for s in f.get("sources", [])
                    ],
                    comparisons       = [
                        ComparisonResult(**c) for c in f.get("comparisons", [])
                    ],
                )
                for f in text_fields.get("fields", [])
            ]
        ),

        document_images = DocumentCropUrls(
            page_0 = page_0_url,
            page_1 = page_1_url,
        ),

        raw_json_url = raw_json_url,

        failure_details  = [FailureDetail(**fd) for fd in failure_details],
        document_verdict = document_verdict,
    )


# ----------------------------------------------------------
# GET /sessions
# Returns a paginated list of all processed sessions.
# Used by the frontend history/dashboard view.
# ----------------------------------------------------------
@app.get(
    "/sessions",
    response_model=SessionListResponse,
    tags=["Sessions"],
    summary="List all processed sessions",
)
async def list_sessions(
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Sessions per page"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a paginated list of all document processing sessions,
    most recent first.
    """
    # Count total sessions for pagination info
    count_result = await db.execute(
        select(func.count()).select_from(SessionModel)
    )
    total = count_result.scalar()

    # Fetch the current page of sessions, newest first
    offset = (page - 1) * page_size
    result = await db.execute(
        select(SessionModel)
        .order_by(SessionModel.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    sessions = result.scalars().all()

    def _get_text_field(text_fields: dict, field_type: int) -> str | None:
        for f in (text_fields or {}).get("fields", []):
            if f.get("field_type") == field_type:
                sources = f.get("sources", [])
                return sources[0].get("value") if sources else None
        return None

    # Build list items (lightweight, no full field details)
    items = []
    for s in sessions:
        # Get document name from first doc_type entry if available
        doc_name = None
        country = None
        if s.doc_type and len(s.doc_type) > 0:
            doc_name = s.doc_type[0].get("name")
            country  = s.doc_type[0].get("country")

        # Extract key text fields from JSONB for the history table
        # field_type: 2=Document Number, 8=Surname, 9=Given Names
        tf = s.text_fields or {}
        surname         = _get_text_field(tf, 8)
        given_names     = _get_text_field(tf, 9)
        document_number = _get_text_field(tf, 2)

        # Get thumbnail URL for front page crop
        thumbnail_url = None
        if s.s3_doc_crops and s.s3_doc_crops.get("page_0"):
            try:
                thumbnail_url = await get_file_url(s.s3_doc_crops["page_0"])
            except Exception:
                pass   # thumbnail is non-critical, skip on error

        items.append(SessionListItem(
            session_id      = s.id,
            transaction_id  = s.transaction_id,
            created_at      = s.created_at,
            overall_status  = s.overall_status,
            document_name   = doc_name,
            country         = country,
            surname         = surname,
            given_names     = given_names,
            document_number = document_number,
            thumbnail_url   = thumbnail_url,
        ))

    return SessionListResponse(
        total     = total,
        page      = page,
        page_size = page_size,
        sessions  = items,
    )


# ----------------------------------------------------------
# GET /sessions/{session_id}
# Returns the full details of one session.
# Used by the frontend when the user clicks on a session
# in the history list to see the full results.
# ----------------------------------------------------------
@app.get(
    "/sessions/{session_id}",
    response_model=ProcessResponse,
    tags=["Sessions"],
    summary="Get full details of a session",
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    }
)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the complete verification result for a previously
    processed session, by its UUID.
    """
    # Look up the session in the database
    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = result.scalar_one_or_none()

    if not session:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error="session_not_found",
                message=f"No session found with ID {session_id}",
            ).model_dump()
        )

    # Rebuild image URLs (in case presigned URLs have expired)
    crop_keys   = session.s3_doc_crops or {}
    raw_json_key = session.s3_raw_json

    page_0_url = None
    page_1_url = None
    if crop_keys.get("page_0"):
        page_0_url = await get_file_url(crop_keys["page_0"])
    if crop_keys.get("page_1"):
        page_1_url = await get_file_url(crop_keys["page_1"])

    raw_json_url = None
    if raw_json_key:
        raw_json_url = await get_file_url(
            raw_json_key,
            filename=f"regula_{session.transaction_id}.json"
        )

    # Rebuild text fields from stored JSONB
    tf_data = session.text_fields or {"overall_status": None, "fields": []}

    # Recompute failure details and document verdict from stored data
    statuses_dict = {
        "overall_status": session.overall_status,
        "optical_status": session.optical_status,
        "expiry_check":   session.expiry_check,
        "mrz_check":      session.mrz_check,
        "text_check":     session.text_check,
        "security_check": session.security_check,
    }
    session_failure_details = _build_failure_details(
        statuses_dict,
        tf_data,
        session.image_quality or [],
        session.raw_response or {},
    )
    non_qa = [
        session.expiry_check,
        session.mrz_check,
        session.text_check,
        session.security_check,
    ]
    session_document_verdict = 0 if any(v == 0 for v in non_qa) else 1

    return ProcessResponse(
        session_id      = session.id,
        transaction_id  = session.transaction_id,
        processed_at    = (
            session.processed_at.isoformat()
            if session.processed_at else None
        ),
        elapsed_time_ms = session.elapsed_time_ms,

        statuses = OverallStatuses(
            overall_status = session.overall_status,
            optical_status = session.optical_status,
            expiry_check   = session.expiry_check,
            mrz_check      = session.mrz_check,
            text_check     = session.text_check,
            security_check = session.security_check,
        ),

        doc_type = [
            DocumentTypePage(**page)
            for page in (session.doc_type or [])
        ],

        image_quality = [
            ImageQualityPage(
                page          = page["page"],
                overall       = page.get("overall"),
                overall_label = page.get("overall_label"),
                checks        = [
                    ImageQualityCheck(**c)
                    for c in page.get("checks", [])
                ]
            )
            for page in (session.image_quality or [])
        ],

        text_fields = TextFieldsResult(
            overall_status = tf_data.get("overall_status"),
            overall_label  = tf_data.get("overall_label"),
            fields         = [
                TextField(
                    field_name        = f["field_name"],
                    field_type        = f["field_type"],
                    overall_status    = f.get("overall_status"),
                    overall_label     = f.get("overall_label"),
                    comparison_status = f.get("comparison_status"),
                    comparison_label  = f.get("comparison_label"),
                    validity_status   = f.get("validity_status"),
                    validity_label    = f.get("validity_label"),
                    sources           = [
                        SourceValue(**s) for s in f.get("sources", [])
                    ],
                    comparisons       = [
                        ComparisonResult(**c) for c in f.get("comparisons", [])
                    ],
                )
                for f in tf_data.get("fields", [])
            ]
        ),

        document_images = DocumentCropUrls(
            page_0 = page_0_url,
            page_1 = page_1_url,
        ),

        raw_json_url = raw_json_url,

        failure_details  = [FailureDetail(**fd) for fd in session_failure_details],
        document_verdict = session_document_verdict,
    )


# ----------------------------------------------------------
# GET /sessions/{session_id}/image/{page}
# Returns a fresh URL to a document crop image.
# Useful when a presigned S3 URL has expired and the
# frontend needs a new one.
# ----------------------------------------------------------
@app.get(
    "/sessions/{session_id}/image/{page}",
    tags=["Sessions"],
    summary="Get a fresh image URL for a session",
    responses={
        404: {"model": ErrorResponse, "description": "Session or image not found"},
    }
)
async def get_session_image(
    session_id: uuid.UUID,
    page: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns a fresh presigned URL (or local URL) for the
    document crop image of the specified page.

    page: 0 = front, 1 = back
    """
    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = result.scalar_one_or_none()

    if not session:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error="session_not_found",
                message=f"No session found with ID {session_id}",
            ).model_dump()
        )

    crop_keys = session.s3_doc_crops or {}
    page_key = f"page_{page}"

    if not crop_keys.get(page_key):
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error="image_not_found",
                message=f"No image found for page {page} in session {session_id}",
            ).model_dump()
        )

    url = await get_file_url(crop_keys[page_key])
    return {"url": url, "page": page, "session_id": str(session_id)}


# ----------------------------------------------------------
# GET /sessions/{session_id}/download
# Streams the raw Regula JSON as a file download.
# ----------------------------------------------------------
@app.get(
    "/sessions/{session_id}/download",
    tags=["Sessions"],
    summary="Download raw Regula JSON for a session",
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    }
)
async def download_session_json(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the full raw Regula JSON response as a downloadable file.
    The browser will prompt to save it as regula_{transaction_id}.json.
    """
    import json as _json

    result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = result.scalar_one_or_none()

    if not session:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error="session_not_found",
                message=f"No session found with ID {session_id}",
            ).model_dump()
        )

    json_bytes = _json.dumps(
        session.raw_response, indent=2, ensure_ascii=False
    ).encode("utf-8")

    return Response(
        content=json_bytes,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="regula_{session.transaction_id}.json"'
            )
        }
    )


# ============================================================
# GLOBAL EXCEPTION HANDLER
#
# Catches any unhandled exception that slips through the
# try/except blocks in individual routes. Without this,
# FastAPI would return a raw Python traceback to the frontend
# (a security risk) or an unhelpful 500 error.
#
# This returns a clean JSON error response instead.
# ============================================================
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(
        f"Unhandled exception on {request.method} {request.url}: "
        f"{type(exc).__name__}: {exc}",
        exc_info=True,   # includes the full stack trace in logs
    )
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="internal_error",
            message="An unexpected error occurred. Please try again.",
            detail=str(exc) if settings.LOG_LEVEL.upper() == "DEBUG" else None,
        ).model_dump()
    )