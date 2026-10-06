from __future__ import annotations

import json
import os
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from opengrader.core.submissions.extra_pages import (
    read_extra_pages_map,
    resolve_extra_pages_path,
    write_extra_pages_map,
)
from opengrader.core.submissions.splitter import read_scan_warnings, write_scan_warnings

from .split import SIDECAR_SUFFIX, describe_order, order_breaks, order_keys, order_warning, read_sidecar, sorted_order

ORDER_REVIEW_FILENAME = "page-order.json"
PENDING = "pending"
KEPT = "kept"
SORTED = "sorted"
# Rearranged by hand between runs; the pages now run forwards.
FIXED = "fixed"
_LEFT_IN_PLACE = "they were left where they were scanned"
_AT_THE_END = "they are kept at the end"


@dataclass(frozen=True)
class OrderFinding:
    name: str
    keys: tuple[int | None, ...]
    by_template: bool = False
    shared: bool = False
    warning: str = ""
    status: str = PENDING
    stamp: str = ""

    @property
    def recommended(self) -> bool:
        # Only a blank template makes the order certain enough to sort by default: page numbers
        # read off a scan can be misread, and a break the whole class shares usually is one.
        return self.by_template and not self.shared

    @property
    def read(self) -> str:
        return describe_order(self.keys, self.by_template)

    @property
    def sorted_as(self) -> str:
        return ", ".join(str(self.keys[i]) for i in sorted_order(self.keys) if self.keys[i] is not None)


def review_path(split_dir: str | Path) -> Path:
    return resolve_extra_pages_path(split_dir).with_name(ORDER_REVIEW_FILENAME)


def write_order_review(
    split_dir: str | Path, findings: Sequence[OrderFinding], template_positions: Mapping[str, int] | None = None,
) -> Path:
    directory = Path(split_dir)
    path = review_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "template_positions": dict(template_positions or {}),
        "submissions": [
            {
                "name": f.name, "keys": list(f.keys), "by_template": f.by_template, "shared": f.shared,
                "warning": f.warning, "status": f.status, "stamp": f.stamp or _stamp(directory / f"{f.name}.pdf"),
            }
            for f in findings
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def read_order_review(split_dir: str | Path) -> tuple[list[OrderFinding], dict[str, int]]:
    # Absent or malformed means nothing to review; it never blocks a run.
    try:
        raw = json.loads(review_path(split_dir).read_text())
        findings = [
            OrderFinding(
                name=str(item["name"]),
                keys=tuple(None if key is None else int(key) for key in item["keys"]),
                by_template=bool(item.get("by_template")), shared=bool(item.get("shared")),
                warning=str(item.get("warning", "")), status=str(item.get("status", PENDING)),
                stamp=str(item.get("stamp", "")),
            )
            for item in raw["submissions"]
        ]
        positions = {str(group): int(position) for group, position in raw.get("template_positions", {}).items()}
    except (OSError, ValueError, KeyError, TypeError):
        return [], {}
    return findings, positions


def pending_findings(split_dir: str | Path) -> list[OrderFinding]:
    # A split PDF changed since it was judged is read again first, so pages someone put in
    # order by hand stop being asked about.
    directory = Path(split_dir)
    findings, positions = read_order_review(directory)
    if not findings:
        return []
    refreshed = [_refresh(directory, finding, positions) for finding in findings]
    current = [finding for finding in refreshed if finding is not None]
    if current != findings:
        write_order_review(directory, current, positions)
    return [finding for finding in current if finding.status == PENDING]


def sort_pages(split_dir: str | Path, names: Collection[str]) -> list[str]:
    return _resolve(Path(split_dir), names, SORTED)


def keep_pages(split_dir: str | Path, names: Collection[str]) -> list[str]:
    return _resolve(Path(split_dir), names, KEPT)


def _resolve(directory: Path, names: Collection[str], status: str) -> list[str]:
    pending_findings(directory)
    findings, positions = read_order_review(directory)
    done: list[str] = []
    for index, finding in enumerate(findings):
        if finding.status != PENDING or finding.name not in names:
            continue
        pdf = directory / f"{finding.name}.pdf"
        if status == SORTED:
            if not _sort_pdf(directory, pdf, finding):
                continue
        # Answered, so it leaves the review queue; this file keeps the record of what was decided.
        _swap_warning(pdf, finding.warning, None, moved=status == SORTED)
        findings[index] = replace(finding, warning="", status=status, stamp=_stamp(pdf))
        done.append(finding.name)
    if done:
        write_order_review(directory, findings, positions)
    return done


def describe_pending(pending: Sequence[OrderFinding]) -> list[str]:
    lines = []
    for number, finding in enumerate(pending, start=1):
        note = "sort recommended" if finding.recommended else (
            "same break as most of the class: probably a misread page number" if finding.shared
            else "read off the scan, so it may be a misread")
        lines.append(f"{number}. {finding.name}.pdf  {finding.read}  ({note})")
    return lines


def review_in_terminal(split_dir: str | Path, *, ask: Callable[[str], str], write: Callable[[str], None]) -> bool:
    # True once nothing is left to confirm. Sorting is offered as the default only on a
    # recommendation; otherwise the default leaves the pages as scanned.
    directory = Path(split_dir)
    pending = pending_findings(directory)
    if not pending:
        return True
    recommended = [finding.name for finding in pending if finding.recommended]
    everyone = [finding.name for finding in pending]
    write(f"\n{len(pending)} submission(s) look out of order. Confirm their page order before extraction:")
    for line in describe_pending(pending):
        write(f"  {line}")
    write(f"  in {directory}")
    write("")
    if recommended:
        write(f"  s  sort the {len(recommended)} recommended, keep the rest as scanned")
    write("  a  sort all of them")
    write("  k  keep all as scanned (they are in order, or the order does not matter)")
    write("  c  choose which to sort")
    write("  q  stop here: check or rearrange the PDFs yourself, then run again")
    default = "s" if recommended else "k"
    while True:
        try:
            answer = ask(f"Choice [{default}]: ").strip().lower() or default
        except EOFError:
            answer = "q"
        if answer == "q":
            write("Stopped before extraction. Nothing was moved.")
            return False
        if answer == "s" and recommended:
            chosen = recommended
        elif answer == "a":
            chosen = everyone
        elif answer == "k":
            chosen = []
        elif answer == "c":
            try:
                picked = ask("Numbers to sort, separated by commas (blank for none): ")
            except EOFError:
                picked = ""
            numbers = {int(part) for part in picked.replace(",", " ").split() if part.isdigit()}
            chosen = [finding.name for number, finding in enumerate(pending, start=1) if number in numbers]
        else:
            continue
        sorted_names = sort_pages(directory, chosen)
        kept_names = keep_pages(directory, [name for name in everyone if name not in sorted_names])
        if sorted_names:
            write(f"Sorted: {', '.join(sorted_names)}")
        if kept_names:
            write(f"Kept as scanned: {', '.join(kept_names)}")
        return True


def _sort_pdf(directory: Path, pdf: Path, finding: OrderFinding) -> bool:
    from pypdf import PdfReader, PdfWriter

    order = sorted_order(finding.keys)
    reader = PdfReader(str(pdf))
    if len(reader.pages) != len(order):
        return False
    writer = PdfWriter()
    for index in order:
        writer.add_page(reader.pages[index])
    temporary = pdf.with_suffix(".pdf.tmp")
    with open(temporary, "wb") as handle:
        writer.write(handle)
    os.replace(temporary, pdf)

    sidecar = read_sidecar(pdf)
    pages = sidecar.get("pages") if isinstance(sidecar, dict) else None
    if isinstance(pages, list) and len(pages) == len(order):
        _write_groups(directory, pdf, [pages[index].get("group") for index in order], [pages[index].get("page_number") for index in order])
    return True


def _write_groups(directory: Path, pdf: Path, groups: Sequence[str | None], numbers: Sequence[int | None]) -> None:
    pdf.with_suffix(SIDECAR_SUFFIX).write_text(json.dumps({
        "pages": [{"group": group, "page_number": number} for group, number in zip(groups, numbers)],
    }, indent=1) + "\n")
    extra = read_extra_pages_map(directory)
    if pdf.stem in extra:
        extra[pdf.stem] = tuple(position for position, group in enumerate(groups, start=1) if group is None)
        write_extra_pages_map(directory, extra)


def _swap_warning(pdf: Path, old: str, new: str | None, *, moved: bool = False) -> None:
    try:
        warnings = list(read_scan_warnings(pdf))
    except ValueError:
        return
    kept = [warning for warning in warnings if warning != old]
    if moved:
        kept = [warning.replace(_LEFT_IN_PLACE, _AT_THE_END) for warning in kept]
    if new:
        kept.insert(0, new)
    if kept != warnings:
        write_scan_warnings(pdf, tuple(kept))


def _refresh(directory: Path, finding: OrderFinding, positions: Mapping[str, int]) -> OrderFinding | None:
    pdf = directory / f"{finding.name}.pdf"
    if not pdf.exists():
        return None
    stamp = _stamp(pdf)
    if finding.status != PENDING or stamp == finding.stamp:
        return finding
    from .catalog import CATALOG_DIRNAME, load_catalog
    from .extract import page_groups

    try:
        catalog = load_catalog(directory / CATALOG_DIRNAME)
        groups = page_groups(pdf, catalog)
    except Exception:  # noqa: BLE001 - an unreadable PDF or catalog leaves the question open
        return finding
    keys, by_template = order_keys(catalog, groups, positions)
    _write_groups(directory, pdf, groups, [catalog.groups[gid].page_number if gid else None for gid in groups])
    if not order_breaks(keys):
        _swap_warning(pdf, finding.warning, None)
        return replace(finding, keys=tuple(keys), by_template=by_template, shared=False, warning="", status=FIXED, stamp=stamp)
    warning = order_warning(keys, by_template=by_template)
    _swap_warning(pdf, finding.warning, warning)
    return replace(finding, keys=tuple(keys), by_template=by_template, shared=False, warning=warning, stamp=stamp)


def _stamp(pdf: Path) -> str:
    try:
        status = pdf.stat()
    except OSError:
        return ""
    return f"{status.st_size}:{status.st_mtime_ns}"
