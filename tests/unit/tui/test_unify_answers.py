"""The fuzzy answer-grouping tier as the grader meets it.

The assertions that matter are the ones about *authority*: a proposal must
change nothing until it is accepted, and once accepted the widened group has to
be what the indicator reports **and** what the batch grade writes — a count that
says one thing while `g` does another is the failure mode this whole design is
arranged to prevent.
"""

from dataclasses import replace

from opengrader.tui.controllers.grade_controller import GradeController
from opengrader.tui.state import PAGE_GRADE, TuiWorkflowState

from .fakes import FakeDataProvider
from .test_grade_controller import FakeApp

# Two renderings of one answer that the *exact* tier keeps apart (internal
# punctuation is not cosmetic to it) but a grader would call the same answer.
_ANSWER = (
    "A stack is a last in first out structure where the newest item added is "
    "the first one removed from it."
)
_ANSWER_VARIANT = (
    "A stack is a last-in-first-out structure where the newest item added is "
    "the first one removed from it!"
)
# Deliberately unrelated wording, not merely a different claim: two answers
# with parallel structure ("a stack is…" / "a queue is…") score ~0.90 on any
# string metric, which is exactly the limitation documented in
# `core/submissions/suggested_groups` — so a fixture meant to test "nothing is
# proposed" has to be different in its words, not only in its meaning.
_DIFFERENT = (
    "Recursion means a function calls itself on a smaller input until it "
    "reaches a base case that returns without recursing."
)


class GroupingFakeDataProvider(FakeDataProvider):
    """Three students on Q1: two near-identical answers and one different."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.responses = responses or {
            "s1": _ANSWER,
            "s2": _ANSWER_VARIANT,
            "s3": _DIFFERENT,
        }
        self.saved_grades: list[dict[str, object]] = []

    def list_students(self, course_id: int | None):
        from opengrader.tui.data import StudentSummary

        if course_id is None:
            return []
        return [
            StudentSummary(1, "Tommy Liu"),
            StudentSummary(2, "Andy Cortes"),
            StudentSummary(3, "Casey Diaz"),
        ]

    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        text = self.responses.get(student_item_id or "", "")
        return [
            replace(seed, response_text=text, question_type="free_response")
            if seed.question_id == "Q1"
            else seed
            for seed in seeds
        ]

    def save_question_grade(self, **kwargs: object) -> None:
        self.saved_grades.append(kwargs)


def build_controller(
    provider: GroupingFakeDataProvider | None = None,
) -> tuple[FakeApp, TuiWorkflowState, GradeController, GroupingFakeDataProvider]:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    data_provider = provider or GroupingFakeDataProvider()
    controller = GradeController(app, state, data_provider)
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    # The shared FakeApp records the page on itself rather than on the workflow
    # state, so the grade-screen guard has to be set the way the other
    # controller tests set it.
    state.current_page = PAGE_GRADE
    return app, state, controller, data_provider


def test_a_near_identical_answer_is_proposed_not_merged() -> None:
    _, _, controller, _ = build_controller()

    suggestions = controller.suggested_merges_for_question("Q1")

    assert len(suggestions) == 1
    assert set(suggestions[0].member_ids) == {"s1", "s2"}
    # Nothing is accepted until a grader says so, so the group is still exact.
    assert controller.accepted_merge_indices("Q1") == frozenset()
    group = controller._answer_group_for_student("Q1", "s1")
    assert group is not None
    assert group.member_ids == ("s1",)


def test_the_indicator_advertises_a_pending_merge() -> None:
    _, _, controller, _ = build_controller()

    assert "1 similar-answer merge(s) available" in controller._answer_group_label()


def test_accepting_widens_the_group_and_says_how() -> None:
    _, _, controller, _ = build_controller()

    status = controller.accept_suggested_merge("Q1", 0)

    assert "2 students" in status
    assert controller.accepted_merge_indices("Q1") == frozenset({0})
    group = controller._answer_group_for_student("Q1", "s1")
    assert group is not None
    assert set(group.member_ids) == {"s1", "s2"}
    # The grader is told which students got there by a similarity judgement.
    assert controller._answer_group_label() == (
        "Answer group: 2 students (1 identical + 1 merged as similar)"
    )


def test_accepting_grades_nobody_on_its_own() -> None:
    """The confirmation authorizes a batch action; it is not the batch action."""
    _, _, controller, provider = build_controller()

    controller.accept_suggested_merge("Q1", 0)

    assert provider.saved_grades == []


def test_the_batch_grade_follows_the_widened_group() -> None:
    _, state, controller, provider = build_controller()
    controller.accept_suggested_merge("Q1", 0)
    # Grade the student in front of us, then batch it out.
    controller.on_rubric_selected(0)
    provider.saved_grades.clear()

    controller.apply_grade_to_answer_group()

    graded = {call["student_item_id"] for call in provider.saved_grades}
    assert graded == {"s2"}  # s1 was already persisted by the selection itself
    assert all(call["question_id"] == "Q1" for call in provider.saved_grades)
    assert state.selection.student_item_id == "s1"


def test_an_unaccepted_merge_leaves_the_batch_grade_exact() -> None:
    _, _, controller, provider = build_controller()
    controller.on_rubric_selected(0)
    provider.saved_grades.clear()

    controller.apply_grade_to_answer_group()

    # s2's answer only *resembles* s1's, and nobody confirmed that.
    assert provider.saved_grades == []


def test_a_student_outside_the_merge_is_untouched() -> None:
    _, _, controller, provider = build_controller()
    controller.accept_suggested_merge("Q1", 0)
    controller.on_rubric_selected(0)
    provider.saved_grades.clear()

    controller.apply_grade_to_answer_group()

    assert "s3" not in {call["student_item_id"] for call in provider.saved_grades}


def test_an_out_of_range_acceptance_is_refused_quietly() -> None:
    _, _, controller, _ = build_controller()

    status = controller.accept_suggested_merge("Q1", 99)

    assert "no longer available" in status
    assert controller.accepted_merge_indices("Q1") == frozenset()


def test_a_question_with_no_similar_answers_proposes_nothing() -> None:
    provider = GroupingFakeDataProvider(
        {"s1": _ANSWER, "s2": _DIFFERENT, "s3": _DIFFERENT}
    )
    _, _, controller, _ = build_controller(provider)

    assert controller.suggested_merges_for_question("Q1") == []
    # s2 and s3 are exactly identical, so the exact tier already covers them.
    group = controller._answer_group_for_student("Q1", "s2")
    assert group is not None
    assert set(group.member_ids) == {"s2", "s3"}


def test_reopening_the_assignment_clears_accepted_merges() -> None:
    """A confirmation is scoped to the session that made it."""
    _, _, controller, _ = build_controller()
    controller.accept_suggested_merge("Q1", 0)

    controller.open_question_picker_for_assignment()

    assert controller.accepted_merge_indices("Q1") == frozenset()
