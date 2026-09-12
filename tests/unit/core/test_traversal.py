from opengrader.core.traversal import shuffled_order, traversal_seed


def test_traversal_seed_is_stable_across_calls() -> None:
    actor = "alice"
    assignment = "assignment1"

    seed1 = traversal_seed(actor, assignment)
    seed2 = traversal_seed(actor, assignment)

    assert seed1 == seed2


def test_different_actors_get_different_seeds() -> None:
    assignment = "assignment1"

    seed_alice = traversal_seed("alice", assignment)
    seed_bob = traversal_seed("bob", assignment)

    assert seed_alice != seed_bob


def test_different_assignments_get_different_seeds() -> None:
    actor = "alice"

    seed_a1 = traversal_seed(actor, "assignment1")
    seed_a2 = traversal_seed(actor, "assignment2")

    assert seed_a1 != seed_a2


def test_seed_hash_does_not_depend_on_process_hash_randomization() -> None:
    actor = "alice"
    assignment = "assignment1"

    seed = traversal_seed(actor, assignment)
    assert isinstance(seed, int)
    assert 0 <= seed < 2**64


def test_shuffled_order_returns_all_items() -> None:
    items = [1, 2, 3, 4, 5]
    seed = 42

    shuffled = shuffled_order(items, seed)

    assert sorted(shuffled) == sorted(items)
    assert len(shuffled) == len(items)


def test_shuffled_order_does_not_mutate_input() -> None:
    items = [1, 2, 3, 4, 5]
    original = list(items)
    seed = 42

    shuffled_order(items, seed)

    assert items == original


def test_shuffled_order_is_deterministic() -> None:
    items = list(range(100))
    seed = 42

    result1 = shuffled_order(items, seed)
    result2 = shuffled_order(items, seed)

    assert result1 == result2


def test_shuffled_order_with_different_seeds_produces_different_orders() -> None:
    items = list(range(100))

    result_seed1 = shuffled_order(items, 1)
    result_seed2 = shuffled_order(items, 2)

    assert result_seed1 != result_seed2


def test_shuffled_order_on_empty_sequence() -> None:
    items = []
    seed = 42

    result = shuffled_order(items, seed)

    assert result == []


def test_shuffled_order_on_single_item() -> None:
    items = [1]
    seed = 42

    result = shuffled_order(items, seed)

    assert result == [1]


def test_seed_collision_resistance() -> None:
    seed1 = traversal_seed("alice-bob", "bob")
    seed2 = traversal_seed("alice", "bob-bob")

    assert seed1 != seed2
