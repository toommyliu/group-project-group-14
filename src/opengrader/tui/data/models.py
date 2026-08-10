from dataclasses import dataclass, field

from opengrader.core.qa_file.scoring import RubricOptionKind, RubricSelectionMode
from opengrader.core.question_types import FREE_RESPONSE, QuestionType
from opengrader.core.submissions.answer_patterns import PatternKind, PatternSeverity
from opengrader.core.submissions.consistency import ConsistencyKind, ConsistencySeverity
from opengrader.core.submissions.page_context import PageContextEntry
from opengrader.core.submissions.regrade import RegradeStatus
from opengrader.core.submissions.similarity import SimilarityKind, SimilaritySeverity
from opengrader.core.submissions.review import ReviewReason, ReviewSeverity
from opengrader.core.submissions.student_file import IdentityCandidate


@dataclass(frozen=True)
class StudentSummary:
    id: int
    full_name: str
    student_id: str = ""


_REVIEW_SEVERITY_RANK: dict[ReviewSeverity, int] = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class ReviewItem:
    """A needs-review flag bound to the nav ids the TUI navigates by.

    Wraps a `core` `ReviewFlag` with the ``student_item_id`` (``"s3"``) the
    grade screen opens by, plus an anonymity-aware label pair, so the review
    modal can both display the flag and jump straight to it.
    """

    student_item_id: str
    student_name: str
    student_display_id: str
    question_id: str
    reason: ReviewReason
    severity: ReviewSeverity
    detail: str
    # The scanned PDF this flag came from, so the reviewer can find the paper.
    source_pdf_name: str = ""
    # Roster people tied on this submission's name (duplicate-name flags only).
    # When present, the review modal offers them as a pick list for assigning
    # the student id.
    id_candidates: tuple[IdentityCandidate, ...] = ()

    @property
    def severity_rank(self) -> int:
        return _REVIEW_SEVERITY_RANK[self.severity]

    @property
    def is_identity_flag(self) -> bool:
        """True when this flag is a missing/suspect student id worth resolving."""
        reason = getattr(self.reason, "value", self.reason)
        return reason in ("missing_student_id", "malformed_student_id")


@dataclass(frozen=True)
class ConsistencyMember:
    """One student inside a consistency variant, bound to a nav target.

    Wraps a `core` `ResponseRef` with the ``student_item_id`` (``"s3"``) the
    grade screen opens by, so the audit modal can jump straight to that exact
    student. ``student_item_id`` is empty when the member can't be mapped to a
    loaded submission (e.g. a blank source PDF); such a row is shown but not
    navigable.
    """

    student_item_id: str
    student_id: str
    student_name: str
    source_pdf_name: str

    @property
    def is_navigable(self) -> bool:
        return bool(self.student_item_id)


@dataclass(frozen=True)
class ConsistencyGradeVariant:
    """A distinct grade outcome within an inconsistent answer group."""

    points_awarded: float
    rubric_labels: tuple[str, ...]
    members: tuple[ConsistencyMember, ...]


@dataclass(frozen=True)
class ConsistencyReportItem:
    """An inconsistently-graded answer group, bound to the TUI's nav ids.

    The provider's UI-facing view of a `core` `ConsistencyFlag`: it mirrors the
    flag's display data and enriches each member with the ``student_item_id``
    the grade screen navigates by, so the audit modal can render the group and
    open any student in it. Attribute names match the core flag so the shared
    `tui/formatting.py:format_consistency_*` helpers render either.
    """

    question_id: str
    assignment_version: str | None
    kind: ConsistencyKind
    severity: ConsistencySeverity
    answer_key: str
    sample_response_text: str
    variants: tuple[ConsistencyGradeVariant, ...]

    @property
    def is_blank(self) -> bool:
        return self.answer_key == ""

    @property
    def member_count(self) -> int:
        return sum(len(variant.members) for variant in self.variants)


@dataclass(frozen=True)
class SimilarityReportMember:
    """One student in a similarity cluster, bound to a nav target.

    Wraps a `core` `SimilarityMember` with the ``student_item_id`` (``"s3"``) the
    grade screen opens by, so the audit modal can jump straight to that exact
    student. ``student_item_id`` is empty when the member can't be mapped to a
    loaded submission; such a row is shown but not navigable.
    """

    student_item_id: str
    student_id: str
    student_name: str
    source_pdf_name: str
    response_text: str

    @property
    def is_navigable(self) -> bool:
        return bool(self.student_item_id)


@dataclass(frozen=True)
class SimilarityReportPair:
    """One above-threshold pairing inside a cluster and how alike it is."""

    a: SimilarityReportMember
    b: SimilarityReportMember
    score: float


@dataclass(frozen=True)
class SimilarityReportItem:
    """A cluster of near-identical free responses, bound to the TUI's nav ids.

    The provider's UI-facing view of a `core` `SimilarityFlag`: it mirrors the
    flag's display data and enriches each member with the ``student_item_id`` the
    grade screen navigates by, so the audit modal can render the cluster and open
    any student in it. Attribute names match the core flag so the shared
    `tui/formatting.py:format_similarity_*` helpers render either.
    """

    question_id: str
    assignment_version: str | None
    kind: SimilarityKind
    severity: SimilaritySeverity
    max_score: float
    members: tuple[SimilarityReportMember, ...]
    pairs: tuple[SimilarityReportPair, ...]

    @property
    def member_count(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class AnswerPatternMember:
    """One student in a flagged objective answer-pattern pair.

    The nav-binding half of a `core` `PatternMember`: ``student_item_id``
    (``"s3"``) is what the grade screen opens by, and is empty when the student
    can't be mapped to a loaded submission (such a row renders inert rather than
    mis-pointing at somebody else) — the same contract
    :class:`SimilarityReportMember` follows. It carries no response text on
    purpose: an objective flag is about a *pattern across questions*, so there is
    no single answer to quote.
    """

    student_item_id: str
    student_id: str
    student_name: str
    source_pdf_name: str

    @property
    def is_navigable(self) -> bool:
        return bool(self.student_item_id)


@dataclass(frozen=True)
class SharedWrongAnswerRow:
    """One question where a flagged pair gave the same incorrect answer."""

    question_id: str
    answer: str
    expected_answer: str
    match_probability: float


@dataclass(frozen=True)
class AnswerPatternReportItem:
    """A pair whose incorrect objective answers agree implausibly often.

    The provider's UI-facing view of a `core` `AnswerPatternFlag`, with both
    students bound to the nav ids the grade screen opens by. Attribute names
    mirror the core flag so `tui/formatting.py:format_answer_pattern_*` renders
    either.
    """

    assignment_version: str | None
    kind: PatternKind
    severity: PatternSeverity
    a: AnswerPatternMember
    b: AnswerPatternMember
    shared_wrong: tuple[SharedWrongAnswerRow, ...]
    expected_shared: float
    z_score: float
    both_wrong_count: int
    difference_count: int
    harpp_hogan_index: float | None

    @property
    def members(self) -> tuple[AnswerPatternMember, AnswerPatternMember]:
        return (self.a, self.b)

    @property
    def shared_wrong_count(self) -> int:
        return len(self.shared_wrong)

    @property
    def first_question_id(self) -> str:
        """Where selecting a member of this pair opens on the grade screen.

        A pattern spans many questions, so there is no single anchor the way
        there is for a similarity cluster; the rarest shared wrong answer (the
        engine's leading entry) is the one most worth looking at first.
        """
        return self.shared_wrong[0].question_id if self.shared_wrong else ""


@dataclass(frozen=True)
class AnswerPatternReportView:
    """The objective half of the similarity modal, findings and context together.

    ``analyzed_question_count`` / ``excluded_question_count`` /
    ``pairs_compared`` are not decoration: a pair count is what says whether one
    flag out of nineteen thousand comparisons is remarkable, and an empty list
    over two analyzable questions means something completely different from an
    empty list over fifty. The modal shows them above the findings for the same
    reason the CLI prints them first.
    """

    items: tuple[AnswerPatternReportItem, ...] = ()
    analyzed_question_count: int = 0
    excluded_question_count: int = 0
    pairs_compared: int = 0
    threshold: float = 0.0
    expected_by_chance: float = 0.0

    @property
    def analyzed(self) -> bool:
        """Whether any question survived the engine's eligibility guards."""
        return self.analyzed_question_count > 0


@dataclass(frozen=True)
class RegradeReportItem:
    """One open regrade request, bound to the TUI's nav ids.

    The provider's UI-facing view of a `core` `RegradeRequest`: it carries the
    dispute's identity plus the ``student_item_id`` (``"s3"``) the grade screen
    opens by, so the regrade modal can render the dispute, route to the disputed
    question, and resolve/deny it. ``student_item_id`` is empty when the disputed
    student can't be matched to a loaded submission (e.g. a missing submission);
    such a row is shown but not navigable. ``question_id`` empty = a whole-
    assignment dispute (routes to the student's first question).
    """

    request_id: str
    student_item_id: str
    student_id: str
    student_name: str
    question_id: str
    reason: str
    status: RegradeStatus
    source_pdf_name: str = ""
    created_at: str = ""
    created_by: str = ""

    @property
    def is_navigable(self) -> bool:
        return bool(self.student_item_id)


@dataclass(frozen=True)
class RubricOptionSeed:
    """A flattened rubric option prepared for grading UI and persistence."""

    label: str
    points_delta: float
    kind: RubricOptionKind
    selection_mode: RubricSelectionMode
    # Feedback auto-inserted into the comment box when this option is selected,
    # sourced from the QA file's ``**Comment**`` bullet. ``None`` when unset.
    default_comment: str | None = None


@dataclass(frozen=True)
class RubricOptionUpdate:
    old_label: str
    old_points_delta: float
    new_label: str
    new_points_delta: float


@dataclass(frozen=True)
class RubricEditResult:
    propagated_count: int
    deleted_blocked_count: int = 0


@dataclass(frozen=True)
class QuestionSeed:
    """Question data prepared by providers for a grading session."""

    question_id: str
    image_path: str
    rubric_options: list[RubricOptionSeed]
    points: float = 0.0
    prompt: str = ""
    expected_answers: list[str] = field(default_factory=list)
    response_text: str = ""
    points_awarded: float | None = None
    selected_rubric_labels: tuple[str, ...] = ()
    analysis: str = ""
    warnings: list[str] = field(default_factory=list)
    question_type: QuestionType = FREE_RESPONSE
    # Submission-level: file name of the student's scanned source PDF.
    source_pdf_name: str = ""
    # Transcription confidence (0-100) for this response, or None when
    # unverified (VLM-transcribed or pre-confidence extraction). Drives the
    # grade screen's confidence chip.
    response_confidence: float | None = None
    # Which reader produced `response_text` (`core/submissions/transcription.py`),
    # or None when it was never recorded. Carried into the runtime state so an
    # undone correction can put the *whole* previous reading back — words,
    # confidence and reader together.
    response_source: str | None = None
    # Submission-level: absolute path to the student's scanned PDF, or "" when
    # it is missing (stub data, a moved scan). Needed to render whole pages.
    source_pdf_path: str = ""
    # The pages a grader can step through for this question: the page(s) this
    # answer was cropped from (with the crop region highlighted), then every
    # extra work page in the submission. Empty when there is no source PDF to
    # render. See `core/submissions/page_context.py`.
    page_context: tuple[PageContextEntry, ...] = ()
