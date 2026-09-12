from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from opengrader.core.question_types import FREE_RESPONSE, QuestionType
from opengrader.core.submissions.answer_groups import AnswerGroup
from opengrader.core.submissions.similarity import normalize_for_similarity

DEFAULT_SUGGESTION_THRESHOLD = 0.85

DEFAULT_SUGGESTION_MIN_LENGTH = 12


@dataclass(frozen=True)
class SuggestedPair:
    a_key: str
    b_key: str
    score: float


@dataclass(frozen=True)
class SuggestedGroup:
    question_type: QuestionType
    groups: tuple[AnswerGroup, ...]
    pairs: tuple[SuggestedPair, ...]
    min_score: float
    max_score: float

    @property
    def representative(self) -> AnswerGroup:
        return self.groups[0]

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def member_ids(self) -> tuple[str, ...]:
        seen: set[str] = set()
        merged: list[str] = []
        for group in self.groups:
            for member_id in group.member_ids:
                if member_id not in seen:
                    seen.add(member_id)
                    merged.append(member_id)
        return tuple(merged)

    @property
    def size(self) -> int:
        return len(self.member_ids)

    @property
    def added_size(self) -> int:
        return self.size - self.representative.size


def _canonical_order(group: AnswerGroup) -> tuple[int, str]:
    return (-group.size, group.key)


def _eligible_groups(
    groups: Iterable[AnswerGroup],
    *,
    min_length: int,
) -> list[tuple[AnswerGroup, str]]:
    eligible: list[tuple[AnswerGroup, str]] = []
    for group in groups:
        if group.question_type != FREE_RESPONSE or group.is_blank:
            continue
        normalized = normalize_for_similarity(group.sample_response_text)
        if len(normalized) < min_length:
            continue
        eligible.append((group, normalized))
    eligible.sort(key=lambda pair: _canonical_order(pair[0]))
    return eligible


def _score_matrix(
    normalized: Sequence[str],
    *,
    threshold: float,
) -> dict[tuple[int, int], float]:
    scores: dict[tuple[int, int], float] = {}
    matcher = SequenceMatcher(autojunk=False)
    for i in range(len(normalized)):
        matcher.set_seq2(normalized[i])
        for j in range(i + 1, len(normalized)):
            matcher.set_seq1(normalized[j])
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            score = matcher.ratio()
            if score >= threshold:
                scores[(i, j)] = score
    return scores


def _pair_score(scores: dict[tuple[int, int], float], a: int, b: int) -> float | None:
    return scores.get((a, b) if a < b else (b, a))


def suggest_group_merges(
    groups: Iterable[AnswerGroup],
    *,
    threshold: float = DEFAULT_SUGGESTION_THRESHOLD,
    min_length: int = DEFAULT_SUGGESTION_MIN_LENGTH,
) -> list[SuggestedGroup]:
    """Uses complete linkage: every pair in a proposed merge must meet threshold."""
    eligible = _eligible_groups(groups, min_length=min_length)
    if len(eligible) < 2:
        return []

    normalized = [text for _, text in eligible]
    scores = _score_matrix(normalized, threshold=threshold)
    if not scores:
        return []

    clustered: set[int] = set()
    suggestions: list[SuggestedGroup] = []

    for seed in range(len(eligible)):
        if seed in clustered:
            continue
        members = [seed]
        # Adding members only tightens complete-linkage constraints; rejected candidates stay
        # rejected.
        for candidate in range(seed + 1, len(eligible)):
            if candidate in clustered:
                continue
            member_scores = [_pair_score(scores, candidate, member) for member in members]
            if any(score is None for score in member_scores):
                continue
            members.append(candidate)
        if len(members) < 2:
            continue

        clustered.update(members)
        pairs: list[SuggestedPair] = []
        for position, a in enumerate(members):
            for b in members[position + 1 :]:
                score = _pair_score(scores, a, b)
                if score is None:
                    continue
                pairs.append(
                    SuggestedPair(
                        a_key=eligible[a][0].key,
                        b_key=eligible[b][0].key,
                        score=score,
                    )
                )
        pair_scores = [pair.score for pair in pairs]
        suggestions.append(
            SuggestedGroup(
                question_type=FREE_RESPONSE,
                groups=tuple(eligible[index][0] for index in members),
                pairs=tuple(sorted(pairs, key=lambda pair: (pair.score, pair.a_key, pair.b_key))),
                min_score=min(pair_scores),
                max_score=max(pair_scores),
            )
        )

    suggestions.sort(
        key=lambda suggestion: (
            -suggestion.size,
            -suggestion.min_score,
            suggestion.representative.key,
        )
    )
    return suggestions


DiffKind = Literal["same", "added", "removed"]

_TOKEN_EDGE_CHARS = ".,;:!?\"'`()[]{}"


@dataclass(frozen=True)
class AnswerDiffSpan:
    text: str
    kind: DiffKind

    @property
    def changed(self) -> bool:
        return self.kind != "same"


def _diff_key(token: str) -> str:
    return token.strip(_TOKEN_EDGE_CHARS).casefold()


def diff_answer_spans(reference: str, other: str) -> tuple[AnswerDiffSpan, ...]:
    """Removed spans contain reference words missing from other. Display text retains case
    and punctuation ignored during comparison.
    """
    reference_tokens = reference.split()
    other_tokens = other.split()
    matcher = SequenceMatcher(
        None,
        [_diff_key(token) for token in reference_tokens],
        [_diff_key(token) for token in other_tokens],
        autojunk=False,
    )

    spans: list[AnswerDiffSpan] = []

    def add(tokens: list[str], kind: DiffKind) -> None:
        if tokens:
            spans.append(AnswerDiffSpan(text=" ".join(tokens), kind=kind))

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            add(other_tokens[j1:j2], "same")
        elif tag == "insert":
            add(other_tokens[j1:j2], "added")
        elif tag == "delete":
            add(reference_tokens[i1:i2], "removed")
        else:
            add(reference_tokens[i1:i2], "removed")
            add(other_tokens[j1:j2], "added")
    return tuple(spans)


@dataclass(frozen=True)
class GroupingEffort:
    question_id: str
    responses: int
    blank_responses: int
    exact_groups: int
    suggested_merges: int
    groups_after_merges: int

    @property
    def saved_by_suggestions(self) -> int:
        return self.exact_groups - self.groups_after_merges

    @property
    def reduction_percent(self) -> float | None:
        if self.responses <= 0:
            return None
        return (1.0 - self.groups_after_merges / self.responses) * 100.0


def summarize_grouping_effort(
    question_id: str,
    groups: Sequence[AnswerGroup],
    suggestions: Sequence[SuggestedGroup],
) -> GroupingEffort:
    responses = sum(group.size for group in groups)
    blank = sum(group.size for group in groups if group.is_blank)
    exact = len(groups)
    removed = sum(suggestion.group_count - 1 for suggestion in suggestions)
    return GroupingEffort(
        question_id=question_id,
        responses=responses,
        blank_responses=blank,
        exact_groups=exact,
        suggested_merges=len(suggestions),
        groups_after_merges=exact - removed,
    )
