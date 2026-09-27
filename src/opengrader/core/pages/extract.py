from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from PIL import Image

from opengrader.core.question_types import FREE_RESPONSE
from opengrader.core.source_location import QuestionSourceSegment

from .catalog import PageCatalog, PageGroup, ensure_features
from .features import Alignment, PageFeatures, align, gray_for_matching, page_box, page_features, rectify
from .layout import Region, question_regions
from .printed import FIELD_LINE_RE
from .split import read_sidecar

CROP_DPI = 300
_WEAK_INLIERS = 40

_cache_lock = threading.Lock()
_reference_features: dict[tuple[str, str], PageFeatures] = {}


@dataclass
class AlignedPage:
    number: int
    image: Image.Image
    group: PageGroup | None
    alignment: Alignment | None
    rectified: Image.Image | None = None


@dataclass
class CatalogCrops:
    name_image: str | None = None
    id_image: str | None = None
    title_image: str | None = None
    title_text: str = ""
    print_boxes: dict[str, tuple[tuple[float, float, float, float], ...]] = field(default_factory=dict)
    written_width: dict[str, float] = field(default_factory=dict)
    questions: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def crop_submission(
    pdf_path: Path,
    submission_dir: Path,
    catalog: PageCatalog,
    *,
    dpi: int = CROP_DPI,
    debug: bool = False,
) -> CatalogCrops:
    from opengrader.core.submissions.student_file import StudentSubmissionQuestion

    pages = _align_pages(pdf_path, catalog, dpi)
    result = CatalogCrops()
    for page in pages:
        if page.group is None:
            result.warnings.append(f"Page {page.number} did not match any printed page; it has no crops.")
        elif page.alignment.inliers < _WEAK_INLIERS:
            result.warnings.append(f"Page {page.number} lined up only weakly with the other copies; check its crops.")

    field_dir, responses_dir = submission_dir / "identifiers", submission_dir / "responses"
    identity = next((p for p in pages if p.group and p.group.layout.has_identity and p.group.page_number in (1, None)), None)
    if identity is not None:
        for kind in ("name", "id", "title"):
            box = identity.group.layout.fields.get(kind)
            if box is None:
                continue
            crop = _crop(identity, box)
            result.written_width[kind] = _before_next_label(identity.group.layout, box)
            result.print_boxes[kind] = _print_inside(identity.group, box)
            field_dir.mkdir(parents=True, exist_ok=True)
            crop.save(field_dir / f"{kind}.png")
            relative = str(Path(field_dir.name) / f"{kind}.png")
            setattr(result, f"{kind}_image", relative)
        result.title_text = identity.group.layout.title

    by_number = {p.group.page_number: p for p in pages if p.group is not None}
    next_layouts = [_continued_by(pages, index) for index in range(len(pages))]
    questions: dict[str, StudentSubmissionQuestion] = {}
    for index, page in enumerate(pages):
        if page.group is None:
            continue
        for region, continuation in question_regions(page.group.layout, next_layouts[index]):
            if region.key in questions:
                continue
            parts = [(page, region.box)]
            if continuation is not None:
                parts.append((pages[index + 1], continuation))
            questions[region.key] = _question(region, parts, page.group, responses_dir)

    for number in catalog.expected_pages:
        if number in by_number:
            continue
        group = next((g for g in catalog.groups.values() if g.page_number == number and g.layout.labels), None)
        if group is None:
            continue
        for region, _ in question_regions(group.layout):
            questions.setdefault(region.key, StudentSubmissionQuestion(
                question_id=region.key, prompt=region.prompt, possible_points=region.points,
                response_image_path=None, response_text="",
            ))
    result.questions = list(questions.values())
    if debug:
        from .debug import write_layout_debug

        write_layout_debug(pages, next_layouts, submission_dir / "layout_debug")
    return result


def _continued_by(pages: list[AlignedPage], index: int):
    page = pages[index]
    following = pages[index + 1] if index + 1 < len(pages) else None
    if page.group is None or following is None or following.group is None or page.group.page_number is None:
        return None
    return following.group.layout if following.group.page_number == page.group.page_number + 1 else None


def _align_pages(pdf_path: Path, catalog: PageCatalog, dpi: int) -> list[AlignedPage]:
    import fitz

    from opengrader.core.pdf_utils import render_page_to_image

    sidecar = read_sidecar(pdf_path) or {}
    hinted = [entry.get("group") for entry in sidecar.get("pages", [])]
    pages: list[AlignedPage] = []
    with fitz.open(str(pdf_path)) as doc:
        for index in range(len(doc)):
            image, _, _ = render_page_to_image(doc, index, dpi=dpi, cache=False)
            feats = page_features(gray_for_matching(image))
            hint = hinted[index] if index < len(hinted) else None
            group, alignment = _match(feats, catalog, hint)
            pages.append(AlignedPage(index + 1, image, group, alignment))
    return pages


def _features_of(catalog: PageCatalog, group: PageGroup) -> PageFeatures:
    key = (str(catalog.directory), group.id)
    with _cache_lock:
        if key not in _reference_features:
            _reference_features[key] = ensure_features(group).features
        group.features = _reference_features[key]
    return group.features


def _match(feats, catalog: PageCatalog, hint: str | None) -> tuple[PageGroup | None, Alignment | None]:
    candidates = sorted(catalog.groups.values(), key=lambda g: (g.id != hint, -g.members))
    best: tuple[PageGroup, Alignment] | None = None
    for group in candidates:
        alignment = align(feats, _features_of(catalog, group))
        if alignment is None:
            continue
        if group.id == hint and alignment.inliers >= _WEAK_INLIERS:
            return group, alignment
        if best is None or alignment.inliers > best[1].inliers:
            best = (group, alignment)
    return best if best is not None else (None, None)


def _rectified(page: AlignedPage) -> Image.Image:
    if page.rectified is None:
        width, height = page.image.size
        aspect = page.group.features.aspect
        out_width = round((width * height / aspect) ** 0.5)
        page.rectified = rectify(page.image, page.alignment, (out_width, round(out_width * aspect)))
    return page.rectified


def _print_inside(group: PageGroup, box: tuple[float, float, float, float]) -> tuple[tuple[float, float, float, float], ...]:
    layout = group.layout
    lines = layout.lines
    if group.copies_read <= 1:
        lines = [line for line in lines if line.text == layout.title or FIELD_LINE_RE.match(line.text)]
    width, height = box[2] - box[0], box[3] - box[1]
    return tuple(
        ((line.box[0] - box[0]) / width, (line.box[1] - box[1]) / height, (line.box[2] - box[0]) / width, (line.box[3] - box[1]) / height)
        for line in lines
        if line.box[0] < box[2] and line.box[2] > box[0] and line.box[1] < box[3] and line.box[3] > box[1]
    )


def _before_next_label(layout, box: tuple[float, float, float, float]) -> float:
    inside = [line.box[0] for line in layout.lines
              if FIELD_LINE_RE.match(line.text) and box[0] + 0.02 < line.box[0] < box[2] and box[1] <= line.center_y <= box[3]]
    return (min(inside) - box[0]) / (box[2] - box[0]) if inside else 1.0


def _crop(page: AlignedPage, box: tuple[float, float, float, float]) -> Image.Image:
    image = _rectified(page)
    width, height = image.size
    x0, y0, x1, y1 = box
    return image.crop((int(x0 * width), int(y0 * height), int(round(x1 * width)), int(round(y1 * height))))


def _question(region: Region, parts, group: PageGroup, responses_dir: Path):
    from opengrader.core.submissions.student_file import StudentSubmissionQuestion

    crops = [_crop(page, box).convert("RGB") for page, box in parts]
    if len(crops) == 1:
        stitched = crops[0]
    else:
        stitched = Image.new("RGB", (max(c.width for c in crops), sum(c.height for c in crops)), (255, 255, 255))
        y = 0
        for crop in crops:
            stitched.paste(crop, (0, y))
            y += crop.height
    responses_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{region.key}.png"
    stitched.save(responses_dir / filename)
    segments = []
    for page, box in parts:
        x0, y0, x1, y1 = page_box(page.alignment, box)
        segments.append(QuestionSourceSegment(page_number=page.number, bbox_norm=(x0, y0, x1 - x0, y1 - y0)))
    return StudentSubmissionQuestion(
        question_id=region.key,
        prompt=region.prompt,
        possible_points=region.points,
        response_image_path=str(Path(responses_dir.name) / filename),
        response_text="",
        question_type=group.question_types.get(region.key, FREE_RESPONSE),
        source_segments=tuple(segments),
    )

