"""What a folder can be opened as, from one path and no other hints.

`opengrader tui FOLDER` names a folder and nothing else — no QA path, no
submissions path — so it has to work out what the folder *is* before opening
it (a class folder ready to grade is imported into a new workspace with its
grades; see `tui/main._open_folder_as_workspace`):

* a **workspace** — it holds `.opengrader/workspace.sqlite3`, and opening it
  means starting the local workspace service;
* a **class folder ready to grade** — one answer key and extracted
  submissions, whose grades an import carries over;
* **scans not yet processed** — a scanned bundle but no submissions, which is
  the pipeline's job, not grading's;
* or none of those, in which case the reason is the answer.

Built on `core/class_folder.scan_class_folder`, which already refuses to guess
between two candidates. That rule matters more here: opening the wrong answer
key grades a whole class against it.

Pure apart from reading directory listings, so it is unit-testable with a
`tmp_path` and never imports Textual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from opengrader.core.class_folder import (
    GRADING_ROLES,
    ClassFolderScan,
    describe_class_folder,
    scan_class_folder,
)

__all__ = ["TargetKind", "OpenTarget", "detect_target"]

TargetKind = Literal["workspace", "folder", "scans", "unknown"]

_WORKSPACE_DATABASE = Path(".opengrader/workspace.sqlite3")


@dataclass(frozen=True)
class OpenTarget:
    kind: TargetKind
    folder: Path | None = None
    qa_file: Path | None = None
    submissions: Path | None = None
    # One line each, already worded for a person; the first is the verdict.
    lines: tuple[str, ...] = field(default_factory=tuple)

    @property
    def openable(self) -> bool:
        if self.kind == "folder":
            return self.qa_file is not None and self.submissions is not None
        return self.kind in ("workspace", "scans")

    @property
    def needs_answer_key(self) -> bool:
        return self.kind == "folder" and self.qa_file is None


def detect_target(path: str | Path, *, qa_file: str | Path | None = None) -> OpenTarget:
    """Classify `path`. Never raises: an unreadable path is an `unknown` target.

    `qa_file` is the answer key the user chose by hand, for a folder whose own
    key is missing or ambiguous; it wins over whatever the scan found.
    """
    text = str(path).strip()
    if not text:
        return OpenTarget("unknown", lines=("Type or browse to a folder.",))
    target = Path(text).expanduser()
    chosen_qa = Path(str(qa_file).strip()).expanduser() if qa_file and str(qa_file).strip() else None

    if target.is_file():
        # A QA file or a single submission: work from the folder holding it.
        if chosen_qa is None and target.suffix.lower() == ".md" and not target.name.endswith(
            ".submission.md"
        ):
            chosen_qa = target
        target = target.parent
    if not target.is_dir():
        return OpenTarget("unknown", lines=(f"No such folder: {target}",))

    if (target / _WORKSPACE_DATABASE).is_file():
        return OpenTarget(
            "workspace",
            folder=target,
            lines=(
                "Workspace: grading is kept in its database and shared with the "
                "web app through a local service.",
            ),
        )

    try:
        scan = scan_class_folder(target)
    except OSError as exc:
        return OpenTarget("unknown", folder=target, lines=(f"Could not read {target}: {exc}",))

    qa = chosen_qa or scan.qa_file
    submissions = scan.submissions
    if submissions is not None and qa is None and submissions == target:
        # Pointed at the submissions folder itself; its answer key is usually
        # one level up, beside it.
        qa = _qa_beside(target)

    details = tuple(
        describe_class_folder(_with(scan, qa), GRADING_ROLES, include_notes=False)
    )
    if submissions is not None:
        if qa is not None and not qa.is_file():
            return OpenTarget(
                "folder",
                folder=target,
                submissions=submissions,
                lines=(f"Answer key not found: {qa}", *details),
            )
        verdict = (
            "Ready to grade in place, with no service."
            if qa is not None
            else "Submissions found, but no single answer key. Choose one below."
        )
        return OpenTarget(
            "folder",
            folder=target,
            qa_file=qa,
            submissions=submissions,
            lines=(verdict, *details),
        )
    if scan.scan is not None or scan.section_scans:
        what = (
            scan.scan.name
            if scan.scan is not None
            else f"one per section, {len(scan.section_scans)} sections"
        )
        return OpenTarget(
            "scans",
            folder=target,
            qa_file=qa,
            lines=(
                f"Scans not processed yet ({what}). Open them in Tools "
                "to run the pipeline, then grade.",
            ),
        )
    return OpenTarget(
        "unknown",
        folder=target,
        lines=("Nothing to grade here: no submissions, scans or workspace.", *details),
    )


def _qa_beside(submissions: Path) -> Path | None:
    parent = submissions.parent
    if parent == submissions:
        return None
    try:
        return scan_class_folder(parent).qa_file
    except OSError:
        return None


def _with(scan: ClassFolderScan, qa: Path | None) -> ClassFolderScan:
    from dataclasses import replace

    if qa is None or qa == scan.qa_file:
        return scan
    kept = tuple(a for a in scan.ambiguities if a.role != "qa")
    return replace(scan, qa_file=qa, ambiguities=kept)
