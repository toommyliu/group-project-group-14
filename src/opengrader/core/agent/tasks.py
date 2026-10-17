"""What the assistant sees and records inside a packet: tasks, grades, readings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.grading.llm_grader import (
    GradeSuggestion,
    RubricChoice,
    format_ai_analysis,
    parse_ai_analysis,
    points_for_selection,
)
from opengrader.core.grading.solutions import solution_entries_for_submission
from opengrader.core.handoff.packet import PACKET_SUBMISSIONS_DIRNAME, PacketError
from opengrader.core.qa_file import QADocument
from opengrader.core.submissions.printed_text import response_without_printed_text
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_markdown,
)
from opengrader.core.submissions.transcription import SOURCE_HUMAN, SOURCE_VISION_MODEL

from .common import (
    AGENT_PROVIDER,
    DEFAULT_AGENT_MODEL,
    DEFAULT_CONFIDENCE,
    PURPOSE_RUBRICS,
    AgentError,
    packet_purpose,
    packet_qa as _packet_qa,
)

@dataclass(frozen=True)
class AgentTask:
    id: str
    alias: str
    question_id: str
    question_type: str
    prompt: str
    possible_points: float
    expected_answers: tuple[str, ...]
    rubric: tuple[RubricChoice, ...]
    response_text: str
    read_by: str
    response_image: str
    status: str  # "ungraded", "graded-by-agent", "graded"
    points: float | None
    rubric_labels: tuple[str, ...]
    comment: str
    # "name" / "student id" when the answer image was held back because
    # Tesseract found that in it (`crop_check`); the teacher grades it.
    image_withheld: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "question": self.question_id,
            "type": self.question_type,
            "prompt": self.prompt,
            "points_possible": self.possible_points,
            "correct_answers": list(self.expected_answers),
            "rubric": [
                {
                    "label": option.label,
                    "points": option.points_delta,
                    "kind": option.kind,
                    "selection": option.selection_mode,
                }
                for option in self.rubric
            ],
            "response_text": self.response_text,
            "read_by": self.read_by,
            "response_image": self.response_image,
            "image_withheld": (
                (
                    "the image could not be checked for the student's name, "
                    if self.image_withheld == "unreadable image"
                    else f"the image may show the student's {self.image_withheld}, "
                )
                + "so it stayed with the teacher: grade from the text only if it is "
                "clearly enough, otherwise leave this one"
                if self.image_withheld
                else ""
            ),
            "status": self.status,
            "points": self.points,
            "chosen": list(self.rubric_labels),
            "comment": self.comment,
        }


def _task_status(points: float | None, analysis: str) -> str:
    if points is None:
        return "ungraded"
    tag = parse_ai_analysis(analysis)
    if tag is not None and tag.provider == AGENT_PROVIDER and not tag.reviewed:
        return "graded-by-agent"
    return "graded"


WITHHELD_FILENAME = "withheld-images.json"


def write_withheld(packet: Path, withheld: Mapping[str, str]) -> None:
    (packet / ".opengrader" / WITHHELD_FILENAME).write_text(
        json.dumps(dict(withheld), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_withheld(packet: Path) -> dict[str, str]:
    try:
        data = json.loads((packet / ".opengrader" / WITHHELD_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _tasks_for(
    document: StudentSubmissionDocument,
    qa: QADocument,
    withheld: Mapping[str, str] | None = None,
) -> list[AgentTask]:
    solutions = solution_entries_for_submission(qa, document)
    tasks: list[AgentTask] = []
    for question in document.questions:
        entry = solutions.get(question.question_id)
        if entry is None:
            continue
        response = response_without_printed_text(
            question.response_text or "",
            source=question.response_source,
            printed=entry.printed_text,
        ).strip()
        image = question.response_image_path or ""
        tasks.append(
            AgentTask(
                id=f"{document.student_id}/{question.question_id}",
                alias=document.student_id,
                question_id=question.question_id,
                question_type=str(question.question_type),
                prompt=entry.prompt or question.prompt,
                possible_points=entry.points,
                expected_answers=tuple(entry.expected_answers),
                rubric=tuple(entry.rubric_options),
                response_text=response,
                read_by=question.response_source or "",
                response_image=image if image and Path(image).is_file() else "",
                status=_task_status(question.points_awarded, question.analysis or ""),
                points=question.points_awarded,
                rubric_labels=tuple(question.rubric_labels),
                comment=question.analysis or "",
                image_withheld=(withheld or {}).get(
                    f"{document.student_id}/{question.question_id}", ""
                ),
            )
        )
    return tasks


def _documents(packet: Path) -> list[tuple[Path, StudentSubmissionDocument]]:
    return [
        (path, load_student_submission_markdown(path))
        for path in discover_submission_paths(packet / PACKET_SUBMISSIONS_DIRNAME)
    ]


def load_tasks(packet: str | Path, *, question: str = "") -> list[AgentTask]:
    root = Path(packet)
    qa = _packet_qa(root)
    withheld = read_withheld(root)
    tasks: list[AgentTask] = []
    for _path, document in _documents(root):
        tasks.extend(
            task
            for task in _tasks_for(document, qa, withheld)
            if not question or task.question_id == question
        )
    # Question by question, the way a person grades: one rubric in mind at a time.
    order = {qid: index for index, qid in enumerate(dict.fromkeys(t.question_id for t in tasks))}
    tasks.sort(key=lambda task: (order[task.question_id], task.alias))
    return tasks


@dataclass(frozen=True)
class PacketStatus:
    students: int
    ungraded: int
    graded_by_agent: int
    graded: int
    unknown_version: tuple[str, ...]


def packet_status(packet: str | Path) -> PacketStatus:
    root = Path(packet)
    qa = _packet_qa(root)
    withheld = read_withheld(root)
    counts = {"ungraded": 0, "graded-by-agent": 0, "graded": 0}
    unknown: list[str] = []
    documents = _documents(root)
    for _path, document in documents:
        tasks = _tasks_for(document, qa, withheld)
        if not tasks and qa.version_questions:
            unknown.append(document.student_id)
        for task in tasks:
            counts[task.status] += 1
    return PacketStatus(
        students=len(documents),
        ungraded=counts["ungraded"],
        graded_by_agent=counts["graded-by-agent"],
        graded=counts["graded"],
        unknown_version=tuple(unknown),
    )


# -- recording ------------------------------------------------------------


def _locate(packet: Path, task_id: str) -> tuple[Path, AgentTask]:
    alias, _, question_id = task_id.strip().partition("/")
    if not alias or not question_id:
        raise AgentError(f"{task_id!r} is not a task id (they look like og-1a2b3c4d/q2)")
    qa = _packet_qa(packet)
    withheld = read_withheld(packet)
    for path, document in _documents(packet):
        if document.student_id != alias:
            continue
        for task in _tasks_for(document, qa, withheld):
            if task.question_id == question_id:
                return path, task
        raise AgentError(f"{alias} has no gradable question {question_id!r}")
    raise AgentError(f"no student {alias!r} in this packet")


def _choose(task: AgentTask, labels: Iterable[str]) -> list[RubricChoice]:
    exact = {option.label: option for option in task.rubric}
    folded = {option.label.casefold().strip(): option for option in task.rubric}
    chosen: list[RubricChoice] = []
    for raw in labels:
        option = exact.get(raw) or folded.get(raw.casefold().strip())
        if option is None:
            offered = "; ".join(f'"{o.label}"' for o in task.rubric)
            raise AgentError(f"{raw!r} is not a rubric option for {task.id}. Options: {offered}")
        if option not in chosen:
            chosen.append(option)
    if not chosen:
        raise AgentError(f"choose at least one rubric option for {task.id}")
    exclusive = [o for o in chosen if o.kind != "bonus" and o.selection_mode == "exclusive"]
    if len(exclusive) > 1:
        raise AgentError(
            f"{task.id}: pick one of the exclusive options, not "
            + " and ".join(f'"{o.label}"' for o in exclusive)
        )
    return chosen


def record_grade(
    packet: str | Path,
    task_id: str,
    rubric_labels: Sequence[str],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    rationale: str = "",
    model: str = "",
) -> GradeSuggestion:
    root = Path(packet)
    if packet_purpose(root) == PURPOSE_RUBRICS:
        raise AgentError(
            "this copy is for drafting rubrics, not grading: suggest the rubric with "
            "'agent suggest <question> --kind rubric'"
        )
    path, task = _locate(root, task_id)
    if task.status == "graded":
        raise AgentError(
            f"{task.id} was already graded by the teacher or by the rules; "
            "an assistant never changes that grade"
        )
    if not 0.0 <= confidence <= 1.0:
        raise AgentError("confidence is a number from 0 to 1")
    chosen = _choose(task, rubric_labels)
    suggestion = GradeSuggestion(
        rubric_labels=tuple(option.label for option in chosen),
        points_awarded=points_for_selection(chosen, task.rubric, task.possible_points),
        confidence=confidence,
        rationale=" ".join(rationale.split()),
    )
    content = path.read_text(encoding="utf-8")
    path.write_text(
        update_student_submission_markdown(
            content,
            StudentSubmissionGradeUpdate(
                question_id=task.question_id,
                possible_points=task.possible_points,
                points_awarded=suggestion.points_awarded,
                rubric_labels=suggestion.rubric_labels,
                analysis=format_ai_analysis(
                    suggestion,
                    model=model.strip() or DEFAULT_AGENT_MODEL,
                    provider=AGENT_PROVIDER,
                ),
            ),
        ),
        encoding="utf-8",
    )
    return suggestion


def record_transcription(
    packet: str | Path, task_id: str, text: str, *, model: str = ""
) -> None:
    root = Path(packet)
    path, task = _locate(root, task_id)
    if not text.strip():
        raise AgentError("a transcription needs the text the student wrote")
    if task.read_by == SOURCE_HUMAN:
        raise AgentError(f"{task.id} was typed in by the teacher; an assistant never replaces it")
    del model  # `Read by` names the reader kind and provider, not the model
    content = path.read_text(encoding="utf-8")
    path.write_text(
        update_student_submission_markdown(
            content,
            StudentSubmissionGradeUpdate(
                question_id=task.question_id,
                response_text=text.strip(),
                clear_response_confidence=True,
                response_source=SOURCE_VISION_MODEL,
                response_provider=AGENT_PROVIDER,
            ),
        ),
        encoding="utf-8",
    )


@dataclass(frozen=True)
class ResultOutcome:
    line: int
    task: str
    ok: bool
    message: str


def record_results(
    packet: str | Path, lines: Iterable[str], *, model: str = ""
) -> list[ResultOutcome]:
    """Results written by an assistant that cannot run commands, one JSON object a line.

    `{"task": "og-…/q2", "transcription": "…", "rubric": ["…"], "confidence": 0.9,
    "comment": "…"}` — transcription and rubric are each optional. A bad line
    is reported and skipped; the others are still recorded.
    """
    outcomes: list[ResultOutcome] = []
    for number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        task_id = ""
        try:
            data = json.loads(raw)
            if not isinstance(data, Mapping):
                raise AgentError("each line must be a JSON object")
            task_id = str(data.get("task", ""))
            done: list[str] = []
            text = data.get("transcription")
            if isinstance(text, str) and text.strip():
                record_transcription(packet, task_id, text, model=model)
                done.append("transcription")
            labels = data.get("rubric")
            if labels is not None:
                if isinstance(labels, str):
                    labels = [labels]
                if not isinstance(labels, list):
                    raise AgentError("rubric must be a list of option labels")
                confidence = data.get("confidence", DEFAULT_CONFIDENCE)
                suggestion = record_grade(
                    packet,
                    task_id,
                    [str(label) for label in labels],
                    confidence=float(confidence),
                    rationale=str(data.get("comment", "")),
                    model=str(data.get("model", "")) or model,
                )
                done.append(f"{suggestion.points_awarded:g} points")
            if not done:
                raise AgentError("nothing to record: give rubric and/or transcription")
            outcomes.append(ResultOutcome(number, task_id, True, ", ".join(done)))
        except (AgentError, ValueError, TypeError, PacketError) as exc:
            outcomes.append(ResultOutcome(number, task_id, False, str(exc)))
    return outcomes
