from __future__ import annotations

from difflib import SequenceMatcher
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from opengrader.core.pdf.annotations import (
    sync_feedback_summary_page,
    sync_question_comment_annotation,
    sync_total_score_annotation,
)
from opengrader.core.pdf.feedback import build_feedback_summary
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
    AspectValue,
    GradeJournal,
    GradeJournalEntry,
    current_actor,
    journal_fingerprint,
    journal_payload,
    read_journal,
    resolve_history_dir,
)
from opengrader.core.pdf.pdf_preview import get_pdf_page_count
from opengrader.core.qa_file import (
    QARubricFlatOption,
    QAQuestion,
    QARubricItem,
    QASubquestion,
    flatten_rubric_options,
    load_qa_markdown,
    outcomes_by_question,
    possible_points_by_question,
    qa_question_type,
    replace_qa_rubric_options,
    save_qa_markdown,
)
from opengrader.core.qa_file.scoring import awarded_points_for_rubric_selection
from opengrader.core.question_types import (
    FREE_RESPONSE,
    MULTIPLE_ANSWER,
    MULTIPLE_CHOICE,
    QuestionType,
    humanize_question_label,
)
from opengrader.core.submissions import (
    SOURCE_HUMAN,
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    StudentSubmissionQuestion,
    load_student_submission_markdown,
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


_VERSION_MARKER_RE = re.compile(r"\[([A-Za-z0-9]+)\]|\(([A-Za-z0-9]+)\)")

_T = TypeVar("_T")


@dataclass(frozen=True)
class _QuestionContext:
    question_id: str
    prompt: str
    points: float
    expected_answers: list[str]
    rubric: list[QARubricItem]
    question_type: QuestionType = FREE_RESPONSE


@dataclass(frozen=True)
class _LoadedSubmission:
    path: Path
    document: StudentSubmissionDocument
    questions_by_id: dict[str, StudentSubmissionQuestion]
    qa_version: str | None
    question_contexts: list[_QuestionContext]
    # Modification time (ns) captured when this submission was read, used to
    # detect out-of-band edits before the provider writes back to the file.
    mtime_ns: int | None = None


@dataclass(frozen=True)
class _RubricOptionDelete:
    label: str
    points_delta: float


@dataclass(frozen=True)
class IncrementalLoadResult:
    """What :meth:`SubmissionFilesProvider.load_new_submissions` actually did.

    ``added`` and ``skipped`` both hold display labels (student name, falling
    back to the manifest key), not raw keys — so a caller can tell the grader
    something true ("12 submissions ready · 1 skipped") without having to
    reach back into the provider to look names up.
    """

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
        self._submissions = self._load_submissions(self._submissions_path)
        self._validate_question_sets()
        # Durable accountability journal for this grading session. Co-located
        # with the submissions so it travels with the assignment data; written
        # best-effort so a read-only history dir never blocks a grade.
        self._history_dir = resolve_history_dir(self._submissions_path)
        self._journal = GradeJournal(self._history_dir)
        # Belt-and-braces half of the report cache's invalidation key — see
        # `_bump_generation`'s docstring for why the mtime-based half isn't
        # sufficient on its own.
        self._generation = 0
        self._report_cache: dict[object, object] = {}
        self._report_cache_fingerprint: tuple[object, ...] | None = None

    @property
    def submissions_path(self) -> Path:
        """The file or directory the loaded submissions came from."""
        return self._submissions_path

    @property
    def qa_file_path(self) -> Path:
        """The answer key this session was opened with.

        The sibling of ``submissions_path``: tools that shell out to a CLI need
        the *pair*, since an answer key alone doesn't say which cohort it grades
        (the AI-grading calibration modal is the first such caller).
        """
        return self._qa_path

    @property
    def course_label(self) -> str:
        course_code = self._submissions[0].document.course_code
        return course_code or "Course"

    @property
    def assignment_label(self) -> str:
        title = self._qa_doc.title or self._submissions[0].document.assignment_title
        return title or "Assignment"

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []

        base_labels = [
            submission.document.student_name.strip() or submission.path.stem
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
            student_id = submission.document.student_id.strip()
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

        # Whole-page context: which pages of the scan a grader can step through
        # from each question, and which of them nothing was cropped from.
        source_pdf_path = self._source_pdf_path(submission)
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
            response = submission.questions_by_id.get(context.question_id)
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
                    # The QA file is authoritative when it declares choices;
                    # otherwise fall back to the type the extractor classified
                    # from the scanned page.
                    question_type=(
                        context.question_type
                        if context.question_type in (MULTIPLE_CHOICE, MULTIPLE_ANSWER)
                        or response is None
                        else response.question_type
                    ),
                    # Name only (not the resolved path): the frontmatter may
                    # hold a relative path and an empty value must stay empty.
                    source_pdf_name=Path(submission.document.source_pdf).name
                    if submission.document.source_pdf
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
        """Pages of a scan that carry work no answer crop covers.

        The submission's own ``extra_pages`` frontmatter wins when present (the
        extractor recorded it, from the splitter's ``--extra-work-pages`` pass or
        from page coverage). Otherwise they are inferred here, so submissions
        extracted before this feature existed still expose their scratch pages
        without being re-extracted. Either way the result is clamped to the pages
        the PDF actually has, so a stale hint can't point at a missing page.
        """
        total_pages = get_pdf_page_count(source_pdf_path)
        pages = submission.document.extra_pages
        if not pages:
            segments = tuple(
                segment
                for question in submission.document.questions
                for segment in question.source_segments
            )
            pages = infer_extra_pages(total_pages, segments)
        return tuple(page for page in pages if 1 <= page <= total_pages)

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
        submission = self._submission_for_student_item(student_item_id)
        old_question = submission.questions_by_id.get(question_id)
        update_student_submission_file(
            submission.path,
            StudentSubmissionGradeUpdate(
                question_id=question_id,
                possible_points=possible_points,
                points_awarded=points_awarded,
                rubric_labels=rubric_labels,
                analysis=analysis,
            ),
            expected_mtime_ns=submission.mtime_ns,
        )
        self._reload_submission(student_item_id)
        self._sync_pdf_annotations_best_effort(student_item_id, question_id)
        self._journal_event(
            kind="grade",
            submission=submission,
            question_id=question_id,
            summary=_format_grade_summary(points_awarded, possible_points, rubric_labels),
            old={
                "points": None if old_question is None else old_question.points_awarded,
                "rubric": [] if old_question is None else list(old_question.rubric_labels),
            },
            new={"points": points_awarded, "rubric": list(rubric_labels)},
        )

    # An analysis edit is deliberately available in two halves, because the
    # grade screen writes one on **every keystroke** so a comment can never be
    # lost. Doing the whole operation each time cost a PDF round trip and a
    # journal row per character: a 33-character comment measured 33 of each,
    # which flooded `grade-history`, inflated `grading-stats`' event counts,
    # and blocked the UI. Staging is cheap and keeps the durability guarantee;
    # the expensive, *derived* half runs once when the edit is finished.
    #
    # `save_question_analysis` remains the complete operation for callers that
    # write a finished note in one shot (undo, the AI-tag retirement).

    def stage_question_analysis(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
    ) -> None:
        """Persist an in-progress note. No PDF sync, no journal entry.

        Safe to call per keystroke: it writes the submission markdown — the
        source of truth — and nothing else. What it skips is derived (the PDF
        annotation) or a record of a completed decision (the journal), and
        neither is meaningful about a half-typed sentence.
        """
        submission = self._submission_for_student_item(student_item_id)
        update_student_submission_file(
            submission.path,
            StudentSubmissionGradeUpdate(
                question_id=question_id,
                analysis=analysis,
            ),
            expected_mtime_ns=submission.mtime_ns,
        )
        self._reload_submission(student_item_id)

    def commit_question_analysis(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
        previous_analysis: str,
    ) -> None:
        """Finish an analysis edit: one journal entry, one PDF sync.

        ``previous_analysis`` is the note as it stood before the *edit* began,
        not before the last keystroke, so the audit trail records the change a
        person actually made instead of the last character they typed.

        The journal is written first on purpose: the text is already on disk,
        so the record must not be contingent on the PDF — a missing or
        relocated source scan is a reason to warn the grader, never a reason to
        lose the accountability entry. The sync then runs with the note passed
        explicitly, which is what surfaces the "no source region to anchor to"
        message to the caller instead of silently skipping the annotation.

        The text itself was already saved (and reloaded) by
        `stage_question_analysis` before this runs, so on the ordinary path
        the report cache's fingerprint has already moved. But this method is
        also callable on its own (the grade screen finishes an edit here
        without necessarily having just staged a keystroke), and the journal
        entry it appends feeds `build_review_items`' rubric-drift flags — a
        report cached in the gap between the last stage and this commit would
        otherwise miss it. Bump by hand rather than assume the caller staged
        first.
        """
        self._bump_generation()
        self._journal_event(
            kind="comment",
            submission=self._submission_for_student_item(student_item_id),
            question_id=question_id,
            summary=_excerpt_comment(analysis),
            old={"analysis": previous_analysis},
            new={"analysis": analysis},
        )
        try:
            self._sync_pdf_annotations(
                student_item_id, question_id, comment_override=analysis
            )
        except FileNotFoundError:
            # Same rule as `_sync_pdf_annotations_best_effort`: a note that is
            # already saved must not fail because the scan moved.
            pass

    def save_question_analysis(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
    ) -> None:
        """Write a finished note in one call: stage it, then commit it."""
        submission = self._submission_for_student_item(student_item_id)
        old_question = submission.questions_by_id.get(question_id)
        previous_analysis = "" if old_question is None else old_question.analysis
        self.stage_question_analysis(
            student_item_id=student_item_id,
            question_id=question_id,
            analysis=analysis,
        )
        self.commit_question_analysis(
            student_item_id=student_item_id,
            question_id=question_id,
            analysis=analysis,
            previous_analysis=previous_analysis,
        )

    def save_question_transcription(
        self,
        *,
        student_item_id: str,
        question_id: str,
        response_text: str,
        response_confidence: float | None = 100.0,
        response_source: str | None = SOURCE_HUMAN,
    ) -> None:
        """Persist a grader's corrected transcription for one question.

        Writes the corrected ``Response text`` and records the given confidence
        (100 = human-verified by default) and reader (``human`` by default),
        which clears the low-confidence review flag. No PDF re-annotation: the
        response text is not drawn onto the returned PDF, only the score and
        comment are.

        **A ``None`` here means "this reading has no number / no recorded
        reader", not "leave it alone"** — the opposite of the convention
        ``StudentSubmissionGradeUpdate`` uses, and deliberately so: this method
        writes one *complete* reading, and its other caller is undo. Restoring
        a vision model's transcription (no confidence, no reader recorded
        before this field existed) while leaving the correction's own
        ``Confidence: 100`` and ``Read by: human`` behind would leave the file
        asserting that a person typed words no person ever typed.

        The journal entry's ``old`` payload carries the reading being replaced
        *together with its confidence and reader*. That is what makes this write
        the pipeline's ground-truth collector: every correction records exactly
        what some machine read, how sure it was and which reader it was, beside
        what a human says it should have been — the input `transcribe-eval`
        measures transcription accuracy from, at no extra cost to the grader.
        """
        submission = self._submission_for_student_item(student_item_id)
        old_question = submission.questions_by_id.get(question_id)
        update_student_submission_file(
            submission.path,
            StudentSubmissionGradeUpdate(
                question_id=question_id,
                response_text=response_text,
                response_confidence=response_confidence,
                clear_response_confidence=response_confidence is None,
                response_source=response_source,
                clear_response_source=response_source is None,
            ),
            expected_mtime_ns=submission.mtime_ns,
        )
        self._reload_submission(student_item_id)
        old_value = AspectValue(
            text="" if old_question is None else old_question.response_text,
            confidence=None if old_question is None else old_question.response_confidence,
            source=None if old_question is None else old_question.response_source,
        )
        self._journal_event(
            kind="transcription",
            submission=submission,
            question_id=question_id,
            summary=_excerpt_comment(response_text),
            # Rendered through the shared payload builder rather than by hand,
            # so this writer and `grade-restore`/`grade-merge` can never disagree
            # about the shape a replay has to read back.
            old=journal_payload("transcription", old_value),
            new=journal_payload(
                "transcription",
                AspectValue(
                    text=response_text,
                    confidence=response_confidence,
                    source=response_source,
                ),
            ),
        )

    def save_question_comment_annotation(
        self,
        *,
        student_item_id: str,
        question_id: str,
        comment: str,
    ) -> Path:
        """Forces a manual sync of PDF annotations for a specific question."""
        return self._sync_pdf_annotations(student_item_id, question_id, comment_override=comment)

    def _sync_pdf_annotations_best_effort(
        self,
        student_item_id: str,
        question_id: str,
    ) -> None:
        """Sync annotations after a save without failing the save itself.

        Grades are already persisted to the submission markdown at this
        point; a missing or relocated source PDF should not surface as a
        failed save.
        """
        try:
            self._sync_pdf_annotations(student_item_id, question_id)
        except FileNotFoundError:
            pass

    def _sync_pdf_annotations(
        self,
        student_item_id: str,
        question_id: str,
        *,
        comment_override: str | None = None,
    ) -> Path:
        submission = self._submission_for_student_item(student_item_id)
        response = submission.questions_by_id.get(question_id)
        if response is None:
            raise ValueError(f"question not found in submission: {question_id}")
        if comment_override is not None and not response.source_segments:
            raise ValueError(
                "Question source metadata missing; regenerate submission files to enable PDF annotations."
            )

        source_pdf_path = self._source_pdf_path(submission)
        annotated_pdf_path = submission.path.parent / "annotated-pdfs" / f"{source_pdf_path.stem}.annotated.pdf"

        # 1. Sync the Total Score on page 1
        total_awarded = sum(q.points_awarded for q in submission.document.questions if q.points_awarded is not None)
        total_possible = sum(q.possible_points for q in submission.document.questions if q.possible_points is not None)
        # Each writer reports whether it actually appended a revision; nothing
        # is written when the annotation already says what it should. `wrote`
        # then forces the summary rewrite below, because that full save is the
        # only thing that compacts the revisions `saveIncr` leaves behind.
        wrote = sync_total_score_annotation(
            source_pdf_path=source_pdf_path,
            annotated_pdf_path=annotated_pdf_path,
            total_points=total_awarded,
            possible_points=total_possible,
        )

        # 2. Sync the specific Question Score and Comment if layout metadata exists
        if response.source_segments:
            wrote |= sync_question_comment_annotation(
                source_pdf_path=source_pdf_path,
                annotated_pdf_path=annotated_pdf_path,
                question_id=question_id,
                comment=comment_override if comment_override is not None else (response.analysis or ""),
                points_awarded=response.points_awarded,
                possible_points=response.possible_points,
                source_segments=response.source_segments,
            )

        # 3. Refresh the returnable Grade Summary page at the back of the PDF.
        # Best-effort: a summary failure must never fail the grade save (the
        # per-question notes and total above are the authoritative feedback).
        try:
            self._sync_feedback_summary_page(
                submission, source_pdf_path, annotated_pdf_path, force=wrote
            )
        except Exception:
            pass
        return annotated_pdf_path

    def _sync_feedback_summary_page(
        self,
        submission: _LoadedSubmission,
        source_pdf_path: Path,
        annotated_pdf_path: Path,
        *,
        force: bool = False,
    ) -> None:
        """(Re)generate the appended Grade Summary page for a submission."""
        sync_feedback_summary_page(
            source_pdf_path=source_pdf_path,
            annotated_pdf_path=annotated_pdf_path,
            summary=build_feedback_summary(
                submission.document, curve=read_curve(self._submissions_path)
            ),
            force=force,
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

        # The QA file is authored/graded truth too: refuse to write over an
        # edit made by another grader since we loaded it.
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
        # `_qa_mtime_ns` (already set above) and every reloaded submission's
        # `mtime_ns` both move the report cache's fingerprint on their own;
        # the explicit bump is the same belt-and-braces insurance as
        # `reload_all`'s.
        self._bump_generation()
        # Rubric edits are question-scoped (they mutate the QA file and can
        # propagate across students), so they are journalled without student
        # attribution, mirroring the grading-log convention.
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
        """Append one accountability entry; never let it fail a save."""
        student_id = "" if submission is None else submission.document.student_id
        student_name = "" if submission is None else submission.document.student_name
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

    # ----------------------------------------------------------------- #
    # Report cache
    #
    # Every cohort report below (item analysis, consistency, similarity,
    # answer-pattern, outcome mastery, the review queue, the regrade queue)
    # is a pure function of state this provider already holds in memory:
    # the parsed submissions, the QA doc, and — for the two queues — the
    # regrade store. None of that is cheap to recompute (the similarity
    # audit alone is an O(n^2) `difflib` pass over the cohort), and a report
    # modal that gets reopened after the grader did nothing else pays that
    # cost again for byte-identical output. Memoizing keyed on a fingerprint
    # of that state fixes it without touching how any report is built.
    # ----------------------------------------------------------------- #

    def _cohort_fingerprint(self) -> tuple[object, ...]:
        """A cheap, total snapshot of everything a cached report can read.

        Built mostly from the mtimes already captured on every loaded
        submission for the optimistic-concurrency guard
        (`core/concurrency.py`) — reusing them means there is no second "is
        this stale" bookkeeping to keep in sync with disk. Two fingerprints
        that compare equal guarantee the cohort looked the same both times
        (same files, same versions of them, same count, same QA file, same
        regrade store), so a cached report served under an unchanged
        fingerprint can never be stale.

        The regrade store's own mtime (`.opengrader/regrades.jsonl`) is
        included alongside the submissions' rather than folded into the
        generation counter, on purpose: `build_review_items` and
        `build_regrade_items` need to notice a regrade filed or resolved by
        the standalone `regrade` CLI in another process too, not just one
        filed through this provider's own `file_regrade`/`resolve_regrade`/
        `deny_regrade` — the same reason `reload_all` exists for submissions
        edited outside this session. A missing file (no regrade ever filed)
        reads as `None`, same convention `file_mtime_ns` uses everywhere else
        in this class.

        The **audit journal** (`.opengrader/history/grade-journal-*.jsonl`) is
        in the key for the same reason and it is not decoration: the review
        queue's rubric-drift flags are read straight out of it
        (`detect_rubric_drift(read_journal(...))`), so a journal that moved
        while nothing else did genuinely changes what a grader should be shown.
        Every write *this* session makes already lands beside a submission or
        regrade-store write, but a **different process** — a second grader on a
        shared folder, `grade-restore`, `grade-merge apply` — touches the
        journal and nothing else, and that case had no signal at all before.
        `journal_fingerprint` is deliberately used rather than one `stat`: the
        journal is a directory of one append-only file per actor+session, so
        another process usually writes a *new* file rather than growing an
        existing one, and only the file list can see that. It never parses a
        line — this runs on every cached report lookup.

        ``self._generation`` is the part *nothing* on disk can cover: see
        `_bump_generation`.
        """
        return (
            self._generation,
            tuple(
                (submission.path, submission.mtime_ns)
                for submission in self._submissions
            ),
            self._qa_mtime_ns,
            file_mtime_ns(resolve_regrades_path(self._submissions_path)),
            journal_fingerprint(self._history_dir),
            len(self._submissions),
        )

    def _bump_generation(self) -> None:
        """Move the report cache's key even when nothing on disk did.

        Every report-affecting mutation ends with a reload that advances some
        submission's `mtime_ns` (or, for a QA edit, `self._qa_mtime_ns`, or,
        for a regrade, the regrade store's own mtime) — that's what
        `_cohort_fingerprint` is built on, and on the happy path this call is
        redundant with one of those. It is still made from every state-
        mutating method here, so the cache fails safe: a future mutating
        method that forgets to reload (and so forgets to move anything the
        fingerprint reads) still invalidates, it just costs one needless
        recompute instead of serving a wrong grade decision.
        """
        self._generation += 1

    def _cached(self, key: object, builder: Callable[[], _T]) -> _T:
        """Return a memoized report, rebuilding only when the cohort moved.

        Deliberately not `functools.lru_cache`: that decorator would key on
        `self` (keeping this provider — and every parsed submission it
        holds — alive for as long as the *class's* cache entry lives, not
        this instance's) and has no visibility into `_cohort_fingerprint`, so
        it cannot tell a submission was reloaded after a save. A plain dict
        scoped to the fingerprint gets the same "already computed this"
        saving without either problem.

        The whole dict is thrown away when the fingerprint changes rather
        than evicting individual keys, so its size is always bounded by "the
        reports opened since the cohort last moved," never by how long the
        session has been running.
        """
        fingerprint = self._cohort_fingerprint()
        if fingerprint != self._report_cache_fingerprint:
            self._report_cache = {}
            self._report_cache_fingerprint = fingerprint
        if key not in self._report_cache:
            self._report_cache[key] = builder()
        return self._report_cache[key]

    def build_review_items(self) -> list[ReviewItem]:
        """Needs-review flags across all loaded submissions, most severe first.

        Each flag is bound to the ``student_item_id`` (``"s{index+1}"``, the
        same id `list_students` assigns) so the review modal can open it, and
        carries both the name and display id for anonymity-aware rendering.

        Memoized (see the "Report cache" section above): reopening the review
        modal after doing nothing else re-walks the regrade store and the
        merged audit journal for nothing otherwise. Both of those sources are
        in the cache key directly — the regrade store by mtime, the journal by
        `journal_fingerprint` — so this is one of the few reports whose inputs
        are watched even when they are changed by a **different process**: a
        second grader on a shared folder, `grade-restore`, or a `grade-merge
        apply` all move the journal without this session doing anything, and
        the rubric-drift flags below are read straight out of it.

        That is a stronger guarantee than the *submissions* get. `self._qa_doc`
        and `self._submissions` are still snapshots taken at load time, and a
        cohort rewritten underneath this session (a merge does exactly that)
        gives cached and uncached reports alike the grades this session last
        read. `reload_all` remains the explicit answer for "something outside
        this session changed the files" — and it busts this cache too, since
        every reloaded submission's mtime is part of the fingerprint.
        """
        return self._cached("review_items", self._build_review_items)

    def _build_review_items(self) -> list[ReviewItem]:
        items: list[ReviewItem] = []
        item_id_by_norm_id: dict[str, str] = {}
        for index, submission in enumerate(self._submissions):
            item_id = f"s{index + 1}"
            document = submission.document
            norm_id = document.student_id.strip().casefold()
            if norm_id:
                item_id_by_norm_id.setdefault(norm_id, item_id)
            display_id = document.student_id.strip() or item_id
            source_pdf_name = Path(document.source_pdf).name if document.source_pdf else ""
            for flag in flag_submission(document):
                # Duplicate-name flags carry the tied roster people so the modal
                # can offer them for one-click assignment.
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

        # Fold in open regrade requests (student grade disputes) so they surface
        # in the unified triage alongside the extraction flags. Each is bound to
        # the disputing student's nav id so the review modal routes straight to
        # the disputed question (resolution lives in the dedicated regrade modal).
        documents = [submission.document for submission in self._submissions]
        # ...and the grades awarded before their question's rubric was edited.
        # Both are cohort-level and neither is visible in a `.submission.md`, so
        # both are read here and folded into the same list rather than given a
        # modal of their own: a grader working the queue should meet every
        # reason an answer needs a second look in one place.
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
        """Persist a manually-resolved student id to a submission file.

        Used by the review queue to fix duplicate-name / missing-id flags: the
        id (and, when picked from a roster candidate, the canonical name) is
        written to the submission frontmatter, the resolved `id_candidates`
        block is dropped, and the change is recorded in the audit journal.
        Goes through the same mtime guard as grade saves.
        """
        submission = self._submission_for_student_item(student_item_id)
        old_id = submission.document.student_id
        old_name = submission.document.student_name
        set_student_identity_file(
            submission.path,
            student_id=student_id,
            student_name=student_name,
            expected_mtime_ns=submission.mtime_ns,
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
        """Open regrade requests (grade disputes) bound to the TUI's nav ids.

        Each still-open request from ``.opengrader/regrades.jsonl`` is mapped to
        the disputing student's ``student_item_id`` (via student id), so the
        regrade modal can route straight to the disputed question. An unmatched
        student id still surfaces (a dispute matters even if the submission is
        missing) but renders as non-navigable. Most-recent first.

        Memoized like `build_review_items`; see `_cohort_fingerprint` for why
        the regrade store's own mtime — not just the submissions' — has to be
        part of the cache key.
        """
        return self._cached("regrade_items", self._build_regrade_items)

    def _build_regrade_items(self) -> list[RegradeReportItem]:
        item_id_by_norm_id: dict[str, str] = {}
        name_by_norm_id: dict[str, str] = {}
        pdf_by_norm_id: dict[str, str] = {}
        for index, submission in enumerate(self._submissions):
            document = submission.document
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
        """Mark a regrade request resolved (the grade was reviewed/adjusted)."""
        resolve_regrade_request(self._submissions_path, request_id, note=note)
        # This write already moves the regrade store's own mtime, which is
        # part of `_cohort_fingerprint` — the explicit bump is belt-and-
        # braces, same as everywhere else. See `_bump_generation`.
        self._bump_generation()

    def deny_regrade(self, request_id: str, *, note: str = "") -> None:
        """Mark a regrade request denied (the grade stands)."""
        deny_regrade_request(self._submissions_path, request_id, note=note)
        self._bump_generation()  # see `resolve_regrade`

    def file_regrade(
        self,
        *,
        student_id: str,
        reason: str,
        question_id: str = "",
    ) -> None:
        """File a new regrade request (grade dispute) for this assignment.

        The in-TUI intake for the same append-only log the `regrade` CLI writes
        (`core/submissions/regrade.add_regrade_request`). ``question_id`` empty is
        a whole-assignment dispute. The record is source-tagged ``instructor``
        and attributed to the current actor by the core layer. Read-only w.r.t.
        grades — a dispute never changes a score, it just surfaces in the review
        queue and the regrade modal.
        """
        add_regrade_request(
            self._submissions_path,
            student_id=student_id,
            reason=reason,
            question_id=question_id,
        )
        self._bump_generation()  # see `resolve_regrade`

    def regrade_student_choices(self) -> list[tuple[str, str]]:
        """(student_id, display label) for every identified student, name-sorted.

        Feeds the file-a-regrade dialog's student dropdown. Students with no id
        can't be the subject of a dispute (the log keys on id), so they're
        skipped; the label carries the name and id for a teacher to recognize.
        """
        seen: dict[str, str] = {}
        for submission in self._submissions:
            document = submission.document
            student_id = document.student_id.strip()
            if not student_id or student_id in seen:
                continue
            name = document.student_name.strip()
            seen[student_id] = f"{name} · {student_id}" if name else student_id
        return sorted(seen.items(), key=lambda pair: pair[1].casefold())

    def regrade_question_choices(self) -> list[tuple[str, str]]:
        """(question_id, humanized label) across the cohort, in first-seen order.

        Feeds the file-a-regrade dialog's question dropdown, so a teacher picks a
        real graded question instead of typing an id that might not exist.
        """
        seen: dict[str, str] = {}
        for submission in self._submissions:
            for question in submission.document.questions:
                seen.setdefault(
                    question.question_id,
                    humanize_question_label(question.question_id),
                )
        return list(seen.items())

    def student_identity_for_item(
        self, student_item_id: str | None
    ) -> tuple[str, str] | None:
        """Resolve a nav ``s{n}`` id to its (student_id, student_name), or None.

        Used to pre-fill the file-a-regrade dialog with the student currently in
        focus. Returns ``None`` when the id doesn't resolve to a loaded student
        (resolved strictly here, not via the grade-screen helper that falls back
        to the first student).
        """
        if not student_item_id:
            return None
        digits = "".join(ch for ch in student_item_id if ch.isdigit())
        if not digits:
            return None
        index = int(digits) - 1
        if not (0 <= index < len(self._submissions)):
            return None
        document = self._submissions[index].document
        return document.student_id.strip(), document.student_name.strip()

    def read_grade_history(self) -> list[GradeJournalEntry]:
        """Return the durable grade-change journal, merged, oldest event first."""
        return read_journal(self._history_dir)

    def build_grading_session_report(self) -> GradingSessionReport:
        """Summarize grading throughput from the audit journal (read-only).

        Delegates to the pure `core/analytics` engine over the same merged
        journal the grade-history view reads. Names graders, so the TUI keeps
        the modal to teacher mode (like grade history).
        """
        return build_grading_session_report(self.read_grade_history())

    def build_grader_agreement_report(self) -> GraderAgreementReport:
        """Compare how this cohort's graders marked, from the audit journal.

        The *leniency and drift* counterpart to the session report's throughput,
        over the same journal — plus the submissions, which is where a
        question's point value and the student's answer live (the journal only
        ever recorded what a grade changed *to*).

        Read-only, and honest about its own limits: on a cleanly divided cohort
        two graders may share no comparable work at all, and the report says so
        rather than presenting residuals as a comparison. A designed comparison
        is what `calibrate pack` is for. Names graders, so the TUI keeps the
        modal to teacher mode like grade history.
        """
        documents = [submission.document for submission in self._submissions]
        adaptation = answers_from_journal(self.read_grade_history(), documents)
        return build_grader_agreement_report(adaptation.answers)

    def default_bundle_name(self) -> str:
        """Suggested return-bundle folder name for this cohort.

        Delegates to the same core helper the CLI uses, so a bundle built from
        the TUI and one built from the command line default to the same name.
        """
        return default_bundle_name(
            [submission.document for submission in self._submissions]
        )

    # ----------------------------------------------------------------- #
    # Multi-grader work claims (`core/claims.py`)
    # ----------------------------------------------------------------- #

    def grader_actor(self) -> str:
        """Who this app instance grades as — the audit journal's identity.

        Shared with `core/history` on purpose: a claim and the journal entries
        it produces have to name the same person, or "who graded this" and "who
        was supposed to" stop lining up.
        """
        return current_actor()

    def read_work_claims(self) -> list[WorkClaim]:
        """Active work claims for this assignment (`.opengrader/claims.jsonl`)."""
        return active_claims(self._submissions_path)

    def cohort_ids(self) -> tuple[list[str], list[str]]:
        """(question ids, student ids) for this assignment, in nav order.

        What the claims modal divides. Student order matches `list_students`,
        so "the first forty" means the same on screen as in the `claim` CLI.
        """
        question_ids: dict[str, None] = {}
        student_ids: list[str] = []
        for submission in self._submissions:
            document = submission.document
            for question in document.questions:
                question_ids.setdefault(question.question_id, None)
            student_id = document.student_id.strip()
            if student_id:
                student_ids.append(student_id)
        return list(question_ids), student_ids

    def claim_work_slice(
        self, work: WorkSlice, *, actor: str | None = None, note: str = ""
    ) -> WorkClaim:
        """Take a slice of the grading. Raises ``ClaimConflictError`` on overlap.

        Defaults to this provider's own ``grader_actor`` rather than letting the
        core resolve it independently, so the claim and the audit-journal entries
        it leads to are always attributed to the same person.
        """
        return claim_work(
            self._submissions_path,
            work,
            actor=actor or self.grader_actor(),
            note=note,
        )

    def release_work_claim(self, claim_id: str) -> None:
        """Hand a claimed slice back to the pool."""
        release_claim(self._submissions_path, claim_id)

    def complete_work_claim(self, claim_id: str) -> None:
        """Mark a claimed slice finished."""
        complete_claim(self._submissions_path, claim_id)

    def read_curve(self) -> Curve:
        """Return the assignment's score curve (identity curve if none is set)."""
        return read_curve(self._submissions_path)

    def save_curve(self, curve: Curve) -> None:
        """Persist the assignment's score curve to `.opengrader/curve.json`."""
        write_curve(self._submissions_path, curve)

    def curve_score_distribution(self) -> list[tuple[float, float | None]]:
        """(raw awarded total, possible total) per loaded submission.

        Feeds the curve preview/histogram — each student is curved against their
        own maximum, so a multi-version cohort stays correct.
        """
        distribution: list[tuple[float, float | None]] = []
        for submission in self._submissions:
            questions = submission.document.questions
            raw_total = sum(
                q.points_awarded for q in questions if q.points_awarded is not None
            )
            possibles = [
                q.possible_points for q in questions if q.possible_points is not None
            ]
            distribution.append((raw_total, sum(possibles) if possibles else None))
        return distribution

    # ----------------------------------------------------------------- #
    # Grade release (`core/release.py`)
    # ----------------------------------------------------------------- #

    def build_readiness_report(self) -> ReadinessReport:
        """Is this cohort ready to hand back, and what is in the way?

        Built from the documents this provider already holds rather than by
        re-reading the folder, so the verdict describes the cohort as the
        session sees it — including grades saved a moment ago that a fresh read
        of disk would also see, but which a stale read would not.
        """
        documents = [submission.document for submission in self._submissions]
        return build_readiness_report(
            documents,
            review_flags=build_review_flags(
                documents,
                regrade_requests=open_regrade_requests(self._submissions_path),
                # Same source as the review queue's, so the release gate and
                # the `R` modal can never disagree about which grades predate
                # their rubric.
                stale_grades=detect_rubric_drift(read_journal(self._history_dir)),
            ),
            consistency_flags=build_consistency_flags(documents),
            claims=self.read_work_claims(),
            curve=self.read_curve(),
            previous_release=self.latest_release(),
        )

    def read_releases(self) -> list[GradeRelease]:
        """Every recorded hand-back for this assignment, oldest first."""
        return read_releases(self._submissions_path)

    def latest_release(self) -> GradeRelease | None:
        """The most recent hand-back, or ``None`` if grades never left."""
        return latest_release(self._submissions_path)

    def create_release(self, *, label: str = "") -> GradeRelease:
        """Record what every student is being handed, right now.

        Writes nothing into any ``.submission.md`` — a release is a record
        beside the grades, never a change to them, which is what lets it be
        taken without disturbing a grading session in progress.
        """
        release = build_release(
            [submission.document for submission in self._submissions],
            curve=self.read_curve(),
            label=label,
            actor=self.grader_actor(),
        )
        write_release(self._submissions_path, release)
        return release

    def compare_to_release(self, release: GradeRelease) -> ReleaseComparison:
        """What has moved since ``release`` handed these grades out.

        Compared against **today's** curve, not the release's, because the
        question a student asks is "is my total still what you told me?" — and a
        re-curve changes that answer just as surely as a regrade does.
        """
        return compare_release(
            release,
            [submission.document for submission in self._submissions],
            curve=self.read_curve(),
        )

    def read_comment_library(self) -> CommentLibrary:
        """Load this folder's reusable feedback snippets (`core/comments.py`).

        Stored alongside the assignment data in `.opengrader/comments.json` so the
        library travels with the folder; a missing/corrupt file reads as empty.
        """
        return read_comments(self._submissions_path)

    def save_comment_library(self, library: CommentLibrary) -> None:
        """Persist the reusable comment library back to `.opengrader/`."""
        write_comments(self._submissions_path, library)

    def available_sections(self) -> list[str]:
        """Distinct class sections present across the loaded submissions, sorted.

        Empty when the assignment wasn't graded across sections (the common
        single-section case), so callers can cheaply decide whether to offer a
        per-section view at all.
        """
        sections = {
            (submission.document.section or "").strip()
            for submission in self._submissions
        }
        sections.discard("")
        return sorted(sections)

    def build_item_analysis_report(
        self, section: str | None = None
    ) -> ItemAnalysisReport:
        """Per-question psychometrics (difficulty, separation, distractors).

        Delegates to the pure `core/analytics` engine (read-only; changes
        nothing), supplying the QA file's authoritative point values so
        difficulty is available even when a submission left `Possible points`
        blank. Keyed by the submission's own `assignment_version` — the same
        version the engine partitions items by. No student identities are
        exposed (every figure is a cohort aggregate), so it needs no anonymity
        gating.

        ``section`` scopes the cohort to one class section (``None`` = all
        sections combined); the engine is section-agnostic, so a scoped report
        is just the analysis over the filtered subset.

        Memoized on ``section`` *and* the cohort fingerprint — a section is
        part of the cache key, not just an argument, so a scoped call can
        never be served the all-sections report a different call cached (or
        vice versa).
        """
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
                if (submission.document.section or "").strip() == section
            ]
        possible_by_item: dict[tuple[str, str | None], float] = {}
        for submission in submissions:
            version = submission.document.assignment_version
            for context in submission.question_contexts:
                possible_by_item.setdefault((context.question_id, version), context.points)
                # A version-agnostic fallback so a version mismatch still resolves.
                possible_by_item.setdefault((context.question_id, None), context.points)
        return build_item_analysis(
            [submission.document for submission in submissions],
            possible_points_by_question=possible_by_item,
        )

    def build_outcome_mastery_report(self, section: str | None = None) -> OutcomeReport | None:
        """Per-learning-outcome mastery, or ``None`` when nothing is tagged.

        ``None`` rather than an empty report on purpose: the caller needs to
        tell "this assignment does not use outcomes" (hide the view entirely)
        from "it does, but nothing is graded yet" (show it, empty) — and only
        the answer key can answer the first, since the ``**Outcome**`` tags live
        there and never reach a ``.submission.md``.

        Scoped by ``section`` exactly like :meth:`build_item_analysis_report`,
        and read-only like it. Every figure is a class aggregate, so no
        anonymity gating is needed.

        Memoized on ``section`` and the cohort fingerprint, same as
        :meth:`build_item_analysis_report` — see that method's note on why
        the section has to be part of the key. ``None`` results (no outcome
        tags) are cached too: recomputing "there is nothing to report" on
        every reopen would be silly for exactly the same reason recomputing
        a populated report would be.
        """
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
                if (submission.document.section or "").strip() == section
            ]
        return build_outcome_report(
            [submission.document for submission in submissions],
            tags,
            possible_points_by_question=possible_points_by_question(self._qa_doc),
        )

    def build_consistency_report(self) -> list[ConsistencyReportItem]:
        """Answer groups graded inconsistently across the cohort, worst first.

        Delegates the analysis to `core/submissions/consistency` (read-only;
        changes nothing), then binds every member to the ``student_item_id``
        (``"s{n}"``) the grade screen navigates by — the same index
        `list_students`/`build_review_items` use — so the audit modal can open
        any conflicting student in place. Members are keyed by their (unique)
        source PDF; one that can't be mapped keeps an empty id and renders as a
        non-navigable row rather than mis-pointing at another student.

        Memoized (see the "Report cache" section above).
        """
        return self._cached("consistency", self._build_consistency_report)

    def _build_consistency_report(self) -> list[ConsistencyReportItem]:
        item_id_by_source_pdf = self._item_id_by_source_pdf()
        flags = build_consistency_flags(
            [submission.document for submission in self._submissions]
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
        """Clusters of students with near-identical free responses, worst first.

        Delegates to `core/submissions/similarity` (read-only; changes nothing),
        then binds every member to the ``student_item_id`` (``"s{n}"``) the grade
        screen navigates by — the same index `list_students`/
        `build_consistency_report` use — so the audit modal can open any student
        in a cluster to compare or regrade. Members are keyed by their (unique)
        source PDF; one that can't be mapped keeps an empty id and renders as a
        non-navigable row rather than mis-pointing at another student.

        Memoized (see the "Report cache" section above) — this is the most
        expensive report on offer (an O(n^2) `difflib` pass over the cohort's
        free responses), so it's also the one that benefits most from not
        rerunning on every reopen.
        """
        return self._cached("similarity", self._build_similarity_report)

    def _build_similarity_report(self) -> list[SimilarityReportItem]:
        item_id_by_source_pdf = self._item_id_by_source_pdf()
        flags = build_similarity_flags(
            [submission.document for submission in self._submissions]
        )
        return [self._to_similarity_item(flag, item_id_by_source_pdf) for flag in flags]

    def _item_id_by_source_pdf(self) -> dict[str, str]:
        """Map each loaded submission's source PDF to its ``s{n}`` nav id.

        The one place the cohort audits bind a `core` member back to something
        the grade screen can open. Keyed by source PDF because that is the only
        field the audit engines carry that is unique per submission — an
        unidentified paper has neither name nor id. First writer wins, so two
        submissions somehow claiming one scan can't make the second steal the
        first's navigation target.
        """
        item_id_by_source_pdf: dict[str, str] = {}
        for index, submission in enumerate(self._submissions):
            source_pdf = submission.document.source_pdf
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
        """Pairs whose *incorrect* objective answers agree implausibly often.

        The other half of the integrity question `build_similarity_report`
        answers: a text diff cannot audit multiple-choice answers (every honest
        pair scores 100% alike on "B"), so the objective questions get the
        shared-wrong-answer statistic instead — see
        `core/submissions/answer_patterns.py`.

        Unlike the similarity audit this one needs the **answer key**, since
        "shared *wrong* answer" is undefined without it. The TUI is the one front
        end that never has to ask for it: the session was launched with the QA
        file and it is already parsed on ``self._qa_doc``, which is why this
        needs no configuration where the CLI needs a ``--solutions`` flag.

        Read-only; changes nothing. Members are bound to the ``s{n}`` nav ids the
        grade screen opens by, exactly as the consistency and similarity reports
        are.

        Memoized (see the "Report cache" section above).
        """
        return self._cached("answer_pattern", self._build_answer_pattern_report)

    def _build_answer_pattern_report(self) -> AnswerPatternReportView:
        item_id_by_source_pdf = self._item_id_by_source_pdf()
        report = build_answer_pattern_report_for_documents(
            [submission.document for submission in self._submissions],
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
        """Tell "wrong folder" apart from "extraction just hasn't landed one yet".

        An empty ``*.submission.md`` glob means the same thing in both cases,
        and they call for very different next steps from the grader — one is a
        typo to fix, the other is "come back in a minute." The extraction
        manifest (`core/submissions/watch.py`) is what tells them apart: a run
        always writes it before its first submission file exists, so a watcher
        that finds it recently touched means extraction, not a bad path.

        Deliberately not a full "waiting room" screen — a live-updating status
        page here would need its own polling and error handling for the same
        payoff a clear one-line message already gives: the grader knows to
        wait, or to retry once the first submission is ready.
        """
        if path.is_dir():
            watcher = ExtractionWatcher(path)
            if watcher.poll().appears_active:
                return (
                    f"Extraction is in progress in {path} — no submissions are "
                    "ready yet. Try again once the first one finishes."
                )
        return f"no *.submission.md files found in {path}"

    def load_new_submissions(self, keys: Iterable[str]) -> IncrementalLoadResult:
        """Append newly-finished submissions without re-parsing the cohort.

        ``reload_all`` already picks up new files on disk, but it rebuilds
        *every* loaded submission on every call — fine for a one-off merge,
        but on a folder that fills in one submission every few seconds over a
        long session that is an O(n^2) re-parse of the whole cohort (this repo
        lists performance first — see ``AGENTS.md``). This instead loads only
        the submissions named by ``keys`` and appends them, leaving every
        already-loaded submission — and its recorded mtime — untouched. That
        matters beyond speed: touching an mtime that didn't need touching is
        exactly how the optimistic concurrency guard (`core/concurrency.py`)
        turns a grader's next save into an unexplained "changed on disk"
        refusal.

        ``keys`` are extraction-manifest keys (an
        :class:`~opengrader.core.submissions.watch.ExtractionWatcher` poll's
        ``new_keys``), not paths — the manifest is the only completion signal
        this provider trusts (see ``watch.py``'s docstring), so a key with no
        submission file on disk yet is skipped rather than guessed at; the
        next poll will bring it back once it is really there.

        A newly-arrived submission whose question IDs don't match the rest of
        the cohort is reported in ``.skipped`` rather than raising the
        ``ValueError`` the initial load's ``_validate_question_sets`` would —
        an assignment a grader already has open must not be torn down by one
        bad file landing in the background; they are told, and grading
        continues on everyone else.
        """
        added: list[str] = []
        skipped: list[str] = []
        loaded_paths = {submission.path for submission in self._submissions}
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
                # A submission that fails to parse is exactly the kind of one
                # bad file this method exists to not let take the session
                # down — report it by key (no document to pull a name from)
                # and move on.
                skipped.append(key)
                continue

            label = loaded.document.student_name.strip() or key
            actual_question_ids = [ctx.question_id for ctx in loaded.question_contexts]
            if (
                expected_question_ids is not None
                and actual_question_ids != expected_question_ids
            ):
                skipped.append(label)
                continue

            self._submissions.append(loaded)
            loaded_paths.add(loaded.path)
            if expected_question_ids is None:
                expected_question_ids = actual_question_ids
            added.append(label)

        if added:
            # `len(self._submissions)` already moved, which alone changes the
            # report cache's fingerprint — this is the same belt-and-braces
            # insurance as everywhere else, not load-bearing on its own.
            self._bump_generation()
        return IncrementalLoadResult(added=tuple(added), skipped=tuple(skipped))

    def _load_submission(self, submission_path: Path) -> _LoadedSubmission:
        document = load_student_submission_markdown(submission_path)
        qa_version, qa_questions = self._questions_for_submission(
            document,
            submission_path,
        )
        return _LoadedSubmission(
            path=submission_path,
            document=document,
            questions_by_id={
                question.question_id: question for question in document.questions
            },
            qa_version=qa_version,
            question_contexts=self._flatten_question_contexts(qa_questions),
            mtime_ns=file_mtime_ns(submission_path),
        )

    def reload_all(self) -> None:
        """Re-read every submission from disk, discarding the cached copies.

        For the one case the per-save reloads cannot cover: something outside
        the session rewrote the files. A merge (`core/handoff/merge.py`) does
        exactly that, and without this the session would not only show stale
        grades — every recorded mtime would be out of date, so the optimistic
        guard (`core/concurrency.py`) would refuse the grader's *next* save with
        a "changed on disk" error they cannot explain.
        """
        self._submissions = self._load_submissions(self._submissions_path)
        # Belt-and-braces alongside the mtimes this move already changes: a
        # coarse filesystem clock can in principle report the same mtime for
        # a file rewritten within one tick, which would otherwise leave a
        # stale report cached forever. See `_bump_generation`.
        self._bump_generation()

    def _reload_submission(self, student_item_id: str) -> None:
        """Re-read one submission after a save, refreshing its recorded mtime.

        Every grade/transcription/identity save routes through here, which is
        what lets the report cache trust the mtime half of its fingerprint
        for all of them without each save site remembering to invalidate
        anything itself. Bumping the generation counter too is redundant
        with that mtime move in the common case, but cheap insurance against
        the same coarse-clock edge case `reload_all` guards against.
        """
        existing = self._submission_for_student_item(student_item_id)
        index = self._submissions.index(existing)
        self._submissions[index] = self._load_submission(existing.path)
        self._bump_generation()

    @staticmethod
    def _source_pdf_path(submission: _LoadedSubmission) -> Path:
        source = Path(submission.document.source_pdf).expanduser()
        if not source.is_absolute():
            source = submission.path.parent / source
        return source.resolve()

    def _question_context_for_submission(
        self,
        submission: _LoadedSubmission,
        question_id: str,
    ) -> _QuestionContext:
        for context in submission.question_contexts:
            if context.question_id == question_id:
                return context
        raise ValueError(f"question not found in submission QA context: {question_id}")

    def _questions_for_submission(
        self,
        document: StudentSubmissionDocument,
        submission_path: Path,
    ) -> tuple[str | None, list[QAQuestion]]:
        if not self._qa_doc.version_questions:
            return None, self._qa_doc.questions

        explicit_version = (document.assignment_version or "").strip()
        inferred_version = self._infer_version_from_submission(document)
        version = explicit_version or inferred_version

        version_by_normalized = {
            candidate.strip().lower(): candidate
            for candidate in self._qa_doc.version_questions
        }
        selected = version_by_normalized.get(version.lower()) if version else None
        if selected is None and explicit_version and inferred_version:
            # An explicit version that matched no QA version: before giving up,
            # try the one inferred from the title/prompts (the frontmatter may be
            # an OCR misread that the printed title got right).
            selected = version_by_normalized.get(inferred_version.lower())
        if selected is None:
            # A version is optional. A missing — or unmatched/misread — version
            # falls back to the first QA version so one bad version string never
            # blocks grading the whole cohort. (A single-version assignment took
            # the `not version_questions` branch above.)
            selected = next(iter(self._qa_doc.version_questions))

        return selected, self._qa_doc.version_questions[selected]

    def _infer_version_from_submission(
        self,
        document: StudentSubmissionDocument,
    ) -> str:
        available_by_normalized = {
            version.strip().lower(): version for version in self._qa_doc.version_questions
        }
        candidates: set[str] = set()
        search_texts = [document.assignment_title]
        search_texts.extend(question.prompt for question in document.questions)

        for text in search_texts:
            for match in _VERSION_MARKER_RE.finditer(text or ""):
                raw = match.group(1) or match.group(2)
                if raw is None:
                    continue
                version = available_by_normalized.get(raw.strip().lower())
                if version is not None:
                    candidates.add(version)

        if len(candidates) == 1:
            return next(iter(candidates))
        return ""

    @classmethod
    def _flatten_question_contexts(
        cls,
        questions: list[QAQuestion],
    ) -> list[_QuestionContext]:
        contexts: list[_QuestionContext] = []
        for question in questions:
            if question.subquestions:
                contexts.extend(
                    cls._context_from_subquestion(subquestion)
                    for subquestion in question.subquestions
                )
                continue
            contexts.append(cls._context_from_question(question))
        return contexts

    @staticmethod
    def _context_from_question(question: QAQuestion) -> _QuestionContext:
        return _QuestionContext(
            question_id=question.id,
            prompt=question.prompt,
            points=float(question.points),
            expected_answers=SubmissionFilesProvider._expected_answers(
                question.answers,
                question.correct_choice_label,
                question.correct_choice_labels,
            ),
            rubric=question.rubric,
            question_type=qa_question_type(question),
        )

    @staticmethod
    def _context_from_subquestion(subquestion: QASubquestion) -> _QuestionContext:
        return _QuestionContext(
            question_id=subquestion.id,
            prompt=subquestion.prompt,
            points=float(subquestion.points),
            expected_answers=SubmissionFilesProvider._expected_answers(
                subquestion.answers,
                subquestion.correct_choice_label,
                subquestion.correct_choice_labels,
            ),
            rubric=subquestion.rubric,
            question_type=qa_question_type(subquestion),
        )

    @staticmethod
    def _expected_answers(
        answers: list[str],
        correct_choice_label: str | None,
        correct_choice_labels: tuple[str, ...] = (),
    ) -> list[str]:
        if correct_choice_labels:  # select all that apply
            return list(correct_choice_labels)
        if correct_choice_label:
            return [correct_choice_label]
        return list(answers)

    def _validate_question_sets(self) -> None:
        expected = [ctx.question_id for ctx in self._submissions[0].question_contexts]
        for submission in self._submissions[1:]:
            actual = [ctx.question_id for ctx in submission.question_contexts]
            if actual == expected:
                continue

            version = submission.qa_version or "unversioned"
            raise ValueError(
                f"{submission.path} version {version} has question IDs {actual}; "
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
        options = [
            RubricOptionSeed(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
                default_comment=option.default_comment,
            )
            for option in flatten_rubric_options(context.rubric)
        ]
        if options:
            return options
        return [
            RubricOptionSeed(
                label="Correct",
                points_delta=0.0,
                kind="correct",
                selection_mode="exclusive",
            ),
            RubricOptionSeed(
                label="Incorrect",
                points_delta=-context.points,
                kind="incorrect",
                selection_mode="exclusive",
            ),
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
            response = submission.questions_by_id.get(question_id)
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
        """Propagate label/weight edits only when saved grades still match."""
        if not changes:
            return 0

        label_changes = {change.old_label: change for change in changes}
        old_option_map = {
            option.label: option
            for option in self._rubric_leaf_options(context.rubric)
        }
        new_option_map = dict(old_option_map)
        for change in changes:
            # Edits rename/reweight within a section, so the option keeps
            # its kind (bonus stays bonus) and selection mode.
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
            response = submission.questions_by_id.get(question_id)
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
                submission.path,
                StudentSubmissionGradeUpdate(
                    question_id=question_id,
                    possible_points=context.points,
                    points_awarded=new_points,
                    rubric_labels=new_labels,
                ),
                expected_mtime_ns=submission.mtime_ns,
            )
            propagated_count += 1
        return propagated_count

    @staticmethod
    def _awarded_points_for_labels(
        points: float,
        labels: tuple[str, ...],
        option_map: dict[str, RubricOptionSeed],
    ) -> float:
        """Compute saved points from persisted labels and rubric weights.

        Bonus options are passed separately so extra credit is added after
        the base score is clamped and never skews scoring-mode inference.
        """
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
        """Flatten QA rubric leaves for grade propagation helpers.

        Delegates to the canonical flattener so labels, kinds (including
        bonus), and selection modes always match the options the grading UI
        was seeded with.
        """
        return [
            RubricOptionSeed(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
            )
            for option in flatten_rubric_options(items)
        ]


def _format_points(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _format_grade_summary(
    points_awarded: float | None,
    possible_points: float,
    rubric_labels: tuple[str, ...],
) -> str:
    """A short human summary for the history journal, e.g. "8/10 · Partial"."""
    if points_awarded is None and not rubric_labels:
        return "Grade cleared"
    summary = f"{_format_points(points_awarded)}/{_format_points(possible_points)} pts"
    if rubric_labels:
        summary += f" · {', '.join(rubric_labels)}"
    return summary


def _excerpt_comment(analysis: str, *, limit: int = 60) -> str:
    collapsed = " ".join(analysis.split())
    if not collapsed:
        return "Comment cleared"
    if len(collapsed) <= limit:
        return f"“{collapsed}”"
    return f"“{collapsed[: limit - 1].rstrip()}…”"
