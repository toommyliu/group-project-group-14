from __future__ import annotations

import difflib
import json
import re
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

import cv2
import numpy as np
from PIL import Image

from .features import Alignment, PageFeatures, align, gray_for_matching, page_features, rectify
from .layout import PART_LABEL_RE, QUESTION_LABEL_RE, PageLayout, QuestionLabel, is_page_number, page_layout, question_regions
from .outline import Outline, OutlineItem, regions_fingerprint
from .printed import FIELD_LINE_RE, TextLine, read_lines, read_title

CATALOG_DIRNAME = ".opengrader-pages"
CATALOG_VERSION = 1
READ_DPI = 200
TITLE_DPI = 300
_CONFIRM_COPIES = 4
_STRONG_INLIERS = 120
_LABEL_MATCH = 0.45
_LABELS_REQUIRED = 0.75
_BLANK_FEATURES = 40


@dataclass(frozen=True)
class ScanPage:
    pdf: Path
    index: int


@dataclass
class PageGroup:
    id: str
    reference: ScanPage
    reference_turns: int
    layout: PageLayout
    page_number: int | None = None
    members: int = 1
    copies_read: int = 1
    question_types: dict[str, str] = field(default_factory=dict)
    features: PageFeatures | None = field(default=None, repr=False)
    gray: np.ndarray | None = field(default=None, repr=False)


@dataclass
class PageMatch:
    page: ScanPage
    group: str | None
    homography: np.ndarray | None
    inliers: int = 0

    @property
    def alignment(self) -> Alignment | None:
        if self.homography is None:
            return None
        return Alignment(self.homography, self.inliers, self.inliers, 0.0)


@dataclass
class PageCatalog:
    groups: dict[str, PageGroup]
    matches: list[PageMatch]
    directory: Path | None = None
    # Questions the answer key has that `guide_catalog` found on no page.
    missing: tuple[OutlineItem, ...] = ()

    @property
    def expected_pages(self) -> list[int]:
        return sorted({g.page_number for g in self.groups.values() if g.page_number is not None})


def render(page: ScanPage, dpi: int, *, turns: int = 0) -> Image.Image:
    import fitz

    from opengrader.core.pdf_utils import render_page_to_image

    with fitz.open(str(page.pdf)) as doc:
        image, _, _ = render_page_to_image(doc, page.index, dpi=dpi, cache=False)
    return image.rotate(-90 * turns, expand=True) if turns else image


def reference_image(group: PageGroup, dpi: int) -> Image.Image:
    return render(group.reference, dpi, turns=group.reference_turns)


def build_catalog(
    pages: list[ScanPage],
    directory: Path | None = None,
    *,
    workers: int = 8,
    emit: Callable[[str], None] = lambda _m: None,
) -> PageCatalog:
    groups: dict[str, PageGroup] = {}
    matches: list[PageMatch] = []
    successors: dict[str, Counter] = {}
    previous: str | None = None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        loaded = _prefetch(pool, _load_for_matching, pages, window=4 * max(1, workers))
        for position, (page, (gray, feats)) in enumerate(zip(pages, loaded), start=1):
            if position % 25 == 0 or position == len(pages):
                emit(f"  Matched {position}/{len(pages)} pages to {len(groups)} printed page(s)")
            if feats.count < _BLANK_FEATURES:
                matches.append(PageMatch(page, None, None))
                previous = None
                continue
            hint = successors.get(previous, Counter()).most_common(1)[0][0] if previous in successors else None
            found = _assign(feats, gray, groups, hint)
            if found is None:
                group = _new_group(page, f"g{len(groups) + 1:02d}", emit)
                if group is None:
                    matches.append(PageMatch(page, None, None))
                    previous = None
                    continue
                groups[group.id] = group
                matches.append(PageMatch(page, group.id, turn_homography(group.reference_turns), group.features.count))
                current = group.id
            else:
                group, alignment = found
                group.members += 1
                matches.append(PageMatch(page, group.id, alignment.homography, alignment.inliers))
                current = group.id
            if previous is not None:
                successors.setdefault(previous, Counter())[current] += 1
            previous = current

    emit(f"Found {len(groups)} printed page(s); confirming their layout on other copies…")
    for group in groups.values():
        _confirm_layout(group, [m for m in matches if m.group == group.id])
        _read_title(group)
    _number_pages(groups, matches)
    _classify_questions(list(groups.values()), workers)
    catalog = PageCatalog(groups=groups, matches=matches, directory=directory)
    if directory is not None:
        save_catalog(catalog, directory)
    return catalog


def turn_homography(turns: int) -> np.ndarray:
    return {
        0: np.eye(3),
        1: np.array([[0.0, -1.0, 1.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),
        2: np.array([[-1.0, 0.0, 1.0], [0.0, -1.0, 1.0], [0.0, 0.0, 1.0]]),
        3: np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, 1.0], [0.0, 0.0, 1.0]]),
    }[turns % 4]


def _prefetch(pool: ThreadPoolExecutor, fn, items: list, *, window: int) -> Iterator:
    pending: deque = deque()
    iterator = iter(items)
    for item in iterator:
        pending.append(pool.submit(fn, item))
        if len(pending) >= window:
            break
    for item in iterator:
        yield pending.popleft().result()
        pending.append(pool.submit(fn, item))
    while pending:
        yield pending.popleft().result()


def _load_for_matching(page: ScanPage) -> tuple[np.ndarray, PageFeatures]:
    gray = gray_for_matching(render(page, 150))
    return gray, page_features(gray)


def _assign(feats: PageFeatures, gray: np.ndarray, groups: dict[str, PageGroup], hint: str | None):
    order = sorted(groups.values(), key=lambda g: (g.id != hint, -g.members))
    best: tuple[PageGroup, Alignment] | None = None
    for group in order:
        alignment = align(feats, group.features)
        if alignment is None:
            continue
        if alignment.inliers >= _STRONG_INLIERS and _labels_present(group, gray, alignment):
            return group, alignment
        if best is None or alignment.inliers > best[1].inliers:
            best = (group, alignment)
    if best is not None and _labels_present(best[0], gray, best[1]):
        return best
    return None


def _labels_present(group: PageGroup, gray: np.ndarray, alignment: Alignment) -> bool:
    labels = group.layout.labels
    if not labels or group.gray is None:
        return True
    ref = group.gray
    height, width = ref.shape
    to_px = np.diag([width, height, 1.0])
    from_page = np.diag([1 / gray.shape[1], 1 / gray.shape[0], 1.0])
    warped = cv2.warpPerspective(gray, to_px @ alignment.homography @ from_page, (width, height), borderValue=255)
    found = sum(_label_found(ref, warped, label) for label in labels)
    return found >= _LABELS_REQUIRED * len(labels)


def _label_found(ref: np.ndarray, page: np.ndarray, label: QuestionLabel) -> bool:
    height, width = ref.shape
    x0, y0, x1, y1 = label.box
    x1 = min(x1, x0 + 0.1)
    left, top, right, bottom = int(x0 * width) - 2, int(y0 * height) - 2, int(x1 * width) + 2, int(y1 * height) + 2
    snippet = ref[max(0, top):bottom, max(0, left):right]
    if snippet.size == 0 or snippet.std() < 8:
        return True
    pad_x, pad_y = int(0.012 * width), int(0.012 * height)
    window = page[max(0, top - pad_y):bottom + pad_y, max(0, left - pad_x):right + pad_x]
    if window.shape[0] < snippet.shape[0] or window.shape[1] < snippet.shape[1]:
        return False
    return float(cv2.matchTemplate(window, snippet, cv2.TM_CCOEFF_NORMED).max()) >= _LABEL_MATCH


def _new_group(page: ScanPage, group_id: str, emit: Callable[[str], None]) -> PageGroup | None:
    turns, lines = _upright_lines(page)
    if not lines:
        return None
    layout = page_layout(lines, printed_only=False)
    upright = render(page, 150, turns=turns)
    gray = gray_for_matching(upright)
    number = f"page {layout.page_number}" if layout.page_number else "unnumbered page"
    emit(f"  New printed page {group_id} ({number}, {len(layout.labels)} question label(s))")
    return PageGroup(id=group_id, reference=page, reference_turns=turns, layout=layout,
                     page_number=layout.page_number, features=page_features(gray), gray=gray)


def _upright_lines(page: ScanPage) -> tuple[int, list[TextLine]]:
    best: tuple[float, int, list[TextLine]] = (-1.0, 0, [])
    sideways = False
    for turns in (0, 2, 1, 3):
        if turns == 2 and sideways:
            continue
        image = render(page, READ_DPI, turns=turns)
        lines = read_lines(image)
        across = _share_across(lines, image.size)
        if across < 0.5:
            sideways = sideways or turns == 0
            continue
        layout = page_layout(lines, printed_only=False)
        if layout.labels or layout.has_identity or layout.page_number:
            return turns, lines
        score = len(layout.labels) * 5 + (3 if layout.has_identity else 0) + (2 if layout.page_number else 0) + across
        if score > best[0]:
            best = (score, turns, lines)
    return best[1], best[2]


def _share_across(lines: list[TextLine], size: tuple[int, int]) -> float:
    width, height = size
    long = [(line.box[2] - line.box[0]) * width - line.height * height for line in lines if len(line.text.strip()) >= 4]
    return sum(1 for d in long if d > 0) / len(long) if long else 0.0


def _confirm_layout(group: PageGroup, members: list[PageMatch]) -> None:
    others = sorted((m for m in members if m.page != group.reference and m.homography is not None), key=lambda m: -m.inliers)
    others = others[:_CONFIRM_COPIES]
    if not others:
        return
    reference = reference_image(group, READ_DPI)
    copies = [list(group.layout.lines)]
    for match in others:
        copies.append(read_lines(rectify(render(match.page, READ_DPI), match.alignment, reference.size)))
    lines = _fit_labels_to_print(consensus_lines(copies), reference)
    layout = page_layout(lines, printed_only=True)
    group.layout = layout
    group.copies_read = len(copies)
    if layout.page_number is not None:
        group.page_number = layout.page_number


def _read_title(group: PageGroup) -> None:
    layout = group.layout
    box = layout.fields.get("title")
    if box is None:
        return
    image = reference_image(group, TITLE_DPI)
    width, height = image.size
    text = read_title(image.crop((int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height))))
    if not text or not _similar(text.replace("_", ""), layout.title):
        return
    lines = [TextLine(text, line.box, line.score) if line.text == layout.title else line for line in layout.lines]
    group.layout = page_layout(lines, printed_only=group.copies_read > 1)


def _fit_labels_to_print(lines: list[TextLine], reference: Image.Image) -> list[TextLine]:
    gray = np.asarray(reference.convert("L"))
    height, width = gray.shape
    fitted = []
    for line in lines:
        if not (QUESTION_LABEL_RE.match(line.text) or PART_LABEL_RE.match(line.text)):
            fitted.append(line)
            continue
        x0, y0, x1, y1 = line.box
        left, right = int(x0 * width), int(min(x1, x0 + 0.03) * width)
        top, bottom = int(max(0.0, y0 - 0.005) * height), int(min(1.0, y1 + 0.005) * height)
        rows = (gray[top:bottom, left:right] < 140).sum(axis=1) if right > left and bottom > top else np.array([])
        inked = np.nonzero(rows > 0)[0]
        if len(inked) == 0:
            fitted.append(line)
            continue
        runs = np.split(inked, np.nonzero(np.diff(inked) > 2)[0] + 1)
        centre = ((y0 + y1) / 2) * height - top
        run = min(runs, key=lambda r: abs((r[0] + r[-1]) / 2 - centre))
        glyph_top, glyph_bottom = (top + run[0]) / height, (top + run[-1] + 1) / height
        pad = 0.25 * (glyph_bottom - glyph_top)
        fitted.append(TextLine(line.text, (x0, max(y0, glyph_top - pad), x1, min(y1, glyph_bottom + pad)), line.score))
    return fitted


def _structural(line: TextLine) -> bool:
    return bool(QUESTION_LABEL_RE.match(line.text) or PART_LABEL_RE.match(line.text)
                or FIELD_LINE_RE.match(line.text) or is_page_number(line))


def consensus_lines(copies: list[list[TextLine]]) -> list[TextLine]:
    need = len(copies) // 2 + 1
    clusters: list[list[tuple[int, TextLine]]] = []
    for copy_index, lines in enumerate(copies):
        for line in lines:
            for cluster in clusters:
                anchor = cluster[0][1]
                if abs(anchor.center_y - line.center_y) < 0.6 * max(anchor.height, line.height) and _overlap_x(anchor, line) and _similar(anchor.text, line.text):
                    cluster.append((copy_index, line))
                    break
            else:
                clusters.append([(copy_index, line)])
    printed = []
    for cluster in clusters:
        if len({index for index, _ in cluster}) >= need:
            text = Counter(line.text for _, line in cluster).most_common(1)[0][0]
            boxes = np.array([line.box for _, line in cluster])
            printed.append(TextLine(text, tuple(float(v) for v in np.median(boxes, axis=0)), 1.0))
    kept = [line for line in copies[0] if _structural(line) and not any(
        _similar(line.text, other.text) and abs(line.center_y - other.center_y) < 0.6 * line.height for other in printed)]
    return sorted(printed + kept, key=lambda line: (line.box[1], line.box[0]))


def _overlap_x(a: TextLine, b: TextLine) -> bool:
    return min(a.box[2], b.box[2]) - max(a.box[0], b.box[0]) > 0.3 * min(a.box[2] - a.box[0], b.box[2] - b.box[0])


def _similar(a: str, b: str) -> bool:
    na, nb = re.sub(r"\s+", "", a.lower()), re.sub(r"\s+", "", b.lower())
    return na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8


def _classify_questions(groups: list[PageGroup], workers: int) -> None:
    from opengrader.core.ocr.question_classifier import classify_question_image

    jobs = []
    for group in groups:
        if not group.layout.labels:
            continue
        image = reference_image(group, READ_DPI)
        width, height = image.size
        for region, _ in question_regions(group.layout):
            x0, y0, x1, y1 = region.box
            jobs.append((group, region.key, image.crop((int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)))))

    def classify(job):
        group, key, crop = job
        try:
            return group, key, classify_question_image(crop)
        except Exception:
            return group, key, "free_response"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for group, key, kind in pool.map(classify, jobs):
            group.question_types[key] = kind


def _number_pages(groups: dict[str, PageGroup], matches: list[PageMatch]) -> None:
    sequence = [m.group for m in matches]
    for _ in range(len(groups)):
        votes: dict[str, Counter] = {g: Counter() for g in groups if groups[g].page_number is None}
        for i, gid in enumerate(sequence):
            if gid not in votes:
                continue
            after = groups[sequence[i + 1]].page_number if i + 1 < len(sequence) and sequence[i + 1] else None
            before = groups[sequence[i - 1]].page_number if i > 0 and sequence[i - 1] else None
            if after is not None and after > 1:
                votes[gid][after - 1] += 1
            elif before is not None:
                votes[gid][before + 1] += 1
        inferred = {gid: counter.most_common(1)[0][0] for gid, counter in votes.items() if counter}
        if not inferred:
            break
        for gid, number in inferred.items():
            groups[gid].page_number = number
    for group in groups.values():
        if group.page_number is None:
            group.page_number = 1 if group.layout.has_identity else None


def _stored(pdf: Path, directory: Path) -> str:
    import os

    try:
        return os.path.relpath(pdf.resolve(), directory.resolve())
    except ValueError:
        return str(pdf.resolve())


def save_catalog(catalog: PageCatalog, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "version": CATALOG_VERSION,
        "groups": [
            {
                "id": g.id, "pdf": _stored(g.reference.pdf, directory), "index": g.reference.index,
                "turns": g.reference_turns, "page_number": g.page_number, "members": g.members,
                "copies_read": g.copies_read, "lines": [asdict(line) for line in g.layout.lines],
                "question_types": g.question_types,
            }
            for g in catalog.groups.values()
        ],
        "pages": [
            {
                "pdf": _stored(m.page.pdf, directory), "index": m.page.index, "group": m.group,
                "homography": m.homography.tolist() if m.homography is not None else None, "inliers": m.inliers,
            }
            for m in catalog.matches
        ],
    }
    path = directory / "catalog.json"
    path.write_text(json.dumps(data, indent=1))
    catalog.directory = directory
    return path


def load_catalog(directory: Path) -> PageCatalog:
    data = json.loads((directory / "catalog.json").read_text())
    if data.get("version") != CATALOG_VERSION:
        raise ValueError(f"{directory}: unsupported page catalog version {data.get('version')}")
    groups: dict[str, PageGroup] = {}
    for raw in data["groups"]:
        lines = tuple(TextLine(l["text"], tuple(l["box"]), l.get("score", 1.0)) for l in raw["lines"])
        layout = page_layout(lines, printed_only=raw["copies_read"] > 1)
        groups[raw["id"]] = PageGroup(
            id=raw["id"], reference=ScanPage((directory / raw["pdf"]).resolve(), raw["index"]), reference_turns=raw["turns"],
            layout=layout, page_number=raw["page_number"], members=raw["members"], copies_read=raw["copies_read"],
            question_types=dict(raw.get("question_types", {})),
        )
    matches = [
        PageMatch(ScanPage((directory / p["pdf"]).resolve(), p["index"]), p["group"],
                  np.array(p["homography"]) if p["homography"] is not None else None, p["inliers"])
        for p in data["pages"]
    ]
    return PageCatalog(groups=groups, matches=matches, directory=directory)


@dataclass(frozen=True)
class GuideReport:
    """What the answer key changed about where the questions are."""

    # Printed pages whose questions the key found (the rest kept their own labels).
    guided_pages: int
    found: tuple[str, ...]
    missing: tuple[str, ...]
    # A digest of the guided pages' regions when they differ from what the
    # pages' own labels gave; "" when the key changed nothing.
    fingerprint: str


def _region_boxes(layout: PageLayout) -> list[tuple[str, tuple[float, ...]]]:
    return [(region.key, region.box) for region, _ in question_regions(layout)]


def guide_catalog(catalog: PageCatalog, outlines: tuple[Outline, ...]) -> GuideReport:
    """Find the answer key's questions on the catalog's printed pages.

    Only the layouts change, in memory: the catalog on disk keeps the lines
    it read, so a different answer key is applied to the same reading. A page
    that prints Opengrader's own labels keeps them.
    """
    before: dict[str, list] = {}
    after: dict[str, list] = {}
    guided = 0
    for group in catalog.groups.values():
        layout = page_layout(group.layout.lines, printed_only=group.layout.printed_only, outlines=outlines)
        if not layout.guided:
            continue
        guided += 1
        before[group.id], after[group.id] = _region_boxes(group.layout), _region_boxes(layout)
        group.layout = layout

    found = {label.key for group in catalog.groups.values() for label in group.layout.labels}
    # With several versions there is no telling which one a missing question
    # belongs to; with one, what the key has and no page shows is a gap.
    expected = outlines[0] if len(outlines) == 1 else ()
    missing = tuple(item for item in expected if not item.stem and item.key not in found)
    kinds = {item.key: item.kind for outline in outlines for item in outline if item.kind}
    for group in catalog.groups.values():
        if group.layout.guided:
            for label in group.layout.labels:
                if label.key in kinds:
                    # The key lists choices: no need to guess the type from the page.
                    group.question_types[label.key] = kinds[label.key]
    catalog.missing = missing if guided else ()
    stems = {item.key for outline in outlines for item in outline if item.stem}
    return GuideReport(
        guided_pages=guided,
        # What gets graded: a stem above its parts is not a question to count.
        found=tuple(sorted(found - stems)),
        missing=tuple(item.key for item in catalog.missing),
        fingerprint=regions_fingerprint(after) if after != before else "",
    )


def scans_need_answer_key(source_dir: Path) -> bool:
    """True when these split scans print nothing that says where the
    questions are — no "3. (10 points)" of Opengrader's own — so the answer
    key is what has to. False when there is no page catalog to tell from."""
    directory = Path(source_dir) / CATALOG_DIRNAME
    if not (directory / "catalog.json").exists():
        return False
    try:
        catalog = load_catalog(directory)
    except (OSError, ValueError, KeyError):
        return False
    if not catalog.groups:
        return False
    return not any(
        label.points is not None for group in catalog.groups.values() for label in group.layout.labels
    )


def ensure_features(group: PageGroup) -> PageGroup:
    if group.features is None:
        gray = gray_for_matching(reference_image(group, 150))
        group.gray = gray
        group.features = page_features(gray)
    return group


def is_scan(pdf: Path) -> bool:
    import fitz

    try:
        with fitz.open(str(pdf)) as doc:
            for page in list(doc)[:3]:
                area = page.rect.width * page.rect.height
                for info in page.get_image_info():
                    x0, y0, x1, y1 = info["bbox"]
                    if (x1 - x0) * (y1 - y0) >= 0.6 * area:
                        return True
    except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError):
        return False
    return False


def ensure_catalog(source: Path, directory: Path) -> Path:
    if not (directory / "catalog.json").exists():
        build_catalog(scan_pages([source]), directory, workers=4)
    return directory


def scan_pages(pdfs: Iterable[Path]) -> list[ScanPage]:
    import fitz

    pages = []
    for pdf in pdfs:
        with fitz.open(str(pdf)) as doc:
            pages.extend(ScanPage(Path(pdf), index) for index in range(len(doc)))
    return pages
