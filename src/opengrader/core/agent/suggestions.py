"""What an assistant thinks is wrong with the answer key, and how to fix it.

While grading, an assistant notices things a teacher would want to know: a
correct answer that is not correct, a rubric that does not fit the answers
students actually gave, a question two readings of which are both fair. It
records each as a suggestion in the packet (`.opengrader/suggestions.jsonl`)
instead of acting on it: an assistant never edits the answer key.

Two kinds carry a fix the teacher can accept with one press:

* `answer` with `correct`: the answers (or, for multiple choice, letters)
  the key should accept. Written with `qa_file.answers.apply_answers`, which
  touches only that question.
* `rubric` with `partial`: a complete set of partial-credit options, the way
  `draft-rubric` writes one. Checked against the rubric rules when the
  assistant makes it, so it learns at once that checklist items must add up
  to the question's points; refused on acceptance if a grade already saved
  uses a label the new rubric would drop.

Accepting writes the class's answer key and refreshes the packet's copy of
it, so the assistant grades the rest with the corrected key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.qa_file.answers import answer_fields, apply_answers
from opengrader.core.qa_file.markdown import (
    QADocument,
    QARubricFlatOption,
    find_qa_question,
    flatten_rubric_options,
    parse_qa_markdown,
    render_qa_markdown,
    replace_qa_rubric_options,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown

from .common import AgentError, packet_qa_path

__all__ = [
    "SUGGESTION_KINDS",
    "Suggestion",
    "accept_suggestion",
    "describe_accepted",
    "dismiss_suggestion",
    "fixed_key",
    "list_suggestions",
    "parse_partial",
    "pending_fix",
    "record_accepted",
    "suggest",
]

SUGGESTION_KINDS = ("answer", "rubric", "question", "other")
SUGGESTIONS_FILENAME = "suggestions.jsonl"
DECISIONS_FILENAME = "suggestion-decisions.json"
_PARTIAL_PREFIX = "Partially Correct - "


@dataclass(frozen=True)
class Suggestion:
    id: str
    kind: str
    question: str
    version: str
    message: str
    correct: tuple[str, ...] = ()
    partial: tuple[tuple[str, float], ...] = ()
    model: str = ""
    created_at: str = ""
    decision: str = ""  # "", "accepted", "dismissed"

    @property
    def fixable(self) -> bool:
        return bool(self.correct or self.partial)

    def describe_fix(self) -> str:
        if self.correct:
            return "accept: " + "; ".join(self.correct)
        if self.partial:
            return "partial credit: " + "; ".join(
                f"{label} ({points:+g})" for label, points in self.partial
            )
        return ""

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "question": self.question,
            "version": self.version,
            "message": self.message,
            "correct": list(self.correct),
            "partial": [{"label": label, "points": points} for label, points in self.partial],
            "model": self.model,
            "created_at": self.created_at,
            "decision": self.decision,
        }


def _path(packet: Path, name: str) -> Path:
    return packet / ".opengrader" / name


def _decisions(packet: Path) -> dict[str, str]:
    try:
        data = json.loads(_path(packet, DECISIONS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def list_suggestions(packet: str | Path) -> list[Suggestion]:
    root = Path(packet)
    decisions = _decisions(root)
    found: list[Suggestion] = []
    try:
        lines = _path(root, SUGGESTIONS_FILENAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for raw in lines:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, Mapping):
            continue
        partial = tuple(
            (str(item.get("label", "")), float(item.get("points", 0)))
            for item in data.get("partial", []) or []
            if isinstance(item, Mapping)
        )
        found.append(
            Suggestion(
                id=str(data.get("id", "")),
                kind=str(data.get("kind", "other")),
                question=str(data.get("question", "")),
                version=str(data.get("version", "")),
                message=str(data.get("message", "")),
                correct=tuple(str(v) for v in data.get("correct", []) or []),
                partial=partial,
                model=str(data.get("model", "")),
                created_at=str(data.get("created_at", "")),
                decision=decisions.get(str(data.get("id", "")), ""),
            )
        )
    return found


def parse_partial(values: Iterable[str]) -> tuple[tuple[str, float], ...]:
    """`"Forgot the base case=-3"` → ("Forgot the base case", -3.0)."""
    parsed: list[tuple[str, float]] = []
    for raw in values:
        label, sep, points = raw.rpartition("=")
        if not sep or not label.strip():
            raise AgentError(f"{raw!r}: write a partial-credit option as \"label=points\", e.g. \"Off by one=-2\"")
        try:
            parsed.append((label.strip(), float(points)))
        except ValueError:
            raise AgentError(f"{raw!r}: {points!r} is not a number of points") from None
    return tuple(parsed)


def _question(document: QADocument, question: str, version: str):  # noqa: ANN202
    if document.version_questions and not version:
        raise AgentError(
            f"this answer key has versions ({', '.join(document.version_questions)}); "
            "say which one with --version"
        )
    try:
        return find_qa_question(document, question, version=version or None)
    except (ValueError, KeyError):
        raise AgentError(f"the answer key has no question {question!r}") from None


def _answer_field(markdown: str, question: str, version: str):  # noqa: ANN202
    for candidate in answer_fields(markdown):
        if candidate.key == question and candidate.version == version:
            return candidate
    raise AgentError(f"{question!r} has no answer that can be set")


def _with_answers(markdown: str, question: str, version: str, correct: Sequence[str]) -> str:
    target = _answer_field(markdown, question, version)
    try:
        return apply_answers(markdown, {target.source_line: list(correct)})
    except ValueError as exc:
        raise AgentError(str(exc)) from None


def _with_partial(
    document: QADocument, question: str, version: str, partial: Sequence[tuple[str, float]]
) -> list[QARubricFlatOption]:
    item = _question(document, question, version)
    existing = flatten_rubric_options(item.rubric)
    keep_before = [o for o in existing if o.kind == "correct"]
    keep_after = [o for o in existing if o.kind in ("incorrect", "bonus")]
    mode = "checklist" if len(partial) > 1 else "exclusive"
    new = [
        QARubricFlatOption(
            label=f"{_PARTIAL_PREFIX}{label}" if len(partial) > 1 else "Partially Correct",
            points_delta=points,
            kind="partial",
            selection_mode=mode,
            # A single option keeps the plain label the key format expects;
            # what the assistant called it becomes its default comment.
            default_comment=label if len(partial) == 1 else None,
        )
        for label, points in partial
    ]
    options = keep_before + new + keep_after
    try:
        replace_qa_rubric_options(document, question, options, version=version or None)
    except ValueError as exc:
        raise AgentError(f"that rubric would not be valid: {exc}") from None
    return options


def suggest(
    packet: str | Path,
    *,
    question: str,
    kind: str,
    message: str,
    version: str = "",
    correct: Sequence[str] = (),
    partial: Sequence[tuple[str, float]] = (),
    model: str = "",
) -> Suggestion:
    root = Path(packet)
    kind = kind.strip().lower()
    if kind not in SUGGESTION_KINDS:
        raise AgentError(f"kind is one of {', '.join(SUGGESTION_KINDS)}")
    message = " ".join(message.split())
    if not message:
        raise AgentError("say what is wrong in --message")
    if correct and kind != "answer":
        raise AgentError("--correct goes with --kind answer")
    if partial and kind != "rubric":
        raise AgentError("--partial goes with --kind rubric")
    markdown = packet_qa_path(root).read_text(encoding="utf-8")
    document = parse_qa_markdown(markdown)
    _question(document, question, version)
    # Checked now, so the assistant hears about a fix that cannot work while
    # it can still change it, not the teacher at acceptance.
    if correct:
        _with_answers(markdown, question, version, correct)
    if partial:
        _with_partial(document, question, version, partial)

    existing = list_suggestions(root)
    suggestion = Suggestion(
        id=f"s{len(existing) + 1}",
        kind=kind,
        question=question,
        version=version,
        message=message,
        correct=tuple(value.strip() for value in correct if value.strip()),
        partial=tuple(partial),
        model=model.strip(),
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    with _path(root, SUGGESTIONS_FILENAME).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(suggestion.to_json(), ensure_ascii=False) + "\n")
    return suggestion


def _find(packet: Path, suggestion_id: str) -> Suggestion:
    for suggestion in list_suggestions(packet):
        if suggestion.id == suggestion_id:
            return suggestion
    raise AgentError(f"no suggestion {suggestion_id!r} in this copy")


def _decide(packet: Path, suggestion_id: str, decision: str) -> None:
    decisions = _decisions(packet)
    decisions[suggestion_id] = decision
    _path(packet, DECISIONS_FILENAME).write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8")


def _labels_in_use(submissions: Path, question: str) -> set[str]:
    used: set[str] = set()
    for path in discover_submission_paths(submissions):
        try:
            document = load_student_submission_markdown(path)
        except (OSError, ValueError):
            continue
        for item in document.questions:
            if item.question_id == question and item.points_awarded is not None:
                used.update(item.rubric_labels)
    return used


def pending_fix(packet: str | Path, suggestion_id: str) -> Suggestion:
    """The suggestion, if it is still open and carries a fix to accept."""
    suggestion = _find(Path(packet), suggestion_id)
    if suggestion.decision:
        raise AgentError(f"{suggestion.id} was already {suggestion.decision}")
    if not suggestion.fixable:
        raise AgentError(f"{suggestion.id} has no fix to apply; read it and change the key yourself")
    return suggestion


def fixed_key(markdown: str, suggestion: Suggestion, *, labels_in_use: set[str]) -> str:
    """The answer key `markdown` with the suggestion's fix in it.

    Only that question changes. A rubric that would drop an option a saved
    grade uses (`labels_in_use`) is refused, as editing it by hand would be.
    """
    if suggestion.correct:
        return _with_answers(markdown, suggestion.question, suggestion.version, suggestion.correct)
    document = parse_qa_markdown(markdown)
    options = _with_partial(document, suggestion.question, suggestion.version, suggestion.partial)
    lost = sorted(labels_in_use - {o.label for o in options})
    if lost:
        raise AgentError(
            f"not applied: grades already saved for {suggestion.question} use "
            + ", ".join(repr(label) for label in lost)
            + ", which this rubric drops"
        )
    return render_qa_markdown(document)


def describe_accepted(suggestion: Suggestion) -> str:
    if suggestion.correct:
        return f"{suggestion.question} now accepts {'; '.join(suggestion.correct)}"
    return f"{suggestion.question} has a new partial-credit rubric"


def record_accepted(packet: str | Path, suggestion: Suggestion, key_markdown: str) -> None:
    """Mark it accepted and give the copy the corrected key.

    The assistant grades the rest with the key as it now is.
    """
    root = Path(packet)
    _decide(root, suggestion.id, "accepted")
    packet_qa_path(root).write_text(key_markdown, encoding="utf-8")


def accept_suggestion(
    packet: str | Path, suggestion_id: str, *, qa_file: str | Path, submissions: str | Path
) -> str:
    """Apply a suggestion's fix to the class's answer key (teacher only)."""
    suggestion = pending_fix(packet, suggestion_id)
    key_path = Path(qa_file)
    used = set() if suggestion.correct else _labels_in_use(Path(submissions), suggestion.question)
    markdown = fixed_key(key_path.read_text(encoding="utf-8"), suggestion, labels_in_use=used)
    key_path.write_text(markdown, encoding="utf-8")
    record_accepted(packet, suggestion, markdown)
    return describe_accepted(suggestion)


def dismiss_suggestion(packet: str | Path, suggestion_id: str) -> None:
    root = Path(packet)
    suggestion = _find(root, suggestion_id)
    if suggestion.decision:
        raise AgentError(f"{suggestion.id} was already {suggestion.decision}")
    _decide(root, suggestion.id, "dismissed")
