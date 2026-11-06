"""The pipeline card's Preview, worded for a person rather than a log.

`plan_pipeline` says, per stage, whether it would run and why, in the terms
the tools use ("no record of this stage having run here before", the exact
command). That stays available as the technical details. This module turns
the same plan into what a teacher needs first:

* one sentence saying what pressing Run Pipeline would do;
* a checklist of the steps in everyday words, each with what will happen;
* what to do about anything that holds the run back, in the app's own
  terms (the box to tick, the tool to open), never a command line.

Pure apart from asking whether the scan is a folder: it reads the request
and the plans it is given, so the wording is testable without Textual. Nothing here decides anything —
`plan_pipeline` does; this only explains it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from opengrader.core.pipeline import (
    STAGE_AUTO_GRADE,
    STAGE_DRAFT_RUBRICS,
    STAGE_EXTRACT,
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    PipelineRequest,
    StagePlan,
    StageStatus,
    describe_uneven_scan,
    split_page_count,
    uneven_scans,
)

__all__ = ["PlanPreview", "PreviewStep", "describe_plan", "technical_lines"]

# What each stage is, for someone who has never seen the pipeline.
_STEP_NAMES = {
    STAGE_SCAN_CHECK: "Check the scan",
    STAGE_SPLIT: "Split it into one PDF per student",
    STAGE_EXTRACT: "Read every answer",
    STAGE_DRAFT_RUBRICS: "Draft missing rubric items with AI",
    STAGE_AUTO_GRADE: "Grade what the answer key settles",
}

# The same steps as the middle of a sentence ("Run Pipeline will …").
_STEP_VERBS = {
    STAGE_SCAN_CHECK: "check the scan for upside-down, blank or missing pages",
    STAGE_SPLIT: "split it into one PDF per student",
    STAGE_EXTRACT: "read every answer",
    STAGE_DRAFT_RUBRICS: "draft rubric items for written questions that have none",
    STAGE_AUTO_GRADE: "grade every answer the answer key settles",
}

_ADD_KEY = (
    "Add an answer key: open Create answer key in Helper Tools below, or "
    "choose an existing one under Advanced settings."
)


@dataclass(frozen=True)
class PreviewStep:
    name: str
    # "will run", "done", or "skipped": what the mark in front shows.
    state: str
    detail: str
    section: str = ""

    @property
    def mark(self) -> str:
        return {"will run": "▸", "done": "✓"}.get(self.state, "·")

    def line(self) -> str:
        where = f"{self.section} · " if self.section else ""
        return f"{self.mark} {where}{self.name} — {self.detail}"


@dataclass(frozen=True)
class PlanPreview:
    headline: str
    steps: tuple[PreviewStep, ...]
    # Easy fixes, in the app's own words; empty when nothing needs doing.
    fixes: tuple[str, ...] = field(default_factory=tuple)

    def lines(self) -> list[str]:
        """The plain-language part, ready to show."""
        out = [
            self.headline,
            "This is only a preview: nothing has been run or changed.",
            "",
            *(step.line() for step in self.steps),
        ]
        if self.fixes:
            out += ["", "What you can do:", *(f"• {fix}" for fix in self.fixes)]
        return out


def describe_plan(request: PipelineRequest, plans: Sequence[StagePlan]) -> PlanPreview:
    # How each section's PDFs came to be: kept from last time's split, or
    # given already split (when this run cannot tell old PDFs from new).
    split_status = {
        plan.section: plan.status
        for plan in plans
        if plan.stage == STAGE_SPLIT and not plan.will_run
    }
    steps = tuple(_step(request, plan, split_status) for plan in plans)
    return PlanPreview(
        headline=_headline(request, plans),
        steps=steps,
        fixes=tuple(_fixes(request, plans)),
    )


def technical_lines(plans: Sequence[StagePlan], request: PipelineRequest) -> list[str]:
    """The plan as the tools see it: stage, status, reason and command."""
    lines: list[str] = []
    for plan in plans:
        verb = "run" if plan.will_run else plan.status.value
        lines.append(f"{plan.stage:<13} {verb:<8} {plan.reason}")
        if plan.command:
            lines.append(f"{'':<13} $ {plan.command}")
    lines += ["", f"class folder: {request.output_dir}"]
    return lines


def _headline(request: PipelineRequest, plans: Sequence[StagePlan]) -> str:
    running = [plan for plan in plans if plan.will_run]
    if {plan.stage for plan in running} <= {STAGE_EXTRACT} and any(
        plan.status is StageStatus.REUSED for plan in plans
    ):
        return (
            "Everything is up to date. Running again only reads answers from "
            "PDFs that are new since last time."
        )
    sections = len({plan.section for plan in plans if plan.section})
    per_section = _join(
        [_STEP_VERBS[s] for s in dict.fromkeys(p.stage for p in running if p.section)]
    )
    whole_class = _join(
        [_STEP_VERBS[s] for s in dict.fromkeys(p.stage for p in running if not p.section)]
    )
    if per_section and whole_class:
        return (
            f"For each of the {sections} sections, Run Pipeline will {per_section}. "
            f"Then it will {whole_class}, for the whole class at once."
        )
    if per_section:
        return f"For each of the {sections} sections, Run Pipeline will {per_section}."
    if whole_class:
        return f"Run Pipeline will {whole_class}."
    return "Run Pipeline has nothing to do."


def _step(
    request: PipelineRequest, plan: StagePlan, split_status: dict[str, StageStatus]
) -> PreviewStep:
    name = _STEP_NAMES.get(plan.stage, plan.stage)
    if plan.status is StageStatus.REUSED:
        return PreviewStep(name, "done", "already done; kept from last time", plan.section)
    if not plan.will_run:
        return PreviewStep(name, "skipped", _why_skipped(request, plan), plan.section)
    split = split_status.get(plan.section)
    if plan.stage == STAGE_EXTRACT and split is not None and not request.fresh:
        detail = (
            "only PDFs that are new since last time; answers already read are kept"
            if split is StageStatus.REUSED
            else "any PDF not read yet; answers already read are kept"
        )
    else:
        detail = _why_running(request, plan)
    return PreviewStep(name, "will run", detail, plan.section)


def _why_running(request: PipelineRequest, plan: StagePlan) -> str:
    if request.fresh:
        return "again from the start (Start over is ticked)"
    if plan.stage == STAGE_DRAFT_RUBRICS:
        return "then pauses so you can check the drafts"
    if "changed" in plan.reason:
        return "again, because the scan, answer key or settings changed"
    return "for the first time here"


def _why_skipped(request: PipelineRequest, plan: StagePlan) -> str:
    if plan.stage == STAGE_SPLIT:
        return "not needed: the scans are already one PDF per student"
    if plan.stage == STAGE_DRAFT_RUBRICS:
        if request.qa_file is None:
            return "needs an answer key"
        return "not asked for (optional, in Settings)"
    if plan.stage == STAGE_AUTO_GRADE:
        if not request.auto_grade:
            return (
                'not ticked ("Auto-grade after reading the scans", in '
                "Settings); you grade every answer yourself"
            )
        if request.qa_file is None:
            return "waiting for an answer key"
    return "not needed this time"


def _fixes(request: PipelineRequest, plans: Sequence[StagePlan]) -> list[str]:
    fixes: list[str] = []
    stages = {plan.stage for plan in plans}
    if request.qa_file is None and STAGE_AUTO_GRADE in stages:
        fixes.append(
            "No answer key yet, so nothing will be graded automatically. " + _ADD_KEY
        )
    elif not request.auto_grade:
        fixes.append(
            'To have answers graded automatically, tick "Auto-grade after '
            'reading the scans" in Settings.'
        )
    if (
        not request.pages_per_submission
        and request.blank_template is None
        # Already-split PDFs have no page count to check; section scans do.
        and (bool(request.section_scans) or not request.scan.is_dir())
    ):
        fixes.append(
            "Pages per submission is blank, so the scan check cannot catch a "
            "missing or swapped page. Fill it in if you know it."
        )
    for scan, count in uneven_scans(request):
        fixes.append(describe_uneven_scan(scan, count, split_page_count(request)[0] or 1))
    return fixes


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]
