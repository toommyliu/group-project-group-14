from dataclasses import dataclass

from opengrader.core.analytics.score_statistics import (
    QuestionStatistics,
    statistics_from_scores,
)
from opengrader.core.grading.questions import flatten_question_contexts
from opengrader.core.qa_file import QADocument
from opengrader.core.workspace.models import Submission


@dataclass(frozen=True)
class AssignmentQuestionStatistics:
    prompt: str
    parent_id: str | None
    parent_prompt: str | None
    submission_id: int | None
    scores: QuestionStatistics


@dataclass(frozen=True)
class AssignmentVersionStatistics:
    version: str | None
    scores: QuestionStatistics
    partial_count: int
    ungraded_count: int
    questions: list[AssignmentQuestionStatistics]


def assignment_statistics(
    document: QADocument, submissions: list[Submission]
) -> list[AssignmentVersionStatistics]:
    versions = document.version_questions or {None: document.questions}
    submissions_by_version: dict[str | None, list[Submission]] = {}
    for submission in submissions:
        submissions_by_version.setdefault(submission.version, []).append(submission)

    reports = []
    for version, questions in versions.items():
        cohort = submissions_by_version.get(version, [])
        contexts = flatten_question_contexts(questions)
        parents = {
            child.id: parent
            for parent in questions
            for child in parent.subquestions
        }
        totals = []
        partial_count = 0
        for submission in cohort:
            scores = [submission.question_scores.get(q.question_id) for q in contexts]
            graded = [score for score in scores if score is not None]
            if graded and len(graded) == len(contexts):
                totals.append(sum(graded))
            elif graded:
                partial_count += 1

        question_stats = []
        for question in contexts:
            parent = parents.get(question.question_id)
            question_stats.append(
                AssignmentQuestionStatistics(
                    prompt=question.prompt,
                    parent_id=parent.id if parent else None,
                    parent_prompt=parent.prompt if parent else None,
                    submission_id=cohort[0].id if cohort else None,
                    scores=statistics_from_scores(
                        question.question_id,
                        [
                            score
                            for submission in cohort
                            if (score := submission.question_scores.get(question.question_id))
                            is not None
                        ],
                        possible_points=question.points,
                        total_submissions=len(cohort),
                    ),
                )
            )
        reports.append(
            AssignmentVersionStatistics(
                version=version,
                scores=statistics_from_scores(
                    "total",
                    totals,
                    possible_points=sum(q.points for q in contexts),
                    total_submissions=len(cohort),
                ),
                partial_count=partial_count,
                ungraded_count=len(cohort) - len(totals) - partial_count,
                questions=question_stats,
            )
        )
    return reports
