# ============================================================
# regula.py — Regula Document Reader Client
#
# This file handles ALL communication with the Regula
# Document Reader container. It has one main job:
#   1. Receive image bytes (raw file data)
#   2. Build the JSON payload Regula expects
#   3. Send it to the Regula container via HTTP
#   4. Return the raw JSON response
#
# IT DOES NOT:
#   - Parse or interpret the response (that's parser.py)
#   - Save anything to the database or S3 (that's storage.py)
#   - Know anything about FastAPI routes (that's main.py)
#
# WHY ISOLATE THIS:
#   If Regula's API changes, only this file needs to change.
#   Every other file just calls process_document() and gets
#   a dictionary back without knowing how Regula works.
#
# ASYNC EXPLAINED:
#   Functions marked "async def" are non-blocking. When this
#   file sends a request to Regula and waits for a response
#   (which can take several seconds), the server is NOT
#   frozen — it can handle other incoming requests at the
#   same time. This is why we use httpx.AsyncClient instead
#   of the simpler "requests" library.
# ============================================================

import base64          # converts binary image data to text
import logging         # for recording what's happening
from typing import Optional

import httpx           # async HTTP client (like "requests" but async)

from app.config import settings

# ----------------------------------------------------------
# Logger setup
# Every file in the project creates its own logger named
# after the file. This makes it easy to find which file
# produced which log message.
# Example log output:
#   [app.regula] INFO  Sending 1 image(s) to Regula...
#   [app.regula] INFO  Regula responded in 1823ms
#   [app.regula] ERROR Regula request failed: Connection refused
# ----------------------------------------------------------
logger = logging.getLogger(__name__)


# ----------------------------------------------------------
# IMAGE QUALITY CHECK TYPE MAPPING
# Regula returns image quality check types as integers.
# This dictionary maps those integers to human-readable names
# so the rest of the application doesn't have to know about
# Regula's internal numbering system.
#
# Source: Regula ImageQualityCheckType enumeration
# ----------------------------------------------------------
IMAGE_QUALITY_TYPE_NAMES = {
    0: "Image Glare",
    1: "Image Focus",
    2: "Image Resolution",
    3: "Image Colorness",
    4: "Perspective Angle",
    5: "Bounds Valid",
    6: "Screen Capture",
    7: "Portrait",
    8: "Handwritten Statement",
    9: "Image Brightness",
}

# ----------------------------------------------------------
# CHECK RESULT MAPPING
# Regula uses integers for check results throughout the
# entire response. This dictionary maps them to readable
# strings. Used here for logging, and also exported for
# use in parser.py.
#
# 0 = OK / PASS
# 1 = ERROR / FAIL
# 2 = WAS NOT DONE / NOT APPLICABLE
# ----------------------------------------------------------
CHECK_RESULT = {
    0: "PASS",
    1: "FAIL",
    2: "N/A",
}


def _image_to_base64(image_bytes: bytes) -> str:
    """
    Converts raw image bytes to a base64-encoded string.

    Regula does not accept raw image files — it expects images
    as base64 strings inside a JSON payload. Base64 is a way
    to represent binary data (like an image) as plain text
    characters so it can be embedded in JSON.

    Example:
        Input:  b'\\xff\\xd8\\xff...'  (raw JPEG bytes)
        Output: '/9j/4AAQSkZJRg...'   (base64 string)

    Args:
        image_bytes: The raw binary content of an image file.

    Returns:
        A base64-encoded string representation of the image.
    """
    # base64.b64encode() returns bytes, so we decode to str
    return base64.b64encode(image_bytes).decode("utf-8")


def _build_request_payload(
    images: list[bytes],
    scenario: str,
) -> dict:
    """
    Builds the JSON payload that Regula's /api/process
    endpoint expects.

    Regula's expected payload structure:
    {
      "processParam": {
        "scenario": "FullProcess",
        "returnUncroppedImage": true,
        "returnCroppedBarcode": false
      },
      "List": [
        {
          "ImageData": { "image": "<base64 string>" },
          "light": 6,
          "page_idx": 0
        },
        {
          "ImageData": { "image": "<base64 string>" },
          "light": 6,
          "page_idx": 1
        }
      ]
    }

    Each image entry contains only "ImageData" — no "light" or
    "page_idx" fields. When those fields are present Regula only
    fully processes the first image and suppresses barcode detection
    on subsequent images. Omitting them lets Regula auto-assign page
    indices by position (first = front/page 0, second = back/page 1)
    and return crops + BARCODE text fields for all pages.

    Args:
        images: List of raw image bytes. One item = one image.
                The first item is always the front of the
                document. The second (if present) is the back.
        scenario: The Regula processing scenario to use.
                  Comes from settings.REGULA_SCENARIO.

    Returns:
        A dictionary ready to be serialized as JSON and sent
        to Regula.
    """
    # Build the list of image entries, one per uploaded image.
    # Do NOT include "light" or "page_idx" — when those fields are present
    # Regula only fully processes the first image and skips barcode detection
    # on subsequent images. Without them, Regula auto-assigns page indices by
    # position (first = front, second = back) and returns crops + barcode data
    # for all submitted pages, enabling VISUAL↔BARCODE cross-comparison.
    image_list = []
    for image_bytes in images:
        image_list.append({
            "ImageData": {
                "image": _image_to_base64(image_bytes)
            }
        })

    return {
        "processParam": {
            # Which set of checks to run on the document
            "scenario": scenario,

            # Keep uncropped images out of the response — they
            # double the payload size and we don't use them.
            # fieldType=207 (the perspective-corrected crop) is
            # always returned regardless of this flag.
            "returnUncroppedImage": False,

            # We don't need cropped barcode images
            "returnCroppedBarcode": False,
        },
        "List": image_list
    }


async def process_document(
    images: list[bytes],
    scenario: Optional[str] = None,
) -> dict:
    """
    Sends document image(s) to Regula for processing and
    returns the raw JSON response as a Python dictionary.

    This is the main function that other files call. It:
      1. Builds the request payload with base64 images
      2. Sends it to Regula via async HTTP POST
      3. Checks for errors (HTTP errors, timeouts, etc.)
      4. Returns the full parsed JSON response

    Args:
        images: List of raw image bytes to process.
                Minimum 1 image (document front).
                Maximum 2 images (front + back).

        scenario: Optional override for the processing
                  scenario. If not provided, uses the value
                  from settings.REGULA_SCENARIO ("FullProcess"
                  by default).

    Returns:
        The full Regula JSON response as a Python dictionary.
        This is the raw, unmodified response — parsing it
        into our own structure is done in parser.py.

    Raises:
        RegulaConnectionError: If the Regula container is
            unreachable (not running, wrong URL, etc.)
        RegulaProcessingError: If Regula returns an error
            response (bad license, invalid image, etc.)
        RegulaTimeoutError: If Regula takes too long to
            respond (longer than REGULA_TIMEOUT_SECONDS)
    """
    # Use the configured scenario or the override
    active_scenario = scenario or settings.REGULA_SCENARIO

    logger.info(
        f"Sending {len(images)} image(s) to Regula "
        f"[scenario={active_scenario}, "
        f"url={settings.regula_process_url}]"
    )

    # Build the JSON payload
    payload = _build_request_payload(images, active_scenario)

    # ----------------------------------------------------------
    # Send the request using httpx AsyncClient
    #
    # "async with" creates the HTTP client and automatically
    # closes it when done (even if an error occurs).
    #
    # timeout=settings.REGULA_TIMEOUT_SECONDS tells httpx to
    # give up and raise an error if Regula hasn't responded
    # within that many seconds. Without a timeout, the server
    # could hang forever waiting for Regula.
    # ----------------------------------------------------------
    try:
        async with httpx.AsyncClient(
            timeout=settings.REGULA_TIMEOUT_SECONDS
        ) as client:

            response = await client.post(
                url=settings.regula_process_url,
                json=payload,    # httpx serializes dict to JSON automatically
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
            )

            # Log how long Regula took to respond
            logger.info(
                f"Regula responded with status {response.status_code} "
                f"in {response.elapsed.total_seconds() * 1000:.0f}ms"
            )

            # --------------------------------------------------
            # Check for HTTP error status codes (4xx, 5xx).
            # raise_for_status() does nothing if the response
            # was successful (2xx), but raises an httpx error
            # if it was a 4xx or 5xx response.
            # We catch this below and convert it to our own
            # custom error type.
            # --------------------------------------------------
            response.raise_for_status()

            # Parse the JSON response body into a Python dict
            result = response.json()

            # --------------------------------------------------
            # Check Regula's own result code inside the response.
            # Even with an HTTP 200 OK, Regula might indicate
            # an error in the body (e.g. bad license = 403,
            # or processing failure).
            # CoreLibResultCode = 0 means success.
            # --------------------------------------------------
            core_result = result.get("CoreLibResultCode", -1)
            if core_result != 0:
                error_msg = (
                    f"Regula processing failed. "
                    f"CoreLibResultCode={core_result}"
                )
                logger.error(error_msg)
                raise RegulaProcessingError(error_msg, result)

            logger.info(
                f"Regula processing successful. "
                f"TransactionID={result.get('TransactionInfo', {}).get('TransactionID')}"
            )

            return result

    except httpx.TimeoutException as e:
        # Regula took too long to respond
        error_msg = (
            f"Regula request timed out after "
            f"{settings.REGULA_TIMEOUT_SECONDS}s"
        )
        logger.error(error_msg)
        raise RegulaTimeoutError(error_msg) from e

    except httpx.ConnectError as e:
        # Could not reach the Regula container at all
        error_msg = (
            f"Could not connect to Regula at "
            f"{settings.regula_process_url}. "
            f"Is the Regula container running?"
        )
        logger.error(error_msg)
        raise RegulaConnectionError(error_msg) from e

    except httpx.HTTPStatusError as e:
        # Regula returned a 4xx or 5xx HTTP status
        error_msg = (
            f"Regula returned HTTP {e.response.status_code}. "
            f"Response: {e.response.text[:500]}"
        )
        logger.error(error_msg)
        raise RegulaProcessingError(error_msg) from e


# ============================================================
# CUSTOM EXCEPTIONS
#
# Custom exception classes make error handling cleaner.
# Instead of catching generic "Exception" everywhere, other
# files can catch specific error types and handle each case
# differently.
#
# Example in main.py:
#   try:
#       result = await process_document(images)
#   except RegulaConnectionError:
#       return {"error": "Document reader is not available"}
#   except RegulaTimeoutError:
#       return {"error": "Processing took too long"}
#   except RegulaProcessingError:
#       return {"error": "Could not process the document"}
# ============================================================

class RegulaError(Exception):
    """
    Base class for all Regula-related errors.
    Catching this catches any of the three specific errors
    below. Useful when you want to handle all Regula errors
    the same way.
    """
    pass


class RegulaConnectionError(RegulaError):
    """
    Raised when the Regula container cannot be reached at all.
    Most likely cause: Regula container is not running, or
    the REGULA_URL in .env is wrong.
    """
    pass


class RegulaTimeoutError(RegulaError):
    """
    Raised when Regula takes longer than REGULA_TIMEOUT_SECONDS
    to respond. Could mean the document is unusually complex,
    or Regula is overloaded.
    """
    pass


class RegulaProcessingError(RegulaError):
    """
    Raised when Regula is reachable but returns an error.
    Most likely causes:
      - Invalid or expired license
      - Corrupt or unreadable image
      - Unsupported document type
      - Internal Regula error

    Stores the raw Regula response if available, so the
    caller can inspect it for debugging.
    """
    def __init__(self, message: str, response: Optional[dict] = None):
        super().__init__(message)
        # Keep the raw response for debugging purposes
        self.regula_response = response