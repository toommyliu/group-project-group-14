"""The AI assistant page of the browser app: `core/agent/workspace.py` over HTTP.

The same jobs as the terminal app's dialogs for a workspace assignment —
make a redacted copy, merge it back, accept or dismiss the fixes it
suggests, give it the answer key and store the key it writes — for the
account that owns the workspace only. The assistant itself still runs on
the teacher's computer, in the folder this page names: the browser shows
where to open it and what to say, never anything of the copy's contents.

Copies are named by their folder name and found among the ones this
assignment made (`list_workspace_packets`), so no request can point the
service at an arbitrary path. A class folder open in the browser (a folder
session) is refused: its files are the truth, and classic mode's assistant
works on them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from opengrader.core.workspace.folder_session import load_folder_session
from opengrader.core.workspace.models import Viewer
from opengrader.core.workspace.store import Workspace

Purpose = Literal["grading", "rubrics"]
KeyFileChoice = Literal["update", "leave"]


class AssistantCopy(BaseModel):
    name: str
    path: str
    purpose: Purpose
    students: int
    ungraded: int
    graded_by_agent: int
    open_suggestions: int
    # What to paste into an MCP app (Claude Desktop, ChatGPT, …) instead.
    mcp_setup: str


class AssistantKeyFolder(BaseModel):
    name: str
    path: str
    has_key: bool


class AssistantPrompts(BaseModel):
    grading: str
    rubrics: str
    key: str


class AssistantOverview(BaseModel):
    title: str
    submission_count: int
    unanswered: int
    rubric_gaps: int
    can_grade: bool
    can_draft_rubrics: bool
    # The assignment's qa.md, when it has one: accepting a fix asks whether
    # it follows.
    key_file: str | None
    output_dir: str
    copies: list[AssistantCopy]
    key_folders: list[AssistantKeyFolder]
    prompts: AssistantPrompts


class MakeCopy(BaseModel):
    purpose: Purpose = "grading"
    questions: list[str] = Field(default_factory=list, max_length=200)
    with_graded: bool = False


class AssistantLines(BaseModel):
    lines: list[str]
    grades: int = 0
    requests: int = 0


class AssistantSuggestion(BaseModel):
    id: str
    kind: str
    question: str
    version: str
    message: str
    fix: str
    fixable: bool
    decision: str


class AcceptFix(BaseModel):
    key_file: KeyFileChoice | None = None


class UseKey(BaseModel):
    name: str
    key_file: KeyFileChoice | None = None
    replace: bool = False


def add_assistant_routes(
    api: APIRouter,
    workspace: Workspace,
    require_assignment: Callable[[str], None],
    require_owner: Callable[[Request, str], Viewer],
    in_process: Callable[[Viewer], Callable[..., bytes]],
) -> None:
    from opengrader.core.agent import AgentError
    from opengrader.core.agent import workspace as agent
    from opengrader.core.agent.common import GRADING_PROMPT, KEY_PROMPT, RUBRIC_PROMPT, packet_purpose
    from opengrader.core.handoff.packet import PacketError

    def owner(request: Request, assignment_id: str) -> Viewer:
        who = require_owner(request, "give its work to an AI assistant")
        require_assignment(assignment_id)
        if load_folder_session(workspace.root) is not None:
            raise HTTPException(
                409,
                "This browser session grades a class folder, whose grades are its files: "
                "use the AI assistant card in classic mode for it.",
            )
        return who

    def refused(exc: Exception) -> HTTPException:
        return HTTPException(422, str(exc))

    def find_copy(assignment_id: str, name: str) -> Path:
        for path in agent.list_workspace_packets(workspace.root, assignment_id):
            if path.name == name:
                return path
        raise HTTPException(404, f"No copy called {name} was made from this assignment.")

    def describe_copy(path: Path) -> AssistantCopy:
        from opengrader.core.agent import client_instructions, list_suggestions, packet_status

        status = packet_status(path)
        return AssistantCopy(
            name=path.name,
            path=str(path),
            purpose="rubrics" if packet_purpose(path) == "rubrics" else "grading",
            students=status.students,
            ungraded=status.ungraded,
            graded_by_agent=status.graded_by_agent,
            open_suggestions=sum(1 for item in list_suggestions(path) if not item.decision),
            mcp_setup=client_instructions(path),
        )

    @api.get("/assignments/{assignment_id}/assistant")
    def assistant_overview(assignment_id: str, request: Request) -> AssistantOverview:
        """What the assignment is ready for, and the copies and key folders made so far."""
        owner(request, assignment_id)
        try:
            state = agent.read_assignment(workspace.root, assignment_id)
        except AgentError as exc:
            raise refused(exc) from exc
        copies = []
        for path in agent.list_workspace_packets(workspace.root, assignment_id):
            try:
                copies.append(describe_copy(path))
            except (OSError, ValueError, AgentError):
                continue  # half deleted by hand: not offered
        return AssistantOverview(
            title=state.title,
            submission_count=state.submission_count,
            unanswered=state.unanswered,
            rubric_gaps=state.rubric_gaps,
            can_grade=state.can_grade,
            can_draft_rubrics=state.can_draft_rubrics,
            key_file=str(state.key_file) if state.key_file else None,
            output_dir=str(agent.default_workspace_output_dir(workspace.root)),
            copies=copies,
            key_folders=[
                AssistantKeyFolder(name=path.name, path=str(path), has_key=(path / "qa.md").is_file())
                for path in agent.workspace_key_folders(workspace.root, assignment_id)
            ],
            prompts=AssistantPrompts(grading=GRADING_PROMPT, rubrics=RUBRIC_PROMPT, key=KEY_PROMPT),
        )

    @api.post("/assignments/{assignment_id}/assistant/copies", status_code=201)
    def make_copy(assignment_id: str, body: MakeCopy, request: Request) -> AssistantCopy:
        """A redacted copy for the assistant (every answer image is read for
        the student's name first, so this takes a moment)."""
        owner(request, assignment_id)
        try:
            packet = agent.prepare_workspace_packet(
                workspace.root,
                assignment_id,
                purpose=body.purpose,
                questions=body.questions,
                include_graded=body.with_graded,
            )
        except (AgentError, PacketError, ValueError) as exc:
            raise refused(exc) from exc
        return describe_copy(packet.path)

    @api.delete("/assignments/{assignment_id}/assistant/copies/{name}", status_code=204)
    def delete_copy(assignment_id: str, name: str, request: Request) -> None:
        owner(request, assignment_id)
        try:
            agent.delete_workspace_packet(find_copy(assignment_id, name), workspace.root, assignment_id)
        except AgentError as exc:
            raise refused(exc) from exc

    def plan(assignment_id: str, name: str):  # noqa: ANN202
        try:
            return agent.plan_workspace_apply(workspace.root, assignment_id, find_copy(assignment_id, name))
        except AgentError as exc:
            raise refused(exc) from exc

    @api.get("/assignments/{assignment_id}/assistant/copies/{name}/merge")
    def preview_merge(assignment_id: str, name: str, request: Request) -> AssistantLines:
        """What merging the copy back would do; nothing is written."""
        owner(request, assignment_id)
        found = plan(assignment_id, name)
        return AssistantLines(lines=found.lines(), grades=len(found.grades), requests=found.requests)

    @api.post("/assignments/{assignment_id}/assistant/copies/{name}/merge")
    def merge_copy(assignment_id: str, name: str, request: Request) -> AssistantLines:
        """Merge it back: AI grades where nobody graded, change requests elsewhere."""
        who = owner(request, assignment_id)
        found = plan(assignment_id, name)
        try:
            outcome = agent.apply_workspace_plan(found, in_process(who))
        except AgentError as exc:
            raise refused(exc) from exc
        return AssistantLines(lines=outcome.lines(), grades=outcome.count("saved"), requests=outcome.count("requested"))

    @api.get("/assignments/{assignment_id}/assistant/copies/{name}/suggestions")
    def copy_suggestions(assignment_id: str, name: str, request: Request) -> list[AssistantSuggestion]:
        from opengrader.core.agent import list_suggestions

        owner(request, assignment_id)
        return [
            AssistantSuggestion(
                id=item.id,
                kind=item.kind,
                question=item.question,
                version=item.version,
                message=item.message,
                fix=item.describe_fix(),
                fixable=item.fixable,
                decision=item.decision,
            )
            for item in list_suggestions(find_copy(assignment_id, name))
        ]

    @api.post("/assignments/{assignment_id}/assistant/copies/{name}/suggestions/{suggestion_id}/accept")
    def accept_fix(
        assignment_id: str, name: str, suggestion_id: str, body: AcceptFix, request: Request
    ) -> AssistantLines:
        """Store the fix in the workspace's key, and in qa.md when `key_file` says so."""
        who = owner(request, assignment_id)
        try:
            done = agent.accept_workspace_suggestion(
                find_copy(assignment_id, name),
                suggestion_id,
                root=workspace.root,
                assignment_id=assignment_id,
                request=in_process(who),
                key_file=body.key_file,
            )
        except (AgentError, ValueError) as exc:
            raise refused(exc) from exc
        return AssistantLines(lines=[f"{done}."])

    @api.post("/assignments/{assignment_id}/assistant/copies/{name}/suggestions/{suggestion_id}/dismiss")
    def dismiss_fix(assignment_id: str, name: str, suggestion_id: str, request: Request) -> AssistantLines:
        from opengrader.core.agent import dismiss_suggestion

        owner(request, assignment_id)
        try:
            dismiss_suggestion(find_copy(assignment_id, name), suggestion_id)
        except AgentError as exc:
            raise refused(exc) from exc
        return AssistantLines(lines=[f"{suggestion_id} dismissed."])

    @api.post("/assignments/{assignment_id}/assistant/key-folders", status_code=201)
    def make_key_folder(assignment_id: str, request: Request) -> AssistantKeyFolder:
        """A folder beside the workspace with the assignment's key and no student work."""
        owner(request, assignment_id)
        try:
            folder = agent.prepare_workspace_key_folder(workspace.root, assignment_id)
        except AgentError as exc:
            raise refused(exc) from exc
        return AssistantKeyFolder(name=folder.path.name, path=str(folder.path), has_key=folder.has_key)

    @api.post("/assignments/{assignment_id}/assistant/key-folders/use")
    def use_key_folder(assignment_id: str, body: UseKey, request: Request) -> AssistantLines:
        """Store the key the assistant wrote, checked first."""
        who = owner(request, assignment_id)
        folder = next(
            (p for p in agent.workspace_key_folders(workspace.root, assignment_id) if p.name == body.name),
            None,
        )
        if folder is None:
            raise HTTPException(404, f"No answer-key folder called {body.name} was made for this assignment.")
        try:
            result, note = agent.use_workspace_key(
                folder,
                root=workspace.root,
                assignment_id=assignment_id,
                request=in_process(who),
                replace_changed=body.replace,
                key_file=body.key_file,
            )
        except AgentError as exc:
            raise refused(exc) from exc
        return AssistantLines(lines=[note, *(f"Warning: {warning}" for warning in result.warnings)])
