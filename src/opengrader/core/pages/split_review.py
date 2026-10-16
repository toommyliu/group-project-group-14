"""Confirm where a scan was cut into submissions before extraction reads it.

The splitter decides where one student's pages end and the next student's
begin. When it gets that wrong (one sheet taken for the start of a new
submission), two neighbours come out the wrong length: a six-page
assignment split 5 + 1 while everything after it is fine. Extraction records
each answer's page by position, so the split has to be right before it runs.

After a split, `record_split_review` notes every submission whose page count
is not the expected one, or whose printed page numbers show a page missing
or repeated. Where neighbouring submissions add up to whole submissions and
every piece would start on a first page, a fix is proposed: cut those pages
again every N pages (5 + 1 → one of 6; 7 + 5 → two of 6). Nothing changes
until a person answers: apply the fix, keep the split as it is, or stop and
edit the PDFs by hand. A folder changed by hand is read again, and only what
is still wrong is asked about.

Counts leave out pages that matched no printed page (scratch paper a student
added), so extra work is never reported as a bad split. The findings live in
`<split>/.opengrader/split-review.json`, beside the page-order review;
absent or malformed means nothing to review and never blocks a run.
"""

from __future__ import annotations

import io
import json
import os
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from opengrader.core.submissions.extra_pages import (
    read_extra_pages_map,
    resolve_extra_pages_path,
    write_extra_pages_map,
)
from opengrader.core.submissions.splitter import write_scan_warnings

from .split import SIDECAR_SUFFIX, read_sidecar

SPLIT_REVIEW_FILENAME = "split-review.json"
PENDING = "pending"
# The proposed cut was made.
APPLIED = "applied"
# A person looked and said the split is right as it is.
KEPT = "kept"
# Changed by hand between runs, and now nothing is wrong.
FIXED = "fixed"
# How many neighbouring submissions one proposed fix may span.
_MAX_WINDOW = 4
_UNMATCHED_NOTE = (
    "{count} page(s) did not match any printed page (blank, extra work, or "
    "another document); they were left where they were scanned."
)


@dataclass(frozen=True)
class SplitPage:
    # The page number printed on it, when it could be read.
    printed: int | None = None
    # False for a page that matched no printed page: extra work, a blank.
    matched: bool = True


@dataclass(frozen=True)
class SplitPart:
    """One split PDF, page by page in file order."""

    name: str
    pages: tuple[SplitPage, ...]

    @property
    def counted(self) -> int:
        return sum(1 for page in self.pages if page.matched)

    @property
    def numbers(self) -> list[int]:
        return [page.printed for page in self.pages if page.printed is not None]


@dataclass(frozen=True)
class SplitFinding:
    """Neighbouring submissions with a problem, and the fix proposed for them."""

    names: tuple[str, ...]
    counts: tuple[int, ...]
    problems: tuple[str, ...]
    # Pages per submission after the fix (counting printed pages only); empty
    # when no fix can be proposed and only a person can tell what is right.
    proposal: tuple[int, ...] = ()
    status: str = PENDING

    @property
    def key(self) -> str:
        return self.names[0]

    @property
    def has_fix(self) -> bool:
        return bool(self.proposal)

    def describe_fix(self) -> str:
        if not self.proposal:
            return "No fix can be proposed: check these PDFs against the scan."
        pieces = " + ".join(str(count) for count in self.counts)
        each = self.proposal[0]
        if len(self.proposal) == 1:
            return f"Merge {_listed(self.names)} ({pieces} pages) into one {each}-page submission."
        return (
            f"Cut {_listed(self.names)} ({pieces} pages) again into "
            f"{len(self.proposal)} submissions of {each} pages."
        )


def _listed(names: Sequence[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def review_path(split_dir: str | Path) -> Path:
    return resolve_extra_pages_path(split_dir).with_name(SPLIT_REVIEW_FILENAME)


# ---------------------------------------------------------------- detection


def typical_submission(parts: Sequence[SplitPart]) -> tuple[int | None, list[int]]:
    """What most of the class looks like: its usual length and the printed
    page numbers more than half the submissions show.

    The page catalog's own list of pages is not used for this: one student's
    extra sheet read as "page 3" would make every two-page submission look
    short.
    """
    if not parts:
        return None, []
    seen = Counter(number for part in parts for number in set(part.numbers))
    printed = sorted(number for number, count in seen.items() if count * 2 > len(parts))
    lengths = Counter(part.counted for part in assignment_pages(parts, printed))
    expected = max(lengths, key=lambda length: (lengths[length], length)) or None
    return expected, printed


def assignment_pages(parts: Sequence[SplitPart], printed_pages: Sequence[int]) -> list[SplitPart]:
    """`parts` with a page whose printed number no usual submission shows
    counted as extra work, like a page that matched nothing: a sheet of
    scratch paper can be taken for a printed "page 3" of a two-page quiz."""
    usual = set(printed_pages)
    if not usual:
        return list(parts)
    return [
        replace(
            part,
            pages=tuple(
                replace(page, matched=False)
                if page.matched and page.printed is not None and page.printed not in usual
                else page
                for page in part.pages
            ),
        )
        for part in parts
    ]


def part_problems(
    part: SplitPart, expected: int | None, printed_pages: Sequence[int] = ()
) -> list[str]:
    """What is wrong with one submission, in words; empty when nothing is."""
    problems: list[str] = []
    if expected and part.counted != expected:
        problems.append(f"{part.counted} page(s) where {expected} are expected")
    numbers = part.numbers
    repeated = sorted({n for n in numbers if numbers.count(n) > 1})
    if repeated:
        problems.append(f"printed page(s) {', '.join(map(str, repeated))} appear more than once")
    missing = [n for n in printed_pages if n not in numbers]
    # A page whose number could not be read may be any of the missing ones.
    unread = sum(1 for page in part.pages if page.matched and page.printed is None)
    if len(missing) > unread:
        problems.append(f"printed page(s) {', '.join(map(str, missing))} missing")
    return problems


def find_split_problems(
    parts: Sequence[SplitPart],
    expected: int | None,
    printed_pages: Sequence[int] = (),
) -> list[SplitFinding]:
    parts = assignment_pages(parts, printed_pages)
    problems = [part_problems(part, expected, printed_pages) for part in parts]
    findings: list[SplitFinding] = []
    index = 0
    while index < len(parts):
        if not problems[index]:
            index += 1
            continue
        window = _recut_window(parts, index, expected)
        end = window[0] if window is not None else index
        involved = parts[index : end + 1]
        findings.append(
            SplitFinding(
                names=tuple(part.name for part in involved),
                counts=tuple(part.counted for part in involved),
                problems=tuple(
                    f"{part.name}: {problem}"
                    for part, found in zip(involved, problems[index : end + 1])
                    for problem in found
                ),
                proposal=window[1] if window is not None else (),
            )
        )
        index = end + 1
    return findings


def _recut_window(
    parts: Sequence[SplitPart], start: int, expected: int | None
) -> tuple[int, tuple[int, ...]] | None:
    """The shortest run of neighbours from `start` that cuts again into whole
    submissions, each beginning on its first page: (last index, counts)."""
    if not expected or parts[start].counted == expected:
        return None
    total = 0
    for end in range(start, min(len(parts), start + _MAX_WINDOW)):
        total += parts[end].counted
        if end == start or total % expected or parts[end].counted == expected:
            continue
        pages = [page for part in parts[start : end + 1] for page in part.pages if page.matched]
        firsts = [pages[k] for k in range(0, total, expected)]
        # A cut that would start a submission on its page 3 is not a fix.
        if all(page.printed in (None, 1) for page in firsts):
            return end, (expected,) * (total // expected)
        return None
    return None


# ------------------------------------------------------------ reading files


def read_part(split_dir: str | Path, name: str) -> SplitPart:
    """A split PDF as it is now: the sidecar when it still describes it, else
    the pages read again against the scan's page catalog."""
    from pypdf import PdfReader

    pdf = Path(split_dir) / f"{name}.pdf"
    count = len(PdfReader(str(pdf)).pages)
    sidecar = read_sidecar(pdf)
    entries = sidecar.get("pages") if isinstance(sidecar, dict) else None
    if isinstance(entries, list) and len(entries) == count:
        return SplitPart(name, tuple(_page_of(entry) for entry in entries))
    return SplitPart(name, _reread(Path(split_dir), pdf) or (SplitPage(),) * count)


def _page_of(entry: object) -> SplitPage:
    if not isinstance(entry, dict):
        return SplitPage()
    number = entry.get("page_number")
    return SplitPage(
        printed=number if isinstance(number, int) else None,
        matched=entry.get("group") is not None,
    )


def _reread(directory: Path, pdf: Path) -> tuple[SplitPage, ...] | None:
    from .catalog import CATALOG_DIRNAME, load_catalog
    from .extract import page_groups

    try:
        catalog = load_catalog(directory / CATALOG_DIRNAME)
        groups = page_groups(pdf, catalog)
    except Exception:  # noqa: BLE001 - no catalog (a typed PDF) or an unreadable file: counts only
        return None
    numbers = [catalog.groups[group].page_number if group else None for group in groups]
    _write_sidecar(pdf, groups, numbers)
    return tuple(SplitPage(number, group is not None) for group, number in zip(groups, numbers))


def _write_sidecar(pdf: Path, groups: Sequence[str | None], numbers: Sequence[int | None]) -> None:
    pdf.with_suffix(SIDECAR_SUFFIX).write_text(json.dumps({
        "pages": [{"group": group, "page_number": number} for group, number in zip(groups, numbers)],
    }, indent=1) + "\n")


def _split_names(directory: Path) -> list[str]:
    # The splitter numbers its files in scan order with zero padding, so name
    # order is scan order. macOS "._" companions on external drives are not PDFs.
    return sorted(path.stem for path in directory.glob("*.pdf") if not path.name.startswith("._"))


def _stamp(pdf: Path) -> str:
    try:
        status = pdf.stat()
    except OSError:
        return ""
    return f"{status.st_size}:{status.st_mtime_ns}"


# ------------------------------------------------------------- the record


def record_split_review(
    split_dir: str | Path,
    parts: Sequence[SplitPart],
    expected: int | None,
    printed_pages: Sequence[int] = (),
) -> list[SplitFinding]:
    """Note what is wrong with a split just written; returns the findings."""
    findings = find_split_problems(parts, expected, printed_pages)
    _write(Path(split_dir), findings, expected, printed_pages)
    return findings


def _write(
    directory: Path,
    findings: Sequence[SplitFinding],
    expected: int | None,
    printed_pages: Sequence[int],
) -> None:
    path = review_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "expected_pages": expected,
        "printed_pages": list(printed_pages),
        # Every split PDF's stamp, so a change anywhere (a page moved by hand
        # into a neighbour no finding names) is noticed.
        "stamps": {name: _stamp(directory / f"{name}.pdf") for name in _split_names(directory)},
        "findings": [
            {
                "names": list(f.names),
                "counts": list(f.counts),
                "problems": list(f.problems),
                "proposal": list(f.proposal),
                "status": f.status,
            }
            for f in findings
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def read_split_review(
    split_dir: str | Path,
) -> tuple[list[SplitFinding], int | None, list[int], dict[str, str]]:
    try:
        raw = json.loads(review_path(split_dir).read_text())
        findings = [
            SplitFinding(
                names=tuple(str(name) for name in item["names"]),
                counts=tuple(int(count) for count in item["counts"]),
                problems=tuple(str(problem) for problem in item.get("problems", [])),
                proposal=tuple(int(count) for count in item.get("proposal", [])),
                status=str(item.get("status", PENDING)),
            )
            for item in raw["findings"]
        ]
        expected = raw.get("expected_pages")
        expected = int(expected) if expected else None
        printed = [int(number) for number in raw.get("printed_pages", [])]
        stamps = {str(name): str(stamp) for name, stamp in raw.get("stamps", {}).items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [], None, [], {}
    return findings, expected, printed, stamps


def pending_split_findings(split_dir: str | Path) -> list[SplitFinding]:
    """What still needs an answer. A folder whose PDFs changed since the
    review was written is read again first, so a split someone mended by
    hand stops being asked about."""
    directory = Path(split_dir)
    findings, expected, printed, stamps = read_split_review(directory)
    if not findings:
        return []
    current = {name: _stamp(directory / f"{name}.pdf") for name in _split_names(directory)}
    if current != stamps:
        findings = _recheck(directory, findings, expected, printed)
        _write(directory, findings, expected, printed)
    return [finding for finding in findings if finding.status == PENDING]


def _recheck(
    directory: Path,
    previous: Sequence[SplitFinding],
    expected: int | None,
    printed: Sequence[int],
) -> list[SplitFinding]:
    parts = [read_part(directory, name) for name in _split_names(directory)]
    found = find_split_problems(parts, expected, printed)
    answered = {f.names: f for f in previous if f.status in (KEPT, APPLIED)}
    # An answer stands while the files it was about still read the same.
    found = [
        replace(f, status=answered[f.names].status)
        if f.names in answered and answered[f.names].counts == f.counts
        else f
        for f in found
    ]
    still = {name for f in found for name in f.names}
    # What was asked before and is no longer wrong: mended by hand, or the
    # record of an answer already given.
    settled = [
        f if f.status != PENDING else replace(f, problems=(), proposal=(), status=FIXED)
        for f in previous
        if not still.intersection(f.names)
    ]
    return [*found, *settled]


# --------------------------------------------------------------- answering


def apply_split_fixes(split_dir: str | Path, keys: Collection[str]) -> list[str]:
    """Make the proposed cut for each finding named by `keys` (its first
    submission's name); returns the keys done."""
    directory = Path(split_dir)
    pending_split_findings(directory)
    findings, expected, printed, _stamps = read_split_review(directory)
    done: list[str] = []
    for index, finding in enumerate(findings):
        if finding.status != PENDING or finding.key not in keys or not finding.has_fix:
            continue
        if not _recut(directory, finding, expected, printed):
            continue
        findings[index] = replace(finding, status=APPLIED)
        done.append(finding.key)
    if done:
        _write(directory, findings, expected, printed)
    return done


def merge_split_pdfs(split_dir: str | Path, names: Sequence[str]) -> None:
    """Join neighbouring split PDFs into the first, because a person said so
    (no length rule is checked): the others are removed. Any finding about
    them is marked as answered."""
    directory = Path(split_dir)
    findings, expected, printed, _stamps = read_split_review(directory)
    parts = assignment_pages([read_part(directory, name) for name in names], printed)
    total = sum(part.counted for part in parts)
    if total == 0:
        raise ValueError(
            f"{', '.join(names)} hold no page of the assignment, only extra work; "
            "join them to the submission they belong to instead."
        )
    finding = SplitFinding(
        names=tuple(names),
        counts=tuple(part.counted for part in parts),
        problems=(),
        proposal=(total,),
    )
    if not _recut(directory, finding, expected, printed):
        raise ValueError(f"Could not join {', '.join(names)}: a PDF is missing or changed while reading it.")
    if findings:
        joined = set(names)
        findings = [replace(f, status=APPLIED) if joined.intersection(f.names) and f.status == PENDING else f for f in findings]
        _write(directory, findings, expected, printed)


def keep_split(split_dir: str | Path, keys: Collection[str]) -> list[str]:
    directory = Path(split_dir)
    pending_split_findings(directory)
    findings, expected, printed, _stamps = read_split_review(directory)
    done: list[str] = []
    for index, finding in enumerate(findings):
        if finding.status == PENDING and finding.key in keys:
            findings[index] = replace(finding, status=KEPT)
            done.append(finding.key)
    if done:
        _write(directory, findings, expected, printed)
    return done


def _recut(
    directory: Path,
    finding: SplitFinding,
    expected: int | None,
    printed: Sequence[int],
) -> bool:
    from pypdf import PdfReader, PdfWriter

    pdfs = [directory / f"{name}.pdf" for name in finding.names]
    if not all(pdf.exists() for pdf in pdfs):
        return False
    parts = assignment_pages([read_part(directory, name) for name in finding.names], printed)
    if tuple(part.counted for part in parts) != finding.counts:
        # The files changed since the fix was worked out; ask again instead.
        return False
    # Read into memory: Windows cannot replace a file that is still open.
    readers = [PdfReader(io.BytesIO(pdf.read_bytes())) for pdf in pdfs]
    sidecars = [read_sidecar(pdf) for pdf in pdfs]
    # (reader, page index, sidecar entry) in scan order across the run.
    pages = [
        (reader, index, _entry(sidecar, index))
        for reader, sidecar in zip(readers, sidecars)
        for index in range(len(reader.pages))
    ]
    infos = [page for part in parts for page in part.pages]
    chunks = _chunk(infos, finding.proposal)
    if chunks is None:
        return False
    extra = read_extra_pages_map(directory)
    for name in finding.names:
        extra.pop(name, None)
    written: list[Path] = []
    for name, (start, end) in zip(finding.names, chunks):
        pdf = directory / f"{name}.pdf"
        writer = PdfWriter()
        for reader, index, _ in pages[start:end]:
            writer.add_page(reader.pages[index])
        temporary = pdf.with_suffix(".pdf.tmp")
        with open(temporary, "wb") as handle:
            writer.write(handle)
        written.append(temporary)
    # Every new file is complete before any old one is replaced or removed.
    for name, temporary, (start, end) in zip(finding.names, written, chunks):
        pdf = directory / f"{name}.pdf"
        os.replace(temporary, pdf)
        entries = [entry for _, _, entry in pages[start:end]]
        if all(entry is not None for entry in entries):
            pdf.with_suffix(SIDECAR_SUFFIX).write_text(
                json.dumps({"pages": entries}, indent=1) + "\n"
            )
        else:
            pdf.with_suffix(SIDECAR_SUFFIX).unlink(missing_ok=True)
        piece = SplitPart(name, tuple(infos[start:end]))
        notes = [f"{problem}." for problem in part_problems(piece, expected, printed)]
        unmatched = sum(1 for page in piece.pages if not page.matched)
        if unmatched:
            notes.append(_UNMATCHED_NOTE.format(count=unmatched))
        write_scan_warnings(pdf, tuple(notes))
        extra[name] = tuple(
            position for position, page in enumerate(piece.pages, start=1) if not page.matched
        )
    for name in finding.names[len(chunks):]:
        pdf = directory / f"{name}.pdf"
        for leftover in (pdf, pdf.with_suffix(".scan.json"), pdf.with_suffix(SIDECAR_SUFFIX)):
            leftover.unlink(missing_ok=True)
    if resolve_extra_pages_path(directory).exists():
        write_extra_pages_map(directory, extra)
    return True


def _entry(sidecar: dict | None, index: int) -> dict | None:
    entries = sidecar.get("pages") if isinstance(sidecar, dict) else None
    if isinstance(entries, list) and index < len(entries) and isinstance(entries[index], dict):
        return entries[index]
    return None


def _chunk(pages: Sequence[SplitPage], proposal: Sequence[int]) -> list[tuple[int, int]] | None:
    """Page ranges for the new submissions: each takes its share of printed
    pages, and a page that matched nothing stays with the page before it."""
    if sum(proposal) != sum(1 for page in pages if page.matched):
        return None
    ranges: list[tuple[int, int]] = []
    start = 0
    for count in proposal:
        seen = 0
        end = start
        while end < len(pages) and seen < count:
            seen += pages[end].matched
            end += 1
        # Carry the unmatched pages that follow this piece's last printed page.
        while end < len(pages) and not pages[end].matched and len(ranges) + 1 < len(proposal):
            end += 1
        ranges.append((start, end))
        start = end
    if ranges:
        ranges[-1] = (ranges[-1][0], len(pages))
    return ranges


# ------------------------------------------------------------- in a terminal


def describe_split_pending(pending: Sequence[SplitFinding]) -> list[str]:
    lines: list[str] = []
    for number, finding in enumerate(pending, start=1):
        lines.append(f"{number}. {'; '.join(finding.problems)}")
        lines.append(f"   {finding.describe_fix()}")
    return lines


def answer_split(split_dir: str | Path, fix: Sequence[str], keep: Sequence[str]) -> list[str]:
    """Apply the fixes in `fix`, keep the rest of `keep`; the fixes made."""
    applied = apply_split_fixes(split_dir, fix)
    keep_split(split_dir, [key for key in keep if key not in applied])
    return applied


def review_split_in_terminal(
    split_dir: str | Path,
    *,
    ask: Callable[[str], str],
    write: Callable[[str], None],
    answer: Callable[[Sequence[str], Sequence[str]], list[str]] | None = None,
) -> bool:
    """True once nothing is left to answer; False when the person stopped.

    `answer(fix, keep)` makes the change; the pipeline passes its own, which
    also tells the run ledger the split changed."""
    directory = Path(split_dir)
    pending = pending_split_findings(directory)
    if not pending:
        return True
    fixable = [finding.key for finding in pending if finding.has_fix]
    everyone = [finding.key for finding in pending]
    write(f"\n{len(pending)} place(s) where the split looks wrong. Check them before extraction:")
    for line in describe_split_pending(pending):
        write(f"  {line}")
    write(f"  in {directory}")
    write("")
    if fixable:
        write(f"  f  apply the {len(fixable)} proposed fix(es), keep the rest as split")
    write("  k  keep all as split (they are right)")
    if len(fixable) > 1:
        write("  c  choose which fixes to apply")
    write("  q  stop here: open the PDFs, fix them yourself, then run again")
    default = "f" if fixable else "q"
    while True:
        try:
            choice = ask(f"Choice [{default}]: ").strip().lower() or default
        except EOFError:
            choice = "q"
        if choice == "q":
            write("Stopped before extraction. Nothing was changed.")
            return False
        if choice == "f" and fixable:
            chosen = fixable
        elif choice == "k":
            chosen = []
        elif choice == "c" and len(fixable) > 1:
            try:
                picked = ask("Numbers of the fixes to apply, separated by commas (blank for none): ")
            except EOFError:
                picked = ""
            numbers = {int(part) for part in picked.replace(",", " ").split() if part.isdigit()}
            chosen = [f.key for n, f in enumerate(pending, start=1) if n in numbers and f.has_fix]
        else:
            continue
        applied = (answer or (lambda fix, keep: answer_split(directory, fix, keep)))(chosen, everyone)
        kept = [key for key in everyone if key not in applied]
        if applied:
            write(f"Fixed the split at: {', '.join(applied)}")
        if kept:
            write(f"Kept as split: {', '.join(kept)}")
        return True
