"""State the scale of a cloud run before it happens.

A pre-flight confirmation needs the *real* count — "847 answer images from
141 students will be sent to `ai.example.edu`. Estimated cost $3.40." — and
that count should come from `plan_pipeline()`, the engine that already
computes what a run *would* do without executing it, rather than a second
traversal of the same folder.

`plan_cloud_egress()` is that reader: it calls `plan_pipeline()` to see which
stages will actually run and which AI features are switched on for them, then
does its own cheap counting (globbing files, counting pages, reading the QA
file's question count) to turn "extraction will run" into "N images". It
never re-implements OCR/vision extraction, and it never sends anything.

A student's name and student ID are never sent to a cloud host, so this
module never counts an "identity-image", "title-image" or "page-image" send:
there is nothing to confirm for them, because there is no configuration under
which they would leave this machine.

One more thing anchors the honesty of the count: `escalate_only` reduces the
answer-image count to an estimate, not a promise — only the answers the
offline OCR could not read confidently are actually sent, and that number
cannot be known until the run happens. The count here is a ceiling ("up to
N"), stated as such in `cost_basis`.

Cost is estimated from real history when there is any: `core.egress`'s log
for this submissions folder, then the per-user log beside `config.json`, then
a documented conservative estimate, in that order — and `None`, honestly,
when the provider states no pricing at all.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.app_config import ResolvedAI
    from opengrader.core.pipeline import PipelineRequest

__all__ = [
    "CloudConfirmationNeeded",
    "CloudPreflight",
    "plan_cloud_egress",
    "cli_confirm_cloud_send",
]

# A conservative (i.e. on the high side) per-call token estimate, used only
# when neither this folder's nor this machine's egress log has ever recorded
# a real call for the category in question. Real costs vary by provider and
# image size; this exists so a first-ever cloud run still gets a number
# rather than nothing, and it always says so in `cost_basis`.
_CONSERVATIVE_TOKENS: dict[str, tuple[float, float]] = {
    "answer-image": (1200.0, 60.0),
    "graded-text": (600.0, 120.0),
    "connection-test": (50.0, 10.0),
}

_CONSERVATIVE_ANSWERS_PER_STUDENT = 12


@dataclass(frozen=True)
class CloudPreflight:
    """The scale of what one run would send, computed but not yet sent."""

    provider_label: str
    host: str
    counts: dict[str, int] = field(default_factory=dict)
    students: int = 0
    estimated_cost: float | None = None
    cost_basis: str = ""
    # Categories whose number cannot be known before the run, mapped to a
    # phrase describing what will be sent instead ("one answer image per
    # answer found in 3 submission PDFs"). How many answers a scan holds is
    # only discovered while it is read, and a consent statement must not
    # present a guess — or a count of files — as the number of images. A
    # category here is described by its phrase even if `counts` also holds a
    # number for it (used only for the cost estimate).
    uncounted: dict[str, str] = field(default_factory=dict)

    @property
    def sends_anything(self) -> bool:
        return bool(self.uncounted) or any(count > 0 for count in self.counts.values())

    def statement(self) -> str:
        """One line: scale, destination, cost."""
        if not self.sends_anything:
            return f"Nothing will be sent to {self.host}."

        order = ("answer-image", "graded-text")
        nouns = {
            "answer-image": "answer image",
            "graded-text": "graded-text call",
        }
        # Any category not in `order` (there shouldn't be one today, but a
        # future counter must not crash the whole pre-flight statement) still
        # gets a readable noun — its own name, spaces for hyphens — rather
        # than a KeyError, and is listed after the known categories.
        categories = list(order) + [
            category
            for category in (*self.counts, *self.uncounted)
            if category not in order
        ]
        parts: list[str] = []
        for category in dict.fromkeys(categories):
            if category in self.uncounted:
                parts.append(self.uncounted[category])
            elif self.counts.get(category, 0) > 0:
                parts.append(
                    f"{self.counts[category]} {nouns.get(category, category.replace('-', ' '))}"
                    + ("" if self.counts[category] == 1 else "s")
                )
        lede = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        lede = lede[:1].upper() + lede[1:]
        student_clause = (
            f" from {self.students} student{'' if self.students == 1 else 's'}"
            if self.students and not self.uncounted
            else ""
        )
        line = f"{lede}{student_clause} will be sent to {self.host}."
        if self.uncounted:
            line += " The exact number is not known until the scans are read."
        if self.estimated_cost is not None:
            line += f" Estimated cost ${self.estimated_cost:,.2f} ({self.cost_basis})."
        else:
            line += f" Cost could not be estimated ({self.cost_basis})."
        return line

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_label": self.provider_label,
            "host": self.host,
            "counts": dict(self.counts),
            "uncounted": dict(self.uncounted),
            "students": self.students,
            "estimated_cost": self.estimated_cost,
            "cost_basis": self.cost_basis,
            "sends_anything": self.sends_anything,
            "statement": self.statement(),
        }


def _estimate_students(request: "PipelineRequest") -> int:
    """A cheap count from the filesystem — never opens or reads a crop.

    0 means "could not tell", never "none": the caller must not read it as
    nothing being sent.
    """
    source = request.extract_source
    if not source.exists() and request.scan.is_file():
        # A combined scan is split into per-student PDFs by the split stage,
        # which has not run yet when this is asked; count from the scan.
        source = request.scan
    try:
        if source.is_dir():
            direct = list(source.glob("*.pdf"))
            return len(direct) if direct else 0
        if source.is_file():
            from pypdf import PdfReader

            total_pages = len(PdfReader(str(source)).pages)
            per = request.pages_per_submission
            if not per and request.blank_template is not None:
                try:
                    per = len(PdfReader(str(request.blank_template)).pages)
                except Exception:  # noqa: BLE001 - best-effort estimate only
                    per = None
            per = per or 1
            return max(1, total_pages // per)
    except Exception:  # noqa: BLE001 - a bad PDF must not break the pre-flight
        return 0
    return 0


def _question_count(request: "PipelineRequest") -> int | None:
    """The answer key's question count, or None when there is no readable key."""
    if request.qa_file is not None:
        try:
            from opengrader.core.qa_file import load_qa_markdown

            doc = load_qa_markdown(request.qa_file)
            if doc.questions:
                return len(doc.questions)
        except Exception:  # noqa: BLE001 - unknown, not zero
            pass
    return None



def _draft_call_ceiling(request: "PipelineRequest") -> int:
    """Most rubric-draft calls a run could make: graded parts across all versions."""
    if request.qa_file is None:
        return 0
    try:
        from opengrader.core.grading.solutions import flatten_solution_entries
        from opengrader.core.qa_file import load_qa_markdown

        doc = load_qa_markdown(request.qa_file)
    except Exception:  # noqa: BLE001 - unreadable key: the stage itself will fail
        return 0
    question_sets = list(doc.version_questions.values()) or [doc.questions]
    return sum(len(flatten_solution_entries(questions)) for questions in question_sets)


def _log_entries(path: Path) -> list[dict]:
    from opengrader.core import egress

    return egress.read_entries(path)


def _tokens_for_category(
    category: str, folder_entries: list[dict], user_entries: list[dict]
) -> tuple[tuple[float, float], str]:
    from opengrader.core import egress

    folder_avg = egress.average_tokens(folder_entries, category=category)
    if folder_avg is not None:
        return folder_avg, "measured from previous calls recorded for this assignment"
    user_avg = egress.average_tokens(user_entries, category=category)
    if user_avg is not None:
        return user_avg, "measured from previous calls to this provider on this machine"
    default = _CONSERVATIVE_TOKENS.get(category, (0.0, 0.0))
    return default, "a conservative estimate — no previous calls are recorded yet"


def _estimate_cost(
    resolved: "ResolvedAI", request: "PipelineRequest", counts: dict[str, int]
) -> tuple[float | None, str]:
    from opengrader.core import app_config, egress
    from opengrader.core.submissions.manifest import resolve_manifest_path

    if not counts:
        return None, "nothing would be sent"

    folder_log = resolve_manifest_path(request.output_dir, filename=egress.EGRESS_FILENAME)
    user_log = app_config._config_dir() / egress.EGRESS_FILENAME
    folder_entries = _log_entries(folder_log)
    user_entries = _log_entries(user_log) if user_log != folder_log else folder_entries

    total_input = 0.0
    total_output = 0.0
    bases: list[str] = []
    for category, count in counts.items():
        if count <= 0:
            continue
        (avg_in, avg_out), basis = _tokens_for_category(category, folder_entries, user_entries)
        total_input += avg_in * count
        total_output += avg_out * count
        if basis not in bases:
            bases.append(basis)

    pricing = resolved.provider.pricing
    if pricing is None or not pricing.stated:
        return None, "provider pricing not stated"
    cost = egress.estimate_cost(total_input, total_output, pricing)
    if cost is None:
        return None, "provider pricing not stated"
    return cost, "; ".join(bases) if bases else "provider pricing not stated"


def plan_cloud_egress(
    request: "PipelineRequest", resolved: "ResolvedAI | None" = None
) -> "CloudPreflight | None":
    """What a run of `request` would send, or None when nothing would leave the machine.

    `None` covers a local provider (host on this machine or the LAN) — there
    is nothing to confirm. A cloud provider always returns a `CloudPreflight`,
    even one whose `counts` are all zero (no AI feature that sends is on for
    a stage that will actually run), so a caller can tell "nothing to send"
    from "no cloud provider" if it needs to.
    """
    from opengrader.core import app_config
    from opengrader.core.pipeline import (
        STAGE_AUTO_GRADE,
        STAGE_DRAFT_RUBRICS,
        STAGE_EXTRACT,
        _read_ledger,
        _stage_ledger_key,
        plan_pipeline,
    )
    from opengrader.core.submissions.manifest import record_is_reusable

    current = resolved if resolved is not None else app_config.resolve()
    if current.kind == "local":
        return None

    plans = {plan.stage: plan for plan in plan_pipeline(request)}
    counts: dict[str, int] = {}

    extract_plan = plans.get(STAGE_EXTRACT)
    # plan_pipeline always marks STAGE_EXTRACT as "will run": extraction
    # resumes file by file (its own --resume flag), not stage by stage, so
    # the plan can't say "skipped" the way it does for split or auto-grade.
    # For a pre-flight count that isn't enough — if every submission was
    # already extracted last time, a re-run sends nothing new even though
    # the stage still "runs" (and quickly finds there is nothing left to
    # do) — so check the same run ledger directly here.
    extract_source, extract_settings_id = _stage_ledger_key(request, STAGE_EXTRACT)
    extract_already_done = record_is_reusable(
        _read_ledger(request).get(STAGE_EXTRACT),
        output_dir=request.output_dir,
        source_sha256=extract_source,
        settings_id=extract_settings_id,
    )
    uncounted: dict[str, str] = {}
    students = _estimate_students(request)
    per_student_known = _question_count(request)
    per_student = per_student_known or _CONSERVATIVE_ANSWERS_PER_STUDENT
    where = (
        f"{students} submission{'' if students == 1 else 's'}"
        if students
        else "every submission in this run"
    )
    read_handwriting = request.llm.read_handwriting or current.read_handwriting
    if (
        extract_plan is not None
        and extract_plan.will_run
        and not extract_already_done
        and read_handwriting
    ):
        if students:
            counts["answer-image"] = students * per_student
        if not (students and per_student_known):
            # Without both the number of submissions and the number of
            # questions, any figure would be a guess presented as a count.
            uncounted["answer-image"] = f"one answer image per answer found in {where}"

    draft_plan = plans.get(STAGE_DRAFT_RUBRICS)
    if draft_plan is not None and draft_plan.will_run:
        # One text call per question drafted (per answer-key version), each
        # carrying up to a dozen distinct answers. Questions that already have
        # partial credit are skipped at run time, so this is an upper bound.
        ceiling = _draft_call_ceiling(request)
        if ceiling:
            counts["graded-text"] = counts.get("graded-text", 0) + ceiling

    grade_plan = plans.get(STAGE_AUTO_GRADE)
    grade_written = request.llm.grade_written or current.grade_written
    if grade_plan is not None and grade_plan.will_run and grade_written:
        if students:
            counts["graded-text"] = counts.get("graded-text", 0) + students * per_student
        if not (students and per_student_known):
            uncounted["graded-text"] = (
                f"one graded-text call per written answer AI-graded in {where}"
            )

    estimated_cost, cost_basis = _estimate_cost(current, request, counts)
    if current.escalate_only and counts.get("answer-image"):
        cost_basis = (
            f"up to {counts['answer-image']} answer images (escalate-only sends only "
            f"what the offline reading could not confirm); {cost_basis}"
        )

    return CloudPreflight(
        provider_label=current.provider.label,
        host=current.host,
        counts=counts,
        students=students,
        estimated_cost=estimated_cost,
        cost_basis=cost_basis,
        uncounted=uncounted,
    )


class CloudConfirmationNeeded(SystemExit):
    """A cloud send was refused because nobody confirmed it. Nothing was sent.

    A `SystemExit` with code 2, so a standalone CLI exits exactly as
    `sys.exit(2)` always made it. An in-process caller that shows its own
    confirmation (the TUI) catches it, shows `statement()` and, if the user
    agrees, re-runs the tool with `--yes-send-to-cloud`.
    """

    def __init__(self, preflight: CloudPreflight, egress_statement: str = "") -> None:
        super().__init__(2)
        self.preflight = preflight
        self.egress_statement = egress_statement

    def statement(self) -> str:
        return "\n\n".join(
            part for part in (self.preflight.statement(), self.egress_statement) if part
        )


def cli_confirm_cloud_send(
    resolved: "ResolvedAI",
    preflight: "CloudPreflight | None",
    *,
    yes_send_to_cloud: bool,
    emit: Callable[[str], None] = print,
    interactive: bool = True,
) -> None:
    """The standalone-CLI convention.

    Every AI-capable CLI calls this right before it would build a vision
    reader or a text-generate callable for a cloud provider. It never prompts
    or refuses for a local provider (`preflight` is None then). Otherwise:
    an interactive terminal is shown `preflight.statement()` plus
    `app_config.consent.egress_statement(resolved)` and asked to type
    confirmation; a non-interactive run without `--yes-send-to-cloud` prints
    why and calls `sys.exit(2)` — refused, not crashed, the same convention
    `automated-grading --llm` and `grade-check --fail-on-blocking` already
    use. `--llm`/`--llm-escalate`/etc. never implies the flag.

    `interactive=False` is for a caller that owns the terminal itself — the
    TUI runs these CLIs in-process, where stdin is still a TTY but `input()`
    would block a worker thread forever. It never prompts: it raises
    `CloudConfirmationNeeded` without printing, and the caller asks.
    """
    if preflight is None or not preflight.sends_anything or yes_send_to_cloud:
        return

    from opengrader.core import app_config

    if not interactive:
        raise CloudConfirmationNeeded(
            preflight, app_config.consent.egress_statement(resolved)
        )

    if not sys.stdin.isatty():
        emit(f"Error: {preflight.statement()}")
        emit(
            "Error: refusing to send to the cloud without confirmation. Re-run "
            "with --yes-send-to-cloud to confirm, or turn off the AI feature "
            "that needs it."
        )
        raise CloudConfirmationNeeded(preflight)

    emit(preflight.statement())
    emit(app_config.consent.egress_statement(resolved))
    try:
        answer = input("Send this to the cloud? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        emit("Error: not confirmed; nothing was sent.")
        sys.exit(2)
