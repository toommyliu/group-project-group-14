"""Draft an answer key from the blank assignment, answers left for the teacher.

A class folder with a blank template but no answer key already says what the
questions are and what each is worth: it is printed on the template. Reading
that gives a key grading can open with straight away — every question, its
points (the standard ones where the template prints none), the usual rubric —
and nothing else: no answer is ever guessed. The teacher fills those in when
they want auto-grading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from opengrader.core.qa_file.standard_rubric import DEFAULT_QUESTION_POINTS
from opengrader.core.qa_file.text2qti import parse_text2qti, render_qa, summarize_points

# Print that belongs to the page, not to a question: "Name: ____", "Page 2 of 4".
_PAGE_FURNITURE_RE = re.compile(
    r"^\s*(?:(?:name|student\s*id|id)\b\s*(?::|$)|(?:page\s*)?\d{1,2}(?:\s*(?:of|/)\s*\d{1,2})?\s*$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DraftKey:
    markdown: str
    questions: int
    # Questions or parts worth grading, and how many of them got the default points.
    graded: int
    defaulted: int


def draft_from_text(
    text: str, *, title: str = "", default_points: float = DEFAULT_QUESTION_POINTS
) -> DraftKey | None:
    """None when the text holds no questions, or does not make a valid key."""
    from opengrader.core.qa_file.markdown import parse_qa_markdown

    lines = [line for line in text.splitlines() if not _PAGE_FURNITURE_RE.match(line)]
    parsed = parse_text2qti("\n".join(lines))
    if not parsed.questions:
        return None
    markdown = render_qa(
        parsed,
        title_override=parsed.title or title,
        default_points=default_points,
        blank_answers=True,
    )
    try:
        parse_qa_markdown(markdown)
    except ValueError:
        # Better no draft than one grading cannot open.
        return None
    summary = summarize_points(parsed)
    return DraftKey(
        markdown=markdown,
        questions=summary.questions,
        graded=summary.graded,
        defaulted=summary.defaulted,
    )


def draft_from_blank_template(
    template: str | Path, *, default_points: float = DEFAULT_QUESTION_POINTS
) -> DraftKey | None:
    """The draft for a blank template PDF that carries its text; None for one
    that does not (a scanned template), or that cannot be read."""
    # Imported here: the PDF reader brings OpenCV with it.
    from opengrader.core.qa_file.pdf_answer_key import pdf_text_lines

    template = Path(template)
    try:
        text = pdf_text_lines(template)
    except Exception:  # noqa: BLE001 - an unreadable template just has no draft
        return None
    title = re.sub(r"[-_]+", " ", template.stem).strip()
    return draft_from_text(text, title=title, default_points=default_points)
