from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

from opengrader.core.submissions.review import DEFAULT_OCR_CONFIDENCE_FLOOR
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)

DEFAULT_REFINE_FLOOR = DEFAULT_OCR_CONFIDENCE_FLOOR


class RefineRejection(str, Enum):
    MISSING = "missing"
    EMPTY = "empty"
    UNVERIFIED = "unverified"
    NOT_BETTER = "not_better"
    UNCHANGED = "unchanged"


REJECTION_LABELS: dict[RefineRejection, str] = {
    RefineRejection.MISSING: "not present in the re-extraction",
    RefineRejection.EMPTY: "re-read produced no text",
    RefineRejection.UNVERIFIED: "re-read has no measured confidence",
    RefineRejection.NOT_BETTER: "re-read is no more confident",
    RefineRejection.UNCHANGED: "re-read is identical",
}


@dataclass(frozen=True)
class RefineTarget:
    question_id: str
    response_text: str
    confidence: float


@dataclass(frozen=True)
class RefineCandidate:
    key: str
    markdown_path: Path
    student_id: str
    student_name: str
    targets: tuple[RefineTarget, ...]

    @property
    def target_ids(self) -> tuple[str, ...]:
        return tuple(target.question_id for target in self.targets)

    @property
    def identity(self) -> str:
        if self.student_id:
            return f"{self.student_name} ({self.student_id})"
        return "Unidentified"


@dataclass(frozen=True)
class RefinedResponse:
    question_id: str
    old_text: str
    new_text: str
    old_confidence: float
    new_confidence: float | None
    old_source: str | None = None
    new_source: str | None = None

    @property
    def confidence_summary(self) -> str:
        if self.new_confidence is None:
            return f"{self.old_confidence:.0f} → model read (unverified)"
        return f"{self.old_confidence:.0f} → {self.new_confidence:.0f}"


@dataclass(frozen=True)
class RefineMergeResult:
    adopted: tuple[RefinedResponse, ...] = ()
    rejected: tuple[tuple[str, RefineRejection], ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.adopted)


def is_refine_target(question: StudentSubmissionQuestion, *, floor: float) -> bool:
    confidence = question.response_confidence
    return confidence is not None and confidence < floor


def select_refine_targets(
    document: StudentSubmissionDocument,
    *,
    floor: float = DEFAULT_REFINE_FLOOR,
) -> tuple[RefineTarget, ...]:
    return tuple(
        RefineTarget(
            question_id=question.question_id,
            response_text=question.response_text or "",
            confidence=float(question.response_confidence),  # type: ignore[arg-type]
        )
        for question in document.questions
        if is_refine_target(question, floor=floor)
    )


def refine_candidate(
    key: str,
    markdown_path: Path,
    document: StudentSubmissionDocument,
    *,
    floor: float = DEFAULT_REFINE_FLOOR,
) -> RefineCandidate | None:
    targets = select_refine_targets(document, floor=floor)
    if not targets:
        return None
    return RefineCandidate(
        key=key,
        markdown_path=markdown_path,
        student_id=document.student_id or "",
        student_name=document.student_name or "",
        targets=targets,
    )


def merge_refined_responses(
    existing: StudentSubmissionDocument,
    refreshed: StudentSubmissionDocument,
    targets: Sequence[RefineTarget],
    *,
    accept_unverified: bool = False,
) -> RefineMergeResult:
    """The caller must reread existing from disk immediately before merging; otherwise
    intervening human corrections cannot be detected as changed targets.
    """
    refreshed_by_id = {
        question.question_id: question for question in refreshed.questions
    }
    existing_by_id = {question.question_id: question for question in existing.questions}

    adopted: list[RefinedResponse] = []
    rejected: list[tuple[str, RefineRejection]] = []

    for target in targets:
        question_id = target.question_id
        current = existing_by_id.get(question_id)
        candidate = refreshed_by_id.get(question_id)
        if candidate is None or current is None:
            rejected.append((question_id, RefineRejection.MISSING))
            continue

        old_confidence = current.response_confidence
        if old_confidence is None or old_confidence != target.confidence:
            rejected.append((question_id, RefineRejection.NOT_BETTER))
            continue

        new_text = (candidate.response_text or "").strip()
        if not new_text:
            rejected.append((question_id, RefineRejection.EMPTY))
            continue

        new_confidence = candidate.response_confidence
        if new_confidence is None:
            if not accept_unverified:
                rejected.append((question_id, RefineRejection.UNVERIFIED))
                continue
        elif new_confidence <= old_confidence:
            rejected.append((question_id, RefineRejection.NOT_BETTER))
            continue

        if new_text == (current.response_text or "").strip():
            rejected.append((question_id, RefineRejection.UNCHANGED))
            continue

        adopted.append(
            RefinedResponse(
                question_id=question_id,
                old_text=current.response_text or "",
                new_text=new_text,
                old_confidence=old_confidence,
                new_confidence=new_confidence,
                old_source=current.response_source,
                new_source=candidate.response_source,
            )
        )

    return RefineMergeResult(adopted=tuple(adopted), rejected=tuple(rejected))


@dataclass(frozen=True)
class RefineSummary:
    submissions_scanned: int = 0
    submissions_with_targets: int = 0
    targets: int = 0
    adopted: int = 0
    submissions_changed: int = 0

    @property
    def unchanged(self) -> int:
        return self.targets - self.adopted
