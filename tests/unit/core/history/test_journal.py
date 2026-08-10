from pathlib import Path

from opengrader.core.history import (
    GradeJournal,
    GradeJournalEntry,
    current_actor,
    journal_fingerprint,
    read_journal,
    resolve_history_dir,
)


def test_resolve_history_dir_for_directory_and_file(tmp_path: Path) -> None:
    subs = tmp_path / "student-submissions"
    subs.mkdir()
    assert resolve_history_dir(subs) == subs / ".opengrader" / "history"

    a_file = subs / "s1.submission.md"
    a_file.write_text("x")
    # A file resolves to its parent's history dir.
    assert resolve_history_dir(a_file) == subs / ".opengrader" / "history"


def test_current_actor_prefers_override(monkeypatch) -> None:
    monkeypatch.setenv("OPENGRADER_GRADER", "Prof. Kim")
    assert current_actor() == "Prof. Kim"
    monkeypatch.delenv("OPENGRADER_GRADER", raising=False)
    monkeypatch.setenv("USER", "ta_bob")
    assert current_actor() == "ta_bob"


def test_append_writes_jsonl_and_reads_back(tmp_path: Path) -> None:
    history = tmp_path / "history"
    journal = GradeJournal(history, actor="ta_bob", session_id="s1")

    journal.append(
        kind="grade",
        student_id="123",
        student_name="Tommy",
        question_id="q1",
        summary="8/10 pts",
        old={"points": None, "rubric": []},
        new={"points": 8.0, "rubric": ["Partial"]},
    )

    assert journal.path == history / "grade-journal-s1.jsonl"
    entries = read_journal(history)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor == "ta_bob"
    assert entry.kind == "grade"
    assert entry.new == {"points": 8.0, "rubric": ["Partial"]}
    assert entry.summary == "8/10 pts"


def test_read_merges_multiple_sessions_in_time_order(tmp_path: Path) -> None:
    history = tmp_path / "history"
    j1 = GradeJournal(history, actor="ta_a", session_id="a")
    j2 = GradeJournal(history, actor="ta_b", session_id="b")

    e1 = j1.append(kind="grade", student_id="1", student_name="A", question_id="q1", summary="one")
    e2 = j2.append(kind="comment", student_id="2", student_name="B", question_id="q1", summary="two")

    merged = read_journal(history)
    # Two separate session files, merged and ordered by timestamp.
    assert [e.actor for e in merged] == ["ta_a", "ta_b"]
    assert merged[0].timestamp <= merged[1].timestamp
    assert {e.summary for e in merged} == {"one", "two"}
    # Distinct files: no contention between the two graders.
    assert j1.path != j2.path


def test_read_skips_malformed_lines(tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.mkdir(parents=True)
    good = GradeJournalEntry(
        timestamp="2026-07-20T18:00:00+00:00",
        actor="ta",
        kind="grade",
        student_id="1",
        student_name="A",
        question_id="q1",
        summary="ok",
    )
    (history / "grade-journal-x.jsonl").write_text(
        good.to_json() + "\n" + "{not valid json\n" + "\n"
    )
    entries = read_journal(history)
    assert len(entries) == 1
    assert entries[0].summary == "ok"


def test_read_journal_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert read_journal(tmp_path / "nope") == []


def _append(journal: GradeJournal, summary: str) -> None:
    journal.append(
        kind="grade",
        student_id="123",
        student_name="Tommy",
        question_id="q1",
        summary=summary,
    )


def test_journal_fingerprint_moves_on_an_appended_entry(tmp_path: Path) -> None:
    """The append-only case: the same session file grows."""
    history = tmp_path / "history"
    journal = GradeJournal(history, actor="ta_bob", session_id="s1")
    _append(journal, "8/10 pts")

    before = journal_fingerprint(history)
    assert journal_fingerprint(history) == before, "a re-read must be stable"

    _append(journal, "9/10 pts")
    assert journal_fingerprint(history) != before


def test_journal_fingerprint_moves_when_another_session_appears(
    tmp_path: Path,
) -> None:
    """The case a single file's size cannot see, and the one that matters most.

    A second grader, `grade-restore` or `grade-merge apply` writes its *own*
    session file rather than growing this session's, so a fingerprint built
    from one known path would report the journal unchanged.
    """
    history = tmp_path / "history"
    _append(GradeJournal(history, actor="ta_a", session_id="a"), "8/10 pts")
    before = journal_fingerprint(history)

    _append(GradeJournal(history, actor="ta_b", session_id="b"), "5/10 pts")

    assert journal_fingerprint(history) != before
    assert len(journal_fingerprint(history)) == 2


def test_journal_fingerprint_missing_dir_is_empty(tmp_path: Path) -> None:
    """Nothing has ever been graded here — not an error, just no journal."""
    assert journal_fingerprint(tmp_path / "nope") == ()
