"""Find a class folder's pipeline inputs so nobody has to type four paths.

Only the folder's own files are read, plus its immediate subfolders as a
possible split-PDF directory. Anything that could be two things is reported as
ambiguous rather than guessed: grading the wrong PDF is far costlier than being
asked which one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from opengrader.core.course import LIKELY_SOLUTION_NAMES

ROLE_SCAN = "scan"
ROLE_QA = "qa"
ROLE_ROSTER = "roster"
ROLE_TEMPLATE = "template"
ROLE_SUBMISSIONS = "submissions"

ROLE_LABELS = {
    ROLE_SCAN: "scanned bundle",
    ROLE_QA: "answer key",
    ROLE_ROSTER: "roster",
    ROLE_TEMPLATE: "blank template",
    ROLE_SUBMISSIONS: "submissions",
}

PIPELINE_ROLES = (ROLE_SCAN, ROLE_QA, ROLE_ROSTER, ROLE_TEMPLATE)
GRADING_ROLES = (ROLE_QA, ROLE_SUBMISSIONS)

# What opengrader-run builds, so a folder it has run in grades its own output.
_PIPELINE_SUBMISSIONS_DIR = "submissions"

_TEMPLATE_WORDS = frozenset({"template", "blank"})
_ANSWER_PDF_WORDS = frozenset({"answer", "answers", "solution", "solutions", "key"})
_SCAN_WORDS = frozenset({"scan", "scans", "scanned", "submission", "submissions", "bundle"})
_ROSTER_WORDS = frozenset({"roster", "students", "ids", "class"})
_SPLIT_DIR_NAMES = ("split", "split-submissions", "split-pdfs")
# Folders the pipeline writes into; their PDFs are never a fresh input.
_GENERATED_DIRS = frozenset({"submissions", "student-submissions", "grader-copies"})

_WORD_RE = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Ambiguity:
    role: str
    candidates: tuple[Path, ...]


@dataclass(frozen=True)
class ClassFolderScan:
    folder: Path
    scan: Path | None = None
    qa_file: Path | None = None
    roster: Path | None = None
    blank_template: Path | None = None
    submissions: Path | None = None
    ambiguities: tuple[Ambiguity, ...] = ()
    notes: tuple[str, ...] = ()

    def found(self, role: str) -> Path | None:
        return {
            ROLE_SCAN: self.scan,
            ROLE_QA: self.qa_file,
            ROLE_ROSTER: self.roster,
            ROLE_TEMPLATE: self.blank_template,
            ROLE_SUBMISSIONS: self.submissions,
        }[role]

    def ambiguity(self, role: str) -> Ambiguity | None:
        return next((a for a in self.ambiguities if a.role == role), None)


def scan_class_folder(folder: str | Path) -> ClassFolderScan:
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a folder")

    files = [path for path in _visible_children(root) if path.is_file()]
    ambiguities: list[Ambiguity] = []
    notes: list[str] = []

    qa_file = _pick(
        ROLE_QA,
        [path for path in files if _is_qa_file(path)],
        preferred=lambda path: path.name.lower() in LIKELY_SOLUTION_NAMES,
        ambiguities=ambiguities,
    )

    roster = _pick(
        ROLE_ROSTER,
        [path for path in files if _is_roster_file(path)],
        preferred=lambda path: bool(_words(path) & _ROSTER_WORDS),
        ambiguities=ambiguities,
    )
    for path in files:
        if path.suffix.lower() == ".csv" and _words(path) & _ROSTER_WORDS:
            notes.append(
                f"{path.name} looks like a roster, but the pipeline reads a "
                "tab-separated file (ID<TAB>NAME) — save it as .tsv"
            )

    templates: list[Path] = []
    bundles: list[Path] = []
    for path in files:
        if path.suffix.lower() != ".pdf":
            continue
        words = _words(path)
        if words & _TEMPLATE_WORDS:
            templates.append(path)
        elif words & _ANSWER_PDF_WORDS:
            notes.append(
                f"{path.name} looks like an answer key PDF and was not used as "
                "the scan; the pipeline grades against a markdown answer key"
            )
        else:
            bundles.append(path)

    blank_template = _pick(
        ROLE_TEMPLATE,
        templates,
        preferred=lambda path: "blank" in _words(path),
        ambiguities=ambiguities,
    )

    scan = _pick(
        ROLE_SCAN,
        bundles,
        preferred=lambda path: bool(_words(path) & _SCAN_WORDS),
        ambiguities=ambiguities,
    )
    if scan is None and not bundles:
        scan = _pick(
            ROLE_SCAN,
            _split_pdf_dirs(root),
            preferred=lambda path: path.name.lower() in _SPLIT_DIR_NAMES,
            ambiguities=ambiguities,
        )

    submissions = _find_submissions(root, ambiguities)

    return ClassFolderScan(
        folder=root,
        scan=scan,
        qa_file=qa_file,
        roster=roster,
        blank_template=blank_template,
        submissions=submissions,
        ambiguities=tuple(ambiguities),
        notes=tuple(notes),
    )


def describe_class_folder(
    result: ClassFolderScan,
    roles: tuple[str, ...] = PIPELINE_ROLES,
    *,
    include_notes: bool = True,
) -> list[str]:
    lines: list[str] = []
    for role in roles:
        label = ROLE_LABELS[role]
        found = result.found(role)
        ambiguity = result.ambiguity(role)
        if found is not None:
            if found == result.folder:
                shown = "this folder"
            else:
                shown = found.name + ("/" if found.is_dir() else "")
            lines.append(f"✓ {label:<15} {shown}")
        elif ambiguity is not None:
            names = ", ".join(path.name for path in ambiguity.candidates)
            lines.append(
                f"? {label:<15} {len(ambiguity.candidates)} candidates, choose "
                f"one: {names}"
            )
        else:
            lines.append(f"· {label:<15} none found")
    if include_notes:
        lines.extend(f"! {note}" for note in result.notes)
    return lines


def _pick(
    role: str,
    candidates: list[Path],
    *,
    preferred,
    ambiguities: list[Ambiguity],
) -> Path | None:
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    favourites = [path for path in candidates if preferred(path)]
    if len(favourites) == 1:
        return favourites[0]
    ambiguities.append(Ambiguity(role=role, candidates=tuple(favourites or candidates)))
    return None


def _visible_children(directory: Path) -> list[Path]:
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return []
    # "._name" files are macOS resource forks on non-Apple drives.
    return [path for path in children if not path.name.startswith(".")]


def _words(path: Path) -> set[str]:
    return set(_WORD_RE.findall(path.stem.lower()))


def _is_qa_file(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() != ".md" or name.endswith(".submission.md"):
        return False
    from opengrader.core.qa_file.markdown import parse_qa_markdown

    try:
        document = parse_qa_markdown(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return bool(document.questions) or bool(document.version_questions)


def _is_roster_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in (".tsv", ".txt"):
        return False
    try:
        lines = [
            line
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError):
        return False
    if not lines:
        return False
    tabbed = sum(1 for line in lines if len(line.split("\t")) >= 2)
    if tabbed * 2 >= len(lines):
        return True
    # A name-only list is a roster only when its filename says so.
    return suffix == ".tsv" or bool(_words(path) & _ROSTER_WORDS)


def _submission_files(directory: Path) -> list[Path]:
    return [
        path
        for path in _visible_children(directory)
        if path.name.endswith(".submission.md") and path.is_file()
    ]


def _find_submissions(root: Path, ambiguities: list[Ambiguity]) -> Path | None:
    if _submission_files(root):
        return root

    # One .submission.md per folder is a student; nested or several is a cohort.
    students: list[Path] = []
    cohorts: list[Path] = []
    for child in _visible_children(root):
        if not child.is_dir():
            continue
        direct = _submission_files(child)
        nested = any(
            grandchild.is_dir() and _submission_files(grandchild)
            for grandchild in _visible_children(child)
        )
        if nested or len(direct) > 1:
            cohorts.append(child)
        elif direct:
            students.append(child)

    if students and not cohorts:
        return root
    return _pick(
        ROLE_SUBMISSIONS,
        cohorts,
        preferred=lambda path: path.name.lower() == _PIPELINE_SUBMISSIONS_DIR,
        ambiguities=ambiguities,
    )


def _split_pdf_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    for child in _visible_children(root):
        if not child.is_dir() or child.name.lower() in _GENERATED_DIRS:
            continue
        if any(
            path.suffix.lower() == ".pdf" and path.is_file()
            for path in _visible_children(child)
        ):
            found.append(child)
    return found
