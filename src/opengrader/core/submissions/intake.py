"""Prepare a reviewed submission using the same core engines as local grading."""

from dataclasses import replace
from pathlib import Path
from opengrader.core import app_config
from opengrader.core.ocr import OcrConfig
from opengrader.core.submissions.batch import ExtractionJob, run_extraction_batch
from opengrader.core.submissions.transcription import DEFAULT_ESCALATION_FLOOR
from opengrader.core.grading.automated import grade_student_file, RunJournal

from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.submissions.student_file import (
    load_student_submission_markdown,
    render_student_submission_markdown,
)


class SubmissionIntake:
    """Resolve assignment and model settings once for a batch of reviewed PDFs."""

    def __init__(self, qa_file: Path, *, auto_grade: bool, assignment_version: str | None):
        self.qa = parse_qa_markdown(qa_file.read_text())
        self.auto_grade = auto_grade
        self.assignment_version = assignment_version
        self.ai = app_config.resolve()
        self.vision = None
        if self.ai.kind == "cloud" and not (self.ai.read_handwriting or self.ai.escalate_only):
            # Extraction without a blank template hands every answer crop to
            # whatever reader it is given. With handwriting reading off, a
            # cloud provider must receive none of them — AI setup and the
            # data-flow report both say so — so no reader is built at all
            # and the answers are read by OCR.
            return
        try:
            self.vision = app_config.vision_reader(self.ai)
            app_config.ensure_ready(self.ai, purpose="vision")
        except (app_config.CloudConsentRequired, app_config.CloudDisabledError) as exc:
            if self.ai.read_handwriting or self.ai.escalate_only:
                raise ValueError(
                    f"{exc} Run 'opengrader providers consent {self.ai.provider.name}' "
                    "(or open AI Setup) to allow it, or turn off handwriting "
                    "reading, and retry."
                ) from exc
            self.vision = None
        except Exception as exc:
            if self.ai.read_handwriting or self.ai.escalate_only:
                raise ValueError(
                    "The configured handwriting model is unavailable. Start the model server and retry."
                ) from exc
            self.vision = None

    def extract(self, pdf: Path, output: Path) -> Path:
        """Write to staging only; the caller owns publishing completed submissions."""
        pdf, output = pdf.resolve(), output.resolve()
        report = run_extraction_batch(
            [ExtractionJob(
                pdf_path=pdf, output_dir=output, ocr_config=OcrConfig(),
                vision_reader=self.vision,
                llm_responses=self.ai.read_handwriting or self.ai.escalate_only,
                llm_math=self.ai.transcribe_math, math_backend=self.ai.math_backend,
                escalate_floor=DEFAULT_ESCALATION_FLOOR if self.ai.escalate_only else None,
            )],
            workers=1,
        )
        if report.failed:
            raise ValueError(f"Could not read this submission: {report.failed[0].error}")
        markdown = output / pdf.stem / f"{pdf.stem}.submission.md"
        document = load_student_submission_markdown(markdown)
        versions = list(self.qa.version_questions)
        version = self.assignment_version or document.assignment_version
        if versions and (version not in versions and (version or len(versions) > 1)):
            raise ValueError("Exam version could not be matched. Choose the version for this scan and retry.")
        # Publication moves this directory, so source and crop references must survive it.
        document = replace(
            document, source_pdf=str(pdf.resolve()), assignment_version=version,
            questions=[
                replace(question, response_image_path=Path(question.response_image_path).relative_to(markdown.parent).as_posix())
                if question.response_image_path else question for question in document.questions
            ],
        )
        if not document.questions:
            raise ValueError("No answers could be located. Check the scan layout and resolution before retrying.")
        markdown.write_text(render_student_submission_markdown(document))
        if self.auto_grade:
            grade_student_file(markdown, self.qa, journal=RunJournal(markdown))
        return markdown.parent
