from dataclasses import dataclass, field

from opengrader.core.qa_file import QARubricFlatOption as RubricOptionSeed
from opengrader.core.question_types import FREE_RESPONSE, QuestionType
from opengrader.core.submissions.answer_patterns import PatternKind, PatternSeverity
from opengrader.core.submissions.consistency import ConsistencyKind, ConsistencySeverity
from opengrader.core.submissions.page_context import PageContextEntry
from opengrader.core.submissions.regrade import RegradeStatus
from opengrader.core.submissions.similarity import SimilarityKind, SimilaritySeverity
from opengrader.core.submissions.review import ReviewReason, ReviewSeverity
from opengrader.core.submissions.student_file import IdentityCandidate


@dataclass(frozen=True)
class SplitPiece:
    """One submission as the splitter cut it, for the join dialog. Named by
    its file, never the student: grading is anonymous by default."""

    student_item_id: str
    stem: str
    pages: int
    graded: int


@dataclass(frozen=True)
class SplitNeighbours:
    here: SplitPiece
    # The submissions scanned just before and after; only these can be joined.
    before: SplitPiece | None
    after: SplitPiece | None


@dataclass(frozen=True)
class StudentSummary:
    id: int
    full_name: str
    student_id: str = ""


_REVIEW_SEVERITY_RANK: dict[ReviewSeverity, int] = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class ReviewItem:
    student_item_id: str
    student_name: str
    student_display_id: str
    question_id: str
    reason: ReviewReason
    severity: ReviewSeverity
    detail: str
    source_pdf_name: str = ""
    id_candidates: tuple[IdentityCandidate, ...] = ()

    @property
    def severity_rank(self) -> int:
        return _REVIEW_SEVERITY_RANK[self.severity]

    @property
    def is_identity_flag(self) -> bool:
        reason = getattr(self.reason, "value", self.reason)
        return reason in ("missing_student_id", "malformed_student_id")

    @property
    def is_version_flag(self) -> bool:
        return getattr(self.reason, "value", self.reason) == "version_unknown"


@dataclass(frozen=True)
class ConsistencyMember:
    student_item_id: str
    student_id: str
    student_name: str
    source_pdf_name: str

    @property
    def is_navigable(self) -> bool:
        return bool(self.student_item_id)


@dataclass(frozen=True)
class ConsistencyGradeVariant:
    points_awarded: float
    rubric_labels: tuple[str, ...]
    members: tuple[ConsistencyMember, ...]


@dataclass(frozen=True)
class ConsistencyReportItem:
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
    a: SimilarityReportMember
    b: SimilarityReportMember
    score: float


@dataclass(frozen=True)
class SimilarityReportItem:
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
    student_item_id: str
    student_id: str
    student_name: str
    source_pdf_name: str

    @property
    def is_navigable(self) -> bool:
        return bool(self.student_item_id)


@dataclass(frozen=True)
class SharedWrongAnswerRow:
    question_id: str
    answer: str
    expected_answer: str
    match_probability: float


@dataclass(frozen=True)
class AnswerPatternReportItem:
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
        return self.shared_wrong[0].question_id if self.shared_wrong else ""


@dataclass(frozen=True)
class AnswerPatternReportView:
    items: tuple[AnswerPatternReportItem, ...] = ()
    analyzed_question_count: int = 0
    excluded_question_count: int = 0
    pairs_compared: int = 0
    threshold: float = 0.0
    expected_by_chance: float = 0.0

    @property
    def analyzed(self) -> bool:
        return self.analyzed_question_count > 0


@dataclass(frozen=True)
class RegradeReportItem:
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
    source_pdf_name: str = ""
    # 0-100 confidence; None means unverified.
    response_confidence: float | None = None
    response_source: str | None = None
    # The service that read this answer, from its `Read by` line; None when unrecorded.
    response_provider: str | None = None
    source_pdf_path: str = ""
    page_context: tuple[PageContextEntry, ...] = ()
