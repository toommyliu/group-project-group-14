import json
import re
from pathlib import Path
from typing import Any

from opengrader.core.ocr.constants import (
    VALID_FIELD_TYPES,
    REQUIRED_SCHEMA_KEYS,
    VALID_RESPONSE_TYPE_FIELDS,
)


class SpecError(Exception):
    pass


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


def _bbox_within(parent: list[float], child: list[float], eps: float = 1e-6) -> bool:
    px, py, pw, ph = parent
    cx, cy, cw, ch = child
    return (
        cx >= px - eps
        and cy >= py - eps
        and (cx + cw) <= (px + pw) + eps
        and (cy + ch) <= (py + ph) + eps
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


def _validate_question_core(question: dict, ctx: str) -> str:
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

    return q_ctx


def validate_question(question: dict, ctx: str) -> None:
    q_ctx = _validate_question_core(question, ctx)

    parent_bbox = question["bbox"]
    parent_container_bbox = parent_bbox

    full_bbox = question.get("full_bbox")
    if full_bbox is not None:
        validate_bbox(full_bbox, f"{q_ctx}.full_bbox")
        if not _bbox_within(full_bbox, parent_bbox):
            raise SpecError(f"{q_ctx}: bbox must be fully inside full_bbox")
        parent_container_bbox = full_bbox

    answer_bbox = question.get("answer_bbox")
    if answer_bbox is not None:
        validate_bbox(answer_bbox, f"{q_ctx}.answer_bbox")

    subquestions = question.get("subquestions")
    if subquestions is None:
        return
    if not isinstance(subquestions, list):
        raise SpecError(f"{q_ctx}: subquestions must be a list when provided")

    for i, subquestion in enumerate(subquestions):
        sq_ctx = f"{q_ctx}.subquestions[{i}]"
        if not isinstance(subquestion, dict):
            raise SpecError(f"{sq_ctx}: subquestion must be an object")
        _validate_question_core(subquestion, sq_ctx)

        if "subquestions" in subquestion:
            raise SpecError(f"{sq_ctx}: nested subquestions are not supported")

        label = subquestion.get("label")
        if label is not None and not isinstance(label, str):
            raise SpecError(f"{sq_ctx}: label must be a string")

        sub_container_bbox = subquestion["bbox"]
        sub_full_bbox = subquestion.get("full_bbox")
        if sub_full_bbox is not None:
            validate_bbox(sub_full_bbox, f"{sq_ctx}.full_bbox")
            if not _bbox_within(sub_full_bbox, subquestion["bbox"]):
                raise SpecError(f"{sq_ctx}: bbox must be fully inside full_bbox")
            sub_container_bbox = sub_full_bbox

        sub_answer_bbox = subquestion.get("answer_bbox")
        if sub_answer_bbox is not None:
            validate_bbox(sub_answer_bbox, f"{sq_ctx}.answer_bbox")

        if not _bbox_within(parent_container_bbox, sub_container_bbox):
            raise SpecError(f"{sq_ctx}: bbox must be fully inside parent question bbox")


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

    if page["page_number"] == 1 and "title_bbox" not in page:
        raise SpecError(f"{ctx}: missing required 'title_bbox' on first page")

    title_bbox = page.get("title_bbox")
    if title_bbox is not None:
        validate_bbox(title_bbox, f"{ctx}.title_bbox")

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
        errors.append(
            SpecError(
                "'versions' must be an object mapping version IDs to anchor ID lists"
            )
        )
        return errors
    for vid, anchor_ids in versions.items():
        if not isinstance(vid, str) or not vid:
            errors.append(
                SpecError(f"versions: key must be a non-empty string, got {vid!r}")
            )
        if not isinstance(anchor_ids, list) or not anchor_ids:
            errors.append(
                SpecError(f"versions[{vid!r}]: must be a non-empty list of anchor IDs")
            )
            continue
        for aid in anchor_ids:
            if not isinstance(aid, str):
                errors.append(
                    SpecError(
                        f"versions[{vid!r}]: anchor IDs must be strings, got {aid!r}"
                    )
                )
    return errors


def _iter_question_ids(question: dict) -> list[str]:
    ids: list[str] = [str(question["id"])]
    for subquestion in question.get("subquestions", []):
        ids.append(str(subquestion["id"]))
    return ids


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
    seen_question_page: dict[str, int] = {}
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

        for question in page.get("questions", []):
            for qid in _iter_question_ids(question):
                first_page = seen_question_page.get(qid)
                if first_page is not None:
                    errors.append(
                        SpecError(
                            "question/subquestion id "
                            f"{qid!r} appears on multiple pages "
                            f"({first_page} and {pn}); cross-page continuation is not supported"
                        )
                    )
                    continue
                seen_question_page[qid] = pn

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
        page_number = page["page_number"]
        for q in page.get("questions", []):
            out.append({**q, "page_number": page_number})
            for subquestion in q.get("subquestions", []):
                out.append(
                    {
                        **subquestion,
                        "page_number": page_number,
                        "parent_question_id": q["id"],
                    }
                )
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


def lookup_version_by_anchors(detected_anchor_ids: set[str], spec: dict) -> str | None:
    """Match detected anchor IDs to a version from the spec's `versions` map.

    Returns the version ID with the best match, or None if no match.
    """
    versions = version_anchor_map(spec)
    if not versions:
        return None
    if not detected_anchor_ids:
        return None

    for vid, expected_ids in versions.items():
        if detected_anchor_ids == set(expected_ids):
            return vid

    best_version: str | None = None
    best_score = float("-inf")
    tied_versions: list[str] = []
    for vid, expected_ids in versions.items():
        expected_set = set(expected_ids)
        if not expected_set:
            continue
        matched = detected_anchor_ids & expected_set
        extras = detected_anchor_ids - expected_set
        missed = expected_set - detected_anchor_ids
        score = (len(matched) * 2 - len(extras) - len(missed)) / len(expected_set)
        if score > best_score:
            best_score = score
            best_version = vid
            tied_versions = [vid]
        elif score == best_score:
            tied_versions.append(vid)

    if best_score < 0.5:
        return None
    if len(tied_versions) != 1:
        return None
    return best_version
