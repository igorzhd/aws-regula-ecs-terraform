# ============================================================
# storage.py — File Storage Handler
#
# This file handles saving and retrieving files: document
# crop images and raw Regula JSON responses. It supports
# two modes controlled by settings.STORAGE_MODE:
#
#   "local" — saves files to a folder on disk.
#             Used in local development. No AWS needed.
#             Files are saved to: LOCAL_STORAGE_PATH/{transaction_id}/
#
#   "s3"    — uploads files to AWS S3.
#             Used in production. Requires S3_BUCKET_NAME
#             and valid AWS credentials (via ECS task role
#             in production, or AWS CLI locally).
#
# IMPORTANT DESIGN PRINCIPLE:
#   main.py calls save_session_files() and get_file_url()
#   without knowing which mode is active. The mode switch
#   happens entirely inside this file. This means you can
#   switch from local to S3 by changing one environment
#   variable — no other file needs to change.
#
# S3 FOLDER STRUCTURE PER SESSION:
#   sessions/{transaction_id}/page_0_crop.jpg
#   sessions/{transaction_id}/page_1_crop.jpg
#   sessions/{transaction_id}/raw_response.json
# ============================================================

import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

import aiofiles          # async file I/O (non-blocking disk writes)
import boto3             # AWS SDK for S3 operations
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)


# ============================================================
# PUBLIC FUNCTIONS
# These are the only functions main.py calls directly.
# ============================================================

async def save_session_files(
    transaction_id: str,
    document_crops: dict[str, str],
    raw_response: dict,
) -> dict:
    """
    Saves document crop images and the raw Regula JSON to
    storage (local disk or S3, depending on STORAGE_MODE).

    This is called once per processing request, after
    parser.py has extracted the document crops from the
    Regula response.

    Args:
        transaction_id: Regula's transaction UUID. Used as
                        the folder/prefix name in storage.
                        Example: "e80afdab-a1af-4070-912e"

        document_crops: Dict of base64-encoded document crop
                        images from parser.py. Keys are page
                        identifiers ("page_0", "page_1").
                        Example:
                        {
                          "page_0": "/9j/4AAQSkZJRg...",
                          "page_1": "/9j/5BBRlaABc..."
                        }

        raw_response: The full Regula JSON response as a
                      Python dictionary. Saved as JSON file
                      so the user can download it later.

    Returns:
        A dictionary of storage keys/paths for each saved
        file. These keys are stored in the database so the
        application knows where to find the files later.
        Example:
        {
          "doc_crops": {
            "page_0": "sessions/uuid/page_0_crop.jpg",
            "page_1": "sessions/uuid/page_1_crop.jpg"
          },
          "raw_json": "sessions/uuid/raw_response.json"
        }
    """
    if settings.is_local_storage:
        return await _save_local(
            transaction_id, document_crops, raw_response
        )
    else:
        return await _save_s3(
            transaction_id, document_crops, raw_response
        )


async def get_file_url(
    file_key: str,
    filename: Optional[str] = None,
) -> str:
    """
    Returns a URL that can be used to access a stored file.

    In LOCAL mode: returns a path string. The API serves
    local files through a static files endpoint defined
    in main.py, so the URL looks like:
      http://localhost:8001/files/sessions/uuid/page_0_crop.jpg

    In S3 mode: generates a presigned URL — a temporary
    signed URL that gives the holder permission to download
    the file from S3 directly, without needing AWS credentials.
    The URL expires after S3_PRESIGNED_URL_EXPIRY_SECONDS
    (default: 1 hour).

    Args:
        file_key: The storage key returned by save_session_files.
                  Example: "sessions/uuid/page_0_crop.jpg"

        filename: Optional. If provided and in S3 mode, sets
                  the Content-Disposition header so the browser
                  downloads the file with this name instead of
                  the S3 key name.
                  Example: "my_passport_scan.jpg"

    Returns:
        A URL string the frontend can use to access the file.
    """
    if settings.is_local_storage:
        # In local mode, files are served by the FastAPI
        # static files mount defined in main.py
        return f"http://localhost:{settings.API_PORT}/files/{file_key}"
    else:
        return _generate_presigned_url(file_key, filename)


# ============================================================
# PRIVATE — LOCAL STORAGE
# ============================================================

async def _save_local(
    transaction_id: str,
    document_crops: dict[str, str],
    raw_response: dict,
) -> dict:
    """
    Saves files to local disk inside LOCAL_STORAGE_PATH.

    Creates a subfolder per session:
      ./local_storage/{transaction_id}/
          page_0_crop.jpg
          page_1_crop.jpg   (if two pages were submitted)
          raw_response.json

    Uses aiofiles for async file writing so the server is
    not blocked while writing to disk.
    """
    # Build the path to this session's folder.
    # The "sessions/" prefix matches the storage key format so that
    # the static file mount at /files → LOCAL_STORAGE_PATH resolves correctly:
    # URL: /files/sessions/{id}/page_0_crop.jpg → LOCAL_STORAGE_PATH/sessions/{id}/page_0_crop.jpg
    session_folder = Path(settings.LOCAL_STORAGE_PATH) / "sessions" / transaction_id

    # Create the folder (and any parent folders) if it doesn't exist
    # exist_ok=True means no error if the folder already exists
    session_folder.mkdir(parents=True, exist_ok=True)

    logger.info(f"Saving files locally to: {session_folder}")

    saved_crops = {}

    # ----------------------------------------------------------
    # Save each document crop image
    # ----------------------------------------------------------
    for page_key, base64_string in document_crops.items():
        # page_key is like "page_0" or "page_1"
        filename = f"{page_key}_crop.jpg"
        file_path = session_folder / filename

        # Convert base64 string back to raw binary image bytes
        # base64_string might have a data URI prefix like
        # "data:image/jpeg;base64,/9j/4AAQ..." — strip it if present
        if "," in base64_string:
            base64_string = base64_string.split(",", 1)[1]

        image_bytes = base64.b64decode(base64_string)

        # Write image bytes to disk asynchronously
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(image_bytes)

        # Store the relative key (not the full path) so it
        # works the same way as S3 keys
        storage_key = f"sessions/{transaction_id}/{filename}"
        saved_crops[page_key] = storage_key

        logger.debug(
            f"Saved crop image: {file_path} "
            f"({len(image_bytes):,} bytes)"
        )

    # ----------------------------------------------------------
    # Save the raw Regula JSON response
    # ----------------------------------------------------------
    json_filename = "raw_response.json"
    json_path = session_folder / json_filename

    # Serialize the Python dict to a pretty-printed JSON string
    json_string = json.dumps(raw_response, indent=2, ensure_ascii=False)

    async with aiofiles.open(json_path, "w", encoding="utf-8") as f:
        await f.write(json_string)

    json_key = f"sessions/{transaction_id}/{json_filename}"

    logger.debug(f"Saved raw JSON: {json_path}")
    logger.info(
        f"Local save complete. "
        f"{len(saved_crops)} crop(s) + 1 JSON file saved."
    )

    return {
        "doc_crops": saved_crops,
        "raw_json":  json_key,
    }


# ============================================================
# PRIVATE — S3 STORAGE
# ============================================================

async def _save_s3(
    transaction_id: str,
    document_crops: dict[str, str],
    raw_response: dict,
) -> dict:
    """
    Uploads files to AWS S3.

    Uses boto3 (the AWS Python SDK) to upload files to the
    S3 bucket specified in settings.S3_BUCKET_NAME.

    In production on ECS, authentication is handled
    automatically by the ECS task role — no credentials
    needed in the code or environment variables.

    In local development with STORAGE_MODE=s3, boto3 uses
    your AWS CLI credentials (~/.aws/credentials) or the
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment
    variables if set.

    NOTE: boto3 is synchronous (not async). We run it in
    a thread pool to avoid blocking the FastAPI event loop.
    This is done with asyncio.get_event_loop().run_in_executor()
    which is the standard pattern for calling sync code from
    async functions.
    """
    import asyncio

    # Create an S3 client. boto3 figures out credentials
    # automatically from the environment or IAM role.
    s3_client = boto3.client("s3", region_name=settings.AWS_REGION)

    saved_crops = {}

    # ----------------------------------------------------------
    # Upload each document crop image
    # ----------------------------------------------------------
    for page_key, base64_string in document_crops.items():
        filename = f"{page_key}_crop.jpg"
        # S3 key = the "path" within the bucket
        s3_key = f"sessions/{transaction_id}/{filename}"

        # Strip data URI prefix if present
        if "," in base64_string:
            base64_string = base64_string.split(",", 1)[1]

        image_bytes = base64.b64decode(base64_string)

        try:
            # Run the synchronous boto3 upload in a thread pool
            # so it doesn't block the async event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,  # use default thread pool
                lambda: s3_client.put_object(
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=s3_key,
                    Body=image_bytes,
                    ContentType="image/jpeg",
                )
            )
            saved_crops[page_key] = s3_key
            logger.debug(
                f"Uploaded to S3: s3://{settings.S3_BUCKET_NAME}/{s3_key} "
                f"({len(image_bytes):,} bytes)"
            )

        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to upload {s3_key} to S3: {e}")
            raise StorageError(
                f"Failed to upload document crop to S3: {e}"
            ) from e

    # ----------------------------------------------------------
    # Upload the raw JSON response
    # ----------------------------------------------------------
    json_key = f"sessions/{transaction_id}/raw_response.json"
    json_bytes = json.dumps(
        raw_response, indent=2, ensure_ascii=False
    ).encode("utf-8")

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: s3_client.put_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=json_key,
                Body=json_bytes,
                ContentType="application/json",
            )
        )
        logger.debug(f"Uploaded raw JSON to S3: {json_key}")

    except (BotoCoreError, ClientError) as e:
        logger.error(f"Failed to upload raw JSON to S3: {e}")
        raise StorageError(
            f"Failed to upload raw JSON to S3: {e}"
        ) from e

    logger.info(
        f"S3 upload complete. "
        f"{len(saved_crops)} crop(s) + 1 JSON file uploaded to "
        f"s3://{settings.S3_BUCKET_NAME}/sessions/{transaction_id}/"
    )

    return {
        "doc_crops": saved_crops,
        "raw_json":  json_key,
    }


def _generate_presigned_url(
    s3_key: str,
    filename: Optional[str] = None,
) -> str:
    """
    Generates a temporary presigned URL for a file in S3.

    A presigned URL is a special signed URL that allows anyone
    who has it to download a specific file from a private S3
    bucket, without needing their own AWS credentials. The URL
    contains a cryptographic signature that proves it was
    generated by an authorized party, and it expires after
    a set time (S3_PRESIGNED_URL_EXPIRY_SECONDS).

    Args:
        s3_key: The S3 key (path) of the file to link to.
        filename: Optional download filename for the browser.

    Returns:
        A presigned HTTPS URL string. Valid for
        S3_PRESIGNED_URL_EXPIRY_SECONDS seconds.
    """
    s3_client = boto3.client("s3", region_name=settings.AWS_REGION)

    # Extra params control how the browser handles the download
    params = {
        "Bucket": settings.S3_BUCKET_NAME,
        "Key":    s3_key,
    }

    # If a filename is provided, set Content-Disposition so
    # the browser downloads the file with that name
    if filename:
        params["ResponseContentDisposition"] = (
            f'attachment; filename="{filename}"'
        )

    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=settings.S3_PRESIGNED_URL_EXPIRY_SECONDS,
        )
        logger.debug(
            f"Generated presigned URL for {s3_key} "
            f"(expires in {settings.S3_PRESIGNED_URL_EXPIRY_SECONDS}s)"
        )
        return url

    except (BotoCoreError, ClientError) as e:
        logger.error(f"Failed to generate presigned URL for {s3_key}: {e}")
        raise StorageError(
            f"Failed to generate download URL: {e}"
        ) from e


# ============================================================
# CUSTOM EXCEPTION
# ============================================================

class StorageError(Exception):
    """
    Raised when a file storage operation fails — whether
    writing to local disk or uploading to S3.

    Catching this in main.py lets us return a clean error
    message to the frontend instead of a raw Python traceback.
    """
    pass