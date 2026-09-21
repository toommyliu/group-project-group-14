from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Literal

from opengrader.core.question_types import FREE_RESPONSE
from opengrader.core.submissions.student_file import StudentSubmissionDocument

SimilaritySeverity = Literal["high", "medium"]

_SEVERITY_RANK: dict[SimilaritySeverity, int] = {"high": 0, "medium": 1}

DEFAULT_SIMILARITY_THRESHOLD = 0.80

STRONG_SIMILARITY = 0.95

DEFAULT_MIN_LENGTH = 25

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class SimilarityKind(str, Enum):
    NEAR_DUPLICATE = "near_duplicate"


def normalize_for_similarity(text: str) -> str:
    return " ".join(_TOKEN_RE.findall((text or "").casefold()))


def similarity_ratio(a: str, b: str) -> float:
    na, nb = normalize_for_similarity(a), normalize_for_similarity(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb, autojunk=False).ratio()


@dataclass(frozen=True)
class SimilarityMember:
    student_id: str
    student_name: str
    source_pdf: str
    response_text: str

    @property
    def label(self) -> str:
        return self.student_name or self.student_id or self.source_pdf or "unknown"


@dataclass(frozen=True)
class SimilarityPair:
    a: SimilarityMember
    b: SimilarityMember
    score: float


@dataclass(frozen=True)
class SimilarityFlag:
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
    member: SimilarityMember
    question_id: str
    assignment_version: str | None
    normalized: str


_GroupKey = tuple[str, str | None]


def _collect_responses(
    documents: list[StudentSubmissionDocument],
    *,
    min_length: int,
) -> dict[_GroupKey, list[_Response]]:
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
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, node: int) -> int:
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
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
    count = len(responses)
    if count < 2:
        return []

    uf = _UnionFind(count)
    qualifying: list[tuple[int, int, float]] = []
    # Quick ratios bound ratio(), so pruning cannot discard a qualifying pair.
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
    """Uses connected components: members can be linked without meeting threshold pairwise."""
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
