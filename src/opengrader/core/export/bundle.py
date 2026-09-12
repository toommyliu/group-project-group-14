from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from opengrader.core.analytics.item_analysis import (
    build_item_analysis,
    build_outcome_report,
)
from opengrader.core.qa_file import (
    outcomes_by_question,
    possible_points_by_question,
)
from opengrader.core.curve import Curve, read_curve
from opengrader.core.pdf.analysis_report import (
    render_analysis_report_pdf,
    score_distribution_from_totals,
)
from opengrader.core.export.gradebook import (
    build_gradebook_rows,
    discover_submission_paths,
    write_gradebook_csv,
)
from opengrader.core.pdf.feedback import build_annotated_pdf
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    load_student_submission_markdown,
)

_GRADEBOOK_DIR = "gradebook"
_STUDENT_PDF_DIR = "student-pdfs"
_ANALYSIS_DIR = "analysis"

_GRADEBOOK_FILENAME = "gradebook.csv"
_ANALYSIS_FILENAME = "analysis-report.pdf"

ProgressFn = Callable[[str], None]


@dataclass(frozen=True)
class BundleContent:
    gradebook: bool = False
    student_pdfs: bool = False
    analysis: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.gradebook or self.student_pdfs or self.analysis)


BUNDLE_PRESETS: dict[str, BundleContent] = {
    "all": BundleContent(gradebook=True, student_pdfs=True, analysis=True),
    "gradebook": BundleContent(gradebook=True),
    "student-pdfs": BundleContent(student_pdfs=True),
    "gradebook-student": BundleContent(gradebook=True, student_pdfs=True),
    "analysis": BundleContent(analysis=True),
}


@dataclass(frozen=True)
class BundleResult:
    bundle_dir: Path
    gradebook_path: Path | None = None
    student_pdf_paths: tuple[Path, ...] = ()
    analysis_path: Path | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def student_count(self) -> int:
        return len(self.student_pdf_paths)


def _slugify(text: str, *, fallback: str = "assignment") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or fallback


def default_bundle_name(
    documents: list[StudentSubmissionDocument],
    *,
    today: date | None = None,
) -> str:
    today = today or date.today()
    title = ""
    for document in documents:
        if document.assignment_title.strip():
            title = document.assignment_title
            break
    return f"{_slugify(title)}-return-{today.isoformat()}"


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^\w.\- ]+", "", text).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned.strip("._") or "student"


def _student_pdf_stem(
    document: StudentSubmissionDocument,
    *,
    anonymous: bool,
) -> str:
    name = document.student_name.strip()
    student_id = document.student_id.strip()
    if anonymous or not name:
        return _safe_filename(student_id or "unidentified")
    parts = name.split()
    if len(parts) >= 2:
        readable = f"{parts[-1]}_{'_'.join(parts[:-1])}"
    else:
        readable = name
    if student_id:
        readable = f"{readable}_{student_id}"
    return _safe_filename(readable)


def _resolve_source_pdf(submission_path: Path, source_pdf: str) -> Path:
    source = Path(source_pdf).expanduser()
    if not source.is_absolute():
        source = submission_path.parent / source
    return source.resolve()


def _noop(_message: str) -> None:
    return None


def build_return_bundle(
    submissions_path: str | Path,
    output_dir: str | Path,
    *,
    content: BundleContent = BUNDLE_PRESETS["all"],
    name: str | None = None,
    curve: Curve | None = None,
    anonymous: bool = False,
    progress: ProgressFn | None = None,
    qa_document: object | None = None,
) -> BundleResult:
    """curve=None uses the assignment's saved curve."""
    emit = progress or _noop
    submissions_path = Path(submissions_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser()

    if content.is_empty:
        raise ValueError("no bundle content selected — choose at least one document")

    paths = discover_submission_paths(submissions_path)
    if not paths:
        raise ValueError(f"no *.submission.md files found in {submissions_path}")

    emit(f"Loading {len(paths)} submission(s)…")
    documents = [load_student_submission_markdown(path) for path in paths]
    if curve is None:
        curve = read_curve(submissions_path)

    bundle_name = name.strip() if name and name.strip() else default_bundle_name(documents)
    bundle_dir = output_dir / bundle_name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    emit(f"Bundle folder: {bundle_dir}")

    warnings: list[str] = []
    gradebook_path: Path | None = None
    student_pdf_paths: list[Path] = []
    analysis_path: Path | None = None

    if content.gradebook:
        gradebook_path = bundle_dir / _GRADEBOOK_DIR / _GRADEBOOK_FILENAME
        emit("Writing gradebook CSV…")
        write_gradebook_csv(build_gradebook_rows(documents, curve), gradebook_path)

    if content.student_pdfs:
        pdf_dir = bundle_dir / _STUDENT_PDF_DIR
        pdf_dir.mkdir(parents=True, exist_ok=True)
        used_stems: set[str] = set()
        for path, document in zip(paths, documents, strict=True):
            stem = _unique_stem(_student_pdf_stem(document, anonymous=anonymous), used_stems)
            source_pdf = _resolve_source_pdf(path, document.source_pdf)
            if not source_pdf.exists():
                warnings.append(
                    f"{document.student_id or document.student_name or path.name}: "
                    f"source PDF not found ({document.source_pdf}); no annotated PDF."
                )
                continue
            emit(f"Annotating {stem}.pdf…")
            annotated = build_annotated_pdf(
                document=document,
                source_pdf_path=source_pdf,
                annotated_pdf_path=pdf_dir / f"{stem}.pdf",
                curve=curve,
                anonymous=anonymous,
            )
            student_pdf_paths.append(annotated)

    if content.analysis:
        emit("Building analysis report…")
        analysis_path = _build_analysis(bundle_dir, documents, curve, qa_document)

    _write_manifest(
        bundle_dir,
        documents=documents,
        content=content,
        gradebook_path=gradebook_path,
        student_pdf_paths=student_pdf_paths,
        analysis_path=analysis_path,
        anonymous=anonymous,
        curve=curve,
    )

    emit("Done.")
    return BundleResult(
        bundle_dir=bundle_dir,
        gradebook_path=gradebook_path,
        student_pdf_paths=tuple(student_pdf_paths),
        analysis_path=analysis_path,
        warnings=tuple(warnings),
    )


def _unique_stem(stem: str, used: set[str]) -> str:
    candidate = stem
    counter = 2
    while candidate in used:
        candidate = f"{stem}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _build_analysis(
    bundle_dir: Path,
    documents: list[StudentSubmissionDocument],
    curve: Curve,
    qa_document: object | None = None,
) -> Path:
    report = build_item_analysis(documents)
    outcomes = None
    if qa_document is not None:
        tags = outcomes_by_question(qa_document)  # type: ignore[arg-type]
        if tags:
            outcomes = build_outcome_report(
                documents,
                tags,
                possible_points_by_question=possible_points_by_question(
                    qa_document  # type: ignore[arg-type]
                ),
            )
    totals = [
        (
            sum(q.points_awarded for q in doc.questions if q.points_awarded is not None),
            sum(q.possible_points for q in doc.questions if q.possible_points is not None),
        )
        for doc in documents
    ]
    distribution = score_distribution_from_totals(totals)

    title = ""
    for document in documents:
        if document.assignment_title.strip():
            title = document.assignment_title
            if document.assignment_version:
                title = f"{title} ({document.assignment_version})"
            break
    title = title or "Assignment"

    return render_analysis_report_pdf(
        bundle_dir / _ANALYSIS_DIR / _ANALYSIS_FILENAME,
        report=report,
        distribution=distribution,
        title=title,
        outcomes=outcomes,
    )


def _write_manifest(
    bundle_dir: Path,
    *,
    documents: list[StudentSubmissionDocument],
    content: BundleContent,
    gradebook_path: Path | None,
    student_pdf_paths: list[Path],
    analysis_path: Path | None,
    anonymous: bool,
    curve: Curve,
) -> None:
    lines = [
        "Opengrader return bundle",
        "=" * 24,
        f"Generated: {date.today().isoformat()}",
        f"Students:  {len(documents)}",
    ]
    if anonymous:
        lines.append("Mode:      anonymous (student names withheld)")
    if curve.is_active:
        lines.append("Curve:     active (curved totals shown alongside raw)")
    lines.append("")
    lines.append("Contents")
    lines.append("-" * 8)
    if content.gradebook and gradebook_path is not None:
        lines.append(
            f"{_GRADEBOOK_DIR}/{_GRADEBOOK_FILENAME}"
            "  — class gradebook: one row per student, one column per question."
        )
    if content.student_pdfs:
        lines.append(
            f"{_STUDENT_PDF_DIR}/  — {len(student_pdf_paths)} annotated PDF(s), one per "
            "student, each with per-question comments and a Grade Summary page."
        )
    if content.analysis and analysis_path is not None:
        lines.append(
            f"{_ANALYSIS_DIR}/{_ANALYSIS_FILENAME}"
            "  — class analysis: score distribution and per-question item analysis."
        )
    (bundle_dir / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
