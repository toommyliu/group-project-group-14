"""Find a class folder's pipeline inputs so nobody has to type four paths.

Only the folder's own files are read, plus its immediate subfolders as a
possible split-PDF directory. Anything that could be two things is reported as
ambiguous rather than guessed: grading the wrong PDF is far costlier than being
asked which one.

Two shapes are recognised beyond "one of each":

* **One scan per class section** — several scanned bundles (in the folder, or
  in one subfolder of it) whose names each say which section they are
  (`section-01.pdf`, `Sec B scans.pdf`, `period 3.pdf`). They are one
  assignment: one answer key, graded as one class, each submission tagged
  with its section. Every candidate must name a different section, or the
  folder is ambiguous as before. Rosters named the same way are paired with
  them by section.
* **Several assignments** (`find_assignment_folders`) — subfolders that each
  hold their own, different answer key. That is what a workspace is for.
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
# What create-answer-key reads.
_ANSWER_SOURCE_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})
_SCAN_WORDS = frozenset({"scan", "scans", "scanned", "submission", "submissions", "bundle"})
_ROSTER_WORDS = frozenset({"roster", "students", "ids", "class"})
_SPLIT_DIR_NAMES = ("split", "split-submissions", "split-pdfs")
# Folders the pipeline writes into; their PDFs are never a fresh input.
_GENERATED_DIRS = frozenset({"submissions", "student-submissions", "grader-copies"})

_WORD_RE = re.compile(r"[a-z]+")
# "section-01", "Sec B", "sect_2", "period 3" in a lowercased file stem. The
# section's own name is digits (with an optional letter) or, after a
# separator, a single letter; it must not run on into a longer word, so
# "second", "sections" and "sec-ond" are not sections.
_SECTION_RE = re.compile(
    r"(?<![a-z])(section|sect|sec|period)"
    r"(?:[\s_.\-]+(\d+[a-z]?|[a-z])|(\d+[a-z]?))(?![a-z0-9])"
)


@dataclass(frozen=True)
class Ambiguity:
    role: str
    candidates: tuple[Path, ...]


@dataclass(frozen=True)
class SectionFile:
    """A file that belongs to one class section, by its name."""

    path: Path
    section: str


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
    # One scanned bundle per class section, in place of `scan`. Set only when
    # every candidate names a different section; `scan` is then None.
    section_scans: tuple[SectionFile, ...] = ()
    # Rosters named after the same sections, found only beside section scans;
    # `roster` is then None.
    section_rosters: tuple[SectionFile, ...] = ()

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

    roster_candidates = [path for path in files if _is_roster_file(path)]
    roster_ambiguities: list[Ambiguity] = []
    roster = _pick(
        ROLE_ROSTER,
        roster_candidates,
        preferred=lambda path: bool(_words(path) & _ROSTER_WORDS),
        ambiguities=roster_ambiguities,
    )
    for path in files:
        if (
            path.suffix.lower() == ".csv"
            and _words(path) & _ROSTER_WORDS
            and not _is_roster_file(path)
        ):
            notes.append(
                f"{path.name} looks like a roster, but a .csv roster needs a "
                "first row naming First Name, Last Name and SID — add it, or "
                "save the file tab-separated (ID<TAB>NAME) as .tsv"
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

    scan: Path | None = None
    section_scans = section_files(bundles)
    if not section_scans:
        scan = _pick(
            ROLE_SCAN,
            bundles,
            preferred=lambda path: bool(_words(path) & _SCAN_WORDS),
            ambiguities=ambiguities,
        )
    if scan is None and not bundles:
        split_dirs = _split_pdf_dirs(root)
        # A subfolder of bundles that each name a section (scans/section-01.pdf,
        # scans/section-02.pdf) is several scans, not one student per PDF.
        by_section = [
            (folder, found)
            for folder in split_dirs
            if (found := section_files(_pdfs_in(folder)))
        ]
        if len(by_section) == 1:
            folder, section_scans = by_section[0]
            split_dirs.remove(folder)
        scan = _pick(
            ROLE_SCAN,
            split_dirs,
            preferred=lambda path: path.name.lower() in _SPLIT_DIR_NAMES,
            ambiguities=ambiguities,
        )
        if scan is not None and section_scans:
            # Both a folder of split PDFs and a folder of section scans: which
            # one this run means is not for the scan to decide.
            ambiguities.append(
                Ambiguity(role=ROLE_SCAN, candidates=(scan, section_scans[0].path.parent))
            )
            scan, section_scans = None, ()

    section_rosters: tuple[SectionFile, ...] = ()
    if section_scans and roster is None:
        section_rosters = section_files(roster_candidates)
    if not section_rosters:
        ambiguities.extend(roster_ambiguities)

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
        section_scans=section_scans,
        section_rosters=section_rosters,
    )


def section_name(path: str | Path) -> str | None:
    """The class section a file's name says it belongs to, or None.

    `section-01.pdf` → "Section 01", `Sec_b.tsv` → "Section B",
    `period 3 scans.pdf` → "Period 3". Only the first mention counts.
    """
    match = _SECTION_RE.search(Path(path).stem.lower())
    if match is None:
        return None
    word = "Period" if match.group(1) == "period" else "Section"
    return f"{word} {(match.group(2) or match.group(3)).upper()}"


def section_files(paths: list[Path]) -> tuple[SectionFile, ...]:
    """`paths` as one file per section, or () when they are not that.

    At least two, each naming a section, and no section twice: anything less
    is not evidence of one file per section, and the caller falls back to
    treating the files as candidates for one role.
    """
    if len(paths) < 2:
        return ()
    found: list[SectionFile] = []
    for path in paths:
        name = section_name(path)
        if name is None:
            return ()
        found.append(SectionFile(path=path, section=name))
    if len({section_key(item.section) for item in found}) != len(found):
        return ()
    return tuple(sorted(found, key=lambda item: _natural_key(item.section)))


def section_key(name: str) -> str:
    """What two spellings of one section have in common: "Section 01",
    "section 1" and "SECTION 1" are the same section."""
    return re.sub(r"\d+", lambda match: str(int(match.group())), name.strip().lower())


@dataclass(frozen=True)
class AssignmentFolders:
    """What `find_assignment_folders` found in a folder of assignments."""

    folder: Path
    # Subfolders with their own answer key and something to grade or prepare.
    assignments: tuple[ClassFolderScan, ...] = ()
    # Subfolders that share one answer key: sections of one assignment, which
    # belong together in one folder, not as separate assignments.
    same_key: tuple[tuple[Path, ...], ...] = ()
    # Subfolders already a workspace of their own: never nested in another.
    workspaces: tuple[Path, ...] = ()

    @property
    def ready(self) -> tuple[ClassFolderScan, ...]:
        """Assignments whose submissions are read: they can be imported now."""
        return tuple(
            item
            for item in self.assignments
            if item.qa_file is not None and item.submissions is not None
        )

    @property
    def is_workspace_candidate(self) -> bool:
        """Two or more different assignments, none of them a section of
        another and none a workspace already."""
        return len(self.assignments) >= 2 and not self.same_key and not self.workspaces


_WORKSPACE_DATABASE = Path(".opengrader/workspace.sqlite3")
# The browser app's temporary session on one class folder (*Open in browser*)
# keeps a workspace database too, marked with this file
# (`core/workspace/folder_session.FOLDER_SESSION`).
_FOLDER_SESSION_MARKER = Path(".opengrader/folder-session.json")


def is_workspace(folder: str | Path) -> bool:
    """True for a real workspace: its database and no browser-session marker.

    A class folder once opened in the browser has the database as well, but
    it is still a class folder, graded from its files; `browser_launcher.
    import_folder` draws the same line.
    """
    root = Path(folder).expanduser()
    return (root / _WORKSPACE_DATABASE).is_file() and not (
        root / _FOLDER_SESSION_MARKER
    ).is_file()


def find_assignment_folders(folder: str | Path) -> AssignmentFolders:
    """The assignments in `folder`'s immediate subfolders.

    A subfolder is an assignment when it has exactly one answer key and
    either submissions or a scan to prepare. Nothing deeper is read, and the
    folder itself is not an assignment here: it is the would-be workspace.
    """
    root = Path(folder).expanduser()
    assignments: list[ClassFolderScan] = []
    workspaces: list[Path] = []
    keys: dict[str, list[Path]] = {}
    for child in _visible_children(root):
        if not child.is_dir() or child.name.lower() in _GENERATED_DIRS:
            continue
        if is_workspace(child):
            workspaces.append(child)
            continue
        try:
            found = scan_class_folder(child)
        except OSError:
            continue
        has_work = (
            found.submissions is not None
            or found.scan is not None
            or bool(found.section_scans)
        )
        if found.qa_file is None or not has_work:
            continue
        assignments.append(found)
        try:
            text = found.qa_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        keys.setdefault(" ".join(text.split()), []).append(child)
    return AssignmentFolders(
        folder=root,
        assignments=tuple(assignments),
        same_key=tuple(tuple(paths) for paths in keys.values() if len(paths) > 1),
        workspaces=tuple(workspaces),
    )


def _natural_key(text: str) -> tuple:
    # "Section 2" before "Section 10".
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.findall(r"\d+|\D+", text)
    )


def _pdfs_in(directory: Path) -> list[Path]:
    return [
        path
        for path in _visible_children(directory)
        if path.suffix.lower() == ".pdf" and path.is_file()
    ]


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
        by_section = {
            ROLE_SCAN: result.section_scans,
            ROLE_ROSTER: result.section_rosters,
        }.get(role, ())
        if found is None and by_section:
            names = ", ".join(
                f"{_shown_name(result.folder, item.path)} ({item.section})"
                for item in by_section
            )
            lines.append(f"✓ {label:<15} one per section: {names}")
        elif found is not None:
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


def _shown_name(folder: Path, path: Path) -> str:
    try:
        return path.relative_to(folder).as_posix()
    except ValueError:
        return path.name


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


def find_roster(folder: str | Path) -> Path | None:
    """The folder's roster file, when exactly one file can be it.

    The roster part of `scan_class_folder` on its own, for a caller that
    asks often and wants nothing else read (a browser session re-reads the
    roster on every sync). Two candidates are no answer, as in the scan.
    """
    root = Path(folder).expanduser()
    return _pick(
        ROLE_ROSTER,
        [
            path
            for path in _visible_children(root)
            if path.is_file() and _is_roster_file(path)
        ],
        preferred=lambda path: bool(_words(path) & _ROSTER_WORDS),
        ambiguities=[],
    )


def find_answer_key_source(folder: str | Path) -> Path | None:
    """A file to make the answer key from, when exactly one looks like it.

    For a folder that has no answer key yet: the instructor's own solutions
    (`answer-key.pdf`, `solutions.docx`, …) are what Create answer key reads.
    Chosen by name only, and two candidates are no answer, as in the scan.
    """
    root = Path(folder).expanduser()
    candidates = [
        path
        for path in _visible_children(root)
        if path.is_file()
        and path.suffix.lower() in _ANSWER_SOURCE_SUFFIXES
        and not path.name.lower().endswith(".submission.md")
        and _words(path) & _ANSWER_PDF_WORDS
        and not _words(path) & _TEMPLATE_WORDS
        and not _is_qa_file(path)
    ]
    return _pick(ROLE_QA, candidates, preferred=lambda path: False, ambiguities=[])


def _is_roster_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        # The browser's roster format: a heading row, then the students.
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return False
        from opengrader.core.workspace.roster_file import csv_has_roster_headings

        return csv_has_roster_headings(text)
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
