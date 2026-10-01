"""AI assistance on the grade screen: draft a rubric, draft grades.

Both features only ever *draft*. A drafted rubric fills the rubric editor and
is written nowhere until the instructor presses the editor's own Save. Drafted
grades are saved, but only for students with no grade yet on the question,
and each carries the `[AI …]` tag so the AI-review mode (v / y) lists them for
a human to confirm or override.

Model calls run in a Textual worker thread. Everything the worker needs is
gathered on the main thread first; the worker only talks to the model, and
its result is applied back on the main thread after re-checking that it
still applies (same question, same editor session, student still ungraded).

Before anything is sent to a cloud provider the instructor sees the real
scale of the run in `CloudPreflightConfirmScreen`; cancelling sends nothing.
Missing cloud consent is never granted here: the status line points to AI
setup instead.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from opengrader.core import app_config
from opengrader.core.grading.draft_grades import (
    DraftGradeResult,
    draft_grades_for_groups,
)
from opengrader.core.grading.llm_grader import (
    GRADE_SUGGESTION_SCHEMA,
    RubricChoice,
    format_ai_analysis,
)
from opengrader.core.grading.rubric_draft import (
    RUBRIC_DRAFT_SCHEMA,
    RubricDraft,
    RubricDraftRequest,
    draft_rubric,
    sample_answers,
)
from opengrader.core.llm import LLMError
from opengrader.core.submissions import AnswerGroup
from opengrader.tui.formatting import format_question_label
from opengrader.tui.state import PAGE_GRADE, GradingLogEntry

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.app_config import ResolvedAI

# The grade-screen key that runs "AI draft grades" (see input_controller).
AI_DRAFT_GRADES_KEY = "Y"
AI_REVIEW_KEY = "v"


def _plural(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"


class AIDraftMixin:
    """Mixed into `GradeController`; uses its state and private helpers."""

    _ai_busy: str | None = None
    _ai_rubric_draft_question: str | None = None

    # ------------------------------------------------------------------
    # Shared plumbing
    # ------------------------------------------------------------------

    def _ai_busy_status(self) -> str:
        return (
            f"The AI is still working on {self._ai_busy}. Wait for it to "
            "finish before starting another AI request."
        )

    def _ai_resolve(self) -> tuple["ResolvedAI | None", str]:
        """The configured AI, or a message saying why nothing can be sent."""
        try:
            resolved = app_config.resolve()
        except Exception as exc:  # noqa: BLE001 - a broken config is reported, not raised
            return None, f"AI is not set up: {exc}. Nothing was sent."
        if resolved.kind == "cloud":
            if not resolved.cloud_allowed:
                return None, (
                    f"{resolved.provider.label} is a cloud AI service and cloud "
                    "providers are turned off on this machine. Nothing was sent."
                )
            if resolved.cloud_consent_required:
                return None, self._ai_consent_message(resolved)
        return resolved, ""

    @staticmethod
    def _ai_consent_message(resolved: "ResolvedAI") -> str:
        return (
            f"{resolved.provider.label} is a cloud AI service and you have not "
            "agreed to send student work to it. Nothing was sent. Agree to it "
            "in AI setup (Tools, F3) or with 'opengrader providers consent "
            f"{resolved.provider.name}', or choose a local provider."
        )

    def _ai_egress_folder(self) -> Path | None:
        """Where this session's cloud-egress log belongs, if anywhere.

        Folder mode logs beside the submissions, like `automated-grading`;
        a workspace logs in its root. With neither, the per-user log beside
        the AI settings is used (egress's own default).
        """
        provider = self._data_provider  # type: ignore[attr-defined]
        folder = getattr(provider, "submissions_path", None)
        if callable(folder):
            folder = folder()
        if folder:
            return Path(folder)
        client = getattr(provider, "client", None)
        root = getattr(getattr(client, "connection", None), "root", None)
        if root:
            return Path(root)
        return None

    def _ai_estimated_cost(
        self, resolved: "ResolvedAI", calls: int
    ) -> tuple[float | None, str]:
        from opengrader.core import egress

        try:
            folder = self._ai_egress_folder()
            entries: list[dict] = []
            basis = "a conservative estimate — no previous calls are recorded yet"
            if folder is not None:
                from opengrader.core.submissions.manifest import resolve_manifest_path

                entries = egress.read_entries(
                    resolve_manifest_path(folder, filename=egress.EGRESS_FILENAME)
                )
            average = egress.average_tokens(entries, category="graded-text")
            if average is not None:
                basis = "measured from previous calls recorded for this assignment"
            else:
                user_entries = egress.read_entries(
                    app_config._config_dir() / egress.EGRESS_FILENAME
                )
                average = egress.average_tokens(user_entries, category="graded-text")
                if average is not None:
                    basis = "measured from previous calls to this provider on this machine"
                else:
                    average = (600.0, 120.0)
            pricing = resolved.provider.pricing
            if pricing is None or not pricing.stated:
                return None, "provider pricing not stated"
            cost = egress.estimate_cost(average[0] * calls, average[1] * calls, pricing)
            if cost is None:
                return None, "provider pricing not stated"
            return cost, basis
        except Exception:  # noqa: BLE001 - an estimate must never block the confirmation
            return None, "no estimate available"

    def _ai_confirm_then(
        self,
        resolved: "ResolvedAI",
        *,
        lede: str,
        calls: int,
        students: int,
        proceed: Callable[[], None],
        on_cancel: Callable[[str], None],
    ) -> None:
        """Run `proceed` now for a local provider; ask first for a cloud one."""
        if resolved.kind != "cloud":
            proceed()
            return

        from opengrader.core.cloud_preflight import CloudPreflight
        from opengrader.tui.ui.screens.cloud_consent_screen import (
            CloudPreflightConfirmScreen,
        )

        cost, basis = self._ai_estimated_cost(resolved, calls)
        preflight = CloudPreflight(
            provider_label=resolved.provider.label,
            host=resolved.host,
            counts={"graded-text": calls},
            students=students,
            estimated_cost=cost,
            cost_basis=basis,
        )
        statement = "\n\n".join(
            part
            for part in (
                lede,
                preflight.statement(),
                app_config.consent.egress_statement(resolved),
            )
            if part
        )

        def _after(confirmed: bool | None) -> None:
            if confirmed:
                proceed()
                return
            on_cancel(
                "Not sent — the cloud send was declined. Nothing left this computer."
            )

        self._app.push_screen(  # type: ignore[attr-defined]
            CloudPreflightConfirmScreen(statement, resolved.host), _after
        )

    def _ai_start_worker(self, work: Callable[[], None], *, name: str) -> None:
        self._app.run_worker(  # type: ignore[attr-defined]
            work,
            name=name,
            group="ai-drafts",
            thread=True,
            exit_on_error=False,
        )

    def _ai_from_thread(self, callback: Callable[..., Any], *args: Any) -> None:
        try:
            self._app.call_from_thread(callback, *args)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - the app is shutting down
            pass

    def _ai_generate(self, resolved: "ResolvedAI", schema: dict) -> Any:
        """Check readiness, then build the generate callable (worker thread)."""
        app_config.ensure_ready(resolved, purpose="text")
        return app_config.text_generate(resolved, response_format=schema)

    @staticmethod
    def _ai_error_message(exc: BaseException, resolved: "ResolvedAI") -> str:
        if isinstance(exc, app_config.CloudConsentRequired):
            return AIDraftMixin._ai_consent_message(resolved)
        if isinstance(exc, app_config.AIUnavailableError):
            return f"AI is not available: {exc} Nothing was sent."
        if isinstance(exc, LLMError):
            return f"The AI call failed: {exc}"
        return f"The AI request failed: {exc}"

    def _ai_is_current_controller(self) -> bool:
        return getattr(self._app, "grade_controller", self) is self  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Feature 1: draft a rubric in the editor
    # ------------------------------------------------------------------

    def draft_rubric_with_ai(self) -> None:
        grade_screen = self._app.get_grade_screen()  # type: ignore[attr-defined]
        editing = getattr(grade_screen, "rubric_edit_active", None)
        if (
            self._state.current_page != PAGE_GRADE  # type: ignore[attr-defined]
            or not callable(editing)
            or not editing()
        ):
            return

        def editor_status(text: str) -> None:
            grade_screen.set_rubric_editor_status(text)

        if self._ai_busy:
            editor_status(self._ai_busy_status())
            return

        question = self._state.grading_run.current_question  # type: ignore[attr-defined]
        if question is None:
            editor_status("No question selected.")
            return

        points = float(question.points or 0.0)
        if points <= 0 or points != int(points):
            editor_status(
                "AI drafting needs a whole-number point value for this question "
                f"(it has {points:g}). Nothing was sent."
            )
            return

        question_id = question.question_id
        groups = self._answer_groups_for_question(question_id)  # type: ignore[attr-defined]
        samples = sample_answers(groups)
        if not samples:
            editor_status(
                "No written answers to learn from yet: AI drafting needs at least "
                "one non-blank student answer. Nothing was sent."
            )
            return

        resolved, problem = self._ai_resolve()
        if resolved is None:
            editor_status(problem)
            return

        request = RubricDraftRequest(
            question_id=question_id,
            prompt=question.prompt or "",
            expected_answers=tuple(question.expected_answers or ()),
            possible_points=points,
            samples=samples,
        )
        session = grade_screen.rubric_edit_session()
        students = sum(sample.count for sample in samples)
        label = format_question_label(question_id)
        busy_label = f"a rubric draft for {label}"
        self._ai_busy = busy_label

        def cancelled(message: str) -> None:
            self._ai_busy = None
            if grade_screen.rubric_edit_active():
                editor_status(message)
            else:
                self._set_active_status(message)  # type: ignore[attr-defined]

        def proceed() -> None:
            editor_status(
                f"Asking the AI to draft partial credit for {label} from "
                f"{_plural(len(samples), 'answer')} "
                f"({_plural(students, 'student')})…"
            )
            folder = self._ai_egress_folder()

            def work() -> None:
                draft: RubricDraft | None = None
                error = ""
                try:
                    from opengrader.core import egress

                    with egress.recording(folder) if folder else nullcontext():
                        generate = self._ai_generate(resolved, RUBRIC_DRAFT_SCHEMA)
                        draft = draft_rubric(request, generate=generate)
                except Exception as exc:  # noqa: BLE001 - reported in the status line
                    error = self._ai_error_message(exc, resolved)
                self._ai_from_thread(
                    self._finish_rubric_draft, question_id, session, draft, error
                )

            self._ai_start_worker(work, name="ai-rubric-draft")

        self._ai_confirm_then(
            resolved,
            lede=(
                f"Draft a rubric for {label} with AI: 1 text call carrying up to "
                f"{_plural(len(samples), 'sample answer')} (given by "
                f"{_plural(students, 'student')}). Names and student IDs are not sent."
            ),
            calls=1,
            students=students,
            proceed=proceed,
            on_cancel=cancelled,
        )

    def _finish_rubric_draft(
        self,
        question_id: str,
        session: int,
        draft: RubricDraft | None,
        error: str,
    ) -> None:
        self._ai_busy = None
        if not self._ai_is_current_controller():
            return
        grade_screen = self._app.get_grade_screen()  # type: ignore[attr-defined]
        question = self._state.grading_run.current_question  # type: ignore[attr-defined]
        still_here = (
            self._state.current_page == PAGE_GRADE  # type: ignore[attr-defined]
            and grade_screen.rubric_edit_active()
            and grade_screen.rubric_edit_session() == session
            and question is not None
            and question.question_id == question_id
        )
        label = format_question_label(question_id)
        if not still_here:
            message = (
                error
                if error
                else f"The AI rubric draft for {label} arrived after you left the "
                "rubric editor, so it was not applied. Nothing was saved."
            )
            self._set_active_status(message)  # type: ignore[attr-defined]
            return
        if error:
            grade_screen.set_rubric_editor_status(
                f"{error} Nothing changed in the editor."
            )
            return
        if draft is None:
            grade_screen.set_rubric_editor_status(
                "The AI's draft could not be used (it did not propose at least "
                "two usable partial-credit items that add up). Nothing changed — "
                "try again, or write the items yourself."
            )
            return

        status = (
            f"AI draft from {_plural(draft.sample_count, 'answer')} "
            f"({_plural(draft.student_count, 'student')}) — review and edit, "
            "then Save. Nothing is saved yet."
        )
        if draft.rationale:
            status += f"\nAI's reasoning: {draft.rationale}"
        removed_in_use = self._labels_in_use_removed_by(question_id, draft)
        if removed_in_use:
            status += (
                "\nNote: saved grades use "
                + ", ".join(f"'{label}'" for label in removed_in_use)
                + ", which this draft removes. Save refuses to delete an option "
                "a saved grade still uses: add it back as a row, or cancel."
            )
        if grade_screen.apply_rubric_draft(list(draft.options), status=status):
            self._ai_rubric_draft_question = question_id

    def _labels_in_use_removed_by(
        self, question_id: str, draft: RubricDraft
    ) -> list[str]:
        """Current rubric labels that saved grades use but the draft drops."""
        kept = {option.label for option in draft.options}
        question = self._state.grading_run.current_question  # type: ignore[attr-defined]
        current = [
            option.label
            for option in (question.rubric_options if question else [])
            if option.kind != "bonus" and option.label not in kept
        ]
        if not current:
            return []
        used: set[str] = set()
        for student in self._state.student_items:  # type: ignore[attr-defined]
            seed = self._question_seed_for_student(question_id, student.item_id)  # type: ignore[attr-defined]
            if seed is not None:
                used.update(seed.selected_rubric_labels or ())
        return [label for label in current if label in used]

    def _ai_rubric_saved_hint(self, question_id: str) -> str:
        """The hint appended to the save status when the rubric came from a draft."""
        drafted = self._ai_rubric_draft_question == question_id
        self._ai_rubric_draft_question = None
        if not drafted:
            return ""
        return (
            f" Press {AI_DRAFT_GRADES_KEY} to have the AI draft grades for the "
            "students still ungraded on this question."
        )

    # ------------------------------------------------------------------
    # Feature 2: AI draft grades for every ungraded student on a question
    # ------------------------------------------------------------------

    def _effective_answer_groups(self, question_id: str) -> list[AnswerGroup]:
        """Exact answer groups, widened by any similar-answer merge accepted."""
        exact_by_member: dict[str, AnswerGroup] = {}
        for group in self._answer_groups_for_question(question_id):  # type: ignore[attr-defined]
            for member in group.member_ids:
                exact_by_member[member] = group
        effective: list[AnswerGroup] = []
        for members in self._grading_groups(question_id):  # type: ignore[attr-defined]
            exact = exact_by_member.get(members[0])
            if exact is None:
                continue
            effective.append(
                AnswerGroup(
                    key=exact.key,
                    member_ids=tuple(members),
                    sample_response_text=exact.sample_response_text,
                    question_type=exact.question_type,
                )
            )
        return effective

    def _ungraded_for_ai(self, question_id: str, student_item_id: str):  # noqa: ANN202
        """The student's seed if the AI may grade them now, else None."""
        student = self._student_item_by_id(student_item_id)  # type: ignore[attr-defined]
        if student is None or self._is_skipped_by_claim(question_id, student):  # type: ignore[attr-defined]
            return None
        pending = getattr(self, "_pending_comment", None)
        if (
            pending is not None
            and pending.student_item_id == student_item_id
            and pending.question_id == question_id
        ):
            return None
        seed = self._question_seed_for_student(question_id, student_item_id)  # type: ignore[attr-defined]
        if seed is None:
            return None
        if seed.points_awarded is not None or seed.selected_rubric_labels:
            return None
        return seed

    def draft_grades_with_ai(self) -> None:
        if not self._on_grade_page():  # type: ignore[attr-defined]
            return
        if self._ai_busy:
            self.refresh_grade_screen(status=self._ai_busy_status())  # type: ignore[attr-defined]
            return

        save_grade = getattr(self._data_provider, "save_question_grade", None)  # type: ignore[attr-defined]
        if not callable(save_grade):
            self.refresh_grade_screen(  # type: ignore[attr-defined]
                status="AI draft grades are not supported by this data provider."
            )
            return

        self.commit_pending_comment()  # type: ignore[attr-defined]
        question = self._state.grading_run.current_question  # type: ignore[attr-defined]
        if question is None:
            self.refresh_grade_screen(status="No question selected.")  # type: ignore[attr-defined]
            return
        question_id = question.question_id
        label = format_question_label(question_id)
        if not question.rubric_options:
            self.refresh_grade_screen(  # type: ignore[attr-defined]
                status=(
                    f"{label} has no rubric yet. Add one with e (Draft with AI "
                    "can help there), then press "
                    f"{AI_DRAFT_GRADES_KEY} again. Nothing was sent."
                )
            )
            return

        rubric_choices = tuple(
            RubricChoice(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
            )
            for option in question.rubric_options
        )
        groups = self._effective_answer_groups(question_id)
        eligible: set[str] = set()
        for group in groups:
            if group.is_blank or not group.sample_response_text.strip():
                continue
            for member in group.member_ids:
                if self._ungraded_for_ai(question_id, member) is not None:
                    eligible.add(member)
        to_send = [
            group
            for group in groups
            if not group.is_blank
            and group.sample_response_text.strip()
            and any(member in eligible for member in group.member_ids)
        ]
        if not to_send:
            self.refresh_grade_screen(  # type: ignore[attr-defined]
                status=(
                    f"Every written answer on {label} already has a grade (blank "
                    "answers are never sent), so there is nothing for the AI to "
                    "draft. Nothing was sent."
                )
            )
            return

        resolved, problem = self._ai_resolve()
        if resolved is None:
            self.refresh_grade_screen(status=problem)  # type: ignore[attr-defined]
            return
        if (
            resolved.grade_written
            and resolved.require_calibration
            and not resolved.settings.min_confidence_measured
        ):
            # The same gate `automated-grading --llm` applies via problems().
            self.refresh_grade_screen(  # type: ignore[attr-defined]
                status=(
                    "AI grading is set to require a measured confidence, but "
                    f"{resolved.min_confidence:g} was never measured. Run "
                    "'Calibrate AI grading' in Helper Tools first. Nothing was sent."
                )
            )
            return

        calls = len(to_send)
        students = len(eligible)
        busy_label = f"draft grades for {label}"
        self._ai_busy = busy_label
        prompt = question.prompt or ""
        expected = tuple(question.expected_answers or ())
        possible = float(question.points or 0.0)
        min_confidence = float(resolved.min_confidence)
        model = resolved.text_model
        provider_name = resolved.provider.name

        def cancelled(message: str) -> None:
            self._ai_busy = None
            self._set_active_status(message)  # type: ignore[attr-defined]

        def proceed() -> None:
            self._set_active_status(  # type: ignore[attr-defined]
                f"AI grading {_plural(calls, 'distinct answer')} "
                f"({_plural(students, 'student')}) on {label}…"
            )
            folder = self._ai_egress_folder()

            def progress(done: int, total: int) -> None:
                self._ai_from_thread(
                    self._set_active_status,
                    f"AI grading answer {done + 1} of {total} on {label}…",
                )

            def work() -> None:
                result: DraftGradeResult | None = None
                error = ""
                try:
                    from opengrader.core import egress

                    with egress.recording(folder) if folder else nullcontext():
                        generate = self._ai_generate(resolved, GRADE_SUGGESTION_SCHEMA)
                        result = draft_grades_for_groups(
                            to_send,
                            prompt=prompt,
                            expected_answers=expected,
                            rubric_options=rubric_choices,
                            possible_points=possible,
                            generate=generate,
                            min_confidence=min_confidence,
                            eligible_member_ids=frozenset(eligible),
                            on_progress=progress,
                        )
                except Exception as exc:  # noqa: BLE001 - reported in the status line
                    error = self._ai_error_message(exc, resolved)
                self._ai_from_thread(
                    self._finish_draft_grades,
                    question_id,
                    result,
                    error,
                    model,
                    provider_name,
                )

            self._ai_start_worker(work, name="ai-draft-grades")

        self._ai_confirm_then(
            resolved,
            lede=(
                f"AI draft grades for {label}: one text call per distinct written "
                f"answer — {_plural(calls, 'call')} for "
                f"{_plural(students, 'ungraded student')}. Each call carries the "
                "question, its answer key and rubric, and one student answer. "
                "Names and student IDs are not sent."
            ),
            calls=calls,
            students=students,
            proceed=proceed,
            on_cancel=cancelled,
        )

    def _finish_draft_grades(
        self,
        question_id: str,
        result: DraftGradeResult | None,
        error: str,
        model: str,
        provider_name: str,
    ) -> None:
        self._ai_busy = None
        label = format_question_label(question_id)
        if error or result is None:
            self._set_active_status(  # type: ignore[attr-defined]
                (error or "The AI request failed.") + " No grades were saved."
            )
            return

        save_grade = getattr(self._data_provider, "save_question_grade", None)  # type: ignore[attr-defined]
        saved = 0
        save_failed = 0
        now_graded = 0
        saved_groups = 0
        current = self._current_student_item()  # type: ignore[attr-defined]
        touched_current = False
        for grade in result.applied:
            suggestion = grade.suggestion
            analysis = format_ai_analysis(
                suggestion, model=model, provider=provider_name
            )
            group_saved = False
            for member_id in grade.member_ids:
                # Re-checked right before saving: the grader may have graded
                # this student while the AI was working. Never overwrite.
                seed = self._ungraded_for_ai(question_id, member_id)
                if seed is None:
                    now_graded += 1
                    continue
                points = self._points_for_seed_labels(  # type: ignore[attr-defined]
                    seed, tuple(suggestion.rubric_labels)
                )
                if points is None:
                    save_failed += 1
                    continue
                try:
                    save_grade(
                        student_item_id=member_id,
                        question_id=question_id,
                        possible_points=seed.points,
                        points_awarded=points,
                        rubric_labels=tuple(suggestion.rubric_labels),
                        analysis=analysis,
                    )
                except Exception:  # noqa: BLE001 - counted and reported
                    save_failed += 1
                    continue
                self._set_qa_student_status(question_id, member_id, graded=True)  # type: ignore[attr-defined]
                saved += 1
                group_saved = True
                if current is not None and current.item_id == member_id:
                    touched_current = True
            if group_saved:
                saved_groups += 1

        self._unconfirmed_ai_cache.pop(question_id, None)  # type: ignore[attr-defined]

        if saved:
            self._record_log_entry(  # type: ignore[attr-defined]
                GradingLogEntry(
                    kind="grade",
                    question_id=question_id,
                    summary=(
                        f"AI draft grades · {_plural(saved, 'student')} · "
                        f"{_plural(saved_groups, 'answer')} · unconfirmed"
                    ),
                )
            )

        parts = [
            f"AI drafted grades for {_plural(saved, 'student')} "
            f"({_plural(saved_groups, 'distinct answer')}) on {label}"
        ]
        low = result.low_confidence_student_count
        if low:
            parts.append(f"{low} left for you (low confidence)")
        if result.failed_groups:
            parts.append(f"{result.failed_groups} failed")
        if now_graded:
            parts.append(f"{now_graded} skipped (graded meanwhile)")
        if save_failed:
            parts.append(f"{save_failed} could not be saved")
        if saved:
            parts.append(f"press {AI_REVIEW_KEY} to confirm them")
        status = " · ".join(parts) + "."

        if not self._ai_is_current_controller():
            return
        refresh_students = getattr(self, "_refresh_students", None)
        if callable(refresh_students):
            refresh_students()

        grade_screen = self._app.get_grade_screen()  # type: ignore[attr-defined]
        on_this_question = (
            self._on_grade_page()  # type: ignore[attr-defined]
            and self._current_question_id() == question_id  # type: ignore[attr-defined]
            and not grade_screen.rubric_edit_active()
            and not grade_screen.transcription_editor_active()
        )
        if on_this_question and touched_current:
            # Reload so the drafted grade for the student on screen shows up.
            self.open_student_by_index(self._state.active_student_index)  # type: ignore[attr-defined]
        if self._on_grade_page() and not grade_screen.rubric_edit_active():  # type: ignore[attr-defined]
            self.refresh_grade_screen(status=status)  # type: ignore[attr-defined]
        else:
            self._set_active_status(status)  # type: ignore[attr-defined]
