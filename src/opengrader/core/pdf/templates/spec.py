from opengrader.core.pdf.templates.constants import QuestionType

from .anchor import ANCHOR_LOCATIONS, Anchor, anchor_entries_norm

# version -> list of anchors that should be filled
VERSION_ANCHOR_MAP: dict[str, list[Anchor]] = {
    "A": [Anchor.TopLeft, Anchor.TopRight, Anchor.Bottomleft],
    "B": [Anchor.TopLeft, Anchor.TopRight, Anchor.BottomRight],
    "C": [Anchor.TopLeft, Anchor.Bottomleft, Anchor.BottomRight],
    "D": [Anchor.TopRight, Anchor.Bottomleft, Anchor.BottomRight],
}


def _bbox_norm(
    rect: tuple[float, float, float, float], page_w: float, page_h: float
) -> list[float]:
    """
    Convert a bounding box from absolute coordinates to normalized coordinates.
    Args:
        rect: (x0, y0, x1, y1) in absolute coordinates
        page_w: width of the page in absolute coordinates
        page_h: height of the page in absolute coordinates
    Returns:
        [x0, y0, width, height] in normalized coordinates
    """
    x0, y0, x1, y1 = rect
    return [
        round(x0 / page_w, 3),
        round(y0 / page_h, 3),
        round((x1 - x0) / page_w, 3),
        round((y1 - y0) / page_h, 3),
    ]


def build_layout_spec(
    unit_id: str,
    versions: list[str],
    page_w: float,
    page_h: float,
    field_rects: dict[str, tuple[float, float, float, float]],
    question_meta: list[dict],
) -> dict:
    """
    Build the layout specification of the PDF template.
    """
    pages: dict[int, dict] = {}

    def page_entry(page_number: int) -> dict:
        if page_number not in pages:
            pages[page_number] = {
                "page_number": page_number,
                "anchors": anchor_entries_norm(page_w=page_w, page_h=page_h),
                "fields": [],
                "questions": [],
            }
        return pages[page_number]

    # first page is special: it contains the fields for student name and id
    first_page = page_entry(1)
    first_page["fields"] = [
        {
            "id": "student_name",
            "bbox": _bbox_norm(field_rects["student_name"], page_w, page_h),
            "type": "name",
            "required": True,
            "psm": 7,  # "Treat the image as a single text line."
            "confidence_threshold": 70,
        },
        {
            "id": "student_id",
            "bbox": _bbox_norm(field_rects["student_id"], page_w, page_h),
            "type": "sid",
            "required": True,
            "regex": "^\\d{9}$",
            "whitelist": "0123456789",
            "psm": 7,
            "confidence_threshold": 70,
        },
    ]

    for question in question_meta:
        qid = str(question["id"])
        full_rect = question["rect"]
        prompt_rect = question.get("prompt_rect") or full_rect
        answer_rect = question.get("answer_rect")
        page_number = int(question["page_number"])
        points = question["points"]
        prompt = question["prompt"]

        page = page_entry(page_number)
        question_spec = {
            "id": qid,
            "bbox": _bbox_norm(prompt_rect, page_w, page_h),
            "prompt_bbox": _bbox_norm(prompt_rect, page_w, page_h),
            "full_bbox": _bbox_norm(full_rect, page_w, page_h),
            "response_type": QuestionType.FREE_RESPONSE,
            "points": points,
            "prompt": prompt,
        }
        if isinstance(answer_rect, (tuple, list)) and len(answer_rect) == 4:
            question_spec["answer_bbox"] = _bbox_norm(tuple(answer_rect), page_w, page_h)

        subquestions = question.get("subquestions", [])
        if subquestions:
            subquestion_specs: list[dict] = []
            for subquestion in subquestions:
                sub_full_rect = subquestion["rect"]
                sub_prompt_rect = subquestion.get("prompt_rect") or sub_full_rect
                sub_answer_rect = subquestion.get("answer_rect")

                subquestion_spec = {
                    "id": str(subquestion["id"]),
                    "label": str(subquestion.get("label", "")),
                    "bbox": _bbox_norm(sub_prompt_rect, page_w, page_h),
                    "prompt_bbox": _bbox_norm(sub_prompt_rect, page_w, page_h),
                    "full_bbox": _bbox_norm(sub_full_rect, page_w, page_h),
                    "response_type": QuestionType.FREE_RESPONSE,
                    "points": subquestion.get("points", 0),
                    "prompt": subquestion.get("prompt", ""),
                }
                if isinstance(sub_answer_rect, (tuple, list)) and len(sub_answer_rect) == 4:
                    subquestion_spec["answer_bbox"] = _bbox_norm(
                        tuple(sub_answer_rect), page_w, page_h
                    )

                subquestion_specs.append(subquestion_spec)

            question_spec["subquestions"] = subquestion_specs

        page["questions"].append(question_spec)

    versions_map = {}
    for v in versions:
        if v in VERSION_ANCHOR_MAP:
            versions_map[v] = [Anchor(a).value for a in VERSION_ANCHOR_MAP[v]]
        else:
            versions_map[v] = sorted(list(ANCHOR_LOCATIONS))

    return {
        "schema_version": "1.0",
        "unit_id": unit_id,
        "versions": versions_map,
        "pages": [pages[k] for k in sorted(pages.keys())],
    }
