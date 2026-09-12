from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opengrader.core.handoff.packet import (
    HandoffKey,
    PacketError,
    PacketManifest,
    PacketMember,
    build_packet,
    default_packet_id,
    default_packet_output_dir,
    is_sealed_packet,
    list_handoff_keys,
    mint_alias,
    parse_handoff_key,
    parse_packet_manifest,
    read_handoff_key,
    read_packet_manifest,
    render_handoff_key,
    render_packet_manifest,
    opened_packet,
    resolve_handoff_dir,
    seal_packet,
    slugify_packet_name,
    unseal_packet,
)
from opengrader.core.history.restore import AspectValue, CellState
from opengrader.core.submissions.student_file import load_student_submission_markdown

SUBMISSION = """---
student_name: "Ada Lovelace"
student_id: "123456789"
course_code: "CS146"
assignment_title: "Quiz 1"
assignment_version: "A"
source_pdf: "../../split/Ada_Lovelace.pdf"
name_image_path: "identifiers/name.png"
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: What is a loop?
  - Response image: ![q1 response](responses/q1.png)
  - Response text: a repeat
  - Possible points: 5
  - Points: 3
  - Rubric: Partially Correct
  - Analysis: close
"""


@pytest.fixture()
def cohort(tmp_path: Path) -> Path:
    root = tmp_path / "subs"
    student = root / "sub-0001"
    (student / "responses").mkdir(parents=True)
    (student / "identifiers").mkdir()
    (student / "sub-0001.submission.md").write_text(SUBMISSION)
    (student / "responses" / "q1.png").write_bytes(b"crop-bytes")
    (student / "identifiers" / "name.png").write_bytes(b"a picture of Ada's name")
    (student / "sub-0001.pdf").write_bytes(b"%PDF-scan-of-Ada")
    return root


def test_an_alias_is_stable_for_the_same_packet_student_and_secret() -> None:
    assert mint_alias("p1", "123", secret="s") == mint_alias("p1", "123", secret="s")


def test_aliases_differ_across_packets_students_and_secrets() -> None:
    assert mint_alias("p1", "123", secret="s") != mint_alias("p2", "123", secret="s")
    assert mint_alias("p1", "123", secret="s") != mint_alias("p1", "456", secret="s")
    assert mint_alias("p1", "123", secret="s") != mint_alias("p1", "123", secret="t")


def test_a_taken_alias_is_probed_past_deterministically() -> None:
    first = mint_alias("p1", "123", secret="s")
    second = mint_alias("p1", "123", secret="s", taken=[first])
    assert second != first
    assert second == mint_alias("p1", "123", secret="s", taken=[first])


def test_an_alias_carries_no_trace_of_the_student() -> None:
    alias = mint_alias("quiz1", "123456789", secret="s")
    assert "123456789" not in alias
    assert alias.startswith("og-")


def test_an_alias_without_a_secret_is_refused() -> None:
    with pytest.raises(PacketError, match="secret"):
        mint_alias("p1", "123", secret="")


def test_default_packet_id_prefers_the_grader_over_a_date() -> None:
    class _Doc:
        assignment_title = "Quiz 1"

    name = default_packet_id([_Doc()], grader="Alice T")
    assert name == "quiz-1-for-alice-t"


def test_default_packet_id_falls_back_to_a_date_without_a_grader() -> None:
    class _Doc:
        assignment_title = "Quiz 1"

    stamp = datetime(2026, 8, 4, tzinfo=timezone.utc)
    assert default_packet_id([_Doc()], today=stamp) == "quiz-1-2026-08-04"


def test_slugify_never_returns_an_empty_name() -> None:
    assert slugify_packet_name("///") == "packet"


def test_a_manifest_round_trips_including_its_baseline() -> None:
    manifest = PacketManifest(
        packet_id="p1",
        created_at="2026-08-04T00:00:00+00:00",
        assignment_title="Quiz 1",
        redacted=True,
        aliases=("og-aaaa",),
        baseline={
            ("og-aaaa", "q1"): CellState(
                values={
                    "grade": AspectValue(points=3.0, rubric=("Partial",)),
                    "comment": AspectValue(text="close"),
                }
            )
        },
    )
    restored = parse_packet_manifest(render_packet_manifest(manifest))
    assert restored.packet_id == "p1"
    assert restored.aliases == ("og-aaaa",)
    cell = restored.baseline_cells()[("og-aaaa", "q1")]
    assert cell.value("grade") == AspectValue(points=3.0, rubric=("Partial",))
    assert cell.value("comment") == AspectValue(text="close")


def test_a_manifest_from_a_newer_format_is_refused_not_misread() -> None:
    with pytest.raises(PacketError, match="newer Opengrader"):
        parse_packet_manifest('{"packet_format": 99, "files": []}')


def test_a_handoff_key_round_trips() -> None:
    key = HandoffKey(
        packet_id="p1",
        created_at="2026-08-04T00:00:00+00:00",
        created_by="prof",
        packet_path="/tmp/p1",
        members=(
            PacketMember(
                alias="og-aaaa",
                student_key="123456789",
                student_id="123456789",
                student_name="Ada Lovelace",
                source_relative_path="sub-0001/sub-0001.submission.md",
            ),
        ),
        alias_secret="ab" * 32,
    )
    restored = parse_handoff_key(render_handoff_key(key))
    assert restored.by_alias()["og-aaaa"].student_name == "Ada Lovelace"
    assert restored.alias_secret == "ab" * 32


def test_a_key_written_before_aliases_were_keyed_still_reads() -> None:
    restored = parse_handoff_key(
        '{"packet_id": "p1", "aliases": {"og-aaaa": {"student_key": "1"}}}'
    )
    assert restored.alias_secret == ""
    assert restored.by_alias()["og-aaaa"].student_key == "1"


def test_a_corrupt_handoff_key_fails_loudly() -> None:
    with pytest.raises(PacketError):
        parse_handoff_key("{not json")
    with pytest.raises(PacketError, match="no alias map"):
        parse_handoff_key('{"packet_id": "p1"}')


def test_a_packet_alias_cannot_be_recomputed_from_a_roster(
    cohort: Path, tmp_path: Path
) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    alias = result.key.members[0].alias
    packet_id = read_packet_manifest(result.packet_path).packet_id

    # Everything the grader holds (packet id + a roster id) no longer yields it.
    seed = f"{packet_id}\x00123456789\x000".encode("utf-8")
    assert alias != "og-" + hashlib.sha256(seed).hexdigest()[:8]
    assert alias == mint_alias("p1", "123456789", secret=result.key.alias_secret)


def test_the_alias_secret_stays_with_the_teacher(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    secret = result.key.alias_secret
    assert len(secret) == 64

    blob = b"".join(
        path.read_bytes() for path in result.packet_path.rglob("*") if path.is_file()
    )
    assert secret.encode("utf-8") not in blob
    assert read_handoff_key(result.key_path).alias_secret == secret


def test_each_packet_gets_its_own_secret(cohort: Path, tmp_path: Path) -> None:
    first = build_packet(cohort, tmp_path / "out", packet_id="p1")
    second = build_packet(cohort, tmp_path / "out", packet_id="p2")
    assert first.key.alias_secret != second.key.alias_secret


def test_an_unredacted_copy_has_no_secret(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1", redact=False)
    assert result.key.alias_secret == ""


def test_reusing_a_packet_name_refuses_rather_than_orphaning_the_first_copy(
    cohort: Path, tmp_path: Path
) -> None:
    first = build_packet(cohort, tmp_path / "a", packet_id="p1")
    with pytest.raises(PacketError, match="already handed out"):
        build_packet(cohort, tmp_path / "b", packet_id="p1")
    assert read_handoff_key(first.key_path).alias_secret == first.key.alias_secret
    assert not (tmp_path / "b" / "p1").exists()


def test_a_packet_contains_no_trace_of_the_student(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    blob = b"".join(
        path.read_bytes()
        for path in result.packet_path.rglob("*")
        if path.is_file()
    )
    assert b"Ada" not in blob
    assert b"123456789" not in blob
    assert not any("Ada" in str(path) for path in result.packet_path.rglob("*"))


def test_a_packet_leaves_the_scan_and_the_identity_crop_behind(
    cohort: Path, tmp_path: Path
) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    assert not list(result.packet_path.rglob("*.pdf"))
    assert not list(result.packet_path.rglob("identifiers/*"))
    assert result.copied_crops == 1
    assert list(result.packet_path.rglob("responses/q1.png"))


def test_a_packet_keeps_the_grades_and_the_answer(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    packed = next(result.packet_path.rglob("*.submission.md"))
    document = load_student_submission_markdown(packed)
    assert document.student_name == ""
    assert document.student_id.startswith("og-")
    assert document.questions[0].points_awarded == 3.0
    assert document.questions[0].response_text == "a repeat"


def test_the_baseline_records_the_grades_as_they_were(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    alias = result.key.members[0].alias
    cell = result.manifest.baseline_cells()[(alias, "q1")]
    assert cell.value("grade") == AspectValue(points=3.0, rubric=("Partially Correct",))


def test_the_key_stays_with_the_teacher_and_never_in_the_packet(
    cohort: Path, tmp_path: Path
) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    assert result.key_path == resolve_handoff_dir(cohort) / "p1.json"
    assert result.key_path.is_file()
    assert not (result.packet_path / "handoff").exists()
    key = read_handoff_key(result.key_path)
    assert key.members[0].student_name == "Ada Lovelace"


def test_the_manifest_travels_with_the_packet(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    manifest = read_packet_manifest(result.packet_path)
    assert manifest is not None and manifest.packet_id == "p1"


def test_reading_a_manifest_from_a_plain_folder_is_none_not_an_error(
    cohort: Path,
) -> None:
    assert read_packet_manifest(cohort) is None


def test_the_answer_key_is_copied_in_when_given(cohort: Path, tmp_path: Path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key")
    result = build_packet(cohort, tmp_path / "out", packet_id="p1", qa_file_path=qa)
    assert result.qa_file_path is not None
    assert result.qa_file_path.read_text() == "# key"


def test_the_readme_tells_the_grader_how_to_open_it(cohort: Path, tmp_path: Path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key")
    result = build_packet(cohort, tmp_path / "out", packet_id="p1", qa_file_path=qa)
    readme = (result.packet_path / "README.txt").read_text()
    assert "uv run tui --qa-file qa.md --submissions submissions/" in readme
    assert "anonymous by design" in readme


def test_scans_and_redaction_together_are_refused_not_silently_dropped(
    cohort: Path, tmp_path: Path
) -> None:
    with pytest.raises(PacketError, match="name across the top"):
        build_packet(cohort, tmp_path / "out", packet_id="p1", include_pdfs=True)


def test_an_unredacted_packet_may_carry_the_scans(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(
        cohort, tmp_path / "out", packet_id="p1", redact=False, include_pdfs=True
    )
    assert list(result.packet_path.rglob("*.pdf"))
    packed = next(result.packet_path.rglob("*.submission.md"))
    assert load_student_submission_markdown(packed).student_name == "Ada Lovelace"


def test_an_unidentifiable_submission_stops_the_packet(tmp_path: Path) -> None:
    root = tmp_path / "subs"
    (root / "sub-0001").mkdir(parents=True)
    (root / "sub-0001" / "sub-0001.submission.md").write_text(
        SUBMISSION.replace('student_name: "Ada Lovelace"', 'student_name: ""').replace(
            'student_id: "123456789"', 'student_id: ""'
        )
    )
    with pytest.raises(PacketError, match="review queue"):
        build_packet(root, tmp_path / "out", packet_id="p1")


def test_an_existing_packet_folder_is_never_overwritten(
    cohort: Path, tmp_path: Path
) -> None:
    build_packet(cohort, tmp_path / "out", packet_id="p1")
    with pytest.raises(PacketError, match="already exists"):
        build_packet(cohort, tmp_path / "out", packet_id="p1")


def test_handed_out_packets_are_listed_newest_first(cohort: Path, tmp_path: Path) -> None:
    build_packet(
        cohort,
        tmp_path / "out",
        packet_id="older",
        now=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    build_packet(
        cohort,
        tmp_path / "out",
        packet_id="newer",
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    assert [key.packet_id for key in list_handoff_keys(cohort)] == ["newer", "older"]


def test_listing_survives_one_corrupt_key_file(cohort: Path, tmp_path: Path) -> None:
    build_packet(cohort, tmp_path / "out", packet_id="good")
    (resolve_handoff_dir(cohort) / "broken.json").write_text("{nope")
    assert [key.packet_id for key in list_handoff_keys(cohort)] == ["good"]


def test_the_default_destination_sits_beside_the_cohort_not_inside_it(
    cohort: Path,
) -> None:
    destination = default_packet_output_dir(cohort)
    assert destination == cohort.parent / "grader-copies"
    assert cohort not in destination.parents and destination != cohort


def test_the_default_destination_works_from_a_single_submission_file(
    cohort: Path,
) -> None:
    submission = next(cohort.rglob("*.submission.md"))
    destination = default_packet_output_dir(submission)
    assert destination == submission.parent.parent / "grader-copies"
    assert submission.parent not in destination.parents


def test_sealing_replaces_the_folder_with_one_encrypted_file(
    cohort: Path, tmp_path: Path
) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1", grader="alice")
    sealed = seal_packet(result.packet_path, "correct horse")

    assert sealed.is_file()
    assert sealed.name.endswith(".zip.enc")
    assert not result.packet_path.exists()
    assert is_sealed_packet(sealed)


def test_a_sealed_packet_reveals_nothing_without_the_passphrase(
    cohort: Path, tmp_path: Path
) -> None:
    result = build_packet(
        cohort, tmp_path / "out", packet_id="p1", redact=False
    )
    sealed = seal_packet(result.packet_path, "p")
    blob = sealed.read_bytes()
    assert b"Ada" not in blob
    assert b"submission" not in blob


def test_unsealing_restores_the_packet_exactly(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    before = {
        str(path.relative_to(result.packet_path)): path.read_bytes()
        for path in sorted(result.packet_path.rglob("*"))
        if path.is_file()
    }
    sealed = seal_packet(result.packet_path, "p")
    opened = unseal_packet(sealed, tmp_path / "opened", "p")

    after = {
        str(path.relative_to(opened)): path.read_bytes()
        for path in sorted(opened.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_unsealing_needs_the_right_passphrase(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    sealed = seal_packet(result.packet_path, "right")
    with pytest.raises(PacketError):
        unseal_packet(sealed, tmp_path / "opened", "wrong")


def test_unsealing_refuses_an_existing_destination(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    sealed = seal_packet(result.packet_path, "p")
    (tmp_path / "taken").mkdir()
    with pytest.raises(PacketError, match="already exists"):
        unseal_packet(sealed, tmp_path / "taken", "p")


def test_opened_packet_hands_back_a_folder_either_way(
    cohort: Path, tmp_path: Path
) -> None:
    plain = build_packet(cohort, tmp_path / "plain", packet_id="p1").packet_path
    with opened_packet(plain) as folder:
        assert (folder / "submissions").is_dir()

    sealed = seal_packet(
        build_packet(cohort, tmp_path / "sealed", packet_id="p2").packet_path, "p"
    )
    with opened_packet(sealed, "p") as folder:
        assert (folder / "submissions").is_dir()
        unpacked = folder
    assert not unpacked.exists()


def test_opened_packet_says_when_a_passphrase_is_missing(
    cohort: Path, tmp_path: Path
) -> None:
    sealed = seal_packet(
        build_packet(cohort, tmp_path / "out", packet_id="p1").packet_path, "p"
    )
    with pytest.raises(PacketError, match="sealed"):
        with opened_packet(sealed):
            pass


def test_sealing_can_keep_the_source_folder_when_asked(
    cohort: Path, tmp_path: Path
) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    seal_packet(result.packet_path, "p", remove_source=False)
    assert result.packet_path.is_dir()


def test_a_plain_folder_is_not_a_sealed_packet(cohort: Path, tmp_path: Path) -> None:
    result = build_packet(cohort, tmp_path / "out", packet_id="p1")
    assert not is_sealed_packet(result.packet_path)
