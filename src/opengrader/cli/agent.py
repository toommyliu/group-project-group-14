"""Work with an AI assistant the teacher already uses, without student identities.

The teacher runs `prepare`, `apply`, `accept`/`dismiss`, `answer-key`,
`use-key` and `delete` on the class folder — or, with `--workspace`, on a
workspace assignment (`core/agent/workspace.py`); the assistant runs the rest
from inside its packet, or reaches the same operations as MCP tools (`mcp`).
The rules live in `core/agent/`.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import NoReturn

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.agent import (
    AgentError,
    AgentTask,
    accept_suggestion,
    check_key_file,
    client_instructions,
    delete_packet,
    dismiss_suggestion,
    find_packet_root,
    list_suggestions,
    load_tasks,
    packet_status,
    parse_partial,
    prepare_agent_packet,
    record_grade,
    record_results,
    record_transcription,
    serve,
    suggest,
    use_key,
)
from opengrader.core.agent.common import KIND_ANSWER_KEY
from opengrader.core.handoff.packet import PacketError

EPILOG = """\
\b
The teacher, on the class folder:
  opengrader agent prepare --submissions class/submissions --qa-file class/qa.md
  opengrader agent apply --submissions class/submissions --incoming <packet> --yes

\b
The teacher, on a workspace (opengrader tui, the browser app):
  opengrader agent prepare --workspace course --assignment "Quiz 1"
  opengrader agent apply --workspace course --assignment "Quiz 1" --incoming <packet> --yes

\b
The assistant, inside the packet:
  opengrader agent status
  opengrader agent tasks --json --question q2
  opengrader agent transcribe og-1a2b3c4d/q2 --text "what the student wrote"
  opengrader agent grade og-1a2b3c4d/q2 --rubric "Partially correct" --confidence 0.8 --comment "Off by one."
  opengrader agent check-key qa.md
  opengrader agent suggest q2 --kind answer --message "101 is 5, not 6" --correct 110
  opengrader agent record results.jsonl

\b
Assistants without a shell (Claude Desktop, ChatGPT, …):
  opengrader agent mcp-config --packet <packet>     # what to paste into the app

\b
Writing the answer key (no student data involved):
  opengrader agent answer-key --class-folder class [--source key.docx]
  opengrader agent use-key --folder class-ai-answer-key --to class/qa.md

\b
What the assistant should know: opengrader ai-readme
"""


def _fail(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(2)


def _packet(kind: str = "grading") -> Path:
    try:
        return find_packet_root(kind=kind)
    except AgentError as exc:
        _fail(str(exc))


_WORKSPACE_HELP = "A workspace folder (opengrader tui, the browser app) instead of a class folder."
_ASSIGNMENT_HELP = "With --workspace: the assignment, by title or id (needed when it has several)."
_KEY_FILE_HELP = (
    "With --workspace: also write the change into the assignment's qa.md file (update), or "
    "store it only in the workspace (leave). Required when the assignment has such a file."
)


def _workspace_target(raw_workspace: str, raw_assignment: str) -> tuple[Path, str]:
    """The workspace and assignment, for the account that owns it only."""
    from opengrader.core.agent.workspace import require_workspace, resolve_assignment
    from opengrader.server.workspace.client import owned_by_another_account

    try:
        root = require_workspace(raw_workspace)
        if owned_by_another_account(root):
            _fail(
                f"{root} belongs to another account: only its owner gives its work to an "
                "AI assistant and merges it back"
            )
        return root, resolve_assignment(root, raw_assignment)
    except AgentError as exc:
        _fail(str(exc))


def _workspace_request(root: Path):  # noqa: ANN202 - WorkspaceClient.request
    """The workspace service, started if it is not running: it alone writes the database."""
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service

    try:
        return WorkspaceClient(ensure_service(root)).request
    except (RuntimeError, OSError) as exc:
        _fail(f"the workspace's service could not be reached: {exc}")


def _workspace_flags(root: Path, assignment_id: str) -> str:
    return f"--workspace {shlex.quote(str(root))} --assignment {shlex.quote(assignment_id)}"


@click.group("agent", cls=Group, context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
def command() -> None:
    """Grade written answers with your own AI assistant (Claude, ChatGPT, …)
    in a copy with no student names or IDs, then merge its suggestions back
    for you to review.
    """


@command.command("prepare")
@click.option("--submissions", "raw_submissions", default="", help="The class's submissions folder.")
@click.option("--qa-file", "raw_qa", default="", help="The answer key (qa.md).")
@click.option("--workspace", "raw_workspace", default="", help=_WORKSPACE_HELP)
@click.option("--assignment", "raw_assignment", default="", help=_ASSIGNMENT_HELP)
@click.option(
    "--out",
    "raw_out",
    default="",
    help="Where to make the packet (default: <class folder>-ai-assistant/, beside the class folder or workspace).",
)
@click.option("--name", default="", help="Packet name (default: ai-assistant-<date-time>).")
@click.option("--question", "questions", multiple=True, help="Only this question (repeat for several).")
@click.option(
    "--with-graded",
    is_flag=True,
    help="Also include answers already graded, as examples of your standard. Off by default: less leaves.",
)
@click.option(
    "--rubrics",
    is_flag=True,
    help="A copy for drafting partial-credit rubrics (from every answer to the questions without any), not for grading.",
)
@click.option(
    "--no-crop-check",
    is_flag=True,
    help="Do not read each answer image with Tesseract for the student's name or ID. Not recommended.",
)
def prepare_command(
    raw_submissions: str,
    raw_qa: str,
    raw_workspace: str,
    raw_assignment: str,
    raw_out: str,
    name: str,
    questions: tuple[str, ...],
    with_graded: bool,
    rubrics: bool,
    no_crop_check: bool,
) -> None:
    """Make a redacted packet for an AI assistant (run by the teacher)."""
    options = dict(
        packet_id=name,
        questions=questions,
        include_graded=with_graded,
        check_crops=not no_crop_check,
        purpose="rubrics" if rubrics else "grading",
    )
    out = Path(raw_out).expanduser() if raw_out else None
    if raw_workspace:
        from opengrader.core.agent.workspace import prepare_workspace_packet

        if raw_submissions or raw_qa:
            _fail("give either --workspace or --submissions and --qa-file, not both")
        root, assignment_id = _workspace_target(raw_workspace, raw_assignment)
        try:
            packet = prepare_workspace_packet(root, assignment_id, out, **options)
        except (AgentError, PacketError, ValueError) as exc:
            _fail(str(exc))
        merge_command = f"opengrader agent apply {_workspace_flags(root, assignment_id)}"
        keep_out = "workspace"
    else:
        if not raw_submissions or not raw_qa:
            _fail("give --submissions and --qa-file (a class folder), or --workspace")
        submissions = Path(raw_submissions).expanduser()
        qa_file = Path(raw_qa).expanduser()
        for path, what in ((submissions, "submissions folder"), (qa_file, "answer key")):
            if not path.exists():
                _fail(f"{what} not found: {path}")
        try:
            packet = prepare_agent_packet(submissions, qa_file, out, **options)
        except (AgentError, PacketError, ValueError) as exc:
            _fail(str(exc))
        merge_command = f"opengrader agent apply --submissions {shlex.quote(str(submissions))}"
        keep_out = "class folder"
    print(f"Made {packet.path}")
    print(f"  {packet.students} student(s), {packet.tasks} answer(s) left to grade.")
    print("  Names, IDs, scans and name/ID crops were left out; every student is an og-… code.")
    if packet.left_out:
        print(f"  {packet.left_out} answer(s) it does not need (already graded, or other questions) stayed here.")
    if no_crop_check:
        print("  Answer images were NOT checked for names or IDs.")
    else:
        print(f"  Checked {packet.images_checked} answer image(s) for the student's name or ID with Tesseract.")
        if packet.images_withheld:
            print(
                f"  {packet.images_withheld} image(s) may show one (or could not be read) and stayed "
                "here: grade those answers yourself."
            )
    if packet.scrubbed:
        print(f"  Removed a name or ID from {packet.scrubbed} answer(s) or comment(s).")
    if packet.unknown_version:
        print(
            f"  {packet.unknown_version} paper(s) have no known version and were left out of the "
            "tasks: set it in the review queue (R) and prepare again."
        )
    print(
        "\nNext: start your AI assistant inside that folder and ask it to grade.\n"
        f"It reads AGENTS.md there. Keep it out of your {keep_out}."
    )
    print(
        "\nWhen it is done, merge its suggestions back (you review them in the app):\n"
        f"  {merge_command} --incoming {shlex.quote(str(packet.path))} --yes"
    )


@command.command("status")
@click.option("--json", "as_json", is_flag=True)
def status_command(as_json: bool) -> None:
    """How much of the packet is left to grade."""
    status = packet_status(_packet())
    if as_json:
        print(
            json.dumps(
                {
                    "students": status.students,
                    "ungraded": status.ungraded,
                    "graded_by_agent": status.graded_by_agent,
                    "graded_by_teacher": status.graded,
                    "unknown_version": list(status.unknown_version),
                },
                indent=2,
            )
        )
        return
    print(f"{status.students} student(s)")
    print(f"  {status.ungraded} answer(s) to grade")
    print(f"  {status.graded_by_agent} suggested by the assistant (the teacher reviews these)")
    print(f"  {status.graded} graded by the teacher or the rules (never changed here)")
    if status.unknown_version:
        print(f"  {len(status.unknown_version)} paper(s) without a known version are not included")


def _describe(task: AgentTask) -> str:
    lines = [
        f"{task.id}  [{task.status}]  {task.possible_points:g} points",
        f"  Question: {task.prompt}",
    ]
    if task.expected_answers:
        lines.append("  Correct: " + "; ".join(task.expected_answers))
    lines.append("  Rubric:")
    for option in task.rubric:
        lines.append(f'    "{option.label}"  {option.points_delta:+g}  ({option.selection_mode})')
    if task.image_withheld:
        lines.append(f"  Image: withheld (it may show the student's {task.image_withheld}); leave it unless the text is enough")
    else:
        lines.append(f"  Image: {task.response_image or '(none)'}")
    lines.append(f"  Read ({task.read_by or 'not read'}): {task.response_text or '(empty)'}")
    if task.status != "ungraded":
        lines.append(f"  Now: {task.points:g} — {', '.join(task.rubric_labels)}")
    return "\n".join(lines)


@command.command("tasks")
@click.option("--question", default="", help="Only this question (e.g. q2).")
@click.option("--all", "include_done", is_flag=True, help="Include answers already graded.")
@click.option("--limit", type=int, default=0, help="At most this many.")
@click.option("--json", "as_json", is_flag=True)
def tasks_command(question: str, include_done: bool, limit: int, as_json: bool) -> None:
    """The answers still to grade, question by question."""
    tasks = [
        task
        for task in load_tasks(_packet(), question=question)
        if include_done or task.status == "ungraded"
    ]
    if limit > 0:
        tasks = tasks[:limit]
    if as_json:
        print(json.dumps([task.to_json() for task in tasks], indent=2, ensure_ascii=False))
        return
    if not tasks:
        print("Nothing left to grade." if not include_done else "No tasks.")
        return
    print("\n\n".join(_describe(task) for task in tasks))


@command.command("show")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True)
def show_command(task_id: str, as_json: bool) -> None:
    """One task."""
    for task in load_tasks(_packet()):
        if task.id == task_id.strip():
            print(
                json.dumps(task.to_json(), indent=2, ensure_ascii=False)
                if as_json
                else _describe(task)
            )
            return
    _fail(f"no task {task_id!r} (see 'agent tasks')")


@command.command("grade")
@click.argument("task_id")
@click.option("--rubric", "labels", multiple=True, required=True, help="A rubric option label, exactly as listed. Repeat for several.")
@click.option("--confidence", type=float, default=0.8, show_default=True, help="How sure, 0 to 1.")
@click.option("--comment", default="", help="One short sentence for the teacher: why.")
@click.option("--model", default="", help="Your model name, shown to the teacher.")
def grade_command(
    task_id: str, labels: tuple[str, ...], confidence: float, comment: str, model: str
) -> None:
    """Suggest a grade: choose rubric options; Opengrader works out the points."""
    try:
        suggestion = record_grade(
            _packet(), task_id, labels, confidence=confidence, rationale=comment, model=model
        )
    except AgentError as exc:
        _fail(str(exc))
    print(f"{task_id}: {suggestion.points_awarded:g} points ({', '.join(suggestion.rubric_labels)})")


@command.command("transcribe")
@click.argument("task_id")
@click.option("--text", required=True, help="Exactly what the student wrote.")
def transcribe_command(task_id: str, text: str) -> None:
    """Record what the student actually wrote, read from the answer image."""
    try:
        record_transcription(_packet(), task_id, text)
    except AgentError as exc:
        _fail(str(exc))
    print(f"{task_id}: transcription saved")


@command.command("record")
@click.argument("results", type=click.File("r", encoding="utf-8"))
@click.option("--model", default="", help="Model name for lines that do not give one.")
def record_command(results, model: str) -> None:  # noqa: ANN001
    """Record a results file (one JSON object a line; '-' reads stdin)."""
    outcomes = record_results(_packet(), results, model=model)
    failed = [outcome for outcome in outcomes if not outcome.ok]
    for outcome in outcomes:
        mark = "ok " if outcome.ok else "ERR"
        print(f"{mark} line {outcome.line} {outcome.task}: {outcome.message}")
    print(f"\n{len(outcomes) - len(failed)} recorded, {len(failed)} refused.")
    if failed:
        sys.exit(1)


@command.command("apply")
@click.option("--submissions", "raw_submissions", default="", help="Your class's submissions folder.")
@click.option("--workspace", "raw_workspace", default="", help=_WORKSPACE_HELP)
@click.option("--assignment", "raw_assignment", default="", help=_ASSIGNMENT_HELP)
@click.option("--incoming", "raw_incoming", required=True, help="The packet folder the assistant worked in.")
@click.option("--yes", is_flag=True, help="Actually write. Without it the merge is only previewed.")
@click.option("--delete-copy", is_flag=True, help="Delete the assistant's copy once the merge is written.")
@click.pass_context
def apply_command(
    ctx: click.Context,
    raw_submissions: str,
    raw_workspace: str,
    raw_assignment: str,
    raw_incoming: str,
    yes: bool,
    delete_copy: bool,
) -> None:
    """Merge the assistant's suggestions back (run by the teacher).

    The same three-way merge as 'opengrader grade-merge apply': an answer you graded
    meanwhile is never overwritten, and 'opengrader grade-restore' can undo it. In a
    workspace such an answer gets a change request instead, for you to
    approve or dismiss (Q while grading).
    """
    from opengrader.cli.grade_merge import apply_command as merge_apply

    if raw_workspace:
        _apply_to_workspace(raw_workspace, raw_assignment, raw_incoming, yes=yes, delete_copy=delete_copy)
        return
    if not raw_submissions:
        _fail("give --submissions (a class folder) or --workspace")

    ctx.invoke(
        merge_apply,
        raw_submissions=raw_submissions,
        raw_incoming=raw_incoming,
        raw_key="",
        packet="",
        conflicts="skip",
        yes=yes,
        import_history=False,
    )
    if yes:
        print(
            "\nThe assistant's grades are marked as AI grades. In the grading app, "
            "press v on a question to step through the ones not yet confirmed and "
            "y to confirm each (or pick another rubric option to change it). "
            "opengrader grade-check and the review queue flag low-confidence ones."
        )
        pending = [s for s in list_suggestions(Path(raw_incoming)) if not s.decision]
        if pending:
            print(
                f"\nThe assistant also suggested {len(pending)} fix(es) to the answer key; "
                f"see them with: opengrader agent suggestions --incoming {shlex.quote(raw_incoming)}"
            )
        if delete_copy:
            try:
                delete_packet(raw_incoming, raw_submissions)
            except AgentError as exc:
                _fail(str(exc))
            print(f"Deleted the copy at {raw_incoming}.")


def _apply_to_workspace(
    raw_workspace: str, raw_assignment: str, raw_incoming: str, *, yes: bool, delete_copy: bool
) -> None:
    from opengrader.core.agent.workspace import (
        apply_workspace_plan,
        delete_workspace_packet,
        plan_workspace_apply,
    )

    root, assignment_id = _workspace_target(raw_workspace, raw_assignment)
    try:
        plan = plan_workspace_apply(root, assignment_id, raw_incoming)
    except AgentError as exc:
        _fail(str(exc))
    for line in plan.lines():
        print(line)
    if not yes:
        print("\nNothing was written. Add --yes to merge it.")
        return
    try:
        outcome = apply_workspace_plan(plan, _workspace_request(root))
    except AgentError as exc:
        _fail(str(exc))
    print()
    for line in outcome.lines():
        print(line)
    print(
        "\nThe assistant's grades are marked as AI grades: press v on a question while "
        "grading to step through them and y to confirm each."
    )
    pending = [s for s in list_suggestions(plan.packet) if not s.decision]
    if pending:
        print(
            f"\nThe assistant also suggested {len(pending)} fix(es) to the answer key; see them with: "
            f"opengrader agent suggestions --incoming {shlex.quote(raw_incoming)}"
        )
    if delete_copy:
        try:
            delete_workspace_packet(plan.packet, root, assignment_id)
        except AgentError as exc:
            _fail(str(exc))
        print(f"Deleted the copy at {raw_incoming}.")


@command.command("delete")
@click.option("--submissions", "raw_submissions", default="", help="Your class's submissions folder.")
@click.option("--workspace", "raw_workspace", default="", help=_WORKSPACE_HELP)
@click.option("--assignment", "raw_assignment", default="", help=_ASSIGNMENT_HELP)
@click.option("--incoming", "raw_incoming", required=True, help="The assistant's copy to delete.")
@click.option("--yes", is_flag=True, help="Really delete it.")
def delete_command(raw_submissions: str, raw_workspace: str, raw_assignment: str, raw_incoming: str, yes: bool) -> None:
    """Delete an assistant's copy once its work is merged back (run by the teacher)."""
    if not yes:
        _fail("deleting a copy cannot be undone; add --yes once its work is merged back")
    try:
        if raw_workspace:
            from opengrader.core.agent.workspace import delete_workspace_packet

            root, assignment_id = _workspace_target(raw_workspace, raw_assignment)
            delete_workspace_packet(raw_incoming, root, assignment_id)
        elif raw_submissions:
            delete_packet(raw_incoming, raw_submissions)
        else:
            _fail("give --submissions (a class folder) or --workspace")
    except AgentError as exc:
        _fail(str(exc))
    print(f"Deleted {raw_incoming}.")


# -- the answer key -----------------------------------------------------------


@command.command("check-key")
@click.argument("qa_file", default="qa.md")
def check_key_command(qa_file: str) -> None:
    """Check an answer key: errors stop it loading, warnings are probably mistakes."""
    result = check_key_file(qa_file)
    for line in result.lines():
        print(line)
    if result.ok:
        print(f"{qa_file} loads" + (f", with {len(result.warnings)} warning(s)." if result.warnings else ", with no warnings."))
    else:
        sys.exit(1)


@command.command("suggest")
@click.argument("question")
@click.option("--kind", type=click.Choice(("answer", "rubric", "question", "other")), required=True)
@click.option("--message", required=True, help="What is wrong, for the teacher.")
@click.option("--version", default="", help="Only for an answer key with versions.")
@click.option("--correct", multiple=True, help="(--kind answer) An answer the key should accept. Repeat for several.")
@click.option("--partial", multiple=True, help='(--kind rubric) A partial-credit option as "label=points". Repeat; a checklist must add up.')
@click.option("--model", default="", help="Your model name.")
def suggest_command(
    question: str, kind: str, message: str, version: str, correct: tuple[str, ...], partial: tuple[str, ...], model: str
) -> None:
    """Tell the teacher the answer key looks wrong, with a fix they can accept."""
    try:
        made = suggest(
            _packet(),
            question=question,
            kind=kind,
            message=message,
            version=version,
            correct=correct,
            partial=parse_partial(partial),
            model=model,
        )
    except AgentError as exc:
        _fail(str(exc))
    print(f"Suggestion {made.id} recorded for the teacher. Leave {question} ungraded until they decide.")


def _incoming(raw: str) -> Path:
    path = Path(raw).expanduser() if raw else _packet()
    if not path.is_dir():
        _fail(f"no copy at {path}")
    return path


@command.command("suggestions")
@click.option("--incoming", "raw_incoming", default="", help="The assistant's copy (default: the one you are in).")
@click.option("--json", "as_json", is_flag=True)
def suggestions_command(raw_incoming: str, as_json: bool) -> None:
    """The fixes to the answer key the assistant suggested."""
    found = list_suggestions(_incoming(raw_incoming))
    if as_json:
        print(json.dumps([s.to_json() for s in found], indent=2, ensure_ascii=False))
        return
    if not found:
        print("No suggestions.")
        return
    for item in found:
        state = f"[{item.decision}]" if item.decision else "[open]"
        version = f" (version {item.version})" if item.version else ""
        print(f"{item.id} {state} {item.kind} · {item.question}{version}: {item.message}")
        if item.fixable:
            print(f"    fix: {item.describe_fix()}")


@command.command("accept")
@click.argument("suggestion_id")
@click.option("--incoming", "raw_incoming", required=True, help="The assistant's copy.")
@click.option("--qa-file", "raw_qa", default="", help="Your class's answer key, which is changed.")
@click.option("--submissions", "raw_submissions", default="", help="Your class's submissions folder.")
@click.option("--workspace", "raw_workspace", default="", help=_WORKSPACE_HELP)
@click.option("--assignment", "raw_assignment", default="", help=_ASSIGNMENT_HELP)
@click.option("--key-file", type=click.Choice(("update", "leave")), default=None, help=_KEY_FILE_HELP)
def accept_command(
    suggestion_id: str,
    raw_incoming: str,
    raw_qa: str,
    raw_submissions: str,
    raw_workspace: str,
    raw_assignment: str,
    key_file: str | None,
) -> None:
    """Apply a suggested fix to your answer key (run by the teacher)."""
    try:
        if raw_workspace:
            from opengrader.core.agent.workspace import accept_workspace_suggestion

            root, assignment_id = _workspace_target(raw_workspace, raw_assignment)
            done = accept_workspace_suggestion(
                _incoming(raw_incoming),
                suggestion_id,
                root=root,
                assignment_id=assignment_id,
                request=_workspace_request(root),
                key_file=key_file,  # type: ignore[arg-type]
            )
        elif raw_qa and raw_submissions:
            done = accept_suggestion(
                _incoming(raw_incoming), suggestion_id, qa_file=raw_qa, submissions=raw_submissions
            )
        else:
            _fail("give --qa-file and --submissions (a class folder), or --workspace")
    except (AgentError, ValueError) as exc:
        _fail(str(exc))
    print(f"{suggestion_id}: {done}. The copy now has the corrected key.")


@command.command("dismiss")
@click.argument("suggestion_id")
@click.option("--incoming", "raw_incoming", required=True, help="The assistant's copy.")
def dismiss_command(suggestion_id: str, raw_incoming: str) -> None:
    """Turn down a suggested fix (run by the teacher)."""
    try:
        dismiss_suggestion(_incoming(raw_incoming), suggestion_id)
    except AgentError as exc:
        _fail(str(exc))
    print(f"{suggestion_id} dismissed.")


@command.command("answer-key")
@click.option("--class-folder", "raw_class", default="", help="The class folder: its qa.md (if any) goes to the assistant.")
@click.option("--source", "raw_source", default="", help="Your own answer key (.docx, .pdf, .md, .txt), if you have one.")
@click.option("--workspace", "raw_workspace", default="", help=_WORKSPACE_HELP)
@click.option("--assignment", "raw_assignment", default="", help=_ASSIGNMENT_HELP)
def answer_key_command(raw_class: str, raw_source: str, raw_workspace: str, raw_assignment: str) -> None:
    """A folder where an assistant writes or fills qa.md (no student data)."""
    from opengrader.core.agent.answer_key import prepare_key_folder
    from opengrader.core.class_folder import scan_class_folder

    if raw_workspace:
        from opengrader.core.agent.workspace import prepare_workspace_key_folder

        root, assignment_id = _workspace_target(raw_workspace, raw_assignment)
        try:
            folder = prepare_workspace_key_folder(root, assignment_id, source=raw_source or None)
        except AgentError as exc:
            _fail(str(exc))
        print(f"Made {folder.path}")
        print("Start your AI assistant inside it and ask it to do what AGENTS.md says; it holds no student work.")
        print(
            "When it is done, store the key in the workspace (choose whether its qa.md file follows):\n"
            f"  opengrader agent use-key --folder {shlex.quote(str(folder.path))} "
            f"{_workspace_flags(root, assignment_id)} --key-file update"
        )
        return

    qa_file = submissions = None
    if raw_class:
        try:
            found = scan_class_folder(raw_class)
        except (OSError, NotADirectoryError) as exc:
            _fail(str(exc))
        qa_file, submissions = found.qa_file, found.submissions
    try:
        folder = prepare_key_folder(
            qa_file=qa_file,
            source=raw_source or None,
            class_folder=raw_class or None,
            submissions=submissions,
        )
    except AgentError as exc:
        _fail(str(exc))
    print(f"Made {folder.path}")
    print("Start your AI assistant inside it and ask it to do what AGENTS.md says; it holds no student work.")
    target = qa_file or (Path(raw_class) / "qa.md" if raw_class else Path("<class folder>/qa.md"))
    print(
        "When it is done, copy the key into your class folder:\n"
        f"  opengrader agent use-key --folder {shlex.quote(str(folder.path))} --to {shlex.quote(str(target))}"
    )


@command.command("use-key")
@click.option("--folder", "raw_folder", required=True, help="The assistant's answer-key folder.")
@click.option("--to", "raw_to", default="", help="Where the answer key goes (your class folder's qa.md).")
@click.option("--workspace", "raw_workspace", default="", help=_WORKSPACE_HELP)
@click.option("--assignment", "raw_assignment", default="", help=_ASSIGNMENT_HELP)
@click.option("--key-file", type=click.Choice(("update", "leave")), default=None, help=_KEY_FILE_HELP)
@click.option("--replace", is_flag=True, help="Replace an answer key already there.")
def use_key_command(
    raw_folder: str, raw_to: str, raw_workspace: str, raw_assignment: str, key_file: str | None, replace: bool
) -> None:
    """Copy the assistant's finished answer key into your class folder (or workspace)."""
    if raw_workspace:
        from opengrader.core.agent.workspace import use_workspace_key

        root, assignment_id = _workspace_target(raw_workspace, raw_assignment)
        try:
            find_packet_root(raw_folder, kind=KIND_ANSWER_KEY)
            result, note = use_workspace_key(
                raw_folder,
                root=root,
                assignment_id=assignment_id,
                request=_workspace_request(root),
                replace_changed=replace,
                key_file=key_file,  # type: ignore[arg-type]
            )
        except AgentError as exc:
            _fail(str(exc))
        print(note)
        for warning in result.warnings:
            print(f"  warning: {warning}")
        return
    if not raw_to:
        _fail("give --to (your class folder's qa.md) or --workspace")
    try:
        find_packet_root(raw_folder, kind=KIND_ANSWER_KEY)
        result = use_key(raw_folder, raw_to, replace=replace)
    except AgentError as exc:
        _fail(str(exc))
    print(f"Copied the answer key to {raw_to}.")
    for warning in result.warnings:
        print(f"  warning: {warning}")


# -- assistants without a shell -----------------------------------------------


@command.command("mcp")
@click.option("--packet", "raw_packet", required=True, help="The assistant's copy this server gives access to.")
def mcp_command(raw_packet: str) -> None:
    """Serve one copy over MCP on stdin/stdout (started by the assistant app)."""
    packet = Path(raw_packet).expanduser().resolve()
    try:
        find_packet_root(packet)
        serve(packet)
    except AgentError as exc:
        _fail(str(exc))


@command.command("mcp-config")
@click.option("--packet", "raw_packet", required=True, help="The assistant's copy.")
def mcp_config_command(raw_packet: str) -> None:
    """What to paste into Claude Desktop, ChatGPT or another MCP app to give it one copy."""
    packet = Path(raw_packet).expanduser().resolve()
    try:
        find_packet_root(packet)
    except AgentError as exc:
        _fail(str(exc))
    print(client_instructions(packet))


@click.command("ai-readme", context_settings=CONTEXT_SETTINGS)
@click.option("--local", "which", flag_value="local-llm.md", help="How to set up a local model (Ollama) by hand.")
@click.option("--packet", "which", flag_value="agent-packet.md", help="The instructions put in every assistant packet.")
@click.option("--format", "which", flag_value="qa-format.md", help="The answer-key (qa.md) format.")
def ai_readme_command(which: str | None) -> None:
    """What an AI assistant needs to know to use Opengrader safely.

    Give it to your assistant (Claude, ChatGPT, Codex, …), or read it yourself.
    """
    from opengrader.core.agent import guide_text

    print(guide_text(which or "ai.md"), end="")


if __name__ == "__main__":
    command()
