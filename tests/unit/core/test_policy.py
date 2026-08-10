from pathlib import Path

from opengrader.core.policy import (
    GradingPolicy,
    read_policy,
    resolve_policy_path,
    write_policy,
)


def test_read_policy_defaults_to_solo_unlocked_when_absent(tmp_path: Path) -> None:
    policy = read_policy(tmp_path)

    assert policy == GradingPolicy(mode="solo", anonymity_locked=False)


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    written = write_policy(
        tmp_path, GradingPolicy(mode="multi", anonymity_locked=True)
    )

    assert written == resolve_policy_path(tmp_path)
    assert written.exists()
    loaded = read_policy(tmp_path)
    assert loaded.mode == "multi"
    assert loaded.anonymity_locked is True


def test_policy_lives_in_opengrader_dot_dir(tmp_path: Path) -> None:
    path = resolve_policy_path(tmp_path)

    assert path == tmp_path / ".opengrader" / "policy.json"


def test_resolve_policy_path_accepts_a_file_and_uses_its_parent(
    tmp_path: Path,
) -> None:
    submission = tmp_path / "alice.submission.md"
    submission.write_text("x")

    path = resolve_policy_path(submission)

    assert path == tmp_path / ".opengrader" / "policy.json"


def test_read_policy_survives_malformed_json(tmp_path: Path) -> None:
    path = resolve_policy_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    # A corrupt policy must never block grading: fall back to the default.
    assert read_policy(tmp_path) == GradingPolicy()


def test_read_policy_ignores_unknown_mode(tmp_path: Path) -> None:
    write_policy(tmp_path, GradingPolicy(mode="multi", anonymity_locked=True))
    path = resolve_policy_path(tmp_path)
    path.write_text('{"mode": "bogus", "anonymity_locked": true}', encoding="utf-8")

    loaded = read_policy(tmp_path)
    assert loaded.mode == "solo"  # unknown mode normalizes to the safe default
    assert loaded.anonymity_locked is True


def test_unlocking_overwrites_a_previously_locked_folder(tmp_path: Path) -> None:
    write_policy(tmp_path, GradingPolicy(mode="multi", anonymity_locked=True))
    write_policy(tmp_path, GradingPolicy(mode="solo", anonymity_locked=False))

    assert read_policy(tmp_path).anonymity_locked is False


def test_traversal_shuffle_round_trips(tmp_path: Path) -> None:
    """The traversal_shuffle field must survive write and read."""
    written = write_policy(
        tmp_path, GradingPolicy(mode="solo", traversal_shuffle=True)
    )

    loaded = read_policy(tmp_path)
    assert loaded.traversal_shuffle is True


def test_traversal_shuffle_defaults_to_false_when_absent(tmp_path: Path) -> None:
    """An old policy file without traversal_shuffle must load as False.

    This ensures backwards compatibility: an existing folder with no
    traversal_shuffle key behaves exactly as it does today (unshuffled).
    """
    path = resolve_policy_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write a legacy policy without the traversal_shuffle key.
    path.write_text('{"mode": "solo", "anonymity_locked": false}', encoding="utf-8")

    loaded = read_policy(tmp_path)
    assert loaded.traversal_shuffle is False


def test_policy_with_all_fields_round_trips(tmp_path: Path) -> None:
    """A complete policy with all fields must survive serialization."""
    policy = GradingPolicy(mode="multi", anonymity_locked=True, traversal_shuffle=True)
    write_policy(tmp_path, policy)

    loaded = read_policy(tmp_path)
    assert loaded.mode == "multi"
    assert loaded.anonymity_locked is True
    assert loaded.traversal_shuffle is True
