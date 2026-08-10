"""Proposed merges of near-identical answer groups — the fuzzy tier of grouping.

:mod:`answer_groups` clusters responses whose *normalized* text is identical, and
it is deliberately conservative: it collapses only cosmetic differences (case,
whitespace, edge punctuation) so it can never silently merge two genuinely
different answers. That conservatism is correct — a grade applied to a wrong
group is a wrong grade for every student in it — but it leaves the biggest
free-response speedup on the table. On a 200-student question, "O(n log n)
because each level does n work" and "O(n log n) since every level does n work"
are the same answer to a grader and two separate groups to the exact tier, so
the grader marks the same thing twenty times.

This module is the second tier: it takes the exact groups and *proposes* merges
between them, for a human to confirm. It never merges anything itself, and
nothing downstream reads its output as fact — a suggestion has to be accepted
before a single grade moves. That is the whole reason it is allowed to be fuzzy
where :func:`~opengrader.core.submissions.answer_groups.normalize_answer_key`
is not.

Design decisions worth knowing before you extend this:

- **It clusters groups, not responses.** The exact tier stays authoritative and
  runs first; this tier only ever proposes joining whole groups together. That
  makes it cheap (the comparison is over *distinct* answers, not students — a
  200-student question with 30 distinct answers costs 435 comparisons, not
  19,900) and it makes the proposal reviewable: a grader reads a handful of
  distinct answers, not a wall of duplicates.

- **Complete linkage, not connected components.** :mod:`similarity` unions
  above-threshold pairs transitively, which is right for an integrity audit — a
  ring of students who all copied from one source should surface as one cluster
  even if the ends of the chain drifted apart. Here transitivity is a hazard:
  chaining A-B-C where A and C are only 0.4 alike would put two visibly
  different answers under one grade. So a group joins a cluster only when it
  meets the threshold against **every** member already in it, and
  :attr:`SuggestedGroup.min_score` reports that weakest link so the grader sees
  the worst pair rather than the best.

- **Free response only.** The same guard as the similarity audit, for a sharper
  reason: for multiple choice, numeric and multiple answer the exact tier is
  already complete and correct, and "A" against "B" or "42" against "4.2" is a
  short string away from a false merge that would silently mis-score a whole
  group. There is nothing to gain and a cohort to lose.

- **The blank bucket never merges.** A blank answer is an *absence*, not a
  short answer that happens to resemble other short answers, and it is the one
  group most likely to be an extraction failure rather than a real response.

- **A minimum length.** Below :data:`DEFAULT_SUGGESTION_MIN_LENGTH` characters
  of normalized text the exact tier already groups effectively (short answers
  are written the same way by everyone) and edit-distance ratios on a handful of
  characters are noise — "42 mL" and "12 mL" score 0.8.

Similarity is measured with :func:`similarity.similarity_ratio`, the same
transparent ``difflib`` metric the integrity audit uses, so "these answers are
94% alike" means one thing across the whole application.

**A limit worth stating plainly, because it shapes the design.** No string
metric can separate a rewording from a reversal. Measured on real phrasings:

===================================================  =========  =========
pair                                                 character      word
===================================================  =========  =========
"halves the interval each step" / "at each step"         0.960      0.897
"is stable and sorts in place" / "is **not** stable"     0.956      0.941
"a **stack** … **newest** item" / "a **queue** …          0.897      0.857
**oldest** item"
===================================================  =========  =========

A genuine duplicate and a flat contradiction land in the same band, and no
threshold anywhere separates them — raising the cutoff to exclude "not stable"
would also exclude the true merge above it. Tuning is not the answer, so the
design does not rely on it:

1. **A proposal is never applied.** A human accepts it, every time. This is the
   actual safety mechanism, not a courtesy step.
2. **The difference is shown, not just the score.** :func:`diff_answer_spans`
   marks exactly which words differ from the answer being graded, so an
   inserted "not" is a highlighted word rather than something to notice inside
   two paragraphs of near-identical text. A reviewer comparing full paragraphs
   by eye will miss it; a reviewer looking at one highlighted word will not.

Get the presentation right and the metric only has to be a good *shortlist*,
which it is.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from opengrader.core.question_types import FREE_RESPONSE, QuestionType
from opengrader.core.submissions.answer_groups import AnswerGroup
from opengrader.core.submissions.similarity import normalize_for_similarity

# Pairs at or above this similarity may be proposed for merging. Higher than the
# integrity audit's 0.80 because the consequence is different: a similarity flag
# asks a human to *look*, while an accepted merge writes the same grade to
# everyone in the cluster. At 0.85 two students who made the same points in
# their own words (~0.5-0.7) stay apart, while reorderings, typos and small
# wording changes of one answer (~0.85-0.98) come together.
DEFAULT_SUGGESTION_THRESHOLD = 0.85

# Minimum characters of normalized text for a group to be eligible. Short
# answers are already grouped well by the exact tier, and a ratio over a few
# characters says more about their length than their meaning.
DEFAULT_SUGGESTION_MIN_LENGTH = 12


@dataclass(frozen=True)
class SuggestedPair:
    """How alike two of the exact groups in a proposal are."""

    a_key: str
    b_key: str
    score: float


@dataclass(frozen=True)
class SuggestedGroup:
    """A proposal to grade two or more exact answer groups as one.

    ``groups`` is ordered representative-first: the largest exact group, which
    is the answer a grader should read and score, followed by the others that
    would join it. Nothing here has been applied — this is a proposal, and
    every field exists so a human can judge it before it is.
    """

    question_type: QuestionType
    groups: tuple[AnswerGroup, ...]
    pairs: tuple[SuggestedPair, ...]
    # The weakest and strongest links in the cluster. ``min_score`` is the one
    # to show: complete linkage guarantees it is the *worst* pair, so a grader
    # who is happy with it is happy with all of them.
    min_score: float
    max_score: float

    @property
    def representative(self) -> AnswerGroup:
        """The exact group whose answer stands for the whole proposal."""
        return self.groups[0]

    @property
    def group_count(self) -> int:
        return len(self.groups)

    @property
    def member_ids(self) -> tuple[str, ...]:
        """Every student in the proposal, representative's members first.

        Order follows ``groups``, and within a group its own first-seen member
        order, so the union is deterministic and the representative's students
        lead — the same order the grader saw them proposed in.
        """
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
        """How many students the proposal would cover in total."""
        return len(self.member_ids)

    @property
    def added_size(self) -> int:
        """How many students the merge *adds* beyond the representative group."""
        return self.size - self.representative.size


def _canonical_order(group: AnswerGroup) -> tuple[int, str]:
    """Largest group first, then key — the order proposals are built in.

    Deterministic and independent of input order, which is what makes the whole
    clustering reproducible: the same cohort always yields the same proposals.
    """
    return (-group.size, group.key)


def _eligible_groups(
    groups: Iterable[AnswerGroup],
    *,
    min_length: int,
) -> list[tuple[AnswerGroup, str]]:
    """Keep the groups that may take part, paired with their normalized text.

    Applies the three guards in one place: free response only, never the blank
    bucket, and long enough to compare. Returns them in canonical order.
    """
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
    """Score every pair once, keeping only those at or above ``threshold``.

    Mirrors the similarity audit's pruning: ``real_quick_ratio``/``quick_ratio``
    are upper bounds on ``ratio()``, so a pair that fails them cannot reach the
    threshold and is skipped without the expensive match. One reused matcher
    keeps the cached ``b2j`` map for the outer sequence.
    """
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
    """The stored score for an unordered pair, or ``None`` if below threshold."""
    return scores.get((a, b) if a < b else (b, a))


def suggest_group_merges(
    groups: Iterable[AnswerGroup],
    *,
    threshold: float = DEFAULT_SUGGESTION_THRESHOLD,
    min_length: int = DEFAULT_SUGGESTION_MIN_LENGTH,
) -> list[SuggestedGroup]:
    """Propose merges of near-identical exact answer groups, best first.

    Takes the output of :func:`~opengrader.core.submissions.answer_groups.group_responses`
    for one question and returns the clusters of two or more groups whose
    answers are alike enough to be graded together. Nothing is merged and no
    grade is touched: the caller shows each proposal to a grader, and only an
    accepted one ever widens a batch grade.

    Clustering is **complete linkage** — a group joins a cluster only when it
    scores at or above ``threshold`` against every group already in it — so
    :attr:`SuggestedGroup.min_score` is a true floor on how alike the members
    are, and no chain of near-misses can drag two different answers together.
    Clusters are grown greedily in canonical order (largest group first, then
    key), which makes the result deterministic and puts the answer the most
    students gave at the head of each proposal.

    Ordering is total: most students covered first, then the tightest weakest
    link, then the representative's key.
    """
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
        # One pass in canonical order is enough for complete linkage: adding a
        # member can only ever *tighten* the constraint, so a candidate rejected
        # against the cluster as it stood can never become acceptable later.
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
                # Complete linkage guarantees this is present for every pair in
                # the cluster; the check keeps the type honest.
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
                # Weakest pair first: it is the one that decides the proposal.
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


# What one span of a diffed answer is, relative to the answer being graded.
# ``removed`` carries the *reference's* words — showing what this answer is
# missing is as much of the difference as showing what it adds.
DiffKind = Literal["same", "added", "removed"]

# Characters trimmed from a token before comparing it, so a trailing comma is
# not reported as a difference. Mirrors the grouping normalizer's intent: only
# genuine word changes should light up, or the highlighting becomes noise and
# stops being read — which would cost exactly the protection it exists to give.
_TOKEN_EDGE_CHARS = ".,;:!?\"'`()[]{}"


@dataclass(frozen=True)
class AnswerDiffSpan:
    """One run of words in a diffed answer, and whether it differs."""

    text: str
    kind: DiffKind

    @property
    def changed(self) -> bool:
        return self.kind != "same"


def _diff_key(token: str) -> str:
    """The comparable form of a display token (casefolded, edges trimmed)."""
    return token.strip(_TOKEN_EDGE_CHARS).casefold()


def diff_answer_spans(reference: str, other: str) -> tuple[AnswerDiffSpan, ...]:
    """Mark how ``other`` differs from ``reference``, word by word.

    The engine behind showing a grader *what* is different rather than only how
    similar two answers are — the step that makes an inserted "not" a
    highlighted word instead of something to spot inside two near-identical
    paragraphs. Since no similarity metric can catch that reversal (see the
    module docstring), this is what makes accepting a merge a real decision.

    Spans cover ``other`` in order, except ``removed`` spans, which carry the
    reference's words at the point they are missing — what an answer *omits* is
    as much a difference as what it adds. Comparison ignores case and edge
    punctuation so only genuine word changes are marked; the text returned is
    always the original for display.

    Pure and total: either side may be empty.
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
        else:  # replace: what the reference said, then what this answer says
            add(reference_tokens[i1:i2], "removed")
            add(other_tokens[j1:j2], "added")
    return tuple(spans)


@dataclass(frozen=True)
class GroupingEffort:
    """How much grading one question costs before and after grouping.

    The unit throughout is *distinct answers a human has to read and score*,
    which is what grouping actually saves — students who share an answer come
    along for free. ``blank_responses`` is called out separately because the
    blank bucket is never merged and is usually reviewed rather than graded.
    """

    question_id: str
    responses: int
    blank_responses: int
    exact_groups: int
    suggested_merges: int
    groups_after_merges: int

    @property
    def saved_by_suggestions(self) -> int:
        """Distinct answers the accepted proposals would remove from the pile."""
        return self.exact_groups - self.groups_after_merges

    @property
    def reduction_percent(self) -> float | None:
        """Share of the *ungrouped* work grouping removes, or ``None`` if idle.

        Measured against the response count rather than the group count, because
        "I would have graded 200 answers and now grade 24" is the number an
        instructor is deciding with.
        """
        if self.responses <= 0:
            return None
        return (1.0 - self.groups_after_merges / self.responses) * 100.0


def summarize_grouping_effort(
    question_id: str,
    groups: Sequence[AnswerGroup],
    suggestions: Sequence[SuggestedGroup],
) -> GroupingEffort:
    """Count the grading effort for one question, exactly and after merges.

    Pure counting over results the caller already has, so the CLI report and any
    UI showing "24 distinct answers" can never disagree about what they mean.
    """
    responses = sum(group.size for group in groups)
    blank = sum(group.size for group in groups if group.is_blank)
    exact = len(groups)
    # Each proposal replaces the groups it covers with a single one, so the
    # groups it removes is one fewer than the groups it contains.
    removed = sum(suggestion.group_count - 1 for suggestion in suggestions)
    return GroupingEffort(
        question_id=question_id,
        responses=responses,
        blank_responses=blank,
        exact_groups=exact,
        suggested_merges=len(suggestions),
        groups_after_merges=exact - removed,
    )
