"""An assignment outline: the answer key's question tree joined to template regions."""

from copy import deepcopy
from dataclasses import dataclass, field, replace
import math

from opengrader.core.qa_file import (
    QADocument,
    QAQuestion,
    QARubricItem,
    QASubquestion,
    parse_qa_markdown,
    render_qa_markdown,
)
from opengrader.core.region_map import MappedQuestion, RegionBox, RegionMap

MIN_BOX_SIZE = 0.005
BOXES_ONLY = "This assignment has submissions, so you can move its boxes but not change its questions."


@dataclass
class OutlinePart:
    title: str
    points: int
    boxes: list[RegionBox] = field(default_factory=list)
    # The answer-key question this row came from; None for a new question.
    id: str | None = None


@dataclass
class OutlineQuestion(OutlinePart):
    parts: list[OutlinePart] = field(default_factory=list)


@dataclass
class Outline:
    questions: list[OutlineQuestion] = field(default_factory=list)
    name_box: RegionBox | None = None
    id_box: RegionBox | None = None


@dataclass(frozen=True)
class BoxChanges:
    questions: tuple[str, ...] = ()
    identity: bool = False


@dataclass(frozen=True)
class TemplateIdentity:
    filename: str
    sha256: str
    pages: int
    page_size: tuple[float, float]


def version_questions(document: QADocument, version: str) -> list[QAQuestion]:
    if not document.version_questions:
        return document.questions
    return document.version_questions[version]


def outline_versions(document: QADocument) -> list[str]:
    return list(document.version_questions) or [""]


def add_version(document: QADocument, label: str, copy_from: str | None) -> QADocument:
    """Add a version; an unversioned answer key becomes version A first."""
    label = " ".join(label.split())
    if not label or len(label) > 20 or not all(char.isalnum() or char in " -_" for char in label):
        raise ValueError("Name versions with up to 20 letters, digits, spaces, or dashes.")
    by_version = dict(document.version_questions) or {"A": document.questions}
    if label in by_version:
        raise ValueError(f"Version {label} already exists.")
    if copy_from is not None and copy_from not in by_version:
        raise ValueError(f"Version {copy_from} does not exist.")
    by_version[label] = deepcopy(by_version[copy_from]) if copy_from is not None else []
    versions = [*by_version]
    return replace(
        document, versions=versions, version_questions=by_version, questions=by_version[versions[0]]
    )


def remove_version(document: QADocument, label: str) -> QADocument:
    if label not in document.version_questions:
        raise ValueError(f"Version {label} does not exist.")
    if len(document.version_questions) == 1:
        raise ValueError("An assignment needs at least one version.")
    by_version = {key: value for key, value in document.version_questions.items() if key != label}
    versions = [*by_version]
    return replace(
        document, versions=versions, version_questions=by_version, questions=by_version[versions[0]]
    )


def question_structure(document: QADocument) -> dict[str, list[tuple[str, ...]]]:
    """Each version's graded question ids, grouped by parent question."""
    return {
        version: [
            (question.id, *(part.id for part in question.subquestions))
            for question in version_questions(document, version)
        ]
        for version in outline_versions(document)
    }


def outline_for(questions: list[QAQuestion], regions: RegionMap | None) -> Outline:
    boxes = {
        question.question_id: list(question.segments)
        for question in (regions.questions if regions else ())
    }
    return Outline(
        questions=[
            OutlineQuestion(
                id=question.id,
                title=question.prompt,
                points=question.points,
                boxes=[] if question.subquestions else boxes.get(question.id, []),
                parts=[
                    OutlinePart(
                        id=part.id,
                        title=part.prompt,
                        points=part.points,
                        boxes=boxes.get(part.id, []),
                    )
                    for part in question.subquestions
                ],
            )
            for question in questions
        ],
        name_box=regions.name_field if regions else None,
        id_box=regions.student_id_field if regions else None,
    )


def apply_outline(
    document: QADocument, version: str, outline: Outline, template: TemplateIdentity
) -> tuple[str, RegionMap]:
    """Rewrite one version's questions from the outline; keep each kept question's key."""
    _check_boxes(outline, template.pages)
    current = version_questions(document, version)
    leaves = {
        item.id: item for question in current for item in [question, *question.subquestions]
    }
    questions = [_question(item, leaves) for item in outline.questions]
    if document.version_questions:
        by_version = {**document.version_questions, version: questions}
        document = replace(
            document, version_questions=by_version, questions=by_version[document.versions[0]]
        )
    else:
        document = replace(document, questions=questions)
    source = render_qa_markdown(document)
    saved = version_questions(parse_qa_markdown(source), version)
    graded = [leaf for question in saved for leaf in question.subquestions or [question]]
    drawn = [leaf for item in outline.questions for leaf in item.parts or [item]]
    regions = RegionMap(
        source_name=template.filename,
        source_sha256=template.sha256,
        source_pages=template.pages,
        source_page_size=template.page_size,
        assignment_title=document.title or "",
        name_field=outline.name_box,
        student_id_field=outline.id_box,
        questions=tuple(
            MappedQuestion(
                question_id=question.id,
                number=number,
                page=leaf.boxes[0].page,
                bbox=leaf.boxes[0].bbox,
                continuations=tuple(leaf.boxes[1:]),
            )
            for number, (leaf, question) in enumerate(zip(drawn, graded, strict=True), start=1)
            if leaf.boxes
        ),
    )
    return source, regions


def move_boxes(
    document: QADocument, version: str, outline: Outline, regions: RegionMap
) -> tuple[RegionMap, BoxChanges]:
    _check_boxes(outline, regions.source_pages)
    if _shape(outline) != _shape(outline_for(version_questions(document, version), None)):
        raise ValueError(BOXES_ONLY)
    saved = {question.question_id: question for question in regions.questions}
    drawn = [leaf for item in outline.questions for leaf in item.parts or [item]]
    questions: list[MappedQuestion] = []
    moved: list[str] = []
    for number, leaf in enumerate(drawn, start=1):
        before = saved.get(leaf.id)
        if (before.segments if before else ()) == tuple(leaf.boxes):
            if before:
                questions.append(before)
            continue
        moved.append(leaf.id)
        if leaf.boxes:
            first, *rest = leaf.boxes
            base = before or MappedQuestion(leaf.id, number, first.page, first.bbox)
            questions.append(
                replace(base, page=first.page, bbox=first.bbox, continuations=tuple(rest))
            )
    return (
        replace(
            regions,
            name_field=outline.name_box,
            student_id_field=outline.id_box,
            questions=tuple(questions),
        ),
        BoxChanges(
            tuple(moved),
            (outline.name_box, outline.id_box) != (regions.name_field, regions.student_id_field),
        ),
    )


def _shape(outline: Outline) -> list[tuple]:
    return [
        (
            question.id,
            question.title.strip(),
            None if question.parts else question.points,
            [(part.id, part.title.strip(), part.points) for part in question.parts],
        )
        for question in outline.questions
    ]


def _check_boxes(outline: Outline, pages: int) -> None:
    named = [("The name box", outline.name_box), ("The ID box", outline.id_box)]
    for number, question in enumerate(outline.questions, start=1):
        if question.parts and question.boxes:
            raise ValueError(
                f"Question {number} has subquestions, so draw its boxes on the subquestions."
            )
        named += [(f"Question {number}", box) for box in question.boxes]
        named += [
            (f"Question {number}.{index}", box)
            for index, part in enumerate(question.parts, start=1)
            for box in part.boxes
        ]
    for label, box in named:
        if box is None:
            continue
        x, y, width, height = box.bbox
        if not all(math.isfinite(value) for value in box.bbox):
            raise ValueError(f"{label} has a box with an invalid position.")
        if not 1 <= box.page <= pages:
            raise ValueError(f"{label} is on page {box.page}, but the template has {pages}.")
        if width < MIN_BOX_SIZE or height < MIN_BOX_SIZE:
            raise ValueError(f"{label} has a box too small to cut out.")
        if x < 0 or y < 0 or x + width > 1.0001 or y + height > 1.0001:
            raise ValueError(f"{label} has a box outside its page.")


def _question(item: OutlineQuestion, leaves: dict[str, QAQuestion | QASubquestion]) -> QAQuestion:
    base = leaves.get(item.id) if item.id else None
    if not item.parts:
        return QAQuestion(id="", **_leaf(item, base))
    parts = [
        QASubquestion(id="", **_leaf(part, leaves.get(part.id) if part.id else None))
        for part in item.parts
    ]
    return QAQuestion(
        id="",
        prompt=item.title.strip(),
        points=sum(part.points for part in parts),
        image_markdown=base.image_markdown if base else None,
        subquestions=parts,
        outcomes=base.outcomes if base else (),
    )


def _leaf(item: OutlinePart, base: QAQuestion | QASubquestion | None) -> dict:
    if item.points < 0:
        raise ValueError("Points cannot be negative.")
    fields = {"prompt": item.title.strip(), "points": item.points}
    if base is None or (isinstance(base, QAQuestion) and base.subquestions):
        return fields
    return fields | {
        "answers": list(base.answers),
        "choices": deepcopy(base.choices),
        "correct_choice_label": base.correct_choice_label,
        "correct_choice_labels": base.correct_choice_labels,
        "rubric": rescale_rubric(base.rubric, base.points, item.points),
        "image_markdown": base.image_markdown,
        "outcomes": base.outcomes,
    }


def rescale_rubric(rubric: list[QARubricItem], previous: int, points: int) -> list[QARubricItem]:
    """Keep each item's share of credit when the maximum changes; bonus values stay fixed."""
    if previous == points:
        return deepcopy(rubric)
    positive = any(item.label == "Correct" and (item.points_delta or 0) > 0 for item in rubric)

    def scale(value: float) -> float:
        return round(value * points / previous, 2) if previous > 0 else 0.0

    scaled = []
    for item in deepcopy(rubric):
        if item.label == "Bonus":
            pass
        elif item.label == "Correct":
            item.points_delta = float(points if positive else 0)
        elif item.label == "Incorrect":
            item.points_delta = float(0 if positive else -points)
        elif item.subitems:
            target = float(points if positive else -points)
            remaining = target
            for index, child in enumerate(item.subitems):
                if index == len(item.subitems) - 1:
                    value = remaining
                elif previous > 0:
                    value = scale(child.points_delta or 0)
                else:
                    value = round(target / len(item.subitems), 2)
                remaining = round(remaining - value, 2)
                child.points_delta = value
            item.points_delta = None
        elif item.points_delta is not None:
            item.points_delta = scale(item.points_delta) if previous > 0 else -points / 2
        scaled.append(item)
    return scaled
