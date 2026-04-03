from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from opengrader.core import get_templates_dir
from opengrader.core.courses.contracts.transaction import TransactionFactory
from opengrader.core.courses.domain.entities import AssignmentTemplate
from opengrader.core.courses.domain.errors import (
    AssignmentNotFoundError,
    AssignmentTemplateNotFoundError,
    CourseNotFoundError,
)


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
            if transaction.courses.get_by_id(course_id) is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")

            template_id = str(uuid4())
            base_dir = get_templates_dir() / f"course_{course_id}" / template_id
            base_dir.mkdir(parents=True, exist_ok=True)

            pdf_dest = base_dir / "template.pdf"
            spec_dest = base_dir / "spec.json"

            pdf_dest.write_bytes(source_pdf.read_bytes())
            spec_dest.write_bytes(source_spec.read_bytes())

            rel_pdf = str(pdf_dest.relative_to(get_templates_dir()))
            rel_spec = str(spec_dest.relative_to(get_templates_dir()))

            created = transaction.assignment_templates.create(
                course_id=course_id,
                name=name,
                template_pdf_relpath=rel_pdf,
                spec_json_relpath=rel_spec,
                template_sha256=_sha256_for(pdf_dest),
                spec_sha256=_sha256_for(spec_dest),
            )
            transaction.commit()
            return created

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
