from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path

from opengrader.core import get_data_dir
from opengrader.core.courses.contracts.transaction import TransactionFactory
from opengrader.core.courses.domain.entities import Course
from opengrader.core.courses.domain.entities import AssignmentTemplate
from opengrader.core.courses.domain.errors import (
    AssignmentNotFoundError,
    AssignmentTemplateNotFoundError,
    CourseNotFoundError,
)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", ascii_value.lower()).strip("-")
    return slug or "item"


def _course_directory_name(course: Course) -> str:
    return f"{course.id}-{_slugify(course.name)}"


def _template_directory_name(template_id: int, template_name: str) -> str:
    return f"{template_id}-{_slugify(template_name)}"


def _sha256_for(path: Path) -> str:
    import hashlib

    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class AssignmentTemplateService:
    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction_factory = transaction_factory

    def create_template(
        self,
        *,
        course_id: int,
        name: str,
        source_template_pdf: str | Path,
        source_spec_json: str | Path,
    ) -> AssignmentTemplate:
        source_pdf = Path(source_template_pdf).expanduser().resolve()
        source_spec = Path(source_spec_json).expanduser().resolve()
        if not source_pdf.exists():
            raise FileNotFoundError(f"template PDF not found: {source_pdf}")
        if not source_spec.exists():
            raise FileNotFoundError(f"spec JSON not found: {source_spec}")

        with self._transaction_factory() as transaction:
            course = transaction.courses.get_by_id(course_id)
            if course is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")

            created = transaction.assignment_templates.create(
                course_id=course_id,
                name=name,
                template_pdf_relpath="pending/template.pdf",
                spec_json_relpath="pending/spec.json",
                template_sha256=_sha256_for(source_pdf),
                spec_sha256=_sha256_for(source_spec),
            )

            data_dir = get_data_dir()
            base_dir = (
                data_dir
                / "courses"
                / _course_directory_name(course)
                / "templates"
                / _template_directory_name(created.id, name)
            )
            base_dir.mkdir(parents=True, exist_ok=True)

            pdf_dest = base_dir / "template.pdf"
            spec_dest = base_dir / "spec.json"

            shutil.copyfile(source_pdf, pdf_dest)
            shutil.copyfile(source_spec, spec_dest)

            rel_pdf = str(pdf_dest.relative_to(data_dir))
            rel_spec = str(spec_dest.relative_to(data_dir))

            transaction.assignment_templates.update_paths(
                template_id=created.id,
                template_pdf_relpath=rel_pdf,
                spec_json_relpath=rel_spec,
            )
            transaction.commit()
            return transaction.assignment_templates.get_by_id(created.id) or created

    def list_templates_for_course(self, course_id: int) -> list[AssignmentTemplate]:
        with self._transaction_factory() as transaction:
            if transaction.courses.get_by_id(course_id) is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")
            return transaction.assignment_templates.list_for_course(course_id)

    def set_assignment_template(
        self,
        *,
        assignment_id: int,
        template_id: int,
    ) -> None:
        with self._transaction_factory() as transaction:
            assignment = transaction.assignments.get_by_id(assignment_id)
            if assignment is None:
                raise AssignmentNotFoundError(
                    f"assignment {assignment_id} does not exist"
                )
            template = transaction.assignment_templates.get_by_id(template_id)
            if template is None:
                raise AssignmentTemplateNotFoundError(
                    f"template {template_id} does not exist"
                )
            if template.course_id != assignment.course_id:
                raise ValueError("template course does not match assignment course")
            transaction.assignment_templates.set_assignment_template(
                assignment_id=assignment_id,
                template_id=template_id,
            )
            transaction.commit()

    def get_assignment_template(self, assignment_id: int) -> AssignmentTemplate | None:
        with self._transaction_factory() as transaction:
            assignment = transaction.assignments.get_by_id(assignment_id)
            if assignment is None:
                raise AssignmentNotFoundError(
                    f"assignment {assignment_id} does not exist"
                )
            return transaction.assignment_templates.get_current_for_assignment(
                assignment_id
            )
