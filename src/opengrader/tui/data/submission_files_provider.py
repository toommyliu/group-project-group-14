from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from opengrader.core.pdf.templates.qa_file import (
    QAQuestion,
    QARubricItem,
    QASubquestion,
    load_qa_markdown,
)
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    StudentSubmissionQuestion,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.tui.data.models import QuestionSeed, RubricOptionSeed, StudentSummary


_VERSION_MARKER_RE = re.compile(r"\[([A-Za-z0-9]+)\]|\(([A-Za-z0-9]+)\)")


@dataclass(frozen=True)
class _QuestionContext:
    question_id: str
    prompt: str
    points: float
    expected_answers: list[str]
    rubric: list[QARubricItem]


@dataclass(frozen=True)
class _LoadedSubmission:
    path: Path
    document: StudentSubmissionDocument
    questions_by_id: dict[str, StudentSubmissionQuestion]
    qa_version: str | None
    question_contexts: list[_QuestionContext]


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
        self._submissions = self._load_submissions(self._submissions_path)
        self._validate_question_sets()

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
            students.append(StudentSummary(id=index + 1, full_name=label))
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
        seeds: list[QuestionSeed] = []

        for context in submission.question_contexts:
            response = submission.questions_by_id.get(context.question_id)
            image_path = self._image_path_for_response(response)
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
                    rubric_label=response.rubric_label if response is not None else "",
                    analysis=response.analysis if response is not None else "",
                )
            )

        return seeds

    def save_question_grade(
        self,
        *,
        student_item_id: str,
        question_id: str,
        possible_points: float,
        points_awarded: float,
        rubric_label: str | None,
        analysis: str | None = None,
    ) -> None:
        submission = self._submission_for_student_item(student_item_id)
        update_student_submission_file(
            submission.path,
            StudentSubmissionGradeUpdate(
                question_id=question_id,
                possible_points=possible_points,
                points_awarded=points_awarded,
                rubric_label=rubric_label,
                analysis=analysis,
            ),
        )
        self._reload_submission(student_item_id)

    def save_question_analysis(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
    ) -> None:
        submission = self._submission_for_student_item(student_item_id)
        update_student_submission_file(
            submission.path,
            StudentSubmissionGradeUpdate(
                question_id=question_id,
                analysis=analysis,
            ),
        )
        self._reload_submission(student_item_id)

    @staticmethod
    def _submission_paths(path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        if path.is_dir():
            return sorted(path.glob("*.submission.md"))
        raise FileNotFoundError(f"submissions path not found: {path}")

    def _load_submissions(self, path: Path) -> list[_LoadedSubmission]:
        submission_paths = self._submission_paths(path)
        if not submission_paths:
            raise ValueError(f"no *.submission.md files found in {path}")

        return [
            self._load_submission(submission_path)
            for submission_path in submission_paths
        ]

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
        )

    def _reload_submission(self, student_item_id: str) -> None:
        existing = self._submission_for_student_item(student_item_id)
        index = self._submissions.index(existing)
        self._submissions[index] = self._load_submission(existing.path)

    def _questions_for_submission(
        self,
        document: StudentSubmissionDocument,
        submission_path: Path,
    ) -> tuple[str | None, list[QAQuestion]]:
        if not self._qa_doc.version_questions:
            return None, self._qa_doc.questions

        version = (document.assignment_version or "").strip()
        if not version:
            version = self._infer_version_from_submission(document)
        if not version:
            available = ", ".join(self._qa_doc.version_questions.keys())
            raise ValueError(
                f"{submission_path} is missing assignment_version; "
                f"available QA versions: {available}"
            )

        version_by_normalized = {
            candidate.strip().lower(): candidate
            for candidate in self._qa_doc.version_questions
        }
        selected = version_by_normalized.get(version.lower())
        if selected is None:
            available = ", ".join(self._qa_doc.version_questions.keys())
            raise ValueError(
                f"{submission_path} has assignment_version {version!r}; "
                f"available QA versions: {available}"
            )

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
            ),
            rubric=question.rubric,
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
            ),
            rubric=subquestion.rubric,
        )

    @staticmethod
    def _expected_answers(
        answers: list[str],
        correct_choice_label: str | None,
    ) -> list[str]:
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
        options = self._rubric_leaf_options(context.rubric)
        if options:
            return options
        return [
            RubricOptionSeed(label="Correct", points_delta=0.0),
            RubricOptionSeed(label="Incorrect", points_delta=-context.points),
        ]

    @classmethod
    def _rubric_leaf_options(
        cls,
        items: list[QARubricItem],
        *,
        parent_labels: tuple[str, ...] = (),
    ) -> list[RubricOptionSeed]:
        options: list[RubricOptionSeed] = []
        for item in items:
            labels = (*parent_labels, item.label)
            if item.subitems:
                options.extend(
                    cls._rubric_leaf_options(
                        item.subitems,
                        parent_labels=labels,
                    )
                )
                continue

            if item.points_delta is None:
                continue
            options.append(
                RubricOptionSeed(
                    label=" - ".join(labels),
                    points_delta=item.points_delta,
                )
            )
        return options
