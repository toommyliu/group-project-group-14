import json
import re
from pathlib import Path
from typing import Any

class SpecError(Exception):
    pass

REQUIRED_SCHEMA_KEYS = {"schema_version", "unit_id", "pages"}
VALID_FIELD_TYPES = {
    "text",
    "sid",
    "section",
    "custom",
    "version_marker",
}
VALID_RESPONSE_TYPE_FIELDS = {
    "free_response",
}

# TODO: refactor to use pydantic?

def validate_bbox(bbox: list[float], ctx: str) -> None:
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise SpecError(
            f"{ctx}: bbox must be a list of 4 numbers [x, y, w, h], got {bbox!r}"
        )
    for i, val in enumerate(bbox):
        if not isinstance(val, (int, float)):
            raise SpecError(f"{ctx}: bbox[{i}] must be numeric, got {val!r}")
        if not (0.0 <= val <= 1.0):
            raise SpecError(f"{ctx}: bbox[{i}] must be in [0.0, 1.0], got {val!r}")
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        raise SpecError(
            f"{ctx}: bbox width/height must be positive, got [x={x}, y={y}, w={w}, h={h}]"
        )

def validate_field(field: dict, ctx: str) -> None:
    if "id" not in field:
        raise SpecError(f"{ctx}: missing required field 'id'")
    field_id = field["id"]
    field_ctx = f"{ctx}.field[{field_id}]"

    ftype = field.get("type")
    if ftype not in VALID_FIELD_TYPES:
        raise SpecError(
            f"{field_ctx}: type must be one of {VALID_FIELD_TYPES}, got {ftype!r}"
        )

    if "bbox" not in field:
        raise SpecError(f"{field_ctx}: missing required 'bbox'")
    validate_bbox(field["bbox"], field_ctx)

    whitelist = field.get("whitelist")
    if whitelist is not None and not isinstance(whitelist, str):
        raise SpecError(f"{field_ctx}: whitelist must be a string")

    psm = field.get("psm")
    if psm is not None and (not isinstance(psm, int) or psm < 0 or psm > 13):
        raise SpecError(f"{field_ctx}: psm must be an integer in [0, 13]")

    conf = field.get("confidence_threshold")
    if conf is not None and not isinstance(conf, (int, float)):
        raise SpecError(f"{field_ctx}: confidence_threshold must be numeric")

    if ftype == "version_marker":
        if "expected" not in field:
            raise SpecError(
                f"{field_ctx}: type='version_marker' requires 'expected' value"
            )

def validate_question(question: dict, ctx: str) -> None:
    if "id" not in question:
        raise SpecError(f"{ctx}: missing required 'id'")
    q_ctx = f"{ctx}.question[{question['id']}]"

    if "bbox" not in question:
        raise SpecError(f"{q_ctx}: missing required 'bbox'")
    validate_bbox(question["bbox"], q_ctx)

    rt = question.get("response_type")
    if rt not in VALID_RESPONSE_TYPE_FIELDS:
        raise SpecError(
            f"{q_ctx}: response_type must be one of {VALID_RESPONSE_TYPE_FIELDS}, got {rt!r}"
        )

    points = question.get("points")
    if points is not None and not isinstance(points, (int, float)):
        raise SpecError(f"{q_ctx}: points must be numeric, got {points!r}")


def validate_page(page: dict, page_idx: int) -> None:
    ctx = f"pages[{page_idx}]"
    if "page_number" not in page:
        raise SpecError(f"{ctx}: missing required 'page_number'")
    if not isinstance(page["page_number"], int) or page["page_number"] < 1:
        raise SpecError(
            f"{ctx}: page_number must be a positive integer, got {page['page_number']!r}"
        )

    for i, anchor in enumerate(page.get("anchors", [])):
        a_ctx = f"{ctx}.anchors[{i}]"
        if "id" not in anchor:
            raise SpecError(f"{a_ctx}: missing required 'id'")
        if "bbox" not in anchor:
            raise SpecError(f"{a_ctx}: missing required 'bbox'")
        validate_bbox(anchor["bbox"], a_ctx)

    for i, field in enumerate(page.get("fields", [])):
        validate_field(field, ctx)

    for i, question in enumerate(page.get("questions", [])):
        validate_question(question, ctx)

    if not page.get("fields") and not page.get("questions"):
        raise SpecError(
            f"{ctx}: page must have at least one 'fields' or 'questions' entry"
        )


def _validate_versions(versions: Any) -> list[SpecError]:
    errors: list[SpecError] = []
    if not isinstance(versions, dict):
        errors.append(SpecError("'versions' must be an object mapping version IDs to anchor ID lists"))
        return errors
    for vid, anchor_ids in versions.items():
        if not isinstance(vid, str) or not vid:
            errors.append(SpecError(f"versions: key must be a non-empty string, got {vid!r}"))
        if not isinstance(anchor_ids, list) or not anchor_ids:
            errors.append(SpecError(f"versions[{vid!r}]: must be a non-empty list of anchor IDs"))
            continue
        for aid in anchor_ids:
            if not isinstance(aid, str):
                errors.append(SpecError(f"versions[{vid!r}]: anchor IDs must be strings, got {aid!r}"))
    return errors


def validate_spec(data: dict) -> list[SpecError]:
    errors: list[SpecError] = []

    if not isinstance(data, dict):
        return [SpecError("spec must be a JSON object")]

    for key in REQUIRED_SCHEMA_KEYS:
        if key not in data:
            errors.append(SpecError(f"missing required top-level key: '{key}'"))

    sv = data.get("schema_version")
    if sv is not None and not re.match(r"^\d+\.\d+$", str(sv)):
        errors.append(SpecError(f"schema_version must be 'X.Y', got {sv!r}"))

    versions = data.get("versions")
    if versions is not None:
        errors.extend(_validate_versions(versions))

    pages = data.get("pages")
    if pages is None:
        return errors
    if not isinstance(pages, list):
        errors.append(SpecError("'pages' must be a list"))
        return errors

    seen_page_numbers: set[int] = set()
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            errors.append(SpecError(f"pages[{i}] must be an object"))
            continue
        validate_page(page, i)
        pn = page.get("page_number")
        if pn is None:
            continue
        if pn in seen_page_numbers:
            errors.append(SpecError(f"pages[{i}]: duplicate page_number {pn}"))
        seen_page_numbers.add(pn)

    return errors


def load_spec(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise SpecError(f"spec file not found: {p}")
    with open(p) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise SpecError(f"invalid JSON: {e}") from e
    errors = validate_spec(data)
    if errors:
        raise SpecError("\n".join(str(e) for e in errors))
    return data


def page_by_number(spec: dict, page_number: int) -> dict | None:
    for page in spec.get("pages", []):
        if page.get("page_number") == page_number:
            return page
    return None


def all_questions(spec: dict) -> list[dict]:
    out = []
    for page in spec.get("pages", []):
        for q in page.get("questions", []):
            out.append({**q, "page_number": page["page_number"]})
    return out


def all_fields(spec: dict) -> list[dict]:
    out = []
    for page in spec.get("pages", []):
        for field in page.get("fields", []):
            out.append({**field, "page_number": page["page_number"]})
    return out


def all_anchors(spec: dict, page_number: int) -> list[dict]:
    page = page_by_number(spec, page_number)
    if page is None:
        return []
    return page.get("anchors", [])


def version_anchor_map(spec: dict) -> dict[str, list[str]]:
    """Return the versions mapping, or empty dict if not present."""
    return spec.get("versions", {})


def lookup_version_by_anchors(
    detected_anchor_ids: set[str], spec: dict
) -> str | None:
    """Match detected anchor IDs to a version from the spec's `versions` map.

    Returns the version ID with the best match, or None if no match.
    """
    versions = version_anchor_map(spec)
    if not versions:
        return None
    best_version: str | None = None
    best_score = 0.0
    for vid, expected_ids in versions.items():
        expected_set = set(expected_ids)
        if not expected_set:
            continue
        matched = detected_anchor_ids & expected_set
        score = len(matched) / len(expected_set)
        if score > best_score:
            best_score = score
            best_version = vid
    if best_score < 0.5:
        return None
    return best_version
