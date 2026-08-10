"""Where a response's transcription came from, and when to try harder.

Two small, closely-related decisions live here, both pure and both shared by
every extraction path so they can never drift apart:

1. **Provenance** — which reader produced the ``Response text`` on disk. The
   pipeline has five of them (a PDF text layer, Tesseract, the Ollama vision
   model, a dedicated maths model, and the bubble-sheet measurer) plus a sixth
   that matters most, the grader who retyped it. Until now the only trace of
   that choice was ``response_confidence``, and the convention that a ``None``
   meant "a model read it". That convention was right and is still true, but it
   cannot tell pix2tex from the vision model, cannot tell an exact text-layer
   read from a human correction (both sit at 100), and cannot say anything at
   all about a bubble sheet. So the reader is now *recorded* rather than
   inferred — which is what lets `transcribe-eval` report accuracy **per
   backend** instead of per confidence band.

2. **Escalation** — whether a reading is poor enough to be worth asking a
   slower, better reader. This is the decision behind
   ``submission-extractor --llm-escalate``: run the cheap reader over every
   crop, then spend a vision-model call only on the answers the cheap reader
   doubted.

The escalation rule is deliberately the *same* rule the review queue flags at
and the refine pass re-reads at (:data:`review.DEFAULT_OCR_CONFIDENCE_FLOOR`),
imported rather than repeated. Three floors for one idea ("this reading is not
good enough") would mean escalating answers nobody was worried about while
leaving flagged ones alone, and an instructor who tuned one number would have no
way to know the other two existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from opengrader.core.submissions.review import DEFAULT_OCR_CONFIDENCE_FLOOR

# ── Provenance ──────────────────────────────────────────────────────────────
#
# These strings are written into `.submission.md` (the `Read by:` line), so they
# are part of the on-disk format: keep them stable, lowercase and hyphenated,
# and add a new one rather than repurposing an old one. They are deliberately
# self-describing, because the person most likely to read one is an instructor
# looking at the file in a text editor and wondering why an answer is wrong.

#: Read exactly out of a born-digital PDF's text layer. Not a *reading* at all —
#: the characters are in the file — which is why it carries confidence 100.
SOURCE_TEXT_LAYER = "text-layer"
#: Tesseract OCR. The only reader that produces a measured confidence.
SOURCE_TESSERACT = "tesseract"
#: The Ollama vision model transcribing handwriting. No calibrated confidence.
SOURCE_VISION_MODEL = "vision-model"
#: A dedicated image-to-LaTeX model (`core/ocr/math_ocr.py`, e.g. pix2tex).
SOURCE_MATH_MODEL = "math-model"
#: Measured off a generated bubble sheet (`core/ocr/omr.py`) — not text
#: recognition at all, which is exactly why it is worth distinguishing.
SOURCE_BUBBLE_SHEET = "bubble-sheet"
#: A human typed it (the grade screen's `x` editor). The ground truth every
#: other reader is measured against, and never a target for re-reading.
SOURCE_HUMAN = "human"

KNOWN_READING_SOURCES: tuple[str, ...] = (
    SOURCE_TEXT_LAYER,
    SOURCE_TESSERACT,
    SOURCE_VISION_MODEL,
    SOURCE_MATH_MODEL,
    SOURCE_BUBBLE_SHEET,
    SOURCE_HUMAN,
)

# Readings produced by a model. Grouped because the two questions callers
# actually ask are "did a person write this?" and "did a *model* write this?" —
# a model reading carries no measured confidence whichever model it was.
MODEL_READING_SOURCES: frozenset[str] = frozenset(
    {SOURCE_VISION_MODEL, SOURCE_MATH_MODEL}
)

READING_SOURCE_LABELS: Mapping[str, str] = {
    SOURCE_TEXT_LAYER: "PDF text layer",
    SOURCE_TESSERACT: "Tesseract OCR",
    SOURCE_VISION_MODEL: "vision model",
    SOURCE_MATH_MODEL: "maths model",
    SOURCE_BUBBLE_SHEET: "bubble sheet",
    SOURCE_HUMAN: "grader",
}

#: What to call a reading whose source was never recorded — every answer
#: extracted before this field existed. Deliberately *not* folded into any of
#: the real sources: "we don't know" is its own answer, the same rule
#: `PageScanFeatures` follows for an unmeasurable page.
UNRECORDED_SOURCE_LABEL = "unrecorded"


def describe_reading_source(source: str | None) -> str:
    """Human-facing name for a stored source string.

    An unknown value is returned as-is rather than mapped to "unknown": a file
    written by a newer version records a reader this one has never heard of, and
    showing its name is far more use than hiding it.
    """
    if not source:
        return UNRECORDED_SOURCE_LABEL
    return READING_SOURCE_LABELS.get(source, source)


def is_model_reading(source: str | None) -> bool:
    """Whether a stored source names a model (vision or maths)."""
    return source in MODEL_READING_SOURCES


# ── Escalation ──────────────────────────────────────────────────────────────

#: Below this measured confidence a transcription is worth a second, better
#: reader. The review queue's floor, imported — see the module docstring.
DEFAULT_ESCALATION_FLOOR: float = float(DEFAULT_OCR_CONFIDENCE_FLOOR)


def should_escalate(
    text: str,
    confidence: float | None,
    *,
    floor: float = DEFAULT_ESCALATION_FLOOR,
) -> bool:
    """Whether this reading is poor enough to be worth a model's time.

    Two ways to qualify, and one deliberate way not to:

    * **Nothing was read.** An empty transcription is the strongest possible
      signal that the cheap reader was the wrong tool for this crop — a
      handwritten answer Tesseract simply cannot see. This case is checked
      first and does not consult the confidence, because a reader that produced
      no words often reports no number either.
    * **A measured confidence below** ``floor``. The reading exists and the
      reader itself says it is shaky.
    * An **unmeasured** confidence (``None``) with real text does *not*
      escalate. That is the convention the whole codebase keeps — ``None`` means
      nobody measured it, never "it is bad" — and it is what stops an escalation
      pass from re-reading answers a model already transcribed, which would
      spend the slowest step in the pipeline asking the same model the same
      question about the same crop.
    """
    if not text.strip():
        return True
    if confidence is None:
        return False
    return confidence < floor


@dataclass(frozen=True)
class ReadingTally:
    """How many answers each reader produced, for a run's closing report.

    Counted from the documents that were actually written rather than from the
    decisions that were taken, so it reports what is on disk: an escalation that
    was attempted and then rejected (the model read nothing, so the OCR text was
    kept) correctly counts as a Tesseract reading.
    """

    counts: tuple[tuple[str, int], ...] = ()

    @property
    def total(self) -> int:
        return sum(count for _source, count in self.counts)

    def count_of(self, source: str) -> int:
        for name, count in self.counts:
            if name == source:
                return count
        return 0

    def merge(self, other: "ReadingTally") -> "ReadingTally":
        merged: dict[str, int] = {name: count for name, count in self.counts}
        for name, count in other.counts:
            merged[name] = merged.get(name, 0) + count
        return ReadingTally(counts=_ordered_counts(merged))

    def describe(self) -> str:
        """"Tesseract OCR 176, vision model 34" — ordered, most-used first."""
        if not self.counts:
            return "no responses transcribed"
        return ", ".join(
            f"{describe_reading_source(source)} {count}"
            for source, count in self.counts
        )


def _ordered_counts(counts: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    """Largest first, ties broken by name so the order is total and stable."""
    return tuple(
        sorted(
            ((source, count) for source, count in counts.items() if count),
            key=lambda item: (-item[1], item[0]),
        )
    )


def tally_reading_sources(sources: Iterable[str | None]) -> ReadingTally:
    """Count readings by source, folding never-recorded ones into one bucket."""
    counts: dict[str, int] = {}
    for source in sources:
        key = source or UNRECORDED_SOURCE_LABEL
        counts[key] = counts.get(key, 0) + 1
    return ReadingTally(counts=_ordered_counts(counts))
