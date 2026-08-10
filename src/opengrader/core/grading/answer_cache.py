"""Grade each *distinct answer* once, not each student.

Batch grading used to walk the cohort student by student and ask the model
about every ungraded free response it met. On a 200-student question with 27
different answers that is 200 model calls to make 27 decisions — and the TUI
had already concluded the same thing for humans in 2026-08 (``b``, group-first
grading). This is that conclusion applied to the automated path.

Two things come out of it, and the second matters more than the speed:

* **Cost.** One call per distinct answer. Measured against the real
  ``samples/cs146-quiz1`` shape, the ratio is the same one the ``answer-groups``
  report already prints for a grader.
* **Consistency by construction.** Two students who wrote the same words now
  get the same AI grade because it is *literally the same decision*, copied.
  Before, they were two independent generations: ``temperature`` is pinned to 0
  in ``core/llm/ollama_client.py``, which makes divergence unlikely rather than
  impossible — and when it happened, the cohort's own ``consistency-audit``
  would flag the auto-grader for a ``POINTS_DIVERGENCE``. A tool that trips its
  own fairness check is a bug, and one nobody would find until a student asked
  why their neighbour's identical answer scored differently.

**This is scheduling, not selection.** Which answers a model is allowed to
grade stays exactly where it was, in ``solutions.needs_llm_grade``; this module
only avoids *re-asking* about an answer already decided. That distinction is
what keeps ``llm-eval`` honest: the harness measures agreement per student
answer, because "how often will an unattended AI grade be wrong for a student?"
is weighted by students, not by distinct answers. Dedup does not change which
answers are graded, nor what the model would say about any of them, so the
harness's population is untouched.

Grouping reuses ``submissions/answer_groups.normalize_answer_key`` rather than
comparing raw text, so "Yes." and "yes" are one decision — and so "the same
answer" means exactly what it means to a grader pressing ``g``, to the
consistency audit, and to group-first navigation. It is deliberately the
*exact* tier: the fuzzy tier (``suggested_groups.py``) proposes merges for a
human to confirm and must never be applied unattended, least of all by the
component doing the unattended grading.

Import note: this module reaches *into* ``core.submissions`` (for the
normalizer), which is the direction ``core/grading/__init__.py`` warns about —
so, like ``solutions`` and ``evaluation``, it must never be re-exported from
that package. Importing it by its full path is safe; re-exporting it would run
this import while ``core.grading`` is still initializing and close the loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from opengrader.core.grading.llm_grader import GradeSuggestion
from opengrader.core.question_types import QuestionType
from opengrader.core.submissions.answer_groups import normalize_answer_key


@dataclass(frozen=True)
class AnswerGradeKey:
    """What makes two answers "the same decision" for grading purposes.

    All three fields are load-bearing. ``question_id`` is obvious. The
    **version** is not: on a multi-version exam the answer key for ``q3``
    differs per version, so the same text is a different decision on form A and
    form B — the same partition ``consistency.py`` makes, and for the same
    reason. ``answer_key`` is the normalized response, never the raw text.
    """

    question_id: str
    assignment_version: str
    answer_key: str


def answer_grade_key(
    *,
    question_id: str,
    assignment_version: str,
    response_text: str,
    question_type: QuestionType,
) -> AnswerGradeKey:
    """Build the grouping key for one student's answer."""
    return AnswerGradeKey(
        question_id=question_id,
        assignment_version=assignment_version,
        answer_key=normalize_answer_key(response_text, question_type),
    )


@dataclass
class AnswerGradeCache:
    """One model decision per distinct answer, for the life of a batch run.

    Not pure — it remembers — but it does no I/O: the model call is the injected
    ``factory``, exactly as ``llm_grader`` injects its transport, so this
    unit-tests with a counter in place of a model.
    """

    _decisions: dict[AnswerGradeKey, GradeSuggestion | None] = field(
        default_factory=dict
    )
    #: Distinct answers actually sent to the model.
    model_calls: int = 0
    #: Answers settled by copying a decision already made this run.
    reused: int = 0

    @property
    def answers_decided(self) -> int:
        """Every answer this cache answered for, called or copied."""
        return self.model_calls + self.reused

    def suggest(
        self,
        key: AnswerGradeKey,
        factory: Callable[[], GradeSuggestion | None],
    ) -> GradeSuggestion | None:
        """Return the decision for ``key``, asking ``factory`` at most once.

        A ``None`` verdict is cached like any other: ``suggest_free_response_grade``
        returns it for an answer whose suggestion cannot be trusted (unparseable
        output, labels that are not in the rubric), and re-asking about the same
        text until a different answer comes back is exactly how two identical
        answers end up graded differently.

        Exceptions are **not** cached and propagate untouched. A transport
        failure says nothing about the answer, and burning the entry would leave
        one arbitrary answer permanently ungradable for the rest of a run that
        recovers a second later.
        """
        if key in self._decisions:
            self.reused += 1
            return self._decisions[key]
        suggestion = factory()
        self._decisions[key] = suggestion
        self.model_calls += 1
        return suggestion

    def summary_line(self) -> str:
        """One line saying what grouping bought, or ``""`` when it bought nothing.

        Silent unless something was actually reused: on a cohort where every
        answer is unique the message would be noise claiming a win that did not
        happen.
        """
        if not self.reused:
            return ""
        return (
            f"Graded {self.answers_decided} answers with {self.model_calls} model "
            f"{'call' if self.model_calls == 1 else 'calls'} "
            f"({self.reused} shared an answer already decided)."
        )
