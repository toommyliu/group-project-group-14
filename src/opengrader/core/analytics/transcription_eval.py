from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.history.restore import student_key
from opengrader.core.submissions.student_file import StudentSubmissionDocument
from opengrader.core.submissions.transcription import describe_reading_source

# Edit distance is quadratic; truncating oversized pairs would distort the error rate.
MAX_COMPARE_CHARS = 2000

DEFAULT_ERROR_TOLERANCE = 0.0

DEFAULT_TARGET_CATCH = 0.90

DEFAULT_MIN_SAMPLE = 8

FLOOR_CANDIDATES: tuple[float, ...] = tuple(float(value) for value in range(0, 101, 5))


def is_tool_actor(actor: str) -> bool:
    return ":" in actor


def normalize_transcription(text: str, *, ignore_case: bool = False) -> str:
    collapsed = " ".join(text.split())
    return collapsed.casefold() if ignore_case else collapsed


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
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
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _edit_distance(reference, hypothesis) / len(reference)


@dataclass(frozen=True)
class TranscriptionSample:
    student_key: str
    student_label: str
    question_id: str
    machine_text: str
    human_text: str
    # None means unmeasured.
    confidence: float | None
    source: str | None
    corrected_at: str
    corrected_by: str
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
        return not self.machine_text.strip()

    @property
    def too_long_to_measure(self) -> bool:
        return (
            len(self.machine_text) > MAX_COMPARE_CHARS
            or len(self.human_text) > MAX_COMPARE_CHARS
        )


@dataclass(frozen=True)
class SampleTally:
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
    ignored = {actor.strip() for actor in ignore_actors if actor.strip()}

    grouped: dict[tuple[str, str], list[GradeJournalEntry]] = {}
    transcription_entries = 0
    unattributable = 0
    for entry in entries:
        if entry.kind != "transcription":
            continue
        transcription_entries += 1
        key = student_key(entry.student_id, entry.student_name)
        if not key or not entry.question_id:
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


_CONFIDENCE_BUCKETS: tuple[tuple[str, float | None, float | None], ...] = (
    ("unmeasured", None, None),
    ("0-59", 0.0, 60.0),
    ("60-79", 60.0, 80.0),
    ("80-89", 80.0, 90.0),
    ("90-100", 90.0, 100.01),
)


@dataclass(frozen=True)
class FloorPoint:
    floor: float
    escalated: int
    caught: int
    total: int
    errors: int

    @property
    def escalation_rate(self) -> float:
        return self.escalated / self.total if self.total else 0.0

    @property
    def catch_rate(self) -> float:
        return self.caught / self.errors if self.errors else 0.0

    @property
    def precision(self) -> float:
        return self.caught / self.escalated if self.escalated else 0.0


def build_floor_curve(
    samples: Sequence[TranscriptionSample],
    *,
    ignore_case: bool = False,
    tolerance: float = DEFAULT_ERROR_TOLERANCE,
    candidates: Sequence[float] = FLOOR_CANDIDATES,
) -> tuple[FloorPoint, ...]:
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
    """Error coverage increases monotonically with the floor; the lowest qualifying floor
    minimizes model calls. None means insufficient evidence.
    """
    if not curve or curve[0].errors < min_sample:
        return None
    for point in sorted(curve, key=lambda item: item.floor):
        if point.catch_rate >= target_catch:
            return point.floor
    return None


@dataclass(frozen=True)
class CoverageStats:
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
    by_source: list[AccuracyStats] = []
    grouped_by_source: dict[str, list[TranscriptionSample]] = {}
    for sample in samples:
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
    collapsed: list[FloorPoint] = []
    for point in sorted(curve, key=lambda item: item.floor):
        if collapsed and collapsed[-1].escalated == point.escalated:
            continue
        collapsed.append(point)
    return tuple(collapsed)


def format_percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def source_breakdown(report: TranscriptionEvalReport) -> Mapping[str, AccuracyStats]:
    return {stats.label: stats for stats in report.by_source}
