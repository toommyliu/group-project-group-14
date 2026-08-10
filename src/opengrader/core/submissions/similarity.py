"""Cross-submission answer-similarity audit: did different students hand in the
*same* free-response answer?

Where :mod:`consistency` asks "did the same answer get the same grade?", this
module asks the integrity question next to it: "did two students give
answers so alike that one likely came from the other?" It is the local,
offline counterpart to a similarity report — a pure, cohort-level function over
the persisted ``.submission.md`` data (no I/O, no OCR, no network) that clusters
students whose answers to a question are near-identical and returns each cluster
worst-first.

Design decisions worth knowing before you extend this:

- **Free response only.** Multiple-choice, multiple-answer and numeric questions
  have a tiny space of valid answers, so two students matching there is
  *expected*, not suspicious — auditing them would drown the real signal in
  noise. Only :data:`~opengrader.core.question_types.FREE_RESPONSE` responses are
  compared. (An exact-match objective answer is already the province of the
  :mod:`consistency` audit, which cares about grade fairness, not authorship.)

- **Length guard.** Short answers ("O(n log n)", "the mitochondria") are
  legitimately identical across a whole class, so a response whose normalized
  text is shorter than ``min_length`` characters is never compared. This is the
  single most important false-positive guard; tune it, don't remove it.

- **Partition by version.** Like the consistency audit, responses are compared
  only within the same ``assignment_version`` — a different version is a
  different prompt, so a match across versions would be meaningless.

- **Metric.** Similarity is :class:`difflib.SequenceMatcher` ratio over a
  normalized token stream (casefolded, punctuation-stripped, whitespace-
  collapsed), so it reads naturally as "these two answers are 96% alike" and is
  robust to cosmetic differences and small edits. It is deliberately *not*
  semantic/embedding-based: this is a flag for a human to inspect, not an
  accusation, and a transparent, explainable metric matters more than recall.

- **Clustering.** Pairs at/above the threshold are unioned into connected
  components, so a ring of three students who all copied surface as one flag
  rather than three overlapping pairs.

The output names students, which is sensitive; callers render it anonymity-aware
(student ids, not names, outside teacher mode), exactly as the consistency audit
does. This engine itself only reports — it never grades, files, or accuses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Literal

from opengrader.core.question_types import FREE_RESPONSE, QuestionType
from opengrader.core.submissions.student_file import StudentSubmissionDocument

SimilaritySeverity = Literal["high", "medium"]

_SEVERITY_RANK: dict[SimilaritySeverity, int] = {"high": 0, "medium": 1}

# Pairs at or above this similarity are flagged. 0.80 catches heavy paraphrase
# and near-verbatim copies while staying clear of the ~0.5–0.7 band where two
# students independently making the same core points naturally land.
DEFAULT_SIMILARITY_THRESHOLD = 0.80

# At or above this, a match is near-verbatim — the strongest signal — so the
# cluster is ranked high rather than medium.
STRONG_SIMILARITY = 0.95

# Minimum length (characters of normalized text) for a response to be compared.
# Below this, identical answers are almost always just short canonical answers,
# not collusion.
DEFAULT_MIN_LENGTH = 25

# Keep only alphanumerics as tokens; everything else (punctuation, symbols) is a
# cosmetic difference for similarity purposes.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class SimilarityKind(str, Enum):
    """Why a cluster was flagged (room to grow; one kind today)."""

    # Two or more students gave near-identical free-response answers.
    NEAR_DUPLICATE = "near_duplicate"


def normalize_for_similarity(text: str) -> str:
    """Reduce a response to a canonical token stream for comparison.

    Casefolds, drops punctuation/symbols, and collapses whitespace so that only
    the words themselves drive the similarity score. Returns ``""`` for a blank
    or symbol-only response.

    Public because :mod:`suggested_groups` measures the same thing on the same
    scale: "94% alike" has to mean one thing whether it is proposing a merge or
    flagging a possible copy, and two normalizers is how that quietly stops
    being true.
    """
    return " ".join(_TOKEN_RE.findall((text or "").casefold()))


def similarity_ratio(a: str, b: str) -> float:
    """Return the 0..1 similarity of two responses on their normalized text.

    A transparent character-sequence ratio (``difflib``) over the normalized
    form, so "f(x) = x^2" and "f(x)=x²"-style cosmetic differences don't lower
    the score. Two blank responses are treated as not comparable (``0.0``)
    rather than a spurious perfect match.
    """
    na, nb = normalize_for_similarity(a), normalize_for_similarity(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb, autojunk=False).ratio()


@dataclass(frozen=True)
class SimilarityMember:
    """One student in a similarity cluster."""

    student_id: str
    student_name: str
    source_pdf: str
    response_text: str

    @property
    def label(self) -> str:
        """A human handle for this student, preferring the name."""
        return self.student_name or self.student_id or self.source_pdf or "unknown"


@dataclass(frozen=True)
class SimilarityPair:
    """One above-threshold pairing inside a cluster, and how alike it is."""

    a: SimilarityMember
    b: SimilarityMember
    score: float


@dataclass(frozen=True)
class SimilarityFlag:
    """A cluster of students whose answers to one question are near-identical.

    Anchored to one question on one version, it carries every member of the
    cluster and each qualifying pairwise score (strongest first). ``max_score``
    is the tightest match in the cluster and drives both ordering and severity.
    """

    question_id: str
    assignment_version: str | None
    kind: SimilarityKind
    severity: SimilaritySeverity
    members: tuple[SimilarityMember, ...]
    pairs: tuple[SimilarityPair, ...]
    max_score: float

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK[self.severity]

    @property
    def member_count(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class _Response:
    """Internal: one comparable free-response answer with its owner."""

    member: SimilarityMember
    question_id: str
    assignment_version: str | None
    normalized: str


# Grouping key: same question, same version. Comparison happens within a group.
_GroupKey = tuple[str, str | None]


def _collect_responses(
    documents: list[StudentSubmissionDocument],
    *,
    min_length: int,
) -> dict[_GroupKey, list[_Response]]:
    """Bucket every comparable free-response answer by (question, version).

    Only free-response answers whose normalized text meets ``min_length`` are
    kept — the two guards (question type and length) that keep the audit
    meaningful. Grading status is irrelevant here: a similar answer is worth
    flagging whether or not it has been scored yet.
    """
    groups: dict[_GroupKey, list[_Response]] = {}
    for document in documents:
        member_base = (
            document.student_id.strip(),
            document.student_name.strip(),
            document.source_pdf,
        )
        for question in document.questions:
            if question.question_type != FREE_RESPONSE:
                continue
            normalized = normalize_for_similarity(question.response_text)
            if len(normalized) < min_length:
                continue
            member = SimilarityMember(
                student_id=member_base[0],
                student_name=member_base[1],
                source_pdf=member_base[2],
                response_text=question.response_text.strip(),
            )
            response = _Response(
                member=member,
                question_id=question.question_id,
                assignment_version=document.assignment_version,
                normalized=normalized,
            )
            key: _GroupKey = (question.question_id, document.assignment_version)
            groups.setdefault(key, []).append(response)
    return groups


class _UnionFind:
    """Minimal union-find for clustering connected similar responses."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, node: int) -> int:
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression keeps repeated lookups flat.
        while self._parent[node] != root:
            self._parent[node], node = root, self._parent[node]
        return root

    def union(self, a: int, b: int) -> None:
        self._parent[self.find(a)] = self.find(b)


def _member_sort_key(member: SimilarityMember) -> tuple[str, str, str]:
    return (member.student_name.lower(), member.student_id, member.source_pdf)


def _flags_for_group(
    question_id: str,
    version: str | None,
    responses: list[_Response],
    *,
    threshold: float,
) -> list[SimilarityFlag]:
    """Find clusters of near-identical answers within one question/version group.

    Every pair is scored once; pairs at/above ``threshold`` union their members;
    each resulting component of two or more students becomes one flag carrying
    all its qualifying pairs (strongest first).
    """
    count = len(responses)
    if count < 2:
        return []

    uf = _UnionFind(count)
    qualifying: list[tuple[int, int, float]] = []
    # One reused matcher: set_seq2 caches the (expensive) b2j map, so the inner
    # loop only re-primes the cheaper seq1. real_quick_ratio/quick_ratio are
    # upper bounds on ratio(), so skipping pairs below them prunes the O(n²)
    # comparisons without changing any result.
    matcher = SequenceMatcher(autojunk=False)
    for i in range(count):
        matcher.set_seq2(responses[i].normalized)
        for j in range(i + 1, count):
            matcher.set_seq1(responses[j].normalized)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            score = matcher.ratio()
            if score >= threshold:
                qualifying.append((i, j, score))
                uf.union(i, j)

    if not qualifying:
        return []

    # Group member indices by their cluster root.
    clusters: dict[int, list[int]] = {}
    for index in range(count):
        clusters.setdefault(uf.find(index), []).append(index)

    pairs_by_root: dict[int, list[tuple[int, int, float]]] = {}
    for i, j, score in qualifying:
        pairs_by_root.setdefault(uf.find(i), []).append((i, j, score))

    flags: list[SimilarityFlag] = []
    for root, indices in clusters.items():
        if len(indices) < 2:
            continue
        members = sorted(
            (responses[index].member for index in indices),
            key=_member_sort_key,
        )
        raw_pairs = pairs_by_root.get(root, [])
        # Canonicalize each pair's member order (lower sort key first) so the
        # report is identical regardless of the documents' input order.
        canonical_pairs = [
            (
                *sorted(
                    (responses[a].member, responses[b].member),
                    key=_member_sort_key,
                ),
                score,
            )
            for a, b, score in raw_pairs
        ]
        pairs = tuple(
            SimilarityPair(a=first, b=second, score=score)
            for first, second, score in sorted(
                canonical_pairs,
                key=lambda pair: (
                    -pair[2],
                    _member_sort_key(pair[0]),
                    _member_sort_key(pair[1]),
                ),
            )
        )
        max_score = max(score for _, _, score in raw_pairs)
        severity: SimilaritySeverity = "high" if max_score >= STRONG_SIMILARITY else "medium"
        flags.append(
            SimilarityFlag(
                question_id=question_id,
                assignment_version=version,
                kind=SimilarityKind.NEAR_DUPLICATE,
                severity=severity,
                members=tuple(members),
                pairs=pairs,
                max_score=max_score,
            )
        )
    return flags


def build_similarity_flags(
    documents: list[StudentSubmissionDocument],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    min_length: int = DEFAULT_MIN_LENGTH,
) -> list[SimilarityFlag]:
    """Return clusters of students with near-identical free-response answers.

    For each question (partitioned by ``assignment_version``), every pair of
    sufficiently-long free responses is scored with :func:`similarity_ratio`;
    pairs at or above ``threshold`` are clustered (connected components), and
    each cluster of two or more students is returned as a
    :class:`SimilarityFlag`. A cluster whose tightest pair is at least
    :data:`STRONG_SIMILARITY` is ``severity="high"`` (near-verbatim), otherwise
    ``"medium"``.

    Only free-response answers are considered, and only those whose normalized
    text is at least ``min_length`` characters — the two guards that keep the
    audit from flagging the naturally-identical short/objective answers a whole
    class gives.

    The ordering is total and deterministic: severity, then tightest match
    first, then question id, version, and the leading member — so the same
    inputs always produce the same report.
    """
    groups = _collect_responses(documents, min_length=min_length)

    flags: list[SimilarityFlag] = []
    for (question_id, version), responses in groups.items():
        flags.extend(
            _flags_for_group(question_id, version, responses, threshold=threshold)
        )

    flags.sort(
        key=lambda flag: (
            flag.severity_rank,
            -flag.max_score,
            flag.question_id,
            flag.assignment_version or "",
            _member_sort_key(flag.members[0]),
        )
    )
    return flags
