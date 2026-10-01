"""Let an AI model draft partial-credit rubric items from students' answers."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from opengrader.cli import CONTEXT_SETTINGS, ai_default, run_command
from opengrader.core import app_config
from opengrader.core.concurrency import (
    ConcurrentModificationError,
    check_unmodified,
    file_mtime_ns,
)
from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.grading.llm_grader import GenerateFn
from opengrader.core.grading.rubric_draft import (
    RUBRIC_DRAFT_SCHEMA,
    RubricDraft,
    RubricDraftRequest,
    draft_rubric,
    has_partial_credit_items,
    render_rubric_markdown,
    sample_answers,
)
from opengrader.core.grading.solutions import (
    SolutionEntry,
    flatten_solution_entries,
    solution_version_for_submission,
)
from opengrader.core.llm.errors import LLMError
from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.qa_file.markdown import (
    QADocument,
    QARubricFlatOption,
    find_qa_question,
    flatten_rubric_options,
    replace_qa_rubric_options,
    save_qa_markdown,
)
from opengrader.core.submissions.answer_groups import GroupableResponse, group_responses
from opengrader.core.submissions.printed_text import response_without_printed_text
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    load_student_submission_markdown,
)

EPILOG = """\
\b
Examples:
  uv run draft-rubric --solutions qa.md --input ./student-submissions/
  uv run draft-rubric --solutions qa.md --input ./student-submissions/ --question q5
  uv run draft-rubric --solutions qa.md --input ./student-submissions/ --write
  uv run draft-rubric --solutions qa.md --input ./student-submissions/ --question q5 --overwrite --write
  uv run draft-rubric --solutions qa.md --input ./student-submissions/ --json

\b
Step 1 of 2: the model only DRAFTS rubric items; you review them. Step 2:
  uv run automated-grading --solutions qa.md --input ./student-submissions/ --llm
"""

DEFAULT_MAX_SAMPLES = 12


@dataclass(frozen=True)
class _Hooks:
    generate_factory: Callable[[app_config.ResolvedAI], GenerateFn] | None = None


@dataclass
class _Target:
    version: str
    question_id: str
    entry: SolutionEntry
    responses: list[GroupableResponse] = field(default_factory=list)
    saved_labels: set[str] = field(default_factory=set)


@dataclass
class DraftResult:
    question_id: str
    version: str
    status: str
    reason: str = ""
    options: tuple[QARubricFlatOption, ...] = ()
    rationale: str = ""
    sample_count: int = 0
    student_count: int = 0
    written: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "version": self.version,
            "status": self.status,
            "reason": self.reason,
            "options": [
                {
                    "label": option.label,
                    "points_delta": option.points_delta,
                    "kind": option.kind,
                    "selection_mode": option.selection_mode,
                    "default_comment": option.default_comment,
                }
                for option in self.options
            ],
            "rationale": self.rationale,
            "sample_count": self.sample_count,
            "student_count": self.student_count,
            "written": self.written,
        }


def _title(version: str, question_id: str) -> str:
    return f"{question_id} (version {version})" if version else question_id


def _member_id(document: StudentSubmissionDocument) -> str:
    return (
        document.student_id.strip()
        or document.student_name.strip()
        or document.source_pdf
    )


def _collect_targets(
    qa_doc: QADocument, documents: list[StudentSubmissionDocument]
) -> list[_Target]:
    """One target per (version, question id) in QA-file order."""
    if qa_doc.version_questions:
        versions = {v: flatten_solution_entries(qs) for v, qs in qa_doc.version_questions.items()}
    else:
        versions = {"": flatten_solution_entries(qa_doc.questions)}
    targets: dict[tuple[str, str], _Target] = {
        (version, qid): _Target(version, qid, entry)
        for version, entries in versions.items()
        for qid, entry in entries.items()
    }
    for document in documents:
        version = solution_version_for_submission(qa_doc, document)
        member = _member_id(document)
        for question in document.questions:
            target = targets.get((version, question.question_id))
            if target is None:
                continue
            target.responses.append(
                GroupableResponse(
                    member_id=member,
                    response_text=response_without_printed_text(
                        question.response_text,
                        source=question.response_source,
                        printed=target.entry.printed_text,
                    ),
                    question_type=question.question_type,
                )
            )
            target.saved_labels.update(question.rubric_labels)
    return list(targets.values())


def _nonblank(target: _Target) -> bool:
    return any(response.response_text.strip() for response in target.responses)


@click.command("draft-rubric", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option("--solutions", required=True, type=click.Path(path_type=Path), help="The QA markdown file (answer key and rubrics).")
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    required=True,
    help="A .submission.md file or a directory of them.",
)
@click.option(
    "--question",
    "questions",
    multiple=True,
    help="Draft only this question id (repeatable). Default: every eligible question.",
)
@click.option("--write", "write", is_flag=True, help="Write the drafts into the QA file. Default: only print them.")
@click.option(
    "--overwrite",
    is_flag=True,
    help=(
        "Allow replacing a rubric that already has partial-credit items "
        "(only for questions named with --question). Refused for a question "
        "if a saved grade uses a label the draft would remove."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable results on stdout; narration goes to stderr.")
@click.option(
    "--max-samples",
    type=click.IntRange(min=1),
    default=DEFAULT_MAX_SAMPLES,
    show_default=True,
    help="Most distinct answers shown to the model per question.",
)
@click.option(
    "--llm-model",
    "text_model",
    default=ai_default("text_model"),
    show_default="from AI Setup",
    help="Model to draft with.",
)
@click.option("--llm-host", default=ai_default("host"), show_default="from AI Setup", help="AI server URL.")
@click.option(
    "--llm-timeout",
    type=float,
    default=app_config.DEFAULT_TIMEOUT_SECONDS,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--yes-send-to-cloud",
    is_flag=True,
    help=(
        "Confirm sending students' answers to a cloud AI provider. The scale "
        "and destination are shown first; nothing is sent without this or an "
        "interactive yes."
    ),
)
@click.pass_obj
def command(
    hooks: _Hooks | None,
    *,
    solutions: Path,
    input_path: Path,
    questions: tuple[str, ...],
    write: bool,
    overwrite: bool,
    as_json: bool,
    max_samples: int,
    text_model: str,
    llm_host: str,
    llm_timeout: float,
    yes_send_to_cloud: bool,
) -> list[DraftResult]:
    """Draft partial-credit rubric items for free-response questions from the
    answers students actually gave. The instructor reviews the draft."""
    hooks = hooks or _Hooks()

    def say(message: str = "") -> None:
        print(message, file=sys.stderr if as_json else sys.stdout)

    def err(message: str) -> None:
        print(message, file=sys.stderr)

    if not solutions.is_file():
        err(f"Error: Solutions file not found at {solutions}")
        sys.exit(1)
    if not input_path.is_file() and not input_path.is_dir():
        err(f"Error: Input path '{input_path}' is not a file or directory.")
        sys.exit(1)

    qa_mtime = file_mtime_ns(solutions)
    try:
        qa_doc = load_qa_markdown(solutions)
    except (OSError, ValueError) as exc:
        err(f"Error: could not read {solutions}: {exc}")
        sys.exit(1)

    paths = discover_submission_paths(input_path)
    documents: list[StudentSubmissionDocument] = []
    for path in paths:
        try:
            documents.append(load_student_submission_markdown(path))
        except (OSError, ValueError) as exc:
            err(f"Warning: could not read {path}: {exc}")
    if not documents:
        err(f"Error: no readable .submission.md files found under {input_path}")
        sys.exit(1)

    targets = _collect_targets(qa_doc, documents)
    known_ids = {target.question_id for target in targets}
    for wanted in questions:
        if wanted not in known_ids:
            err(f"Error: question {wanted} not found in {solutions}")
            sys.exit(1)

    results: list[DraftResult] = []
    to_draft: list[_Target] = []
    for target in targets:
        explicit = target.question_id in questions
        if questions and not explicit:
            continue
        entry = target.entry
        if entry.is_multiple_choice or entry.is_multiple_answer:
            if explicit:
                results.append(
                    DraftResult(target.question_id, target.version, "skipped", "multiple choice question")
                )
            continue
        if not _nonblank(target):
            if explicit:
                results.append(
                    DraftResult(target.question_id, target.version, "skipped", "no answers to draft from")
                )
            continue
        options = flatten_rubric_options(
            find_qa_question(qa_doc, target.question_id, version=target.version or None).rubric
        )
        if has_partial_credit_items(options) and not (explicit and overwrite):
            results.append(
                DraftResult(
                    target.question_id,
                    target.version,
                    "skipped",
                    "already has partial-credit items "
                    f"(use --question {target.question_id} --overwrite to redraft)",
                )
            )
            continue
        to_draft.append(target)

    generate: GenerateFn | None = None
    if to_draft:
        # Checked only when something will be sent, so a run with nothing
        # left to draft (the pipeline's usual re-run) never fails on AI setup.
        resolved_ai = app_config.resolve()
        problems = resolved_ai.problems()
        if problems:
            for problem in problems:
                err(f"Error: {problem}")
            sys.exit(2)
        resolved = app_config.with_overrides(
            resolved_ai,
            host=llm_host,
            text_model=text_model,
            timeout_seconds=llm_timeout,
        )
        if resolved.kind == "cloud":
            from opengrader.core.cloud_preflight import CloudPreflight, cli_confirm_cloud_send

            calls = len(to_draft)
            cli_confirm_cloud_send(
                resolved,
                CloudPreflight(
                    provider_label=resolved.provider.label,
                    host=resolved.host,
                    counts={"graded-text": calls},
                    students=len(documents),
                ),
                yes_send_to_cloud=yes_send_to_cloud,
                emit=err,
            )
        if hooks.generate_factory is not None:
            generate = hooks.generate_factory(resolved)
        else:
            try:
                app_config.ensure_ready(resolved, purpose="text")
            except (app_config.CloudConsentRequired, app_config.CloudDisabledError) as exc:
                err(f"Error: {exc}")
                err(
                    f"Fix: run 'opengrader providers consent {resolved.provider.name}' "
                    "(or open AI Setup), then retry."
                )
                sys.exit(2)
            except app_config.AIUnavailableError as exc:
                err(f"Error: {exc}")
                sys.exit(1)
            generate = app_config.text_generate(resolved, response_format=RUBRIC_DRAFT_SCHEMA)
        say(f"Drafting rubrics for {len(to_draft)} question(s) (model={resolved.text_model}).")

    from opengrader.core import egress

    drafts: dict[tuple[str, str], RubricDraft] = {}
    with egress.recording(input_path):
        for target in to_draft:
            assert generate is not None
            label = _title(target.version, target.question_id)
            samples = sample_answers(group_responses(target.responses), max_groups=max_samples)
            request = RubricDraftRequest(
                question_id=target.question_id,
                prompt=target.entry.prompt,
                expected_answers=target.entry.expected_answers,
                possible_points=target.entry.points,
                samples=samples,
            )
            try:
                draft = draft_rubric(request, generate=generate)
            except LLMError as exc:
                err(f"{label}: model call failed: {exc}")
                results.append(DraftResult(target.question_id, target.version, "failed", f"model error: {exc}"))
                continue
            if draft is None:
                results.append(
                    DraftResult(
                        target.question_id,
                        target.version,
                        "failed",
                        "the model produced no usable rubric",
                    )
                )
                continue
            existing = flatten_rubric_options(
                find_qa_question(qa_doc, target.question_id, version=target.version or None).rubric
            )
            result = DraftResult(
                target.question_id,
                target.version,
                "drafted",
                options=draft.options,
                rationale=draft.rationale,
                sample_count=draft.sample_count,
                student_count=draft.student_count,
            )
            if write and existing:
                lost = sorted(target.saved_labels - {option.label for option in draft.options})
                if lost:
                    result.status = "skipped"
                    result.written = False
                    result.reason = (
                        "not overwritten: saved grades use rubric label(s) the draft "
                        "does not contain: " + ", ".join(repr(name) for name in lost)
                    )
                    results.append(result)
                    continue
            drafts[(target.version, target.question_id)] = draft
            results.append(result)

    if write and drafts:
        try:
            check_unmodified(solutions, qa_mtime)
            for result in results:
                draft = drafts.get((result.version, result.question_id))
                if draft is None or result.status != "drafted":
                    continue
                try:
                    replace_qa_rubric_options(
                        qa_doc,
                        result.question_id,
                        list(draft.options),
                        version=result.version or None,
                    )
                except ValueError as exc:
                    result.status = "failed"
                    result.reason = f"could not write rubric: {exc}"
                    continue
                result.written = True
            if any(result.written for result in results):
                save_qa_markdown(qa_doc, solutions)
        except ConcurrentModificationError as exc:
            err(f"Error: {exc} Nothing was written; re-run draft-rubric.")
            for result in results:
                result.written = False
            if as_json:
                print(json.dumps([r.to_dict() for r in results], indent=2))
            sys.exit(1)

    if as_json:
        print(json.dumps([result.to_dict() for result in results], indent=2))

    drafted_any = False
    for result in results:
        label = _title(result.version, result.question_id)
        if result.status == "drafted":
            drafted_any = True
            say(
                f"\n{label}: drafted from {result.sample_count} distinct answers "
                f"({result.student_count} students)"
                + (" - written to the QA file" if result.written else "")
            )
            say(render_rubric_markdown(result.options))
            if result.rationale:
                say(f"Rationale: {result.rationale}")
        elif result.status == "skipped":
            say(f"\n{label}: skipped - {result.reason}")
        else:
            say(f"\n{label}: could not draft - {result.reason}")

    if not results:
        say("No questions to draft: none has free-response answers without partial credit.")

    if drafted_any:
        wrote = any(result.written for result in results)
        say("")
        if not wrote:
            say("Nothing was written. Re-run with --write to save these drafts into the QA file.")
        say(
            "Next: review the rubric (edit the QA file, or use the grading app's "
            "rubric editor), then grade with it:\n"
            f"  uv run automated-grading --solutions {solutions} --input {input_path} --llm"
        )
    return results


def run_cli(
    argv: Sequence[str],
    *,
    generate_factory: Callable[[app_config.ResolvedAI], GenerateFn] | None = None,
) -> list[DraftResult] | None:
    return run_command(command, argv, obj=_Hooks(generate_factory=generate_factory))


if __name__ == "__main__":
    command()
