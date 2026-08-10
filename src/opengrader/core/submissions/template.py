"""The structural template a scan is cropped against — one shape, many sources.

Extraction only ever needs to know four things about an assignment: what it is
called, where the student's name and id go, where each answer goes, and how many
pages it runs to. :class:`AssignmentTemplate` is that answer, and it is the
*only* structural input the scan-matching pipeline in
``core/submissions/processor.py`` consumes. Everything downstream of it — the
per-page offset alignment, the crop of each answer region, OCR, the vision-model
pass, ``--llm-escalate``, ``--resume`` — is written against this shape and knows
nothing about where it came from.

That is deliberate, and it is why this lives in its own module rather than
inside the processor that first defined it. There is more than one honest way to
learn an exam's geometry:

* **Read it off a generated blank.** ``processor._load_blank_assignment_template``
  parses the text layer of a blank Opengrader template PDF, matching the
  ``N. (M points) …`` question lines it wrote itself. Exact, free, and only
  possible for an exam Opengrader generated.
* **Have the instructor draw it.** ``core/region_map.py`` loads a saved region
  map — boxes drawn once on a blank copy of an exam written in Word, in LaTeX,
  or photocopied from paper — and converts it here. No text layer required,
  which is the entire point: this is how an exam Opengrader did *not* generate
  gets graded.

A third or fourth source can be added the same way, and none of them may fork
the extraction path. Two structural pipelines would double the surface area and
drift apart within a release.

Decisions recorded here, not to be reversed
-------------------------------------------

**Normalized coordinates, always.** Every bbox is ``(x, y, w, h)`` as fractions
of its page, matching ``pdf_utils.bbox_norm_to_px`` / ``crop_norm``. A template
therefore survives being applied at any DPI, to any paper size, and to a rescan
at different scanner settings — which a pixel or a PDF-point box would not.

**Pages are 1-based**, like every other page number in the codebase
(``QuestionSourceSegment.page_number``, ``render_pdf_page``, the splitter's
sidecars). Off-by-one here mis-crops a whole class, so there is one convention.

**Plain values only.** A template is carried into a worker process by
``core/submissions/batch.ExtractionJob``, which must stay picklable: frozen
dataclasses of str/float/tuple, never a callable, a file handle, or an open
``fitz.Document``.

``exact_regions`` is the one flag that changes how the processor treats the
questions, and it exists because the two sources above mean genuinely different
things by "the question's bbox" — see the field comment.
"""

from __future__ import annotations

from dataclasses import dataclass

from .student_file import StudentSubmissionQuestion


@dataclass(frozen=True)
class FieldLocation:
    """Where an identity field (name or student id) sits on the paper.

    ``bbox`` is the *crop* region — the blank space a student writes into —
    not the printed label next to it.
    """

    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # normalized [x, y, w, h]


@dataclass(frozen=True)
class AssignmentTemplate:
    """Everything extraction needs to know about an assignment's layout.

    ``questions`` are carried as :class:`StudentSubmissionQuestion` values with
    empty responses — the same type the finished document holds — so the
    template's prompt, points, question type and source bbox flow straight into
    the student's ``.submission.md`` without a second conversion step. Their
    ``source_segments`` hold the answer region: the first segment is the primary
    one and any others are continuations onto later pages.
    """

    assignment_title: str
    name_field: FieldLocation | None
    student_id_field: FieldLocation | None
    questions: tuple[StudentSubmissionQuestion, ...]
    total_pages: int
    # Are the question bboxes the answer regions themselves, or merely where the
    # question *starts*?
    #
    # A template read off a generated blank's text layer knows only where each
    # question's first printed line is; the answer region below it is inferred
    # at crop time from the next question's position, so the recorded bbox is a
    # y-anchor and its width/height are not meaningful. The processor also
    # measures each scan's vertical shift by matching those anchors against the
    # question prefixes it OCRs on the paper.
    #
    # A region map is the opposite: an instructor drew the actual answer box, so
    # the bbox is the whole truth and re-deriving it would throw that away. With
    # this flag set the processor crops the recorded rectangles verbatim, skips
    # the alignment OCR entirely (nothing consumes it, and it is the expensive
    # part of the path), and takes the identity fields from the template rather
    # than from label detection — on someone else's exam there may be no
    # "Name:" label to find, and the drawn box is better evidence than a guess.
    exact_regions: bool = False
