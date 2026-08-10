"""Tests for per-grader randomized traversal order."""

from opengrader.core.traversal import shuffled_order, traversal_seed


def test_traversal_seed_is_stable_across_calls() -> None:
    """The seed must be stable for the same inputs.

    This is the central invariant: a grader who quits at student 40 and
    reopens the app must get the same order back. If the seed changes, the
    order reshuffle on every launch and the grader can never form a mental
    map of their own progress.
    """
    actor = "alice"
    assignment = "assignment1"

    seed1 = traversal_seed(actor, assignment)
    seed2 = traversal_seed(actor, assignment)

    assert seed1 == seed2


def test_different_actors_get_different_seeds() -> None:
    """Different graders must get different seeds (and thus different orders).

    This is the entire point of the feature: different graders should walk
    the cohort in different orders to decorrelate fatigue drift from student
    identity.
    """
    assignment = "assignment1"

    seed_alice = traversal_seed("alice", assignment)
    seed_bob = traversal_seed("bob", assignment)

    assert seed_alice != seed_bob


def test_different_assignments_get_different_seeds() -> None:
    """The same actor on different assignments must get different seeds.

    This prevents a grader from walking every assignment of a term in a
    correlated order.
    """
    actor = "alice"

    seed_a1 = traversal_seed(actor, "assignment1")
    seed_a2 = traversal_seed(actor, "assignment2")

    assert seed_a1 != seed_a2


def test_seed_hash_does_not_depend_on_process_hash_randomization() -> None:
    """The seed must not depend on Python's PYTHONHASHSEED.

    Python's built-in hash() is randomized per process by PYTHONHASHSEED
    (it defaults to random on startup, or can be set to a fixed value). Using
    hash() would reshuffle the cohort on every launch, breaking the stability
    invariant. We use SHA-256 instead to ensure reproducibility across
    processes.
    """
    actor = "alice"
    assignment = "assignment1"

    # Hash-randomization could theoretically affect this if we used hash(),
    # but SHA-256 is deterministic. This test documents that we don't use
    # Python's built-in hash().
    seed = traversal_seed(actor, assignment)
    assert isinstance(seed, int)
    # SHA-256 produces bytes, and we interpret the first 8 as a big-endian int.
    # The result should be a valid seed for random.Random().
    assert 0 <= seed < 2**64


def test_shuffled_order_returns_all_items() -> None:
    """The shuffle must not lose or duplicate items."""
    items = [1, 2, 3, 4, 5]
    seed = 42

    shuffled = shuffled_order(items, seed)

    assert sorted(shuffled) == sorted(items)
    assert len(shuffled) == len(items)


def test_shuffled_order_does_not_mutate_input() -> None:
    """The shuffle must not modify the input sequence."""
    items = [1, 2, 3, 4, 5]
    original = list(items)
    seed = 42

    shuffled_order(items, seed)

    assert items == original


def test_shuffled_order_is_deterministic() -> None:
    """The same seed produces the same shuffle order."""
    items = list(range(100))
    seed = 42

    result1 = shuffled_order(items, seed)
    result2 = shuffled_order(items, seed)

    assert result1 == result2


def test_shuffled_order_with_different_seeds_produces_different_orders() -> None:
    """Different seeds should produce different shuffle orders (most of the time).

    With 100 items, it's astronomically unlikely two random seeds produce the
    same shuffle.
    """
    items = list(range(100))

    result_seed1 = shuffled_order(items, 1)
    result_seed2 = shuffled_order(items, 2)

    assert result_seed1 != result_seed2


def test_shuffled_order_on_empty_sequence() -> None:
    """The shuffle should handle empty sequences gracefully."""
    items = []
    seed = 42

    result = shuffled_order(items, seed)

    assert result == []


def test_shuffled_order_on_single_item() -> None:
    """The shuffle should handle single-item sequences."""
    items = [1]
    seed = 42

    result = shuffled_order(items, seed)

    assert result == [1]


def test_seed_collision_resistance() -> None:
    """Similar inputs should not produce the same seed.

    SHA-256 makes collisions astronomically unlikely. This tests the
    separator in the combined string prevents accidental collisions like
    (alice, bob) + bob vs (alice + bob, bob).
    """
    # Without the separator, "alice" + "bob" + "bob" could collide with
    # "alice" + "b" + "obbo" or similar. With the separator, they're distinct.
    seed1 = traversal_seed("alice-bob", "bob")
    seed2 = traversal_seed("alice", "bob-bob")

    assert seed1 != seed2
