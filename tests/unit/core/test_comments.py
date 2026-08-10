from pathlib import Path

from opengrader.core.comments import (
    CommentLibrary,
    CommentSnippet,
    read_comments,
    resolve_comments_path,
    write_comments,
)


def test_read_comments_defaults_to_empty_when_absent(tmp_path: Path) -> None:
    assert read_comments(tmp_path) == CommentLibrary()


def test_comments_live_in_opengrader_dot_dir(tmp_path: Path) -> None:
    assert resolve_comments_path(tmp_path) == tmp_path / ".opengrader" / "comments.json"


def test_resolve_comments_path_accepts_a_file_and_uses_its_parent(
    tmp_path: Path,
) -> None:
    submission = tmp_path / "alice.submission.md"
    submission.write_text("x")

    assert (
        resolve_comments_path(submission)
        == tmp_path / ".opengrader" / "comments.json"
    )


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    library = (
        CommentLibrary()
        .with_snippet("See Lecture 5")
        .with_snippet("Off-by-one in loop bound")
    )

    written = write_comments(tmp_path, library)

    assert written == resolve_comments_path(tmp_path)
    assert read_comments(tmp_path) == library


def test_read_survives_malformed_json(tmp_path: Path) -> None:
    path = resolve_comments_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    # A corrupt store only costs saved phrases — it must never block grading.
    assert read_comments(tmp_path) == CommentLibrary()


def test_with_snippet_is_no_op_for_blank_or_duplicate() -> None:
    library = CommentLibrary().with_snippet("See Lecture 5")

    assert library.with_snippet("   ") is library
    # Whitespace-only differences don't create a duplicate.
    assert library.with_snippet("See   Lecture 5") is library
    assert len(library.snippets) == 1


def test_with_snippet_normalizes_whitespace() -> None:
    library = CommentLibrary().with_snippet("  See\tLecture   5 ")

    assert library.snippets == (CommentSnippet(text="See Lecture 5"),)


def test_without_removes_by_text() -> None:
    library = (
        CommentLibrary().with_snippet("first").with_snippet("second")
    )

    trimmed = library.without("first")

    assert [s.text for s in trimmed.snippets] == ["second"]
    # Unknown text is a no-op.
    assert trimmed.without("missing") is trimmed


def test_record_use_bumps_count_and_reorders_display() -> None:
    library = (
        CommentLibrary().with_snippet("first").with_snippet("second")
    )

    used = library.record_use("second").record_use("second")

    # Storage order is stable (insertion order)...
    assert [s.text for s in used.snippets] == ["first", "second"]
    # ...but display order puts the most-used first.
    assert [s.text for s in used.ordered()] == ["second", "first"]
    assert used.record_use("missing") is used


def test_ordered_is_stable_for_ties() -> None:
    library = (
        CommentLibrary()
        .with_snippet("a")
        .with_snippet("b")
        .with_snippet("c")
    )

    # No uses recorded: display order matches insertion order.
    assert [s.text for s in library.ordered()] == ["a", "b", "c"]


def test_record_use_with_question_bumps_global_and_per_question() -> None:
    library = CommentLibrary().with_snippet("See Lecture 5")

    used = (
        library.record_use("See Lecture 5", "q1")
        .record_use("See Lecture 5", "q1")
        .record_use("See Lecture 5", "q2")
    )

    snippet = used.snippets[0]
    assert snippet.uses == 3  # global total across all questions
    assert snippet.uses_on("q1") == 2
    assert snippet.uses_on("q2") == 1
    assert snippet.uses_on("q3") == 0


def test_record_use_without_question_only_bumps_global() -> None:
    used = CommentLibrary().with_snippet("x").record_use("x")

    snippet = used.snippets[0]
    assert snippet.uses == 1
    assert dict(snippet.uses_by_question) == {}


def test_per_question_ordering_prioritizes_this_question_then_global() -> None:
    library = (
        CommentLibrary()
        .with_snippet("global favorite")
        .with_snippet("loop note")
    )
    # "global favorite" is used a lot overall, but "loop note" is the one used
    # on q1. Per-question order must surface "loop note" first on q1, while the
    # global order keeps "global favorite" on top everywhere.
    library = (
        library.record_use("global favorite", "q2")
        .record_use("global favorite", "q2")
        .record_use("global favorite", "q3")
        .record_use("loop note", "q1")
    )

    assert [s.text for s in library.ordered()] == ["global favorite", "loop note"]
    assert [s.text for s in library.ordered("q1")] == ["loop note", "global favorite"]
    # A question with no specific uses falls back to the global order.
    assert [s.text for s in library.ordered("q9")] == [
        "global favorite",
        "loop note",
    ]


def test_per_question_counts_survive_round_trip_and_omit_when_empty(
    tmp_path: Path,
) -> None:
    library = (
        CommentLibrary()
        .with_snippet("with q")
        .with_snippet("global only")
        .record_use("with q", "q1")
        .record_use("global only")
    )

    write_comments(tmp_path, library)
    reloaded = read_comments(tmp_path)
    assert reloaded == library

    # The per-question map is emitted only for the snippet that has one.
    raw = (resolve_comments_path(tmp_path)).read_text(encoding="utf-8")
    assert '"uses_by_question"' in raw
    assert raw.count('"uses_by_question"') == 1


def test_from_dict_drops_bad_entries_and_dedupes() -> None:
    library = CommentLibrary.from_dict(
        {
            "snippets": [
                {"text": "keep", "uses": 3},
                {"text": "   "},  # blank -> dropped
                {"uses": 1},  # no text -> dropped
                "not a dict",  # dropped
                {"text": "keep", "uses": 9},  # duplicate -> dropped
                {"text": "other", "uses": -5},  # negative uses -> 0
            ]
        }
    )

    assert library.snippets == (
        CommentSnippet(text="keep", uses=3),
        CommentSnippet(text="other", uses=0),
    )


def test_from_dict_sanitizes_per_question_counts() -> None:
    library = CommentLibrary.from_dict(
        {
            "snippets": [
                {
                    "text": "keep",
                    "uses": 5,
                    "uses_by_question": {
                        "q1": 2,
                        "q2": 0,  # non-positive -> dropped
                        "q3": -1,  # negative -> dropped
                        "": 4,  # blank question id -> dropped
                        "q4": "3",  # non-int -> dropped
                    },
                },
            ]
        }
    )

    assert library.snippets[0].uses == 5
    assert dict(library.snippets[0].uses_by_question) == {"q1": 2}
