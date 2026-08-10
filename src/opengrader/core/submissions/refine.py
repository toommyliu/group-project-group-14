"""Confidence-driven selective re-extraction: re-read only what came back badly.

``--resume`` (``manifest.py``) answers "what didn't finish?". Nothing answered
the question next to it: **what finished badly?** A page fed in a little too
light, a cramped answer box, handwriting the fallback OCR only half caught — all
of those complete successfully, get a low ``response_confidence``, and land in
the review queue as ``LOW_CONFIDENCE_TRANSCRIPTION``. Until now that flag was
purely a request for a human to retype an answer the machine could very often
read correctly on a second attempt at a higher resolution, or with a bigger
model.

This module is the planning and merging half of that second attempt. The
extraction itself is the ordinary pipeline (the CLI re-runs it into a scratch
directory); everything here decides **which answers to re-read** and **whether
the new reading is actually better**.

Three rules carry the whole feature, and none of them is negotiable:

1. **Only the answers that were asked about are ever rewritten.** A refine
   targets responses whose *measured* confidence is below the floor. Everything
   else in the ``.submission.md`` — every other answer, every grade, rubric
   selection, comment, identity field, crop path and source segment — is left
   byte-identical, because the write is a surgical field update and not a
   re-render. A re-extraction that quietly re-cut a page would be a data-loss
   bug wearing a fix's clothes.

2. **A re-read is adopted only when it is better evidence.** Higher measured
   confidence is better. An *unmeasured* model reading (``None``, what a vision
   model produces) is deliberately **not** treated as better than a measured
   one: swapping a known-42% reading for an unknown one launders a flagged
   answer out of the review queue without anybody having checked it, which is
   the exact opposite of what the flag is for. It is adopted only when the
   caller explicitly escalated to a model for this pass (``accept_unverified``),
   because then it is the thing the user asked for rather than a silent
   downgrade — and even then the confidence is *cleared* rather than kept, so
   the file never claims a number for words nothing scored.

3. **An empty re-read is never an improvement.** A second attempt that reads
   nothing means the crop is hard, not that the student wrote nothing; keeping
   the original text preserves whatever partial reading a grader can still
   correct by hand.

A human-verified transcription (confidence 100, written by the grade screen's
``x`` editor) is never a target and so is never at risk — the floor keeps it out
by construction, which is why the selection rule is stated in terms of the
measured number rather than in terms of provenance.

Everything here is pure: it takes documents and returns decisions. The scratch
extraction, the file writes and the journalling live in
``cli/submission_extractor.py``, so the policy can be tested without rendering a
single page.
"""

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

# The confidence below which a transcription is worth re-reading. Deliberately
# the *same* number the review queue flags at (`review.DEFAULT_OCR_CONFIDENCE_
# FLOOR`): the whole point of the feature is to turn that triage flag into an
# automatic fix attempt, and two floors would mean re-reading answers nobody was
# worried about while leaving flagged ones alone.
DEFAULT_REFINE_FLOOR = DEFAULT_OCR_CONFIDENCE_FLOOR


class RefineRejection(str, Enum):
    """Why a re-read was not adopted. Reported, never silent."""

    # The re-extraction produced no block for this question at all.
    MISSING = "missing"
    # It read nothing. The original (however poor) is still the better record.
    EMPTY = "empty"
    # A model reading with no measured confidence, on a pass that did not
    # escalate to a model — adopting it would clear the review flag without
    # improving the evidence.
    UNVERIFIED = "unverified"
    # Measured, but no better than the reading already on disk.
    NOT_BETTER = "not_better"
    # Same text at the same confidence: nothing to write.
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
    """One answer worth a second attempt, and the reading it has today."""

    question_id: str
    response_text: str
    confidence: float


@dataclass(frozen=True)
class RefineCandidate:
    """One submission with at least one answer worth re-reading."""

    # The source PDF's stem, which is both the output folder name and the resume
    # ledger's key — so a candidate names the same thing every stage of the
    # pipeline names it by.
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
        """Human-facing 'Name (id)' label, matching `batch.ExtractionOutcome`."""
        if self.student_id:
            return f"{self.student_name} ({self.student_id})"
        return "Unidentified"


@dataclass(frozen=True)
class RefinedResponse:
    """One adopted improvement, in the form the writer and the journal need."""

    question_id: str
    old_text: str
    new_text: str
    old_confidence: float
    # ``None`` when a model produced the new reading: unmeasured, and recorded
    # as such rather than given the old number.
    new_confidence: float | None
    # Which reader produced each side (`core/submissions/transcription.py`).
    # Carried through so the adopted reading lands on disk labelled with the
    # reader that actually produced it — the whole point of escalating to a
    # bigger model is lost if the file still says Tesseract read it.
    old_source: str | None = None
    new_source: str | None = None

    @property
    def confidence_summary(self) -> str:
        """"42 → 88" / "42 → model read", for the run's report."""
        if self.new_confidence is None:
            return f"{self.old_confidence:.0f} → model read (unverified)"
        return f"{self.old_confidence:.0f} → {self.new_confidence:.0f}"


@dataclass(frozen=True)
class RefineMergeResult:
    """What a second attempt at one submission actually achieved."""

    adopted: tuple[RefinedResponse, ...] = ()
    rejected: tuple[tuple[str, RefineRejection], ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.adopted)


def is_refine_target(question: StudentSubmissionQuestion, *, floor: float) -> bool:
    """Whether this answer's transcription is worth a second attempt.

    A *measured* confidence below ``floor``. Two exclusions fall out of that and
    both are deliberate:

    * ``None`` (a vision-model reading) is not a target. It is unverified, not
      bad — the same convention ``review.py`` follows when it declines to flag
      one — and re-running the same model over the same crop would mostly
      produce the same words while costing the slowest step in the pipeline.
    * a human-verified correction sits at 100 and is never below any sane floor,
      so a grader's typing can never be overwritten by a machine's second guess.
    """
    confidence = question.response_confidence
    return confidence is not None and confidence < floor


def select_refine_targets(
    document: StudentSubmissionDocument,
    *,
    floor: float = DEFAULT_REFINE_FLOOR,
) -> tuple[RefineTarget, ...]:
    """Every answer in one submission whose reading is below ``floor``."""
    return tuple(
        RefineTarget(
            question_id=question.question_id,
            response_text=question.response_text or "",
            # Safe: `is_refine_target` already established it is a real number.
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
    """Describe one submission's refine work, or ``None`` when it needs none.

    Returning ``None`` rather than an empty candidate is what lets the caller
    write ``if candidate is None: continue`` and never schedule an extraction
    that would re-render every page of a submission with nothing to fix — the
    selectivity that makes a refine pass cheap in the first place.
    """
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
    """Decide which of a re-extraction's readings replace the ones on disk.

    ``targets`` bounds the whole operation: a question absent from it is not
    consulted, so a refine can only ever touch the answers it was launched to
    fix. For each target the re-read is adopted when it is *better evidence*
    (see the module docstring for why an unmeasured model reading only counts as
    better when the caller escalated to a model on purpose), and otherwise
    rejected with a reason a caller can print.

    ``existing`` is re-read from disk by the caller immediately before this
    runs, so a grader who corrected an answer by hand while the re-extraction was
    in flight is protected the same way ``targets`` protects everyone else: their
    correction sits at confidence 100 and no longer matches a target's recorded
    value, so the comparison below refuses it.
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

        # The reading on disk right now, which may have moved since the plan was
        # made (a grader fixing it by hand mid-run is the case that matters).
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
            # Better-scored but the same words: there is nothing to correct, and
            # writing it would journal a change that changed nothing. The
            # confidence alone is not worth a rewrite — the answer reads the
            # same and a grader still sees the same chip.
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
    """Cohort-level totals for the run's closing report."""

    submissions_scanned: int = 0
    submissions_with_targets: int = 0
    targets: int = 0
    adopted: int = 0
    submissions_changed: int = 0

    @property
    def unchanged(self) -> int:
        """Answers that were re-read and kept their original transcription.

        Not a failure: it is the honest outcome for a crop that is genuinely
        unreadable, and it is what tells an instructor which answers really do
        need a human rather than another pass.
        """
        return self.targets - self.adopted
