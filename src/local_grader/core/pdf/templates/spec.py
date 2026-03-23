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
    question_meta: list[tuple[str, tuple[float, float, float, float], int, int, str]],
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
            "type": "text",
            "required": True,
            "psm": 7,  # "Treat the image as a single text line."
            "confidence_threshold": 70,
        },
        {
            "id": "student_id",
            "bbox": _bbox_norm(field_rects["student_id"], page_w, page_h),
            "type": "text",
            "required": True,
            "whitelist": "0123456789",
            "psm": 7,
            "confidence_threshold": 70,
        },
    ]

    for qid, rect, page_number, points, prompt in question_meta:
        page = page_entry(page_number)
        page["questions"].append(
            {
                "id": qid,
                "bbox": _bbox_norm(rect, page_w, page_h),
                # TODO: support multiple response types?
                "response_type": "free_response",
                "points": points,
                "prompt": prompt,
            }
        )

    versions_map = {}
    for v in versions:
        if v in VERSION_ANCHOR_MAP:
            versions_map[v] = [a.value for a in VERSION_ANCHOR_MAP[v]]
        else:
            versions_map[v] = [a.value for a in ANCHOR_LOCATIONS]

    return {
        "schema_version": "1.0",
        "unit_id": unit_id,
        "versions": versions_map,
        "pages": [pages[k] for k in sorted(pages.keys())],
    }
