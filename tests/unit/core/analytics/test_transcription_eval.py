from __future__ import annotations

from opengrader.core.analytics.transcription_eval import (
    MAX_COMPARE_CHARS,
    build_coverage,
    build_floor_curve,
    build_transcription_eval_report,
    collapse_curve,
    collect_transcription_samples,
    is_tool_actor,
    normalize_transcription,
    recommend_floor,
)
from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)


def _correction(
    *,
    at: str = "2026-08-01T10:00:00+00:00",
    student_id: str = "s1",
    student_name: str = "Tommy Liu",
    question_id: str = "q1",
    machine: str = "Ohm's Iaw",
    human: str = "Ohm's law",
    confidence: float | None = 42.0,
    source: str | None = "tesseract",
    actor: str = "prof_kim",
) -> GradeJournalEntry:
    old: dict = {"response_text": machine}
    if confidence is not None:
        old["response_confidence"] = confidence
    if source is not None:
        old["response_source"] = source
    return GradeJournalEntry(
        timestamp=at,
        actor=actor,
        kind="transcription",
        student_id=student_id,
        student_name=student_name,
        question_id=question_id,
        summary="fixed",
        old=old,
        new={"response_text": human, "response_confidence": 100.0, "response_source": "human"},
    )


def test_character_error_rate_counts_substitutions():
    sample, _ = collect_transcription_samples([_correction(machine="cat", human="bat")])
    assert sample[0].character_error_rate() == 1 / 3


def test_word_error_rate_is_token_level():
    sample, _ = collect_transcription_samples(
        [_correction(machine="the quick brovvn fox", human="the quick brown fox")]
    )
    assert sample[0].word_error_rate() == 1 / 4


def test_a_perfect_reading_scores_zero():
    sample, _ = collect_transcription_samples(
        [_correction(machine="recursion", human="recursion")]
    )
    assert sample[0].character_error_rate() == 0.0


def test_whitespace_differences_are_not_errors():
    sample, _ = collect_transcription_samples(
        [_correction(machine="a  b\nc", human="a b c")]
    )
    assert sample[0].character_error_rate() == 0.0


def test_case_matters_unless_asked_otherwise():
    sample, _ = collect_transcription_samples([_correction(machine="Law", human="law")])
    assert sample[0].character_error_rate() > 0
    assert sample[0].character_error_rate(ignore_case=True) == 0.0


def test_punctuation_is_kept():
    sample, _ = collect_transcription_samples([_correction(machine="42", human="4.2")])
    assert sample[0].character_error_rate() > 0


def test_a_blank_reading_is_a_total_failure_and_is_counted_apart():
    sample, _ = collect_transcription_samples(
        [_correction(machine="", human="The stack is LIFO", confidence=None)]
    )
    assert sample[0].character_error_rate() == 1.0
    assert sample[0].machine_read_nothing


def test_invented_text_against_an_empty_reference_is_one_not_infinity():
    sample, _ = collect_transcription_samples([_correction(machine="noise", human="")])
    assert sample[0].character_error_rate() == 1.0


def test_normalize_only_collapses_whitespace():
    assert normalize_transcription("  a   b \n c ") == "a b c"
    assert normalize_transcription("A B", ignore_case=True) == "a b"


def test_a_tools_re_read_is_never_ground_truth():
    samples, tally = collect_transcription_samples(
        [_correction(actor="submission-extractor:refine")]
    )

    assert samples == ()
    assert tally.tool_only == 1
    assert tally.transcription_entries == 1


def test_is_tool_actor_follows_the_name_colon_role_convention():
    assert is_tool_actor("submission-extractor:refine")
    assert is_tool_actor("automated-grading:llm")
    assert is_tool_actor("merge:packet-3")
    assert not is_tool_actor("prof_kim")


def test_the_machine_reading_is_the_one_the_human_replaced():
    samples, _ = collect_transcription_samples(
        [
            _correction(
                at="2026-08-01T09:00:00+00:00",
                actor="submission-extractor:refine",
                machine="xxx",
                human="hash tabel",
                confidence=30.0,
            ),
            _correction(
                at="2026-08-01T10:00:00+00:00",
                machine="hash tabel",
                human="hash table",
                confidence=None,
                source="vision-model",
            ),
        ]
    )

    assert len(samples) == 1
    assert samples[0].machine_text == "hash tabel"
    assert samples[0].source == "vision-model"


def test_a_second_human_fix_moves_the_reference_not_the_reading():
    samples, _ = collect_transcription_samples(
        [
            _correction(at="2026-08-01T10:00:00+00:00", machine="teh cat", human="the cat"),
            _correction(at="2026-08-01T11:00:00+00:00", machine="the cat", human="the cats"),
        ]
    )

    assert len(samples) == 1
    assert samples[0].machine_text == "teh cat"
    assert samples[0].human_text == "the cats"


def test_an_unattributable_correction_is_excluded_and_counted():
    samples, tally = collect_transcription_samples(
        [_correction(student_id="", student_name="")]
    )

    assert samples == ()
    assert tally.unattributable == 1


def test_an_oversized_pair_is_excluded_rather_than_truncated():
    huge = "x" * (MAX_COMPARE_CHARS + 1)
    samples, tally = collect_transcription_samples([_correction(machine=huge, human=huge)])

    assert samples == ()
    assert tally.oversized == 1


def test_named_actors_can_be_excluded():
    samples, _ = collect_transcription_samples(
        [_correction(actor="ta_sam")], ignore_actors=["ta_sam"]
    )
    assert samples == ()


def test_non_transcription_entries_are_ignored():
    entry = GradeJournalEntry(
        timestamp="2026-08-01T10:00:00+00:00",
        actor="prof_kim",
        kind="grade",
        student_id="s1",
        student_name="Tommy",
        question_id="q1",
        summary="8/10",
    )
    samples, tally = collect_transcription_samples([entry])

    assert samples == ()
    assert tally.transcription_entries == 0


def _mixed_corrections() -> list[GradeJournalEntry]:
    entries: list[GradeJournalEntry] = []
    for index, confidence in enumerate((20.0, 40.0, 60.0, 80.0)):
        entries.append(
            _correction(
                question_id=f"wrong{index}",
                machine="teh cat",
                human="the cat",
                confidence=confidence,
            )
        )
        entries.append(
            _correction(
                question_id=f"right{index}",
                machine="the cat",
                human="the cat",
                confidence=confidence,
            )
        )
    return entries


def test_the_curve_reports_cost_and_catch_at_each_floor():
    samples, _ = collect_transcription_samples(_mixed_corrections())
    curve = {point.floor: point for point in build_floor_curve(samples)}

    assert curve[50.0].escalated == 4
    assert curve[50.0].caught == 2
    assert curve[50.0].errors == 4
    assert curve[50.0].precision == 0.5
    assert curve[100.0].escalated == 8
    assert curve[100.0].catch_rate == 1.0


def test_an_unmeasured_reading_is_outside_the_curve():
    samples, _ = collect_transcription_samples(
        [_correction(confidence=None, source="vision-model", machine="a", human="b")]
    )
    curve = build_floor_curve(samples)

    assert curve[0].total == 0


def test_a_blank_reading_is_escalated_at_every_floor():
    samples, _ = collect_transcription_samples(
        [_correction(machine="", human="answer", confidence=None)]
    )
    curve = {point.floor: point for point in build_floor_curve(samples)}

    assert curve[0.0].escalated == 1
    assert curve[0.0].caught == 1


def test_catch_is_monotonic_in_the_floor():
    samples, _ = collect_transcription_samples(_mixed_corrections())
    caught = [point.caught for point in build_floor_curve(samples)]

    assert caught == sorted(caught)


def test_the_lowest_floor_meeting_the_target_is_recommended():
    samples, _ = collect_transcription_samples(_mixed_corrections())
    curve = build_floor_curve(samples)

    assert recommend_floor(curve, target_catch=1.0, min_sample=4) == 85.0


def test_too_few_errors_recommends_nothing():
    samples, _ = collect_transcription_samples([_correction()])
    assert recommend_floor(build_floor_curve(samples), min_sample=8) is None


def test_an_unreachable_target_recommends_nothing():
    entries = _mixed_corrections()
    entries.append(
        _correction(
            question_id="unmeasured",
            machine="a",
            human="b",
            confidence=None,
            source="vision-model",
        )
    )
    samples, _ = collect_transcription_samples(entries)

    assert recommend_floor(build_floor_curve(samples), target_catch=1.0, min_sample=4) == 85.0


def test_collapse_keeps_one_row_per_distinct_behaviour():
    samples, _ = collect_transcription_samples(_mixed_corrections())
    collapsed = collapse_curve(build_floor_curve(samples))

    escalated = [point.escalated for point in collapsed]
    assert escalated == sorted(set(escalated))
    assert len(collapsed) < len(build_floor_curve(samples))


def _document(question_count: int = 4) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name="Tommy Liu",
        student_id="s1",
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf="submission-0001.pdf",
        questions=[
            StudentSubmissionQuestion(
                question_id=f"q{index + 1}",
                prompt="",
                possible_points=10.0,
                response_image_path=f"responses/q{index + 1}.png",
                response_text="something",
            )
            for index in range(question_count)
        ],
    )


def test_coverage_is_the_share_of_answers_anyone_checked():
    samples, _ = collect_transcription_samples(
        [_correction(question_id="q1"), _correction(question_id="q2")]
    )
    coverage = build_coverage([_document(4)], samples)

    assert coverage.answers_with_text == 4
    assert coverage.answers_corrected == 2
    assert coverage.sampled_share == 0.5


def test_report_groups_by_reader():
    report = build_transcription_eval_report(
        [
            _correction(question_id="q1", source="tesseract", machine="a", human="b"),
            _correction(question_id="q2", source="tesseract", machine="c", human="c"),
            _correction(
                question_id="q3", source="vision-model", confidence=None, machine="d", human="d"
            ),
        ],
        [_document(3)],
    )

    labels = {stats.label: stats for stats in report.by_source}
    assert labels["Tesseract OCR"].samples == 2
    assert labels["Tesseract OCR"].exact == 1
    assert labels["vision model"].samples == 1
    assert labels["vision model"].exact_rate == 1.0


def test_a_reading_with_no_recorded_reader_is_its_own_bucket():
    report = build_transcription_eval_report([_correction(source=None)], [])

    assert [stats.label for stats in report.by_source] == ["unrecorded"]


def test_empty_confidence_buckets_are_dropped():
    report = build_transcription_eval_report([_correction(confidence=42.0)], [])

    assert [stats.label for stats in report.by_confidence] == ["0-59"]


def test_worst_misreads_come_back_worst_first():
    report = build_transcription_eval_report(
        [
            _correction(question_id="q1", machine="the cat", human="the cat"),
            _correction(question_id="q2", machine="", human="a long answer here"),
            _correction(question_id="q3", machine="teh cat", human="the cat"),
        ],
        [],
    )

    assert [sample.question_id for sample in report.worst[:2]] == ["q2", "q3"]


def test_a_journal_with_no_corrections_reports_nothing_rather_than_zero_accuracy():
    report = build_transcription_eval_report([], [_document()])

    assert not report.has_samples
    assert report.overall.samples == 0
    assert report.recommended_floor is None
