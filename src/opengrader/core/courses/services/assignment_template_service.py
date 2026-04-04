from __future__ import annotations

import json
import re
import shutil
import unicodedata
import hashlib
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
    return _slugify(f"{course.code}-{course.name}-{course.term}-{course.section}")


def _assignment_directory_name(assignment_id: int, assignment_title: str) -> str:
    return f"{assignment_id}-{_slugify(assignment_title)}"


def _template_directory_name(template_id: int, template_name: str) -> str:
    return f"{_slugify(template_name)}-t{template_id}"


def _sha256_for(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sha256_for_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _pending_spec_json(template_name: str) -> str:
    payload = {
        "schema_version": "1.0",
        "unit_id": _slugify(template_name),
        "pages": [],
        "meta": {
            "status": "pending_spec",
            "message": "Spec not uploaded yet. Generate or edit a spec and replace this placeholder.",
        },
    }
    return json.dumps(payload, indent=2) + "\n"


class AssignmentTemplateService:
    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction_factory = transaction_factory

    def create_template(
        self,
        *,
        assignment_id: int,
        name: str,
        source_template_pdf: str | Path,
        source_spec_json: str | Path | None = None,
    ) -> AssignmentTemplate:
        source_pdf = Path(source_template_pdf).expanduser().resolve()
        if not source_pdf.exists():
            raise FileNotFoundError(f"template PDF not found: {source_pdf}")

        source_spec: Path | None = None
        pending_spec_content: str | None = None
        if source_spec_json is not None:
            source_spec = Path(source_spec_json).expanduser().resolve()
            if not source_spec.exists():
                raise FileNotFoundError(f"spec JSON not found: {source_spec}")
            spec_sha256 = _sha256_for(source_spec)
            spec_filename = "spec.json"
        else:
            pending_spec_content = _pending_spec_json(name)
            spec_sha256 = _sha256_for_bytes(pending_spec_content.encode("utf-8"))
            spec_filename = "spec.pending.json"

        with self._transaction_factory() as transaction:
            assignment = transaction.assignments.get_by_id(assignment_id)
            if assignment is None:
                raise AssignmentNotFoundError(
                    f"assignment {assignment_id} does not exist"
                )

            course = transaction.courses.get_by_id(assignment.course_id)
            if course is None:
                raise CourseNotFoundError(
                    f"course {assignment.course_id} does not exist"
                )

            created = transaction.assignment_templates.create(
                course_id=assignment.course_id,
                name=name,
                template_pdf_relpath="pending/template.pdf",
                spec_json_relpath="pending/spec.json",
                template_sha256=_sha256_for(source_pdf),
                spec_sha256=spec_sha256,
            )

            data_dir = get_data_dir()
            base_dir = (
                data_dir
                / "courses"
                / _course_directory_name(course)
                / "assignments"
                / _assignment_directory_name(assignment.id, assignment.title)
                / "templates"
                / _template_directory_name(created.id, name)
            )
            base_dir.mkdir(parents=True, exist_ok=True)

            pdf_dest = base_dir / "template.pdf"
            spec_dest = base_dir / spec_filename

            shutil.copyfile(source_pdf, pdf_dest)
            if source_spec is not None:
                shutil.copyfile(source_spec, spec_dest)
            else:
                assert pending_spec_content is not None
                spec_dest.write_text(pending_spec_content, encoding="utf-8")

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

    def replace_assignment_templates(
        self,
        *,
        assignment_id: int,
        template_ids: list[int],
        current_template_id: int,
    ) -> None:
        unique_template_ids = list(dict.fromkeys(template_ids))
        if not unique_template_ids:
            raise ValueError("at least one template is required")
        if current_template_id not in unique_template_ids:
            raise ValueError("current template must be included in template_ids")

        with self._transaction_factory() as transaction:
            assignment = transaction.assignments.get_by_id(assignment_id)
            if assignment is None:
                raise AssignmentNotFoundError(
                    f"assignment {assignment_id} does not exist"
                )

            for template_id in unique_template_ids:
                template = transaction.assignment_templates.get_by_id(template_id)
                if template is None:
                    raise AssignmentTemplateNotFoundError(
                        f"template {template_id} does not exist"
                    )
                if template.course_id != assignment.course_id:
                    raise ValueError("template course does not match assignment course")

            transaction.assignment_templates.replace_assignment_templates(
                assignment_id=assignment_id,
                template_ids=unique_template_ids,
                current_template_id=current_template_id,
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

    def list_assignment_templates(self, assignment_id: int) -> list[AssignmentTemplate]:
        with self._transaction_factory() as transaction:
            assignment = transaction.assignments.get_by_id(assignment_id)
            if assignment is None:
                raise AssignmentNotFoundError(
                    f"assignment {assignment_id} does not exist"
                )
            return transaction.assignment_templates.list_for_assignment(assignment_id)
