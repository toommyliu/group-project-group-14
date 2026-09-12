from __future__ import annotations

from difflib import SequenceMatcher
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from opengrader.core.grading.files import FileGradingSession
from opengrader.core.grading.questions import (
    QuestionContext as _QuestionContext,
    flatten_question_contexts,
    questions_for_submission,
    rubric_options,
)
from opengrader.core.export import default_bundle_name
from opengrader.core.comments import CommentLibrary, read_comments, write_comments
from opengrader.core.claims import (
    WorkClaim,
    WorkSlice,
    active_claims,
    claim_work,
    complete_claim,
    release_claim,
)
from opengrader.core.curve import Curve, read_curve, write_curve
from opengrader.core.concurrency import check_unmodified, file_mtime_ns
from opengrader.core.history import (
    GradeJournal,
    GradeJournalEntry,
    current_actor,
    journal_fingerprint,
    read_journal,
    resolve_history_dir,
)
from opengrader.core.pdf.pdf_preview import get_pdf_page_count
from opengrader.core.qa_file import (
    QARubricFlatOption,
    QARubricItem,
    flatten_rubric_options,
    load_qa_markdown,
    outcomes_by_question,
    possible_points_by_question,
    replace_qa_rubric_options,
    save_qa_markdown,
)
from opengrader.core.qa_file.scoring import awarded_points_for_rubric_selection
from opengrader.core.question_types import (
    MULTIPLE_ANSWER,
    MULTIPLE_CHOICE,
    humanize_question_label,
)
from opengrader.core.submissions import (
    SOURCE_HUMAN,
    StudentSubmissionGradeUpdate,
    StudentSubmissionQuestion,
    set_student_identity_file,
    update_student_submission_file,
)
from opengrader.core.submissions.watch import ExtractionWatcher
from opengrader.core.analytics import (
    GraderAgreementReport,
    GradingSessionReport,
    ItemAnalysisReport,
    OutcomeReport,
    answers_from_journal,
    build_grader_agreement_report,
    build_grading_session_report,
    build_item_analysis,
    build_outcome_report,
)
from opengrader.core.submissions.page_context import (
    build_page_context,
    infer_extra_pages,
)
from opengrader.core.submissions.answer_patterns import (
    AnswerPatternFlag,
    build_answer_pattern_report_for_documents,
)
from opengrader.core.submissions.consistency import ConsistencyFlag, build_consistency_flags
from opengrader.core.submissions.similarity import SimilarityFlag, build_similarity_flags
from opengrader.core.submissions.regrade import (
    add_regrade_request,
    deny_regrade_request,
    open_regrade_requests,
    resolve_regrade_request,
    resolve_regrades_path,
)
from opengrader.core.submissions.review import (
    ReviewReason,
    build_review_flags,
    flag_regrade_requests,
    flag_rubric_drift,
    flag_submission,
)
from opengrader.core.analytics.rubric_drift import detect_rubric_drift
from opengrader.core.release import (
    GradeRelease,
    ReadinessReport,
    ReleaseComparison,
    build_readiness_report,
    build_release,
    compare_release,
    latest_release,
    read_releases,
    write_release,
)
from opengrader.tui.data.models import (
    AnswerPatternMember,
    AnswerPatternReportItem,
    AnswerPatternReportView,
    ConsistencyGradeVariant,
    ConsistencyMember,
    ConsistencyReportItem,
    SharedWrongAnswerRow,
    QuestionSeed,
    RegradeReportItem,
    ReviewItem,
    RubricEditResult,
    RubricOptionSeed,
    RubricOptionUpdate,
    SimilarityReportItem,
    SimilarityReportMember,
    SimilarityReportPair,
    StudentSummary,
)


_T = TypeVar("_T")


@dataclass(frozen=True)
class _LoadedSubmission:
    grading: FileGradingSession
    qa_version: str | None
    question_contexts: list[_QuestionContext]


@dataclass(frozen=True)
class _RubricOptionDelete:
    label: str
    points_delta: float


@dataclass(frozen=True)
class IncrementalLoadResult:
    added: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


class SubmissionFilesProvider:
    def __init__(
        self,
        *,
        qa_file_path: str | Path,
        submissions_path: str | Path,
        default_image_path: str,
    ) -> None:
        self._default_image_path = default_image_path
        self._qa_path = Path(qa_file_path).expanduser().resolve()
        self._submissions_path = Path(submissions_path).expanduser().resolve()
        self._qa_doc = load_qa_markdown(self._qa_path)
        self._qa_mtime_ns = file_mtime_ns(self._qa_path)
        self._history_dir = resolve_history_dir(self._submissions_path)
        self._journal = GradeJournal(self._history_dir)
        self._submissions = self._load_submissions(self._submissions_path)
        self._validate_question_sets()
        self._generation = 0
        self._report_cache: dict[object, object] = {}
        self._report_cache_fingerprint: tuple[object, ...] | None = None

    @property
    def submissions_path(self) -> Path:
        return self._submissions_path

    @property
    def qa_file_path(self) -> Path:
        return self._qa_path

    @property
    def course_label(self) -> str:
        course_code = self._submissions[0].grading.document.course_code
        return course_code or "Course"

    @property
    def assignment_label(self) -> str:
        title = self._qa_doc.title or self._submissions[0].grading.document.assignment_title
        return title or "Assignment"

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []

        base_labels = [
            submission.grading.document.student_name.strip() or submission.grading.path.stem
            for submission in self._submissions
        ]
        duplicate_labels = {
            label for label in base_labels if base_labels.count(label) > 1 and label
        }

        students: list[StudentSummary] = []
        for index, (submission, base_label) in enumerate(
            zip(self._submissions, base_labels, strict=True)
        ):
            label = base_label or f"Student {index + 1}"
            student_id = submission.grading.document.student_id.strip()
            if label in duplicate_labels and student_id:
                label = f"{label} ({student_id})"
            students.append(
                StudentSummary(
                    id=index + 1,
                    full_name=label,
                    student_id=student_id,
                )
            )
        return students

    def build_question_seeds(
        self,
        *,
        assignment_id: int | None = None,
        assignment_title: str | None = None,
        student_item_id: str | None = None,
    ) -> list[QuestionSeed]:
        _ = (assignment_id, assignment_title)
        submission = self._submission_for_student_item(student_item_id)

        source_pdf_path = submission.grading.source_pdf_path
        has_source_pdf = source_pdf_path.exists()
        extra_pages = (
            self._extra_pages_for(submission, source_pdf_path) if has_source_pdf else ()
        )

        submission_warnings = []
        if extra_pages:
            submission_warnings.append(
                f"Submission has {len(extra_pages)} page(s) outside the answer "
                f"crops (scratch/overflow work) — press w to page through them."
            )

        seeds: list[QuestionSeed] = []

        for i, context in enumerate(submission.question_contexts):
            response = submission.grading.questions_by_id.get(context.question_id)
            image_path = self._image_path_for_response(response)
            page_context = (
                build_page_context(
                    response.source_segments if response is not None else (),
                    extra_pages,
                )
                if has_source_pdf
                else ()
            )
            seeds.append(
                QuestionSeed(
                    question_id=context.question_id,
                    image_path=image_path,
                    rubric_options=self._rubric_options(context),
                    points=context.points,
                    prompt=context.prompt,
                    expected_answers=context.expected_answers,
                    response_text=response.response_text if response is not None else "",
                    points_awarded=(
                        response.points_awarded if response is not None else None
                    ),
                    selected_rubric_labels=(
                        response.rubric_labels if response is not None else ()
                    ),
                    analysis=response.analysis if response is not None else "",
                    warnings=submission_warnings if i == 0 else [],
                    question_type=(
                        context.question_type
                        if context.question_type in (MULTIPLE_CHOICE, MULTIPLE_ANSWER)
                        or response is None
                        else response.question_type
                    ),
                    source_pdf_name=Path(submission.grading.document.source_pdf).name
                    if submission.grading.document.source_pdf
                    else "",
                    response_confidence=(
                        response.response_confidence if response is not None else None
                    ),
                    response_source=(
                        response.response_source if response is not None else None
                    ),
                    source_pdf_path=str(source_pdf_path) if has_source_pdf else "",
                    page_context=page_context,
                )
            )

        return seeds

    @staticmethod
    def _extra_pages_for(
        submission: _LoadedSubmission,
        source_pdf_path: Path,
    ) -> tuple[int, ...]:
        total_pages = get_pdf_page_count(source_pdf_path)
        pages = submission.grading.document.extra_pages
        if not pages:
            segments = tuple(
                segment
                for question in submission.grading.document.questions
                for segment in question.source_segments
            )
            pages = infer_extra_pages(total_pages, segments)
        return tuple(page for page in pages if 1 <= page <= total_pages)

    @contextmanager
    def _edit_submission(self, student_item_id: str) -> Iterator[FileGradingSession]:
        grading = self._submission_for_student_item(student_item_id).grading
        try:
            yield grading
        finally:
            # A PDF failure can happen after the submission was already saved.
            self._bump_generation()

    def save_question_grade(
        self,
        *,
        student_item_id: str,
        question_id: str,
        possible_points: float,
        points_awarded: float | None,
        rubric_labels: tuple[str, ...],
        analysis: str | None = None,
    ) -> None:
        with self._edit_submission(student_item_id) as grading:
            grading.save_grade(
                question_id=question_id,
                possible_points=possible_points,
                points_awarded=points_awarded,
                rubric_labels=rubric_labels,
                analysis=analysis,
            )

    def stage_question_analysis(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
    ) -> None:
        with self._edit_submission(student_item_id) as grading:
            grading.stage_comment(question_id=question_id, analysis=analysis)

    def commit_question_analysis(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
        previous_analysis: str,
    ) -> None:
        with self._edit_submission(student_item_id) as grading:
            grading.commit_comment(
                question_id=question_id,
                analysis=analysis,
                previous_analysis=previous_analysis,
            )

    def save_question_analysis(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
    ) -> None:
        with self._edit_submission(student_item_id) as grading:
            grading.save_comment(question_id=question_id, analysis=analysis)

    def save_question_transcription(
        self,
        *,
        student_item_id: str,
        question_id: str,
        response_text: str,
        response_confidence: float | None = 100.0,
        response_source: str | None = SOURCE_HUMAN,
    ) -> None:
        with self._edit_submission(student_item_id) as grading:
            grading.save_transcription(
                question_id=question_id,
                response_text=response_text,
                response_confidence=response_confidence,
                response_source=response_source,
            )

    def save_question_comment_annotation(
        self,
        *,
        student_item_id: str,
        question_id: str,
        comment: str,
    ) -> Path:
        grading = self._submission_for_student_item(student_item_id).grading
        return grading.sync_pdf_annotations(
            question_id, comment_override=comment
        )

    def save_rubric_options(
        self,
        *,
        student_item_id: str,
        question_id: str,
        options: list[RubricOptionSeed],
        original_options: list[RubricOptionSeed],
    ) -> RubricEditResult:
        submission = self._submission_for_student_item(student_item_id)
        context = self._question_context_for_submission(submission, question_id)
        changes, deleted_options = self._rubric_option_diff(
            original_options,
            options,
        )

        blocked_count = self._matching_saved_grade_count(
            qa_version=submission.qa_version,
            question_id=question_id,
            context=context,
            options=deleted_options,
        )
        if blocked_count:
            raise ValueError(
                "Cannot delete rubric option used by "
                f"{blocked_count} saved grade{'s' if blocked_count != 1 else ''}."
            )

        check_unmodified(self._qa_path, self._qa_mtime_ns)

        replace_qa_rubric_options(
            self._qa_doc,
            question_id,
            [
                QARubricFlatOption(
                    label=option.label,
                    points_delta=option.points_delta,
                    kind=option.kind,
                    selection_mode=option.selection_mode,
                )
                for option in options
            ],
            version=submission.qa_version,
        )
        save_qa_markdown(self._qa_doc, self._qa_path)
        self._qa_mtime_ns = file_mtime_ns(self._qa_path)

        propagated_count = self._propagate_rubric_option_changes(
            qa_version=submission.qa_version,
            question_id=question_id,
            context=context,
            changes=changes,
        )

        self._qa_doc = load_qa_markdown(self._qa_path)
        self._qa_mtime_ns = file_mtime_ns(self._qa_path)
        self._submissions = self._load_submissions(self._submissions_path)
        self._bump_generation()
        self._journal_event(
            kind="rubric",
            submission=None,
            question_id=question_id,
            summary=(
                f"Rubric edited · {propagated_count} "
                f"grade{'s' if propagated_count != 1 else ''} updated"
            ),
            old={"options": [option.label for option in original_options]},
            new={"options": [option.label for option in options]},
        )
        return RubricEditResult(propagated_count=propagated_count)

    def _journal_event(
        self,
        *,
        kind: str,
        submission: "_LoadedSubmission | None",
        question_id: str,
        summary: str,
        old: dict[str, object],
        new: dict[str, object],
    ) -> None:
        student_id = "" if submission is None else submission.grading.document.student_id
        student_name = "" if submission is None else submission.grading.document.student_name
        try:
            self._journal.append(
                kind=kind,  # type: ignore[arg-type]
                student_id=student_id,
                student_name=student_name,
                question_id=question_id,
                summary=summary,
                old=old,
                new=new,
            )
        except OSError:
            pass


    def _cohort_fingerprint(self) -> tuple[object, ...]:
        return (
            self._generation,
            tuple(
                (submission.grading.path, submission.grading.mtime_ns)
                for submission in self._submissions
            ),
            self._qa_mtime_ns,
            file_mtime_ns(resolve_regrades_path(self._submissions_path)),
            journal_fingerprint(self._history_dir),
            len(self._submissions),
        )

    def _bump_generation(self) -> None:
        self._generation += 1

    def _cached(self, key: object, builder: Callable[[], _T]) -> _T:
        fingerprint = self._cohort_fingerprint()
        if fingerprint != self._report_cache_fingerprint:
            self._report_cache = {}
            self._report_cache_fingerprint = fingerprint
        if key not in self._report_cache:
            self._report_cache[key] = builder()
        return self._report_cache[key]

    def build_review_items(self) -> list[ReviewItem]:
        """External journal and regrade changes invalidate this cache; external submission or
        QA changes require reload_all.
        """
        return self._cached("review_items", self._build_review_items)

    def _build_review_items(self) -> list[ReviewItem]:
        items: list[ReviewItem] = []
        item_id_by_norm_id: dict[str, str] = {}
        for index, submission in enumerate(self._submissions):
            item_id = f"s{index + 1}"
            document = submission.grading.document
            norm_id = document.student_id.strip().casefold()
            if norm_id:
                item_id_by_norm_id.setdefault(norm_id, item_id)
            display_id = document.student_id.strip() or item_id
            source_pdf_name = Path(document.source_pdf).name if document.source_pdf else ""
            for flag in flag_submission(document):
                candidates = (
                    document.id_candidates
                    if flag.reason in (ReviewReason.MISSING_STUDENT_ID, ReviewReason.MALFORMED_STUDENT_ID)
                    else ()
                )
                items.append(
                    ReviewItem(
                        student_item_id=item_id,
                        student_name=document.student_name.strip(),
                        student_display_id=display_id,
                        question_id=flag.question_id,
                        reason=flag.reason,
                        severity=flag.severity,
                        detail=flag.detail,
                        source_pdf_name=source_pdf_name,
                        id_candidates=candidates,
                    )
                )

        documents = [submission.grading.document for submission in self._submissions]
        cohort_flags = [
            *flag_regrade_requests(
                open_regrade_requests(self._submissions_path), documents
            ),
            *flag_rubric_drift(
                detect_rubric_drift(read_journal(self._history_dir)), documents
            ),
        ]
        for flag in cohort_flags:
            norm_id = flag.student_id.strip().casefold()
            item_id = item_id_by_norm_id.get(norm_id, "")
            source_pdf_name = Path(flag.source_pdf).name if flag.source_pdf else ""
            items.append(
                ReviewItem(
                    student_item_id=item_id,
                    student_name=flag.student_name,
                    student_display_id=flag.student_id or item_id or "unknown",
                    question_id=flag.question_id,
                    reason=flag.reason,
                    severity=flag.severity,
                    detail=flag.detail,
                    source_pdf_name=source_pdf_name,
                )
            )
        items.sort(
            key=lambda item: (
                item.severity_rank,
                item.student_name.lower(),
                item.student_display_id,
                item.question_id,
            )
        )
        return items

    def assign_student_identity(
        self,
        *,
        student_item_id: str,
        student_id: str,
        student_name: str | None = None,
    ) -> None:
        submission = self._submission_for_student_item(student_item_id)
        old_id = submission.grading.document.student_id
        old_name = submission.grading.document.student_name
        set_student_identity_file(
            submission.grading.path,
            student_id=student_id,
            student_name=student_name,
            expected_mtime_ns=submission.grading.mtime_ns,
        )
        self._reload_submission(student_item_id)
        resolved_name = student_name if student_name is not None else old_name
        self._journal_event(
            kind="identity",
            submission=submission,
            question_id="",
            summary=(
                f"assigned id {student_id}"
                + (f" ({resolved_name})" if resolved_name else "")
            ),
            old={"student_id": old_id, "student_name": old_name},
            new={"student_id": student_id, "student_name": resolved_name},
        )

    def build_regrade_items(self) -> list[RegradeReportItem]:
        return self._cached("regrade_items", self._build_regrade_items)

    def _build_regrade_items(self) -> list[RegradeReportItem]:
        item_id_by_norm_id: dict[str, str] = {}
        name_by_norm_id: dict[str, str] = {}
        pdf_by_norm_id: dict[str, str] = {}
        for index, submission in enumerate(self._submissions):
            document = submission.grading.document
            norm_id = document.student_id.strip().casefold()
            if norm_id and norm_id not in item_id_by_norm_id:
                item_id_by_norm_id[norm_id] = f"s{index + 1}"
                name_by_norm_id[norm_id] = document.student_name.strip()
                pdf_by_norm_id[norm_id] = (
                    Path(document.source_pdf).name if document.source_pdf else ""
                )

        items: list[RegradeReportItem] = []
        for request in open_regrade_requests(self._submissions_path):
            norm_id = request.student_id.strip().casefold()
            items.append(
                RegradeReportItem(
                    request_id=request.request_id,
                    student_item_id=item_id_by_norm_id.get(norm_id, ""),
                    student_id=request.student_id,
                    student_name=name_by_norm_id.get(norm_id, ""),
                    question_id=request.question_id,
                    reason=request.reason,
                    status=request.status,
                    source_pdf_name=pdf_by_norm_id.get(norm_id, ""),
                    created_at=request.created_at,
                    created_by=request.created_by,
                )
            )
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items

    def resolve_regrade(self, request_id: str, *, note: str = "") -> None:
        resolve_regrade_request(self._submissions_path, request_id, note=note)
        self._bump_generation()

    def deny_regrade(self, request_id: str, *, note: str = "") -> None:
        deny_regrade_request(self._submissions_path, request_id, note=note)
        self._bump_generation()

    def file_regrade(
        self,
        *,
        student_id: str,
        reason: str,
        question_id: str = "",
    ) -> None:
        add_regrade_request(
            self._submissions_path,
            student_id=student_id,
            reason=reason,
            question_id=question_id,
        )
        self._bump_generation()

    def regrade_student_choices(self) -> list[tuple[str, str]]:
        seen: dict[str, str] = {}
        for submission in self._submissions:
            document = submission.grading.document
            student_id = document.student_id.strip()
            if not student_id or student_id in seen:
                continue
            name = document.student_name.strip()
            seen[student_id] = f"{name} · {student_id}" if name else student_id
        return sorted(seen.items(), key=lambda pair: pair[1].casefold())

    def regrade_question_choices(self) -> list[tuple[str, str]]:
        seen: dict[str, str] = {}
        for submission in self._submissions:
            for question in submission.grading.document.questions:
                seen.setdefault(
                    question.question_id,
                    humanize_question_label(question.question_id),
                )
        return list(seen.items())

    def student_identity_for_item(
        self, student_item_id: str | None
    ) -> tuple[str, str] | None:
        if not student_item_id:
            return None
        digits = "".join(ch for ch in student_item_id if ch.isdigit())
        if not digits:
            return None
        index = int(digits) - 1
        if not (0 <= index < len(self._submissions)):
            return None
        document = self._submissions[index].grading.document
        return document.student_id.strip(), document.student_name.strip()

    def read_grade_history(self) -> list[GradeJournalEntry]:
        return read_journal(self._history_dir)

    def build_grading_session_report(self) -> GradingSessionReport:
        return build_grading_session_report(self.read_grade_history())

    def build_grader_agreement_report(self) -> GraderAgreementReport:
        documents = [submission.grading.document for submission in self._submissions]
        adaptation = answers_from_journal(self.read_grade_history(), documents)
        return build_grader_agreement_report(adaptation.answers)

    def default_bundle_name(self) -> str:
        return default_bundle_name(
            [submission.grading.document for submission in self._submissions]
        )


    def grader_actor(self) -> str:
        return current_actor()

    def read_work_claims(self) -> list[WorkClaim]:
        return active_claims(self._submissions_path)

    def cohort_ids(self) -> tuple[list[str], list[str]]:
        question_ids: dict[str, None] = {}
        student_ids: list[str] = []
        for submission in self._submissions:
            document = submission.grading.document
            for question in document.questions:
                question_ids.setdefault(question.question_id, None)
            student_id = document.student_id.strip()
            if student_id:
                student_ids.append(student_id)
        return list(question_ids), student_ids

    def claim_work_slice(
        self, work: WorkSlice, *, actor: str | None = None, note: str = ""
    ) -> WorkClaim:
        return claim_work(
            self._submissions_path,
            work,
            actor=actor or self.grader_actor(),
            note=note,
        )

    def release_work_claim(self, claim_id: str) -> None:
        release_claim(self._submissions_path, claim_id)

    def complete_work_claim(self, claim_id: str) -> None:
        complete_claim(self._submissions_path, claim_id)

    def read_curve(self) -> Curve:
        return read_curve(self._submissions_path)

    def save_curve(self, curve: Curve) -> None:
        write_curve(self._submissions_path, curve)

    def curve_score_distribution(self) -> list[tuple[float, float | None]]:
        distribution: list[tuple[float, float | None]] = []
        for submission in self._submissions:
            questions = submission.grading.document.questions
            raw_total = sum(
                q.points_awarded for q in questions if q.points_awarded is not None
            )
            possibles = [
                q.possible_points for q in questions if q.possible_points is not None
            ]
            distribution.append((raw_total, sum(possibles) if possibles else None))
        return distribution


    def build_readiness_report(self) -> ReadinessReport:
        documents = [submission.grading.document for submission in self._submissions]
        return build_readiness_report(
            documents,
            review_flags=build_review_flags(
                documents,
                regrade_requests=open_regrade_requests(self._submissions_path),
                stale_grades=detect_rubric_drift(read_journal(self._history_dir)),
            ),
            consistency_flags=build_consistency_flags(documents),
            claims=self.read_work_claims(),
            curve=self.read_curve(),
            previous_release=self.latest_release(),
        )

    def read_releases(self) -> list[GradeRelease]:
        return read_releases(self._submissions_path)

    def latest_release(self) -> GradeRelease | None:
        return latest_release(self._submissions_path)

    def create_release(self, *, label: str = "") -> GradeRelease:
        release = build_release(
            [submission.grading.document for submission in self._submissions],
            curve=self.read_curve(),
            label=label,
            actor=self.grader_actor(),
        )
        write_release(self._submissions_path, release)
        return release

    def compare_to_release(self, release: GradeRelease) -> ReleaseComparison:
        return compare_release(
            release,
            [submission.grading.document for submission in self._submissions],
            curve=self.read_curve(),
        )

    def read_comment_library(self) -> CommentLibrary:
        return read_comments(self._submissions_path)

    def save_comment_library(self, library: CommentLibrary) -> None:
        write_comments(self._submissions_path, library)

    def available_sections(self) -> list[str]:
        sections = {
            (submission.grading.document.section or "").strip()
            for submission in self._submissions
        }
        sections.discard("")
        return sorted(sections)

    def build_item_analysis_report(
        self, section: str | None = None
    ) -> ItemAnalysisReport:
        return self._cached(
            ("item_analysis", section),
            lambda: self._build_item_analysis_report(section),
        )

    def _build_item_analysis_report(self, section: str | None) -> ItemAnalysisReport:
        submissions = self._submissions
        if section is not None:
            submissions = [
                submission
                for submission in submissions
                if (submission.grading.document.section or "").strip() == section
            ]
        possible_by_item: dict[tuple[str, str | None], float] = {}
        for submission in submissions:
            version = submission.grading.document.assignment_version
            for context in submission.question_contexts:
                possible_by_item.setdefault((context.question_id, version), context.points)
                possible_by_item.setdefault((context.question_id, None), context.points)
        return build_item_analysis(
            [submission.grading.document for submission in submissions],
            possible_points_by_question=possible_by_item,
        )

    def build_outcome_mastery_report(self, section: str | None = None) -> OutcomeReport | None:
        """None means no tagged outcomes; a tagged but ungraded assignment returns a report."""
        return self._cached(
            ("outcome_mastery", section),
            lambda: self._build_outcome_mastery_report(section),
        )

    def _build_outcome_mastery_report(self, section: str | None) -> OutcomeReport | None:
        tags = outcomes_by_question(self._qa_doc)
        if not tags:
            return None

        submissions = self._submissions
        if section is not None:
            submissions = [
                submission
                for submission in submissions
                if (submission.grading.document.section or "").strip() == section
            ]
        return build_outcome_report(
            [submission.grading.document for submission in submissions],
            tags,
            possible_points_by_question=possible_points_by_question(self._qa_doc),
        )

    def build_consistency_report(self) -> list[ConsistencyReportItem]:
        return self._cached("consistency", self._build_consistency_report)

    def _build_consistency_report(self) -> list[ConsistencyReportItem]:
        item_id_by_source_pdf = self._item_id_by_source_pdf()
        flags = build_consistency_flags(
            [submission.grading.document for submission in self._submissions]
        )
        return [self._to_consistency_item(flag, item_id_by_source_pdf) for flag in flags]

    @staticmethod
    def _to_consistency_item(
        flag: ConsistencyFlag,
        item_id_by_source_pdf: dict[str, str],
    ) -> ConsistencyReportItem:
        variants = tuple(
            ConsistencyGradeVariant(
                points_awarded=variant.points_awarded,
                rubric_labels=variant.rubric_labels,
                members=tuple(
                    ConsistencyMember(
                        student_item_id=item_id_by_source_pdf.get(member.source_pdf, ""),
                        student_id=member.student_id,
                        student_name=member.student_name,
                        source_pdf_name=Path(member.source_pdf).name if member.source_pdf else "",
                    )
                    for member in variant.members
                ),
            )
            for variant in flag.variants
        )
        return ConsistencyReportItem(
            question_id=flag.question_id,
            assignment_version=flag.assignment_version,
            kind=flag.kind,
            severity=flag.severity,
            answer_key=flag.answer_key,
            sample_response_text=flag.sample_response_text,
            variants=variants,
        )

    def build_similarity_report(self) -> list[SimilarityReportItem]:
        return self._cached("similarity", self._build_similarity_report)

    def _build_similarity_report(self) -> list[SimilarityReportItem]:
        item_id_by_source_pdf = self._item_id_by_source_pdf()
        flags = build_similarity_flags(
            [submission.grading.document for submission in self._submissions]
        )
        return [self._to_similarity_item(flag, item_id_by_source_pdf) for flag in flags]

    def _item_id_by_source_pdf(self) -> dict[str, str]:
        item_id_by_source_pdf: dict[str, str] = {}
        for index, submission in enumerate(self._submissions):
            source_pdf = submission.grading.document.source_pdf
            if source_pdf:
                item_id_by_source_pdf.setdefault(source_pdf, f"s{index + 1}")
        return item_id_by_source_pdf

    @staticmethod
    def _to_similarity_item(
        flag: SimilarityFlag,
        item_id_by_source_pdf: dict[str, str],
    ) -> SimilarityReportItem:
        def to_member(member) -> SimilarityReportMember:
            return SimilarityReportMember(
                student_item_id=item_id_by_source_pdf.get(member.source_pdf, ""),
                student_id=member.student_id,
                student_name=member.student_name,
                source_pdf_name=Path(member.source_pdf).name if member.source_pdf else "",
                response_text=member.response_text,
            )

        members = tuple(to_member(member) for member in flag.members)
        pairs = tuple(
            SimilarityReportPair(
                a=to_member(pair.a),
                b=to_member(pair.b),
                score=pair.score,
            )
            for pair in flag.pairs
        )
        return SimilarityReportItem(
            question_id=flag.question_id,
            assignment_version=flag.assignment_version,
            kind=flag.kind,
            severity=flag.severity,
            max_score=flag.max_score,
            members=members,
            pairs=pairs,
        )

    def build_answer_pattern_report(self) -> AnswerPatternReportView:
        return self._cached("answer_pattern", self._build_answer_pattern_report)

    def _build_answer_pattern_report(self) -> AnswerPatternReportView:
        item_id_by_source_pdf = self._item_id_by_source_pdf()
        report = build_answer_pattern_report_for_documents(
            [submission.grading.document for submission in self._submissions],
            self._qa_doc,
        )
        return AnswerPatternReportView(
            items=tuple(
                self._to_answer_pattern_item(flag, item_id_by_source_pdf)
                for flag in report.flags
            ),
            analyzed_question_count=len(report.analyzed_questions),
            excluded_question_count=len(report.excluded_questions),
            pairs_compared=report.pairs_compared,
            threshold=report.threshold,
            expected_by_chance=report.expected_by_chance,
        )

    @staticmethod
    def _to_answer_pattern_item(
        flag: AnswerPatternFlag,
        item_id_by_source_pdf: dict[str, str],
    ) -> AnswerPatternReportItem:
        def to_member(member) -> AnswerPatternMember:
            return AnswerPatternMember(
                student_item_id=item_id_by_source_pdf.get(member.source_pdf, ""),
                student_id=member.student_id,
                student_name=member.student_name,
                source_pdf_name=Path(member.source_pdf).name if member.source_pdf else "",
            )

        return AnswerPatternReportItem(
            assignment_version=flag.assignment_version,
            kind=flag.kind,
            severity=flag.severity,
            a=to_member(flag.a),
            b=to_member(flag.b),
            shared_wrong=tuple(
                SharedWrongAnswerRow(
                    question_id=shared.question_id,
                    answer=shared.answer,
                    expected_answer=shared.expected_answer,
                    match_probability=shared.match_probability,
                )
                for shared in flag.shared_wrong
            ),
            expected_shared=flag.expected_shared,
            z_score=flag.z_score,
            both_wrong_count=flag.both_wrong_count,
            difference_count=flag.difference_count,
            harpp_hogan_index=flag.harpp_hogan_index,
        )

    @staticmethod
    def _submission_paths(path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        if path.is_dir():
            direct = sorted(path.glob("*.submission.md"))
            nested = sorted(path.glob("*/*.submission.md"))
            return [*direct, *nested]
        raise FileNotFoundError(f"submissions path not found: {path}")

    def _load_submissions(self, path: Path) -> list[_LoadedSubmission]:
        submission_paths = self._submission_paths(path)
        if not submission_paths:
            raise ValueError(self._empty_folder_message(path))

        return [
            self._load_submission(submission_path)
            for submission_path in submission_paths
        ]

    @staticmethod
    def _empty_folder_message(path: Path) -> str:
        if path.is_dir():
            watcher = ExtractionWatcher(path)
            if watcher.poll().appears_active:
                return (
                    f"Extraction is in progress in {path} — no submissions are "
                    "ready yet. Try again once the first one finishes."
                )
        return f"no *.submission.md files found in {path}"

    def load_new_submissions(self, keys: Iterable[str]) -> IncrementalLoadResult:
        """keys are completed manifest keys, not paths. Missing files are skipped."""
        added: list[str] = []
        skipped: list[str] = []
        loaded_paths = {submission.grading.path for submission in self._submissions}
        expected_question_ids = (
            [ctx.question_id for ctx in self._submissions[0].question_contexts]
            if self._submissions
            else None
        )
        for key in keys:
            submission_path = self._submissions_path / key / f"{key}.submission.md"
            if not submission_path.exists() or submission_path in loaded_paths:
                continue
            try:
                loaded = self._load_submission(submission_path)
            except Exception:
                skipped.append(key)
                continue

            label = loaded.grading.document.student_name.strip() or key
            actual_question_ids = [ctx.question_id for ctx in loaded.question_contexts]
            if (
                expected_question_ids is not None
                and actual_question_ids != expected_question_ids
            ):
                skipped.append(label)
                continue

            self._submissions.append(loaded)
            loaded_paths.add(loaded.grading.path)
            if expected_question_ids is None:
                expected_question_ids = actual_question_ids
            added.append(label)

        if added:
            self._bump_generation()
        return IncrementalLoadResult(added=tuple(added), skipped=tuple(skipped))

    def _load_submission(self, submission_path: Path) -> _LoadedSubmission:
        grading = FileGradingSession(
            submission_path,
            submissions_path=self._submissions_path,
            journal=self._journal,
        )
        qa_version, qa_questions = questions_for_submission(
            self._qa_doc,
            grading.document,
        )
        return _LoadedSubmission(
            grading=grading,
            qa_version=qa_version,
            question_contexts=flatten_question_contexts(qa_questions),
        )

    def reload_all(self) -> None:
        self._submissions = self._load_submissions(self._submissions_path)
        # Multiple writes can share an mtime on coarse filesystem clocks.
        self._bump_generation()

    def _reload_submission(self, student_item_id: str) -> None:
        existing = self._submission_for_student_item(student_item_id)
        index = self._submissions.index(existing)
        self._submissions[index] = self._load_submission(existing.grading.path)
        self._bump_generation()

    def _question_context_for_submission(
        self,
        submission: _LoadedSubmission,
        question_id: str,
    ) -> _QuestionContext:
        for context in submission.question_contexts:
            if context.question_id == question_id:
                return context
        raise ValueError(f"question not found in submission QA context: {question_id}")

    def _validate_question_sets(self) -> None:
        expected = [ctx.question_id for ctx in self._submissions[0].question_contexts]
        for submission in self._submissions[1:]:
            actual = [ctx.question_id for ctx in submission.question_contexts]
            if actual == expected:
                continue

            version = submission.qa_version or "unversioned"
            raise ValueError(
                f"{submission.grading.path} version {version} has question IDs {actual}; "
                f"expected {expected}"
            )

    def _submission_for_student_item(
        self,
        student_item_id: str | None,
    ) -> _LoadedSubmission:
        if student_item_id:
            digits = "".join(ch for ch in student_item_id if ch.isdigit())
            if digits:
                index = int(digits) - 1
                if 0 <= index < len(self._submissions):
                    return self._submissions[index]
        return self._submissions[0]

    def _image_path_for_response(
        self,
        response: StudentSubmissionQuestion | None,
    ) -> str:
        if response is None or not response.response_image_path:
            return self._default_image_path
        response_path = Path(response.response_image_path)
        if response_path.exists():
            return str(response_path)
        return self._default_image_path

    def _rubric_options(self, context: _QuestionContext) -> list[RubricOptionSeed]:
        return [
            RubricOptionSeed(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
                default_comment=option.default_comment,
            )
            for option in rubric_options(context)
        ]

    @staticmethod
    def _rubric_option_diff(
        original_options: list[RubricOptionSeed],
        options: list[RubricOptionSeed],
    ) -> tuple[list[RubricOptionUpdate], list[_RubricOptionDelete]]:
        original_keys = [
            (option.label, option.points_delta) for option in original_options
        ]
        new_keys = [(option.label, option.points_delta) for option in options]
        matcher = SequenceMatcher(
            a=original_keys,
            b=new_keys,
            autojunk=False,
        )
        changes: list[RubricOptionUpdate] = []
        deleted_options: list[_RubricOptionDelete] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            if tag == "delete":
                deleted_options.extend(
                    _RubricOptionDelete(
                        label=option.label,
                        points_delta=option.points_delta,
                    )
                    for option in original_options[i1:i2]
                )
                continue
            if tag == "insert":
                continue

            original_segment = original_options[i1:i2]
            new_segment = options[j1:j2]
            if len(original_segment) != len(new_segment):
                deleted_options.extend(
                    _RubricOptionDelete(
                        label=option.label,
                        points_delta=option.points_delta,
                    )
                    for option in original_segment
                )
                continue

            for original, new in zip(original_segment, new_segment, strict=True):
                if (
                    original.label == new.label
                    and abs(original.points_delta - new.points_delta) <= 1e-6
                ):
                    continue
                changes.append(
                    RubricOptionUpdate(
                        old_label=original.label,
                        old_points_delta=original.points_delta,
                        new_label=new.label,
                        new_points_delta=new.points_delta,
                    )
                )
        return changes, deleted_options

    def _matching_saved_grade_count(
        self,
        *,
        qa_version: str | None,
        question_id: str,
        context: _QuestionContext,
        options: list[_RubricOptionDelete],
    ) -> int:
        count = 0
        deleted_labels = {option.label for option in options}
        for submission in self._submissions:
            if submission.qa_version != qa_version:
                continue
            response = submission.grading.questions_by_id.get(question_id)
            if response is None:
                continue
            if deleted_labels.intersection(response.rubric_labels):
                count += 1
        return count

    def _propagate_rubric_option_changes(
        self,
        *,
        qa_version: str | None,
        question_id: str,
        context: _QuestionContext,
        changes: list[RubricOptionUpdate],
    ) -> int:
        if not changes:
            return 0

        label_changes = {change.old_label: change for change in changes}
        old_option_map = {
            option.label: option
            for option in self._rubric_leaf_options(context.rubric)
        }
        new_option_map = dict(old_option_map)
        for change in changes:
            old_option = new_option_map.pop(change.old_label, None)
            new_option_map[change.new_label] = RubricOptionSeed(
                label=change.new_label,
                points_delta=change.new_points_delta,
                kind=old_option.kind if old_option else "partial",
                selection_mode=(
                    old_option.selection_mode if old_option else "checklist"
                ),
            )

        propagated_count = 0
        for submission in self._submissions:
            if submission.qa_version != qa_version:
                continue
            response = submission.grading.questions_by_id.get(question_id)
            if response is None:
                continue
            if not set(response.rubric_labels).intersection(label_changes):
                continue
            old_points = self._awarded_points_for_labels(
                context.points,
                response.rubric_labels,
                old_option_map,
            )
            if response.points_awarded is None or abs(response.points_awarded - old_points) > 1e-6:
                continue

            new_labels = tuple(
                label_changes[label].new_label if label in label_changes else label
                for label in response.rubric_labels
            )
            new_points = self._awarded_points_for_labels(
                context.points,
                new_labels,
                new_option_map,
            )

            update_student_submission_file(
                submission.grading.path,
                StudentSubmissionGradeUpdate(
                    question_id=question_id,
                    possible_points=context.points,
                    points_awarded=new_points,
                    rubric_labels=new_labels,
                ),
                expected_mtime_ns=submission.grading.mtime_ns,
            )
            propagated_count += 1
        return propagated_count

    @staticmethod
    def _awarded_points_for_labels(
        points: float,
        labels: tuple[str, ...],
        option_map: dict[str, RubricOptionSeed],
    ) -> float:
        selected = [option_map[label] for label in labels if label in option_map]
        return awarded_points_for_rubric_selection(
            points,
            [o.points_delta for o in selected if o.kind != "bonus"],
            [o.points_delta for o in option_map.values() if o.kind != "bonus"],
            bonus_points_values=[
                o.points_delta for o in selected if o.kind == "bonus"
            ],
        )

    @classmethod
    def _rubric_leaf_options(
        cls,
        items: list[QARubricItem],
    ) -> list[RubricOptionSeed]:
        return [
            RubricOptionSeed(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
            )
            for option in flatten_rubric_options(items)
        ]
