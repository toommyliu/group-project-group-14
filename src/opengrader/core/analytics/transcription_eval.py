"""How good are the machine's transcriptions? Measured, from work already done.

Every reading in the pipeline is trusted by something downstream: the grader
reads ``Response text`` and not the scan, the LLM grader is *only* given the
text, auto-grading matches on it, and answer grouping decides who shares an
answer by it. There was an evidence harness for LLM *grading*
(`core/grading/evaluation.py`) and none at all for the transcription underneath
it — the confidence floors were sensible numbers nobody had measured.

**The ground truth already exists and costs nothing to collect.** When a grader
fixes a misread answer with the grade screen's ``x`` editor, the audit journal
records a ``transcription`` entry whose ``old`` is exactly what the machine read
— with the confidence it claimed and the reader that produced it — and whose
``new`` is what a human says it should have been. That is a labelled pair per
correction, accumulating in every session anybody has ever graded. This module
turns that log into an accuracy report.

Three decisions carry the honesty of the output:

1. **Only a human's correction is ground truth.** A refine pass
   (``submission-extractor --refine``) writes transcription entries too, and
   they are machine-to-machine: scoring against one would measure a model
   against another model and call it accuracy. Tool actors are excluded by the
   codebase's own convention that a tool names itself ``tool:role``
   (``submission-extractor:refine``, ``automated-grading:llm``, ``merge:<id>``),
   and :func:`is_tool_actor` is the one place that rule lives.

2. **The machine reading is the one the human actually replaced**, i.e. the
   ``old`` of their *first* correction — not the original extraction. If a
   refine already re-read the answer, the human corrected *that*, and the
   payload records which reader it was. The reference is the ``new`` of their
   *last* correction, so a grader who fixed a typo in their own fix is measured
   against what they settled on. (Same "first entry's ``old``" idiom
   `history/restore.py` uses for its pre-journal baseline.)

3. **The sample is biased and the report says so.** A grader corrects answers
   they *looked at*, and they look at the ones the review queue flagged — which
   are the low-confidence ones. So this measures "when a human checked, how
   often was the machine wrong", never "how often is the machine wrong". The
   sampled share is reported beside every rate for exactly that reason:
   an answer nobody checked is not evidence that it was right.

Everything here is pure — it takes journal entries and parsed documents — so the
whole harness unit-tests without a folder, a scanner or a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.history.restore import student_key
from opengrader.core.submissions.student_file import StudentSubmissionDocument
from opengrader.core.submissions.transcription import describe_reading_source

# A correction pair longer than this is measured by nobody: the edit distance is
# quadratic, and an exam answer is a sentence or two. Rather than silently
# truncating (which would report a made-up rate) an oversized pair is excluded
# and *counted*, so a corpus full of them is visible instead of invented.
MAX_COMPARE_CHARS = 2000

# Below this character error rate a reading counts as correct. Zero by default —
# a single wrong character in a numeric answer is a wrong answer — but exposed
# because "close enough" is genuinely different for a 200-word essay.
DEFAULT_ERROR_TOLERANCE = 0.0

# The share of measured errors an escalation floor should catch. The default is
# deliberately high: the cost of escalating an answer is one model call, and the
# cost of missing one is a wrong grade.
DEFAULT_TARGET_CATCH = 0.90

# Fewer measured errors than this and no floor is recommended. Three corrections
# is not a finding about a scanner.
DEFAULT_MIN_SAMPLE = 8

# Candidate floors, coarse enough to read as a table and fine enough to choose
# from. 0 means "never escalate", 100 means "escalate every measured reading".
FLOOR_CANDIDATES: tuple[float, ...] = tuple(float(value) for value in range(0, 101, 5))


def is_tool_actor(actor: str) -> bool:
    """Whether a journal actor is a tool rather than a person.

    The convention is the codebase's own and is enforced at every write site: a
    tool journals under ``<tool>:<role>`` (``automated-grading:llm``,
    ``submission-extractor:refine``, ``merge:<packet-id>``) while a person
    journals under their bare OS/`OPENGRADER_GRADER` name. A grader whose name
    genuinely contains a colon would be misread as a tool, which costs their
    corrections from the sample — the safe direction, since the alternative is
    scoring a model against another model's output and printing it as accuracy.
    """
    return ":" in actor


def normalize_transcription(text: str, *, ignore_case: bool = False) -> str:
    """Collapse the differences that are not transcription errors.

    Runs of whitespace become one space and the ends are stripped, because a
    line break inside an answer box is a layout artefact rather than something
    the reader got wrong. **Punctuation and case are kept** — a missing decimal
    point changes a numeric answer and a lost capital is a real misread — with
    ``ignore_case`` available for the instructor who disagrees about the second.
    """
    collapsed = " ".join(text.split())
    return collapsed.casefold() if ignore_case else collapsed


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Levenshtein distance over characters or tokens (two-row DP).

    Deliberately the exact edit distance rather than a `difflib` ratio: CER/WER
    are *defined* as this quantity, and `similarity.py`'s choice of difflib was
    for a different job (an explainable similarity score for a human to judge),
    not for a metric that gets compared against published numbers.
    """
    if not reference:
        return len(hypothesis)
    if not hypothesis:
        return len(reference)

    previous = list(range(len(hypothesis) + 1))
    for row, ref_item in enumerate(reference, start=1):
        current = [row]
        for column, hyp_item in enumerate(hypothesis, start=1):
            substitution = previous[column - 1] + (0 if ref_item == hyp_item else 1)
            deletion = previous[column] + 1
            insertion = current[column - 1] + 1
            current.append(min(substitution, deletion, insertion))
        previous = current
    return previous[-1]


def _rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    """Edit distance normalized by the reference length.

    Two empty sides are a perfect reading (0.0). A non-empty hypothesis against
    an empty reference is a total failure (1.0) rather than an infinity: the
    machine invented text where the student wrote none, which is as wrong as a
    reading can be and should not dominate an average as a special value.
    """
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _edit_distance(reference, hypothesis) / len(reference)


@dataclass(frozen=True)
class TranscriptionSample:
    """One machine reading beside the human correction that replaced it."""

    student_key: str
    student_label: str
    question_id: str
    machine_text: str
    human_text: str
    # What the machine claimed about its own reading, as recorded at the moment
    # the human replaced it. ``None`` = never measured (a model reading, or an
    # entry from before confidence was journalled) — never "bad".
    confidence: float | None
    # Which reader produced `machine_text`; ``None`` when never recorded.
    source: str | None
    corrected_at: str
    corrected_by: str
    # Which pooled folder this correction came from; empty for a single cohort.
    # Part of the grouping key, because two terms of the same course number
    # their students from s1 and would otherwise merge into one sample.
    cohort: str = ""

    def character_error_rate(self, *, ignore_case: bool = False) -> float:
        reference = normalize_transcription(self.human_text, ignore_case=ignore_case)
        hypothesis = normalize_transcription(self.machine_text, ignore_case=ignore_case)
        return _rate(reference, hypothesis)

    def word_error_rate(self, *, ignore_case: bool = False) -> float:
        reference = normalize_transcription(self.human_text, ignore_case=ignore_case).split()
        hypothesis = normalize_transcription(self.machine_text, ignore_case=ignore_case).split()
        return _rate(reference, hypothesis)

    @property
    def machine_read_nothing(self) -> bool:
        """The reader produced no text at all — a failure of a different kind.

        Counted apart from a *wrong* reading because the fixes differ: an empty
        transcription is a crop, resolution or reader-choice problem, while a
        garbled one is the reader being asked something it cannot do well.
        """
        return not self.machine_text.strip()

    @property
    def too_long_to_measure(self) -> bool:
        return (
            len(self.machine_text) > MAX_COMPARE_CHARS
            or len(self.human_text) > MAX_COMPARE_CHARS
        )


@dataclass(frozen=True)
class SampleTally:
    """Why journal entries did not become samples. Reported, never silent."""

    transcription_entries: int = 0
    tool_only: int = 0
    unattributable: int = 0
    oversized: int = 0

    @property
    def excluded(self) -> int:
        return self.tool_only + self.unattributable + self.oversized

    def merge(self, other: "SampleTally") -> "SampleTally":
        return SampleTally(
            transcription_entries=self.transcription_entries + other.transcription_entries,
            tool_only=self.tool_only + other.tool_only,
            unattributable=self.unattributable + other.unattributable,
            oversized=self.oversized + other.oversized,
        )


def collect_transcription_samples(
    entries: Sequence[GradeJournalEntry],
    *,
    ignore_actors: Iterable[str] = (),
    cohort: str = "",
) -> tuple[tuple[TranscriptionSample, ...], SampleTally]:
    """Fold a journal into (machine reading, human correction) pairs.

    One sample per corrected answer, however many times it was corrected: the
    machine reading is the ``old`` of the first human entry and the reference is
    the ``new`` of the last, so a grader refining their own fix is measured
    against what they settled on rather than counted twice.

    Call it **once per folder** when pooling and pass a distinct ``cohort``:
    student ids are only unique within a cohort, so two terms of the same
    course would otherwise collapse two different people's corrections into one
    sample and silently discard the second.
    """
    ignored = {actor.strip() for actor in ignore_actors if actor.strip()}

    # (student, question) → entries, in the journal's own (time-sorted) order.
    grouped: dict[tuple[str, str], list[GradeJournalEntry]] = {}
    transcription_entries = 0
    unattributable = 0
    for entry in entries:
        if entry.kind != "transcription":
            continue
        transcription_entries += 1
        key = student_key(entry.student_id, entry.student_name)
        if not key or not entry.question_id:
            # Nothing to file it under: two corrections to two different
            # anonymous submissions would otherwise merge into one sample.
            unattributable += 1
            continue
        grouped.setdefault((key, entry.question_id), []).append(entry)

    samples: list[TranscriptionSample] = []
    tool_only = 0
    oversized = 0
    for (key, question_id), question_entries in grouped.items():
        human_entries = [
            entry
            for entry in question_entries
            if not is_tool_actor(entry.actor) and entry.actor not in ignored
        ]
        if not human_entries:
            # Only a machine ever touched this reading, so there is no verdict
            # to score it against.
            tool_only += 1
            continue

        first, last = human_entries[0], human_entries[-1]
        sample = TranscriptionSample(
            student_key=key,
            student_label=(first.student_name or first.student_id or key),
            cohort=cohort,
            question_id=question_id,
            machine_text=str(first.old.get("response_text", "") or ""),
            human_text=str(last.new.get("response_text", "") or ""),
            confidence=_optional_float(first.old.get("response_confidence")),
            source=_optional_source(first.old.get("response_source")),
            corrected_at=first.timestamp,
            corrected_by=first.actor,
        )
        if sample.too_long_to_measure:
            oversized += 1
            continue
        samples.append(sample)

    samples.sort(
        key=lambda item: (item.corrected_at, item.cohort, item.student_key, item.question_id)
    )
    return (
        tuple(samples),
        SampleTally(
            transcription_entries=transcription_entries,
            tool_only=tool_only,
            unattributable=unattributable,
            oversized=oversized,
        ),
    )


def _optional_float(raw: object) -> float | None:
    try:
        return None if raw is None else float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _optional_source(raw: object) -> str | None:
    return raw.strip() or None if isinstance(raw, str) else None


@dataclass(frozen=True)
class AccuracyStats:
    """Accuracy over one slice of the samples (all of them, or one reader)."""

    label: str
    samples: int = 0
    exact: int = 0
    blank_readings: int = 0
    mean_cer: float = 0.0
    median_cer: float = 0.0
    mean_wer: float = 0.0

    @property
    def exact_rate(self) -> float:
        return self.exact / self.samples if self.samples else 0.0

    @property
    def error_rate(self) -> float:
        """The share of *checked* readings a human had to change."""
        return 1.0 - self.exact_rate


def _summarize(
    label: str,
    samples: Sequence[TranscriptionSample],
    *,
    ignore_case: bool,
    tolerance: float,
) -> AccuracyStats:
    if not samples:
        return AccuracyStats(label=label)
    cers = sorted(sample.character_error_rate(ignore_case=ignore_case) for sample in samples)
    wers = [sample.word_error_rate(ignore_case=ignore_case) for sample in samples]
    middle = len(cers) // 2
    median = cers[middle] if len(cers) % 2 else (cers[middle - 1] + cers[middle]) / 2
    return AccuracyStats(
        label=label,
        samples=len(samples),
        exact=sum(1 for value in cers if value <= tolerance),
        blank_readings=sum(1 for sample in samples if sample.machine_read_nothing),
        mean_cer=sum(cers) / len(cers),
        median_cer=median,
        mean_wer=sum(wers) / len(wers),
    )


# Confidence buckets, in the order they are reported. The unmeasured bucket is
# first and named, not folded into "0-59": a reading with no number is not a
# reading that scored zero, and saying so is the whole point of the `None`
# convention. It holds two different things — a model reading, which never
# carries a number, and an OCR pass that read nothing and so had nothing to
# score — which is why the label says only what is certain and the *blank*
# count beside it separates them.
_CONFIDENCE_BUCKETS: tuple[tuple[str, float | None, float | None], ...] = (
    ("unmeasured", None, None),
    ("0-59", 0.0, 60.0),
    ("60-79", 60.0, 80.0),
    ("80-89", 80.0, 90.0),
    ("90-100", 90.0, 100.01),
)


@dataclass(frozen=True)
class FloorPoint:
    """What one escalation floor would have cost and caught, in hindsight."""

    floor: float
    # Readings this floor would send to a model (measured confidence below it,
    # or nothing read at all — the same rule `should_escalate` applies).
    escalated: int
    # Of those, how many really were wrong.
    caught: int
    total: int
    errors: int

    @property
    def escalation_rate(self) -> float:
        return self.escalated / self.total if self.total else 0.0

    @property
    def catch_rate(self) -> float:
        """The share of measured errors this floor would have re-read."""
        return self.caught / self.errors if self.errors else 0.0

    @property
    def precision(self) -> float:
        """Of the answers escalated, the share that needed it.

        Not a target to maximize — a low precision only means model calls spent
        on readings that were already right, which is cheap. It is here because
        it is the cost side of the trade the floor makes.
        """
        return self.caught / self.escalated if self.escalated else 0.0


def build_floor_curve(
    samples: Sequence[TranscriptionSample],
    *,
    ignore_case: bool = False,
    tolerance: float = DEFAULT_ERROR_TOLERANCE,
    candidates: Sequence[float] = FLOOR_CANDIDATES,
) -> tuple[FloorPoint, ...]:
    """What each candidate ``--escalate-confidence`` would have done here.

    A pure re-slice of results already collected (the same discipline
    `evaluation.threshold_curve` keeps), so the whole curve costs one pass over
    the samples rather than one pass per candidate.

    Only samples whose confidence was *measured* can be judged by a floor at
    all; a model reading (``None``) is never escalated by
    :func:`~core.submissions.transcription.should_escalate` and is excluded here
    for the same reason, so the rates describe the population the flag actually
    governs. An empty reading is escalated at every floor, matching that same
    function's first rule.
    """
    judged = [sample for sample in samples if sample.confidence is not None or sample.machine_read_nothing]
    errors = [
        sample
        for sample in judged
        if sample.character_error_rate(ignore_case=ignore_case) > tolerance
    ]

    points: list[FloorPoint] = []
    for floor in candidates:
        escalated = [sample for sample in judged if _would_escalate(sample, floor)]
        caught = [sample for sample in errors if _would_escalate(sample, floor)]
        points.append(
            FloorPoint(
                floor=floor,
                escalated=len(escalated),
                caught=len(caught),
                total=len(judged),
                errors=len(errors),
            )
        )
    return tuple(points)


def _would_escalate(sample: TranscriptionSample, floor: float) -> bool:
    """Mirror of `transcription.should_escalate` over a recorded sample."""
    if sample.machine_read_nothing:
        return True
    if sample.confidence is None:
        return False
    return sample.confidence < floor


def recommend_floor(
    curve: Sequence[FloorPoint],
    *,
    target_catch: float = DEFAULT_TARGET_CATCH,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> float | None:
    """The cheapest floor that catches ``target_catch`` of the measured errors.

    **The lowest qualifying floor is the right one here**, which is the opposite
    of `evaluation.recommend_threshold`'s rule — and the difference is a
    property of the two quantities, not a change of mind. LLM agreement is *not*
    monotonic in the confidence threshold, so the lowest cutoff that hits a
    target can be a lucky dip under a bad stretch. Escalation coverage **is**
    monotonic: raising the floor can only add readings to the escalated set, so
    it can only catch more errors. The lowest qualifying floor is therefore the
    same catch at the fewest model calls, with nothing hiding underneath it.

    Returns ``None`` when too few errors were measured to recommend anything —
    a rate over three corrections is arithmetic, not evidence.
    """
    if not curve or curve[0].errors < min_sample:
        return None
    for point in sorted(curve, key=lambda item: item.floor):
        if point.catch_rate >= target_catch:
            return point.floor
    return None


@dataclass(frozen=True)
class CoverageStats:
    """How much of the cohort this measurement actually saw.

    The single most important number in the report, and the reason it is
    computed from the *documents* rather than the journal: a rate over the
    answers a human checked says nothing about the ones nobody opened, and a
    reader who cannot see the sampled share will read it as if it did.
    """

    answers_with_text: int = 0
    answers_corrected: int = 0

    @property
    def sampled_share(self) -> float:
        return (
            self.answers_corrected / self.answers_with_text
            if self.answers_with_text
            else 0.0
        )

    def merge(self, other: "CoverageStats") -> "CoverageStats":
        return CoverageStats(
            answers_with_text=self.answers_with_text + other.answers_with_text,
            answers_corrected=self.answers_corrected + other.answers_corrected,
        )


def build_coverage(
    documents: Sequence[StudentSubmissionDocument],
    samples: Sequence[TranscriptionSample],
) -> CoverageStats:
    """Count the transcribed answers, and how many of them were ever checked."""
    answers = sum(
        1
        for document in documents
        for question in document.questions
        if (question.response_text or "").strip() or question.response_image_path
    )
    return CoverageStats(
        answers_with_text=answers,
        answers_corrected=len(
            {(sample.cohort, sample.student_key, sample.question_id) for sample in samples}
        ),
    )


@dataclass(frozen=True)
class TranscriptionEvalReport:
    """Everything the CLI prints and `--json` serializes."""

    overall: AccuracyStats
    by_source: tuple[AccuracyStats, ...] = ()
    by_confidence: tuple[AccuracyStats, ...] = ()
    curve: tuple[FloorPoint, ...] = ()
    recommended_floor: float | None = None
    coverage: CoverageStats = field(default_factory=CoverageStats)
    tally: SampleTally = field(default_factory=SampleTally)
    worst: tuple[TranscriptionSample, ...] = ()
    ignore_case: bool = False
    tolerance: float = DEFAULT_ERROR_TOLERANCE
    # Carried so the renderer can state what the recommendation was aiming at;
    # a floor with no stated target is a number the reader cannot argue with.
    target_catch: float = DEFAULT_TARGET_CATCH

    @property
    def has_samples(self) -> bool:
        return self.overall.samples > 0


def build_transcription_eval_report(
    entries: Sequence[GradeJournalEntry],
    documents: Sequence[StudentSubmissionDocument] = (),
    *,
    ignore_case: bool = False,
    tolerance: float = DEFAULT_ERROR_TOLERANCE,
    target_catch: float = DEFAULT_TARGET_CATCH,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    ignore_actors: Iterable[str] = (),
    worst_count: int = 5,
) -> TranscriptionEvalReport:
    """The whole measurement, from one cohort's journal and its submissions.

    The single-folder convenience over :func:`build_report_from_samples`. A
    caller pooling several folders must collect each one separately (student
    ids are only unique within a cohort) and call that instead.
    """
    samples, tally = collect_transcription_samples(entries, ignore_actors=ignore_actors)
    return build_report_from_samples(
        samples,
        coverage=build_coverage(documents, samples),
        tally=tally,
        ignore_case=ignore_case,
        tolerance=tolerance,
        target_catch=target_catch,
        min_sample=min_sample,
        worst_count=worst_count,
    )


def build_report_from_samples(
    samples: Sequence[TranscriptionSample],
    *,
    coverage: CoverageStats = CoverageStats(),
    tally: SampleTally = SampleTally(),
    ignore_case: bool = False,
    tolerance: float = DEFAULT_ERROR_TOLERANCE,
    target_catch: float = DEFAULT_TARGET_CATCH,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    worst_count: int = 5,
) -> TranscriptionEvalReport:
    """Aggregate already-collected samples, however many folders they came from.

    Split out from the function above so pooling stays honest: the collection
    step is per-cohort (its grouping key has to be) while every rate, bucket and
    curve below is over the pool, and one function doing both would have to
    choose one or the other.
    """
    by_source: list[AccuracyStats] = []
    grouped_by_source: dict[str, list[TranscriptionSample]] = {}
    for sample in samples:
        # `describe_reading_source` already answers `None` with the unrecorded
        # label, so every reader — including "we never recorded one" — is named
        # by the same function the rest of the app names it by.
        grouped_by_source.setdefault(describe_reading_source(sample.source), []).append(
            sample
        )
    for label in sorted(grouped_by_source, key=lambda name: (-len(grouped_by_source[name]), name)):
        by_source.append(
            _summarize(
                label,
                grouped_by_source[label],
                ignore_case=ignore_case,
                tolerance=tolerance,
            )
        )

    by_confidence = tuple(
        _summarize(
            label,
            [sample for sample in samples if _in_bucket(sample.confidence, low, high)],
            ignore_case=ignore_case,
            tolerance=tolerance,
        )
        for label, low, high in _CONFIDENCE_BUCKETS
    )

    curve = build_floor_curve(
        samples, ignore_case=ignore_case, tolerance=tolerance
    )
    worst = tuple(
        sorted(
            samples,
            key=lambda sample: (
                -sample.character_error_rate(ignore_case=ignore_case),
                sample.student_key,
                sample.question_id,
            ),
        )[:worst_count]
    )

    return TranscriptionEvalReport(
        overall=_summarize("all readings", samples, ignore_case=ignore_case, tolerance=tolerance),
        by_source=tuple(by_source),
        # Empty buckets are dropped here rather than in the renderer: a table of
        # mostly-zero rows buries the two buckets that have data in them.
        by_confidence=tuple(stats for stats in by_confidence if stats.samples),
        curve=curve,
        recommended_floor=recommend_floor(
            curve, target_catch=target_catch, min_sample=min_sample
        ),
        coverage=coverage,
        tally=tally,
        worst=worst,
        ignore_case=ignore_case,
        tolerance=tolerance,
        target_catch=target_catch,
    )


def _in_bucket(confidence: float | None, low: float | None, high: float | None) -> bool:
    if low is None or high is None:
        return confidence is None
    return confidence is not None and low <= confidence < high


def collapse_curve(curve: Sequence[FloorPoint]) -> tuple[FloorPoint, ...]:
    """Drop consecutive floors that would escalate exactly the same readings.

    The same rule `cli/llm_eval.py` applies to its threshold table and for the
    same reason: a dozen identical rows buries the shape of the curve. The
    **lowest** floor of each run is kept — the smallest setting that produces
    that behaviour, and therefore the boundary at which the behaviour changed,
    which is the row a reader is actually choosing between.
    """
    collapsed: list[FloorPoint] = []
    for point in sorted(curve, key=lambda item: item.floor):
        if collapsed and collapsed[-1].escalated == point.escalated:
            continue
        collapsed.append(point)
    return tuple(collapsed)


def format_percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def source_breakdown(report: TranscriptionEvalReport) -> Mapping[str, AccuracyStats]:
    """Per-reader stats keyed by label, for a caller that wants a lookup."""
    return {stats.label: stats for stats in report.by_source}
