# ============================================================
# parser.py — Regula JSON Response Parser
#
# This file takes the raw JSON response from Regula (a large
# dictionary with 21+ containers) and extracts exactly what
# this application needs for the database and frontend.
#
# HOW IT WORKS:
#   - One private function per extraction task
#   - One public function (parse_regula_response) that calls
#     all the private ones and returns a single clean result
#   - main.py only ever calls parse_regula_response()
#
# RESULT TYPES (Regula's container numbering system):
#   result_type 9  → OneCandidate    → document type info
#   result_type 30 → ImageQualityCheckList → quality checks
#   result_type 33 → Status          → overall check results
#   result_type 36 → Text            → all text fields
#   result_type 37 → Images          → graphic fields
#
# DEFENSIVE PROGRAMMING NOTE:
#   Every extraction function uses .get() with fallback values
#   instead of direct dictionary access (data["key"]).
#   This means if Regula changes its response structure or a
#   field is missing, the parser returns None/empty instead
#   of crashing. Regula's response can vary by document type.
# ============================================================

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ----------------------------------------------------------
# CONSTANTS
# These field type integers come from Regula's
# TextFieldType enumeration. We only extract the 6 fields
# the frontend needs to display.
# ----------------------------------------------------------

# The 6 text field types we want to extract from result_type 36
TARGET_TEXT_FIELDS = {
    2:  "Document Number",
    3:  "Date of Expiry",
    4:  "Date of Issue",
    5:  "Date of Birth",
    8:  "Surname",
    9:  "Given Names",
}

# Image field type for document crops (full document image)
# fieldType 207 = "Document front side" in Regula's enumeration
DOCUMENT_CROP_FIELD_TYPE = 207

# Image quality check type names (Regula ImageQualityCheckType)
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

# Regula CheckResult enum (eCheckResult / CheckResult, all SDK versions)
#   0 = ERROR        → check performed, result NEGATIVE  → FAIL
#   1 = OK           → check performed, result POSITIVE  → PASS
#   2 = WAS_NOT_DONE → check was not performed           → N/A
# This mapping applies to every status/validity/comparison integer field
# in the SDK response: field.status, field.validityStatus, field.comparisonStatus,
# validityList[].status (originalValidity), comparisonList[].status, etc.
CHECK_RESULT_NAMES = {
    0: "FAIL",  # ERROR
    1: "PASS",  # OK
    2: "N/A",   # WAS_NOT_DONE
}

# TextFieldType integers for Regula's internal checksum/check-digit fields and
# low-level machine fields that carry no user-interpretable meaning.
# Excluded from all failure messages.
#   40–44, 80–84  → MRZ check-digit fields
#   194, 195      → internal composite fields
#   35            → Composite (internal)
#   51            → MRZ Line (raw MRZ string, not a named field)
#   172           → Surname and Given Names (combined; duplicates individual fields)
_CHECKSUM_FIELD_TYPES = frozenset({
    35, 40, 41, 42, 43, 44, 51,
    80, 81, 82, 83, 84,
    172, 194, 195,
    364,   # RemainderTerm (months to expire) — internal computed field
})

# Failure descriptions for image quality checks used in section E of
# _build_failure_details().  result=0 means the check RAN and FAILED.
_IMAGE_QUALITY_FAIL_DESCRIPTIONS: dict[int, str] = {
    0: "Glare detected on document",
    1: "Image is blurry",
    2: "Image resolution too low",
    3: "Image appears grayscale",
    4: "Document angle too steep",
    5: "Document not fully in frame",
    6: "Appears to be a photo of a screen",
    7: "Portrait not detected",
    8: "Handwritten text detected",
    9: "Too dark or overexposed",
}


# ============================================================
# PRIVATE HELPER FUNCTIONS
# Each function extracts one specific piece of data from the
# Regula response. They are private (underscore prefix) and
# only called by parse_regula_response() at the bottom.
# ============================================================

def _get_containers_by_type(
    regula_response: dict,
    result_type: int
) -> list[dict]:
    """
    Finds all containers in the Regula response that match
    a given result_type.

    Regula's ContainerList.List is a flat array of containers,
    each with a "result_type" field. Multiple containers can
    share the same result_type (e.g. one per page).

    Args:
        regula_response: The full Regula JSON as a dict.
        result_type: The integer type to find (e.g. 30).

    Returns:
        A list of matching container dicts. Empty list if none
        found (never raises an error).
    """
    containers = (
        regula_response
        .get("ContainerList", {})
        .get("List", [])
    )
    return [
        c for c in containers
        if c.get("result_type") == result_type
    ]


def _extract_overall_statuses(regula_response: dict) -> dict:
    """
    Extracts the top-level check statuses from result_type 33
    (the Status container).

    These are the summary results of all checks Regula ran:
    did the document pass overall? Did MRZ check pass?
    Is the document expired? etc.

    Returns a flat dictionary of status integers where:
        0 = PASS, 1 = FAIL, 2 = N/A (not performed)
    """
    containers = _get_containers_by_type(regula_response, 33)

    if not containers:
        logger.warning("No Status container (result_type 33) found in response")
        return {}

    # There should only be one Status container
    status = containers[0].get("Status", {})
    optical = status.get("detailsOptical", {})

    return {
        "overall_status":  status.get("overallStatus"),
        "optical_status":  status.get("optical"),
        "expiry_check":    optical.get("expiry"),
        "mrz_check":       optical.get("mrz"),
        "text_check":      optical.get("text"),
        "security_check":  optical.get("security"),
    }


def _extract_doc_type(regula_response: dict) -> list[dict]:
    """
    Extracts document type information from result_type 9
    (OneCandidate containers).

    There is one OneCandidate container per page submitted.
    For a passport front + back, you get two containers.

    Each container tells you: what kind of document is this?
    Which country issued it? What year? What format?

    Returns:
        A list of dicts, one per page. Example:
        [
          {
            "page": 0,
            "name": "United States - ePassport (2020)",
            "country": "United States",
            "icao_code": "USA",
            "doc_type": 11,
            "doc_format": 2,
            "doc_year": "2020"
          }
        ]
    """
    containers = _get_containers_by_type(regula_response, 9)
    result = []

    for container in containers:
        candidate = container.get("OneCandidate", {})
        fds = candidate.get("FDSIDList", {})

        page_data = {
            # page_idx is the index of the page this result
            # corresponds to (0 = front, 1 = back)
            "page":       container.get("page_idx", 0),
            "name":       candidate.get("DocumentName"),
            "country":    fds.get("dCountryName"),
            "icao_code":  fds.get("ICAOCode"),
            "doc_type":   fds.get("dType"),
            "doc_format": fds.get("dFormat"),
            "doc_year":   fds.get("dYear"),
        }
        result.append(page_data)
        logger.debug(
            f"Doc type page {page_data['page']}: "
            f"{page_data['name']} ({page_data['country']})"
        )

    return result


def _extract_image_quality(regula_response: dict) -> list[dict]:
    """
    Extracts image quality check results from result_type 30
    (ImageQualityCheckList containers).

    There is one ImageQualityCheckList container per page.
    Each container has an overall quality result and a list
    of individual checks (focus, glare, resolution, etc.).

    Returns:
        A list of dicts, one per page. Example:
        [
          {
            "page": 0,
            "overall": 1,
            "overall_label": "FAIL",
            "checks": [
              {
                "type": 1,
                "name": "Image Focus",
                "result": 1,
                "result_label": "FAIL",
                "probability": 99,
                "mean": -3.94
              },
              {
                "type": 0,
                "name": "Image Glare",
                "result": 0,
                "result_label": "PASS",
                "probability": 5,
                "mean": 0.0
              },
              ...
            ]
          }
        ]
    """
    containers = _get_containers_by_type(regula_response, 30)
    result = []

    for container in containers:
        iq = container.get("ImageQualityCheckList", {})
        overall = iq.get("result")

        checks = []
        for check in iq.get("List", []):
            check_type = check.get("type")
            check_result = check.get("result")

            checks.append({
                "type":         check_type,
                "name":         IMAGE_QUALITY_TYPE_NAMES.get(
                                    check_type,
                                    f"Check type {check_type}"
                                ),
                "result":       check_result,
                "result_label": CHECK_RESULT_NAMES.get(check_result, "UNKNOWN"),
                "probability":  check.get("probability", 0),
                "mean":         round(check.get("mean", 0.0), 3),
            })

        page_data = {
            "page":          container.get("page_idx", len(result)),
            "overall":       overall,
            "overall_label": CHECK_RESULT_NAMES.get(overall, "UNKNOWN"),
            "checks":        checks,
        }
        result.append(page_data)
        logger.debug(
            f"Image quality page {page_data['page']}: "
            f"{page_data['overall_label']} "
            f"({len(checks)} checks)"
        )

    return result


def _extract_text_fields(regula_response: dict) -> dict:
    """
    Extracts the 6 target text fields from result_type 36
    (the Text container).

    This is the most complex extraction because each field
    can have multiple sources (MRZ, VISUAL, BARCODE), and
    Regula also provides cross-comparison results between
    those sources.

    HOW REGULA STRUCTURES TEXT DATA:
        Text.fieldList is a list of fields.
        Each field has:
          - fieldType: integer identifying which field this is
            (2=document number, 5=date of birth, etc.)
          - fieldName: human-readable name
          - status: overall result for this field (0/1/2)
          - validityStatus: was the value format valid?
          - comparisonStatus: did all sources agree?
          - valueList: list of values, one per source
              Each value has:
              - source: "MRZ", "VISUAL", or "BARCODE"
              - value: the actual text value
              - originalValidity: was this source's value valid?
          - comparisonList: list of pairwise comparisons
              Each comparison has:
              - sourceLeft: first source name
              - sourceRight: second source name
              - status: did they match? (0=match, 1=mismatch)

    Returns:
        A dict with overall status and a list of field dicts.
        Example:
        {
          "overall_status": 0,
          "overall_label": "PASS",
          "fields": [
            {
              "field_name": "Document Number",
              "field_type": 2,
              "overall_status": 0,
              "overall_label": "PASS",
              "comparison_status": 0,
              "comparison_label": "PASS",
              "validity_status": 1,
              "validity_label": "FAIL",
              "sources": [
                {
                  "source": "MRZ",
                  "value": "E00007929",
                  "validity": 1,
                  "validity_label": "FAIL"
                },
                {
                  "source": "VISUAL",
                  "value": "E00007929",
                  "validity": 1,
                  "validity_label": "FAIL"
                }
              ],
              "comparisons": [
                {
                  "source_left": "MRZ",
                  "source_right": "VISUAL",
                  "status": 0,
                  "status_label": "PASS",
                  "match": true
                }
              ]
            },
            ...
          ]
        }
    """
    # result_type 36 is the Text container
    containers = _get_containers_by_type(regula_response, 36)

    if not containers:
        logger.warning("No Text container (result_type 36) found")
        return {"overall_status": None, "fields": []}

    # There is typically only one Text container
    text_container = containers[0]
    text_data = text_container.get("Text", {})

    overall_status = text_data.get("status")
    field_list = text_data.get("fieldList", [])

    extracted_fields = []

    for field in field_list:
        field_type = field.get("fieldType")

        # Skip fields we don't need — only process the 6 target fields
        if field_type not in TARGET_TEXT_FIELDS:
            continue

        field_name = TARGET_TEXT_FIELDS[field_type]
        overall = field.get("status")
        comparison_status = field.get("comparisonStatus")
        validity_status = field.get("validityStatus")

        # --------------------------------------------------
        # Extract per-source values
        # valueList contains one entry per source that
        # provided a value for this field.
        # --------------------------------------------------
        sources = []
        for value_entry in field.get("valueList", []):
            source_name = value_entry.get("source", "UNKNOWN")
            validity = value_entry.get("originalValidity")

            sources.append({
                "source":        source_name,
                "value":         value_entry.get("value", ""),
                "validity":      validity,
                "validity_label": CHECK_RESULT_NAMES.get(validity, "UNKNOWN"),
                # pageIndex tells us which document page this
                # value came from (0=front, 1=back)
                "page_index":    value_entry.get("pageIndex", 0),
            })

        # --------------------------------------------------
        # Extract cross-comparison results
        # comparisonList contains pairwise comparisons between
        # sources. For example, if MRZ and VISUAL both have
        # a value for Document Number, Regula compares them
        # and tells you if they match.
        # --------------------------------------------------
        comparisons = []
        for comp in field.get("comparisonList", []):
            comp_status = comp.get("status")
            comparisons.append({
                "source_left":  comp.get("sourceLeft", ""),
                "source_right": comp.get("sourceRight", ""),
                "status":       comp_status,
                "status_label": CHECK_RESULT_NAMES.get(comp_status, "UNKNOWN"),
                # "match" is a convenience boolean for the frontend
                "match":        comp_status == 0,
            })

        extracted_fields.append({
            "field_name":        field_name,
            "field_type":        field_type,
            "overall_status":    overall,
            "overall_label":     CHECK_RESULT_NAMES.get(overall, "UNKNOWN"),
            "comparison_status": comparison_status,
            "comparison_label":  CHECK_RESULT_NAMES.get(
                                     comparison_status, "UNKNOWN"
                                 ),
            "validity_status":   validity_status,
            "validity_label":    CHECK_RESULT_NAMES.get(
                                     validity_status, "UNKNOWN"
                                 ),
            "sources":           sources,
            "comparisons":       comparisons,
        })

        logger.debug(
            f"Field '{field_name}': "
            f"overall={CHECK_RESULT_NAMES.get(overall)} "
            f"comparison={CHECK_RESULT_NAMES.get(comparison_status)} "
            f"sources={[s['source'] for s in sources]}"
        )

    logger.info(
        f"Extracted {len(extracted_fields)} text fields "
        f"(overall: {CHECK_RESULT_NAMES.get(overall_status)})"
    )

    return {
        "overall_status": overall_status,
        "overall_label":  CHECK_RESULT_NAMES.get(overall_status, "UNKNOWN"),
        "fields":         extracted_fields,
    }


def _extract_document_crops(regula_response: dict) -> dict[str, str]:
    """
    Extracts base64-encoded document crop images from
    result_type 37 (the Images container).

    We only want fieldType 207 ("Document front side") which
    is the full cropped image of the entire document page.

    We do NOT want:
      - fieldType 201 (Portrait — just the face photo)
      - fieldType 204 (Signature)
      - fieldType 210 (Ghost portrait)

    There can be one crop per submitted page (0=front, 1=back).

    Returns:
        A dict keyed by page number. Example:
        {
          "page_0": "<base64 encoded JPEG string>",
          "page_1": "<base64 encoded JPEG string>"
        }

        The base64 strings are the raw image data that will
        be decoded and saved to S3 (or local disk) by
        storage.py. The actual S3 keys are stored in the
        database, not the base64 strings.
    """
    containers = _get_containers_by_type(regula_response, 37)

    if not containers:
        logger.warning("No Images container (result_type 37) found")
        return {}

    # When two images are submitted separately, Regula returns one Images container
    # per page rather than a single combined container. Iterate all containers
    # so crops from both pages are collected.
    crops = {}

    for images_container in containers:
        images_data = images_container.get("Images", {})
        field_list = images_data.get("fieldList", [])

        for field in field_list:
            # Only process document crops, skip portrait/signature/etc.
            if field.get("fieldType") != DOCUMENT_CROP_FIELD_TYPE:
                continue

            for value_entry in field.get("valueList", []):
                # containerType=1  → perspective-corrected crop  (what we want)
                # containerType=16 → original uncropped image    (skip)
                # Without this filter the uncropped version overwrites the crop
                # because both share the same pageIndex.
                if value_entry.get("containerType") != 1:
                    continue

                page_index = value_entry.get("pageIndex", 0)
                base64_image = value_entry.get("value", "")

                if base64_image:
                    key = f"page_{page_index}"
                    crops[key] = base64_image
                    logger.debug(
                        f"Document crop found: page={page_index} "
                        f"size={len(base64_image)} chars"
                    )

    logger.info(f"Extracted {len(crops)} document crop image(s)")
    return crops


def _extract_transaction_info(regula_response: dict) -> dict:
    """
    Extracts metadata from the TransactionInfo block.

    This is the top-level metadata about the processing job:
    when it happened, what SDK version was used, how long it
    took, and Regula's own transaction identifier.

    Returns:
        A dict with transaction metadata fields.
    """
    tx = regula_response.get("TransactionInfo", {})

    return {
        "transaction_id":  tx.get("TransactionID"),
        "processed_at":    tx.get("DateTime"),
        "regula_version":  tx.get("Version"),
        "elapsed_time_ms": regula_response.get("elapsedTime"),
    }


# ============================================================
# FULL FIELD FAILURE SCANNER
# Reads result_type 36 without the TARGET_TEXT_FIELDS filter
# so _build_failure_details can find failures on any field,
# not just the 6 fields shown in the display table.
# ============================================================

def _extract_all_failing_fields(regula_response: dict) -> list[dict]:
    """
    Returns a normalized list of every field in result_type 36
    that has a validity or comparison failure, regardless of
    fieldType — including fields outside TARGET_TEXT_FIELDS
    (e.g. Inventory Number, Authority, DL Class, Address).

    Internal checksum/machine field types in _CHECKSUM_FIELD_TYPES
    are silently skipped.

    Each returned dict has:
      field_name        — human-readable name (or "Field <type>")
      field_type        — integer fieldType
      validity_status   — 0=FAIL, 1=PASS, 2=N/A
      comparison_status — 0=FAIL, 1=PASS, 2=N/A
      sources           — list of {source, value, validity}
      comparisons       — list of {source_left, source_right, status}
    """
    containers = _get_containers_by_type(regula_response, 36)
    if not containers:
        return []

    field_list = containers[0].get("Text", {}).get("fieldList", [])
    result = []

    for field in field_list:
        field_type = field.get("fieldType")
        if field_type in _CHECKSUM_FIELD_TYPES:
            continue

        validity_status    = field.get("validityStatus")
        comparison_status  = field.get("comparisonStatus")

        # Only include fields that actually failed something
        if validity_status != 0 and comparison_status != 0:
            continue

        sources = [
            {
                "source":   v.get("source", "UNKNOWN"),
                "value":    v.get("value", ""),
                "validity": v.get("originalValidity"),
            }
            for v in field.get("valueList", [])
        ]

        comparisons = [
            {
                "source_left":  c.get("sourceLeft", ""),
                "source_right": c.get("sourceRight", ""),
                "status":       c.get("status"),
            }
            for c in field.get("comparisonList", [])
        ]

        result.append({
            "field_name":        field.get("fieldName") or f"Field {field_type}",
            "field_type":        field_type,
            "validity_status":   validity_status,
            "comparison_status": comparison_status,
            "sources":           sources,
            "comparisons":       comparisons,
        })

    logger.debug(f"_extract_all_failing_fields: {len(result)} failing field(s) found")
    return result


# ============================================================
# FAILURE DETAIL BUILDER
# Converts the already-extracted statuses / text_fields /
# image_quality data into a flat list of human-readable
# failure reasons for the frontend Details panel.
# ============================================================

def _build_failure_details(
    statuses: dict,
    text_fields: dict,
    image_quality: list,
    regula_response: dict,
) -> list[dict]:
    """
    Generates a structured list of specific failure reasons.
    Each item has:
      category — short slug (expiry, validity, comparison, mrz,
                              image_quality, security, unknown)
      message  — one-line human-readable failure statement
      detail   — additional context (value, source, page, etc.)

    Sections B and C scan the FULL result_type 36 field list via
    _extract_all_failing_fields(), not just the 6 display fields,
    so failures on fields like Inventory Number, Personal Number,
    Document Status, etc. are captured correctly.

    Image quality failures (category='image_quality') appear in the
    list but do NOT affect document_verdict — see parse_regula_response.

    Returns an empty list when the document passes.
    """
    failures: list[dict] = []
    had_validity_failure = False

    # Fields reported in section A are excluded from section B to avoid
    # duplicate entries for the same underlying field (e.g. Date of Expiry
    # fires both A and B when the document is expired).
    already_reported_types: set[int] = set()

    # Full list of fields with any failure, across all field types
    all_failing = _extract_all_failing_fields(regula_response)

    # ----------------------------------------------------------
    # A) EXPIRY
    # text_fields still used here because it already normalized
    # the Date of Expiry value for us.
    # ----------------------------------------------------------
    if statuses.get("expiry_check") == 0:
        expiry_field = next(
            (f for f in text_fields.get("fields", []) if f.get("field_type") == 3),
            None,
        )
        value = None
        if expiry_field:
            for src_name in ("MRZ", "VISUAL", "BARCODE"):
                for s in expiry_field.get("sources", []):
                    if s.get("source") == src_name and s.get("value"):
                        value = s["value"]
                        break
                if value:
                    break
        failures.append({
            "category": "expiry",
            "message":  "Document is expired",
            "detail":   f"Expiry date: {value}" if value else "Expiry date: unknown",
        })
        # Prevent Date of Expiry from appearing again in section B
        already_reported_types.add(3)

    # ----------------------------------------------------------
    # B) VALIDITY FAILURES — full field list
    # One entry per field (sources combined), two code paths:
    #   1. Per-source validity=0: Regula's checksum/format check failed
    #   2. Field-level validity=0 with no source-level failure: Regula
    #      flagged the field value as suspicious (e.g. "SPECIMEN",
    #      "000000000", "0") even though individual checksums are N/A
    # ----------------------------------------------------------
    for field in all_failing:
        if field.get("field_type") in already_reported_types:
            continue
        if field.get("validity_status") != 0:
            continue

        field_name = field["field_name"]
        sources = field.get("sources", [])

        # Path 1: at least one source explicitly failed validity
        hard_failing = [s for s in sources if s.get("validity") == 0]
        if hard_failing:
            had_validity_failure = True
            detail = ", ".join(
                f"{s['source']}: {s.get('value', '')}"
                for s in hard_failing
            )
            failures.append({
                "category": "validity",
                "message":  f"{field_name} failed validation",
                "detail":   detail,
            })
            continue

        # Path 2: field-level failure but no per-source validity=0
        # Show all source values so the user can see what was flagged
        sources_with_values = [s for s in sources if s.get("value")]
        if sources_with_values:
            had_validity_failure = True
            detail = ", ".join(
                f"{s['source']}: {s['value']}"
                for s in sources_with_values
            )
            failures.append({
                "category": "validity",
                "message":  f"{field_name} failed validation",
                "detail":   detail,
            })

    # ----------------------------------------------------------
    # C) COMPARISON FAILURES — full field list
    # ----------------------------------------------------------
    for field in all_failing:
        if field.get("comparison_status") != 0:
            continue
        src_vals = {
            s["source"]: s.get("value", "")
            for s in field.get("sources", [])
        }
        for comp in field.get("comparisons", []):
            if comp.get("status") == 0:
                sl = comp.get("source_left", "")
                sr = comp.get("source_right", "")
                failures.append({
                    "category": "comparison",
                    "message":  f"{field['field_name']} mismatch between sources",
                    "detail":   (
                        f"{sl}: {src_vals.get(sl, '')} "
                        f"/ {sr}: {src_vals.get(sr, '')}"
                    ),
                })

    # ----------------------------------------------------------
    # D) MRZ FAILURE (catch-all when no validity failure found)
    # ----------------------------------------------------------
    if statuses.get("mrz_check") == 0 and not had_validity_failure:
        failures.append({
            "category": "mrz",
            "message":  "MRZ (machine-readable zone) validation failed",
            "detail":   "Check digit or format error in MRZ data",
        })

    # ----------------------------------------------------------
    # E) IMAGE QUALITY FAILURES
    # These are photo-capture issues, not document authenticity issues.
    # They are included here so the user knows to retake the photo,
    # but document_verdict (computed in parse_regula_response) ignores
    # them — only non-QA checks determine the PASS/FAIL indicator.
    # ----------------------------------------------------------
    for page in image_quality:
        for check in page.get("checks", []):
            if check.get("result") == 0:
                check_type = check.get("type")
                check_name = (
                    check.get("name")
                    or IMAGE_QUALITY_TYPE_NAMES.get(check_type, f"Check {check_type}")
                )
                fail_desc = _IMAGE_QUALITY_FAIL_DESCRIPTIONS.get(
                    check_type, "Quality check failed"
                )
                failures.append({
                    "category": "image_quality",
                    "message":  check_name,
                    "detail":   fail_desc,
                })

    # ----------------------------------------------------------
    # F) OPTICAL SECURITY CATCH-ALL (only when nothing else fired)
    # ----------------------------------------------------------
    if (
        statuses.get("optical_status") == 0
        and statuses.get("security_check") == 0
        and not failures
    ):
        failures.append({
            "category": "security",
            "message":  "Optical security checks failed",
            "detail":   "Document authenticity could not be confirmed",
        })

    # Overall PASS with no failures → clean result
    if statuses.get("overall_status") == 1 and not failures:
        return []

    # Overall FAIL but nothing specific was identified
    if statuses.get("overall_status") == 0 and not failures:
        return [{
            "category": "unknown",
            "message":  "Verification failed",
            "detail":   "No specific failure reason could be determined",
        }]

    return failures


# ============================================================
# PUBLIC FUNCTION
# This is the only function other files should call.
# It runs all the private extractors and returns one clean
# dictionary with everything the application needs.
# ============================================================

def parse_regula_response(regula_response: dict) -> dict:
    """
    Parses the full Regula JSON response and returns a clean
    structured dictionary with exactly what this application
    needs.

    This is the only function main.py calls. It internally
    runs all the private extraction functions above and
    combines their results into one object.

    Args:
        regula_response: The raw Regula JSON response as a
                         Python dictionary (from regula.py).

    Returns:
        A dictionary with the following structure:
        {
          "transaction_info": {
            "transaction_id":  "uuid-string",
            "processed_at":    "2026-05-02T14:55:09Z",
            "regula_version":  "9.4.319820.2195",
            "elapsed_time_ms": 820
          },
          "statuses": {
            "overall_status":  0,
            "optical_status":  0,
            "expiry_check":    1,
            "mrz_check":       1,
            "text_check":      0,
            "security_check":  1
          },
          "doc_type": [
            {
              "page": 0,
              "name": "United States - ePassport (2020)",
              "country": "United States",
              ...
            }
          ],
          "image_quality": [
            {
              "page": 0,
              "overall": 1,
              "overall_label": "FAIL",
              "checks": [ ... ]
            }
          ],
          "text_fields": {
            "overall_status": 0,
            "fields": [ ... ]
          },
          "document_crops": {
            "page_0": "<base64 string>",
            "page_1": "<base64 string>"
          }
        }
    """
    logger.info("Starting Regula response parsing...")

    # Run all extractors
    transaction_info = _extract_transaction_info(regula_response)
    statuses         = _extract_overall_statuses(regula_response)
    doc_type         = _extract_doc_type(regula_response)
    image_quality    = _extract_image_quality(regula_response)
    text_fields      = _extract_text_fields(regula_response)
    document_crops   = _extract_document_crops(regula_response)
    failure_details  = _build_failure_details(statuses, text_fields, image_quality, regula_response)

    # document_verdict: PASS/FAIL based only on document-level checks.
    # Image quality failures are photo-capture issues and must not flip
    # an otherwise-valid document to FAIL.
    #   0 = FAIL, 1 = PASS  (same as CheckResult OK/ERROR)
    non_qa = [
        statuses.get("expiry_check"),
        statuses.get("mrz_check"),
        statuses.get("text_check"),
        statuses.get("security_check"),
    ]
    document_verdict = 0 if any(v == 0 for v in non_qa) else 1

    logger.info(
        f"Parsing complete. "
        f"transaction_id={transaction_info.get('transaction_id')} "
        f"overall_status={statuses.get('overall_status')} "
        f"document_verdict={document_verdict} "
        f"pages={len(doc_type)} "
        f"fields={len(text_fields.get('fields', []))} "
        f"crops={len(document_crops)} "
        f"failure_details={len(failure_details)}"
    )

    return {
        "transaction_info":  transaction_info,
        "statuses":          statuses,
        "doc_type":          doc_type,
        "image_quality":     image_quality,
        "text_fields":       text_fields,
        "document_crops":    document_crops,
        "failure_details":   failure_details,
        "document_verdict":  document_verdict,
    }