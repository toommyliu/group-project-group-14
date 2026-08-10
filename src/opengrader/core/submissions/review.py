"""Needs-review triage: turn silent extraction/grading anomalies into a list.

The OCR + extraction pipeline (`core/ocr`, `core/submissions/processor.py`)
already *knows* when it is unsure — a blank handwriting transcription, a name
that never matched the roster, a missing box — but today those signals are
scattered and easy to miss until a wrong grade reaches Canvas. This module is a
pure function over the *persisted* submission data (`.submission.md` files) that
surfaces the risky ones as a prioritized, reviewable list.

It deliberately derives everything from what is already on disk, so it neither
re-runs OCR nor destabilizes the extraction pipeline. Per-question transcription
confidence is one such persisted signal: a response read by Tesseract below the
confidence floor becomes a :attr:`ReviewReason.LOW_CONFIDENCE_TRANSCRIPTION` flag
(a VLM transcription has no calibrated confidence and is left unflagged here).
Per-*field* confidence is the identity counterpart: a name or id whose recorded
``student_name_confidence``/``student_id_confidence`` is below the floor becomes
a :attr:`ReviewReason.LOW_CONFIDENCE_IDENTITY` flag. Both follow the same rule —
a ``None`` confidence means *unmeasured* and is never flagged, since there is no
number to distrust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, Sequence

from opengrader.core.grading.llm_grader import (
    ai_analysis_confidence,
    ai_analysis_is_reviewed,
)
from opengrader.core.grading.solutions import MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE
from opengrader.core.question_types import FREE_RESPONSE
from opengrader.core.roster import RosterStudent
from opengrader.core.submissions.regrade import RegradeRequest
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle: analytics reads the journal
    from opengrader.core.analytics.rubric_drift import StaleGrade

ReviewSeverity = Literal["high", "medium", "low"]

_SEVERITY_RANK: dict[ReviewSeverity, int] = {"high": 0, "medium": 1, "low": 2}

# Response transcriptions that mean the extractor produced nothing usable.
# Kept conservative: only values that are unambiguously "no answer captured".
_EMPTY_RESPONSE_SENTINELS = {"", "none", "n/a", "na", "null", "-", "—", "–"}

# A student id that looks like OCR noise rather than a real id. Real ids are a
# run of alphanumerics; spaces/symbols or a very short value signal a misread.
_DEFAULT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{5,}$")

# Below this AI confidence (percent) a suggested grade is worth a human look.
DEFAULT_AI_CONFIDENCE_FLOOR = 80

# Below this OCR/transcription confidence (0-100) a response's text is worth
# verifying against the scan. Matches the extractor's field-validation default.
#
# Imported rather than repeated: the LLM grader refuses to grade below the same
# number (`needs_llm_grade`), and the two must agree. That equality is what
# guarantees the grading gate creates no silent orphans — every answer a model
# is not allowed to grade is an answer already sitting in this queue.
DEFAULT_OCR_CONFIDENCE_FLOOR = int(MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE)

# Below this confidence (0-100) a *name or student id* is worth confirming.
# Deliberately stricter than the response floor: a shaky answer transcription
# costs one question's accuracy, while a shaky identity posts a whole exam to
# the wrong person — and unlike an answer, nobody reads the id again later.
DEFAULT_IDENTITY_CONFIDENCE_FLOOR = 80

# Flag a systematic-failure sweep only once enough questions agree, so a single
# legitimately-zero answer never trips it.
_ZERO_SWEEP_MIN_GRADED = 2


class ReviewReason(str, Enum):
    MISSING_STUDENT_ID = "missing_student_id"
    MALFORMED_STUDENT_ID = "malformed_student_id"
    MISSING_STUDENT_NAME = "missing_student_name"
    EMPTY_RESPONSE = "empty_response"
    LOW_CONFIDENCE_TRANSCRIPTION = "low_confidence_transcription"
    # A name/id that *was* read but shakily — or resolved against the roster by
    # a fuzzy match that barely cleared its threshold. Distinct from
    # MISSING_STUDENT_ID (nothing read) and MALFORMED_STUDENT_ID (read, and the
    # shape is wrong): here the value looks perfectly plausible and may still be
    # the wrong person, which is the case a grader would otherwise never catch.
    LOW_CONFIDENCE_IDENTITY = "low_confidence_identity"
    # The reader looked at the answer and could not resolve it — measured, but
    # unusable. Distinct from EMPTY_RESPONSE (nothing was read, no opinion on
    # why) and from LOW_CONFIDENCE_TRANSCRIPTION (something *was* read, just
    # shakily). A bubble sheet with two circles filled is the clearest case:
    # the mark is definitely there and definitely un-interpretable.
    UNREADABLE_RESPONSE = "unreadable_response"
    MISSING_SOURCE_SEGMENTS = "missing_source_segments"
    LOW_CONFIDENCE_AI_GRADE = "low_confidence_ai_grade"
    ZERO_SCORE_SWEEP = "zero_score_sweep"
    UNGRADED = "ungraded"
    MISSING_SUBMISSION = "missing_submission"
    OPEN_REGRADE_REQUEST = "open_regrade_request"
    # This answer was graded before the question's rubric was last edited, so
    # it was measured against an instrument that no longer exists. Distinct
    # from every other reason here in that the *grade* may be perfectly
    # defensible — what is wrong is that nobody has decided whether it is,
    # which is why it is a prompt and not a verdict.
    GRADED_BEFORE_RUBRIC_CHANGE = "graded_before_rubric_change"


@dataclass(frozen=True)
class ReviewFlag:
    """One anomaly worth a human's eyes, anchored to a student (and question)."""

    student_id: str
    student_name: str
    source_pdf: str
    reason: ReviewReason
    severity: ReviewSeverity
    detail: str
    # Empty for submission-level flags (missing id/name, zero-score sweep).
    question_id: str = ""

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK[self.severity]


def _is_graded(question: StudentSubmissionQuestion) -> bool:
    return question.points_awarded is not None or bool(question.rubric_labels)


def _response_is_empty(text: str) -> bool:
    return text.strip().lower() in _EMPTY_RESPONSE_SENTINELS


def _ai_confidence(analysis: str) -> int | None:
    """Return the AI confidence percent tagged in an analysis, else ``None``.

    The tag's shape is defined next to the code that writes it
    (``core/grading/llm_grader.py``), so the triage here and the LLM evaluation
    harness can never disagree about what an AI-graded answer looks like.
    """
    return ai_analysis_confidence(analysis)


def flag_submission(
    document: StudentSubmissionDocument,
    *,
    id_pattern: re.Pattern[str] = _DEFAULT_ID_PATTERN,
    ai_confidence_floor: int = DEFAULT_AI_CONFIDENCE_FLOOR,
    ocr_confidence_floor: float = DEFAULT_OCR_CONFIDENCE_FLOOR,
    identity_confidence_floor: float = DEFAULT_IDENTITY_CONFIDENCE_FLOOR,
    include_ungraded: bool = False,
) -> list[ReviewFlag]:
    """Return every review flag for a single student's submission.

    Ordering within a submission is deterministic: identity flags first, then
    per-question flags in question order, then the sweep summary.
    """
    student_id = document.student_id.strip()
    student_name = document.student_name.strip()
    source_pdf = document.source_pdf

    def _flag(
        reason: ReviewReason,
        severity: ReviewSeverity,
        detail: str,
        *,
        question_id: str = "",
    ) -> ReviewFlag:
        return ReviewFlag(
            student_id=student_id,
            student_name=student_name,
            source_pdf=source_pdf,
            reason=reason,
            severity=severity,
            detail=detail,
            question_id=question_id,
        )

    flags: list[ReviewFlag] = []

    # ── Identity: a wrong id/name posts a grade to the wrong student. ──
    if not student_id:
        flags.append(
            _flag(
                ReviewReason.MISSING_STUDENT_ID,
                "high",
                "No student id extracted; Canvas export would be unmatched.",
            )
        )
    elif id_pattern.match(student_id) is None:
        flags.append(
            _flag(
                ReviewReason.MALFORMED_STUDENT_ID,
                "medium",
                f"Student id {student_id!r} looks like an OCR misread.",
            )
        )
    if not student_name:
        flags.append(
            _flag(
                ReviewReason.MISSING_STUDENT_NAME,
                "medium",
                "No student name extracted.",
            )
        )

    # A shaky *reading* of an identity that is present and well-formed. Checked
    # after the two structural id flags and only for fields that survived them,
    # so one submission never raises two flags about the same field: "missing"
    # and "malformed" already say something is wrong, and adding "and we weren't
    # sure either" is noise.
    unsure: list[str] = []
    if (
        student_id
        and id_pattern.match(student_id) is not None
        and document.id_confidence is not None
        and document.id_confidence < identity_confidence_floor
    ):
        unsure.append(f"id {document.id_confidence:.0f}%")
    if (
        student_name
        and document.name_confidence is not None
        and document.name_confidence < identity_confidence_floor
    ):
        unsure.append(f"name {document.name_confidence:.0f}%")
    if unsure:
        flags.append(
            _flag(
                ReviewReason.LOW_CONFIDENCE_IDENTITY,
                "medium",
                f"Identity read with low confidence ({', '.join(unsure)}); "
                "confirm it against the scan before grades are returned.",
            )
        )

    # ── Per-question extraction/grading anomalies. ──
    graded_scores: list[float] = []
    for question in document.questions:
        # Three mutually exclusive readings of "this answer looks wrong",
        # ordered strongest evidence first so one question raises exactly one
        # of them. A real confidence number below the floor is the strong
        # signal: a VLM transcription carries no confidence (None) and is
        # deliberately never flagged here, since the model gives no calibrated
        # score to trust or distrust.
        response_empty = _response_is_empty(question.response_text)
        low_confidence = (
            question.response_confidence is not None
            and question.response_confidence < ocr_confidence_floor
        )
        if response_empty and low_confidence:
            # Measured, and the measurement itself says it is unusable — a
            # double-marked bubble, or an OCR pass that read nothing and knew
            # it. The student's answer is unknown and will score zero unless
            # somebody looks at the paper, so this outranks the other two.
            flags.append(
                _flag(
                    ReviewReason.UNREADABLE_RESPONSE,
                    "high",
                    f"Response could not be resolved (confidence "
                    f"{question.response_confidence:.0f}%); check the scan — "
                    "a bubble sheet with two marks reads this way.",
                    question_id=question.question_id,
                )
            )
        elif response_empty and question.question_type == FREE_RESPONSE:
            flags.append(
                _flag(
                    ReviewReason.EMPTY_RESPONSE,
                    "low",
                    "Free-response transcription is empty; verify the scan.",
                    question_id=question.question_id,
                )
            )
        elif low_confidence:
            # A shaky transcription we *can* read is worth verifying.
            flags.append(
                _flag(
                    ReviewReason.LOW_CONFIDENCE_TRANSCRIPTION,
                    "medium",
                    f"OCR transcription confidence {question.response_confidence:.0f}%; "
                    "verify the text against the scan.",
                    question_id=question.question_id,
                )
            )
        if not question.source_segments:
            flags.append(
                _flag(
                    ReviewReason.MISSING_SOURCE_SEGMENTS,
                    "low",
                    "No source region; the PDF comment cannot be anchored.",
                    question_id=question.question_id,
                )
            )
        confidence = _ai_confidence(question.analysis)
        if (
            confidence is not None
            and confidence < ai_confidence_floor
            # A grade a human has already settled is not waiting for anyone —
            # whether they kept the model's answer (`confirmed`) or replaced it
            # with their own (`overridden`). Leaving either flagged would mean
            # the queue never empties and the release pre-flight (which counts
            # these) never clears, which is how a warning becomes something
            # people click past. The override case is the one that matters:
            # disagreeing is done with the ordinary rubric keys, so it is the
            # *common* way an AI grade gets reviewed.
            and not ai_analysis_is_reviewed(question.analysis)
        ):
            flags.append(
                _flag(
                    ReviewReason.LOW_CONFIDENCE_AI_GRADE,
                    "high",
                    f"AI-suggested grade at {confidence}% confidence; confirm it.",
                    question_id=question.question_id,
                )
            )
        if include_ungraded and not _is_graded(question):
            flags.append(
                _flag(
                    ReviewReason.UNGRADED,
                    "low",
                    "Not yet graded.",
                    question_id=question.question_id,
                )
            )
        if _is_graded(question) and question.points_awarded is not None:
            graded_scores.append(question.points_awarded)

    # ── A whole submission scoring zero often means a systematic failure
    # (wrong version matched, misaligned scan) rather than a bad student. ──
    if len(graded_scores) >= _ZERO_SWEEP_MIN_GRADED and all(
        score == 0 for score in graded_scores
    ):
        flags.append(
            _flag(
                ReviewReason.ZERO_SCORE_SWEEP,
                "medium",
                f"All {len(graded_scores)} graded questions scored 0; "
                "check the version/alignment.",
            )
        )

    return flags


def _normalize_id(value: str) -> str:
    return value.strip().casefold()


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def flag_missing_submissions(
    roster: list[RosterStudent],
    documents: list[StudentSubmissionDocument],
) -> list[ReviewFlag]:
    """Flag roster students who have no matching submission.

    This is a *cohort-level* check (the whole roster against every submission),
    so — like the consistency audit — it is a sibling of the per-submission
    :func:`flag_submission` rather than a part of it: a student who never turned
    in an assignment has no ``.submission.md`` for ``flag_submission`` to inspect.

    A roster student counts as having submitted when a submission carries their
    roster id (the strong signal, since extraction backfills canonical roster
    ids onto the ``.submission.md``), or — only when their roster name is unique
    — when a submission carries that name (covers a submission whose id box was
    blank or unreadable, which is itself already surfaced as a missing-id flag).
    A shared name can't disambiguate a missing twin, so the name fallback is
    withheld there rather than risk masking a genuine gap. Everything is exact,
    normalized matching (no fuzzy), so the result is deterministic.
    """
    submitted_ids = {
        _normalize_id(doc.student_id)
        for doc in documents
        if doc.student_id.strip()
    }
    submitted_names = {
        _normalize_name(doc.student_name)
        for doc in documents
        if doc.student_name.strip()
    }

    roster_name_counts: dict[str, int] = {}
    for student in roster:
        roster_name_counts[_normalize_name(student.name)] = (
            roster_name_counts.get(_normalize_name(student.name), 0) + 1
        )

    flags: list[ReviewFlag] = []
    for student in roster:
        norm_id = _normalize_id(student.student_id)
        norm_name = _normalize_name(student.name)
        if not norm_id and not norm_name:
            continue  # A blank roster row is nothing to look for.
        if norm_id and norm_id in submitted_ids:
            continue
        if norm_name and roster_name_counts[norm_name] == 1 and norm_name in submitted_names:
            continue
        flags.append(
            ReviewFlag(
                student_id=student.student_id.strip(),
                student_name=student.name.strip(),
                source_pdf="",
                reason=ReviewReason.MISSING_SUBMISSION,
                severity="medium",
                detail="On the roster but no matching submission was found.",
            )
        )
    return flags


def flag_regrade_requests(
    regrade_requests: list[RegradeRequest],
    documents: list[StudentSubmissionDocument],
) -> list[ReviewFlag]:
    """Flag every *open* regrade request as a high-severity review item.

    This is a *cohort-level* check (open disputes against every submission), so —
    like :func:`flag_missing_submissions` — it is a sibling of the per-submission
    :func:`flag_submission`: a dispute is tracked outside the ``.submission.md``
    (in ``.opengrader/regrades.jsonl``, see :mod:`core.submissions.regrade`).

    Each open request becomes a flag anchored to the disputed ``question_id``
    (empty for a whole-assignment dispute, which the reviewer opens at the
    student's first question). The student's name and source PDF are looked up
    from the matching submission (by id) so the flag renders and routes exactly
    like the extraction flags; an unmatched id still surfaces (the dispute
    matters even if the submission is missing).
    """
    doc_by_id = {
        _normalize_id(doc.student_id): doc
        for doc in documents
        if doc.student_id.strip()
    }

    flags: list[ReviewFlag] = []
    for request in regrade_requests:
        if not request.is_open:
            continue
        doc = doc_by_id.get(_normalize_id(request.student_id))
        student_name = doc.student_name.strip() if doc is not None else ""
        source_pdf = doc.source_pdf if doc is not None else ""
        reason = request.reason.strip()
        detail = (
            f"Student is disputing this grade: {reason}"
            if reason
            else "Student is disputing this grade."
        )
        flags.append(
            ReviewFlag(
                student_id=request.student_id.strip(),
                student_name=student_name,
                source_pdf=source_pdf,
                reason=ReviewReason.OPEN_REGRADE_REQUEST,
                severity="high",
                detail=detail,
                question_id=request.question_id.strip(),
            )
        )
    return flags


def flag_rubric_drift(
    stale_grades: "Sequence[StaleGrade]",
    documents: list[StudentSubmissionDocument],
) -> list[ReviewFlag]:
    """Flag grades awarded under a rubric that has since been edited.

    The third *cohort-level* check, and a sibling of the other two for the same
    structural reason: the evidence is not in any one ``.submission.md``. A
    grade records what a student got, never which version of the rubric was on
    screen when it was given — that is only knowable by comparing the audit
    journal's grade timestamps against its rubric-edit timestamps, which
    :func:`core.analytics.rubric_drift.detect_rubric_drift` does.

    ``medium``, deliberately. It is not ``high``: the answer *has* a grade, and
    it may well be the same grade today's rubric would give. It is not ``low``
    either: an instructor who added a partial-credit option did so because the
    old options were wrong for somebody, and the students who never got the
    benefit of it are exactly the ones nobody will hear from — the ones who
    scored badly and assumed that was the rule.

    Name and source PDF are enriched from the matching submission (by student
    id) so the flag renders and routes exactly like the extraction flags. A
    stale grade whose submission is gone still surfaces: it is a grade that was
    handed out either way.
    """
    doc_by_id = {
        _normalize_id(doc.student_id): doc
        for doc in documents
        if doc.student_id.strip()
    }

    flags: list[ReviewFlag] = []
    for stale in stale_grades:
        doc = doc_by_id.get(_normalize_id(stale.student_id))
        flags.append(
            ReviewFlag(
                student_id=stale.student_id.strip(),
                student_name=(
                    doc.student_name.strip() if doc is not None else stale.student_name.strip()
                ),
                source_pdf=doc.source_pdf if doc is not None else "",
                reason=ReviewReason.GRADED_BEFORE_RUBRIC_CHANGE,
                severity="medium",
                detail=(
                    f"Graded before the rubric changed ({stale.change.describe()}); "
                    "confirm the grade still stands under the current options."
                ),
                question_id=stale.question_id,
            )
        )
    return flags


def build_review_flags(
    documents: list[StudentSubmissionDocument],
    *,
    id_pattern: re.Pattern[str] = _DEFAULT_ID_PATTERN,
    ai_confidence_floor: int = DEFAULT_AI_CONFIDENCE_FLOOR,
    ocr_confidence_floor: float = DEFAULT_OCR_CONFIDENCE_FLOOR,
    identity_confidence_floor: float = DEFAULT_IDENTITY_CONFIDENCE_FLOOR,
    include_ungraded: bool = False,
    roster: list[RosterStudent] | None = None,
    regrade_requests: list[RegradeRequest] | None = None,
    stale_grades: "Sequence[StaleGrade] | None" = None,
) -> list[ReviewFlag]:
    """Return review flags across submissions, most severe first.

    The sort is stable and total: severity, then student name, then student id,
    then question id — so the same inputs always produce the same triage order.

    When ``roster`` is given, roster students with no matching submission are
    included as cohort-level ``MISSING_SUBMISSION`` flags (see
    :func:`flag_missing_submissions`); omit it to skip that check entirely.

    When ``regrade_requests`` is given, every *open* request is folded in as a
    high-severity ``OPEN_REGRADE_REQUEST`` flag (see
    :func:`flag_regrade_requests`); omit it to skip that check entirely.

    When ``stale_grades`` is given (from
    :func:`core.analytics.rubric_drift.detect_rubric_drift`), each grade awarded
    before its question's rubric was last edited becomes a
    ``GRADED_BEFORE_RUBRIC_CHANGE`` flag (see :func:`flag_rubric_drift`); omit
    it to skip that check entirely. It is passed in rather than computed here
    for the same reason the roster and the regrades are: this module's contract
    is that it reads the persisted submissions and nothing else.
    """
    flags: list[ReviewFlag] = []
    for document in documents:
        flags.extend(
            flag_submission(
                document,
                id_pattern=id_pattern,
                ai_confidence_floor=ai_confidence_floor,
                ocr_confidence_floor=ocr_confidence_floor,
                identity_confidence_floor=identity_confidence_floor,
                include_ungraded=include_ungraded,
            )
        )
    if roster is not None:
        flags.extend(flag_missing_submissions(roster, documents))
    if regrade_requests is not None:
        flags.extend(flag_regrade_requests(regrade_requests, documents))
    if stale_grades is not None:
        flags.extend(flag_rubric_drift(stale_grades, documents))
    flags.sort(
        key=lambda flag: (
            flag.severity_rank,
            flag.student_name.lower(),
            flag.student_id,
            flag.question_id,
        )
    )
    return flags
