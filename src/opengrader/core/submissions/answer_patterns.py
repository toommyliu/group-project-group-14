"""Response-pattern integrity for *objective* questions: did two students give
the same **wrong** answers far more often than chance allows?

:mod:`similarity` deliberately refuses to look at multiple-choice answers, and
that refusal is correct for what it does — a text diff over "B" and "B" is 100%
alike for every honest pair in the class, so a diff-based audit of objective
answers is nothing but false positives. But refusing to look is not the same as
having nothing to say, and on a bubble-sheet exam it leaves the *entire* paper
unaudited.

The right instrument is the one educational-measurement literature has used
since the 1990s: the **shared incorrect answer**. Two students picking the same
correct answer is the exam working. Two students picking the same *wrong* answer
— repeatedly, out of several available wrong answers — is the one signal that
distinguishes copying from competence, because a distractor is chosen for a
reason personal to the student who chose it.

Two numbers are reported for every flagged pair, deliberately:

* the **z-score** (Wesolowsky-style): how far the pair's count of identical
  wrong answers sits above what the *cohort's own* distractor distribution says
  to expect. This is the statistic the flag is decided on.
* the **Harpp-Hogan index** (``EEIC / D``): identical wrong answers divided by
  the number of questions the two answered differently. It is a ratio a person
  can sanity-check by hand and the number the original literature is written
  around, so it is printed alongside — never as the flag's decision rule, since
  it has no notion of how many distractors a question offered.

Design decisions worth knowing before you extend this:

- **The answer key is required.** "Shared *wrong* answer" is undefined without
  it, which is the one hard difference from :mod:`similarity` (which needs no
  key at all). Callers build :class:`ObjectiveResponse` records through
  :func:`objective_responses_from_documents`, mirroring how
  ``analytics/grader_agreement`` takes a prepared ``GradedAnswer`` rather than
  reaching for files itself.

- **The probability model is the cohort's, not an assumption.** For each
  question, the chance that two independent wrong students land on the *same*
  distractor is estimated from how the class actually spread across the
  distractors — no equal-likelihood assumption, which would be badly wrong for
  the one attractive distractor most questions have. The estimator is
  ``Σ n_c(n_c-1) / (N(N-1))``, the probability that two students *drawn from the
  observed answers* match, not the naive ``Σ (n_c/N)²``: the pair being tested
  is part of the cohort that produced the counts, and each student inflates the
  frequency of their own choice, so the squared-fraction form is biased upward
  and would understate every z-score.

- **Two eligibility guards, and both are load-bearing.** A question is analyzed
  only when at least :data:`MIN_DISTINCT_WRONG_ANSWERS` different wrong answers
  were given and at least :data:`MIN_WRONG_RESPONDENTS` students got it wrong.
  The first is what silently and correctly excludes **True/False** questions:
  with one available wrong answer, every wrong student matches every other by
  construction and the question carries no information about authorship. The
  second is what stops a two-student distractor distribution from being treated
  as a probability model. Between them they leave the eligible set as
  "multiple-choice questions with a real spread of wrong answers", which is
  exactly the set the statistic is valid on — arrived at by rule rather than by
  special-casing a question type.

- **Numeric and select-all questions never enter.** Not by exclusion here, but
  because ``grading/solutions.objective_correctness`` deliberately returns
  ``None`` rather than ``False`` for those (a "wrong" number is usually a
  transcription artifact). They therefore contribute no wrong answers and drop
  out on their own — the conservative stance is inherited rather than re-decided.

- **The threshold accounts for how many pairs were tested.** A 200-student class
  is 19,900 pairs; at z ≥ 3 pure chance produces about 27 flags, which is a
  report nobody can act on. :data:`DEFAULT_PATTERN_THRESHOLD` is therefore 4.0
  (~0.6 expected false flags at that class size), and every report carries
  :attr:`AnswerPatternReport.expected_by_chance` so the reader can judge the
  list against the number of comparisons behind it rather than against a
  reassuring absolute. Raising the threshold, not lowering it, is the right
  response to a noisy report on a large class.

- **``min_shared_wrong`` is a hard gate, not a tie-breaker.** A pair whose only
  shared ground is one or two questions can clear the threshold on coincidence
  alone once those questions have many distractors — see
  :data:`DEFAULT_MIN_SHARED_WRONG` for the arithmetic. One coincidence is not
  evidence, so a pair must clear that many identical wrong answers *before* its
  z-score is consulted at all.

- **Partition by version**, like every other cohort audit here: a different
  version is a different answer key, so a "match" across versions is meaningless.

**The limitation to state rather than hide.** The statistic needs shared *wrong*
answers to work with, so a short or easy objective section cannot carry it.
Simulated, a student who copied a neighbour's paper **in full** scores a median
z of 3.3 on a 20-question section the class got 70% right — under any threshold
that isn't swamped by false positives — against 5.5 on 50 questions and 7.1 when
the class averages 50%. That is a property of the evidence, not a tuning
problem: twenty questions mostly answered correctly contain perhaps six shared
wrong answers, and six is not enough to separate friendship from copying. A
report over too few analyzable questions is therefore reported as such
(:attr:`AnswerPatternReport.analyzed`, and the excluded-question list) rather
than as a clean result.

The output names students, which is sensitive; callers render it anonymity-aware
(ids, not names, outside teacher mode), exactly as the similarity and
consistency audits do. Like them, this engine only reports — it never grades,
files, or accuses. A high z-score is a reason to look at two papers, and nothing
more: the statistic cannot tell copying from two friends who studied from the
same wrong set of notes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from typing import Iterable, Literal, Mapping, Sequence

from opengrader.core.grading.solutions import (
    normalize_choice_response,
    objective_correctness,
    solution_entries_for_submission,
)
from opengrader.core.qa_file import QADocument
from opengrader.core.submissions.student_file import StudentSubmissionDocument

PatternSeverity = Literal["high", "medium"]

_SEVERITY_RANK: dict[PatternSeverity, int] = {"high": 0, "medium": 1}

# Standard deviations above the expected number of identical wrong answers at
# which a pair is flagged. 4.0 rather than the conventional 3.0 because the test
# runs over *every pair* in the class, and the difference is not theoretical:
# simulated over 20 independent honest cohorts of 60 students x 50 four-option
# questions (35,098 pair-tests in total, no copying anywhere), z >= 3.0 produced
# 2.3 flags per cohort — a report an instructor would learn to ignore — while
# z >= 4.0 produced 0.05 and z >= 4.5 produced none. 4.0 keeps the false-flag
# rate at roughly one cohort in twenty while still catching a full copier, who
# scores 5-7 on an exam of that size (see DEFAULT_MIN_SHARED_WRONG for the short
# -exam limitation this cannot fix).
DEFAULT_PATTERN_THRESHOLD = 4.0

# At or above this the pattern is extreme even after the pair count is taken into
# account (~0.006 expected flags across 19,900 pairs), so the flag is ranked high
# rather than medium. In the same simulation a wholesale copier on a 50-question
# exam lands here, while the worst honest pair never exceeded 4.4.
STRONG_PATTERN_THRESHOLD = 6.0

# A pair must share at least this many identical wrong answers before its
# z-score is consulted at all, because a pair whose *only* shared ground is one
# or two questions can clear the threshold on coincidence alone. How easily
# depends on how finely the class split across the distractors, and the spread
# is wide: for a pair whose both-wrong set is exactly those questions, k
# agreements score z = k(1-p)/sqrt(kp(1-p)). On an evenly-answered four-option
# question (p≈0.35) that is only 1.9 at k=2 — but on an eight-option one
# (p≈0.12) it is 3.8, and where the wrong answers scatter further (p≈0.05) a
# *single* agreement already scores 4.4. Requiring three agreements first is
# what stops the report's quality depending on how many distractors a question
# happened to offer.
DEFAULT_MIN_SHARED_WRONG = 3

# A question is only analyzed when the class gave at least this many *different*
# wrong answers to it. Below it, every wrong student agrees by construction —
# which is precisely the True/False case, and why that question type needs no
# special handling here.
MIN_DISTINCT_WRONG_ANSWERS = 2

# ...and when at least this many students got it wrong, so the distractor spread
# is an estimate rather than an anecdote.
MIN_WRONG_RESPONDENTS = 3


class PatternKind(str, Enum):
    """Why a pair was flagged (room to grow; one kind today)."""

    # The pair gave identical incorrect answers far more often than the cohort's
    # own distractor distribution predicts.
    SHARED_WRONG_ANSWERS = "shared_wrong_answers"


class ExclusionReason(str, Enum):
    """Why an objective question was left out of the analysis."""

    # Nobody got it wrong, so it carries no shared-wrong-answer signal.
    NO_WRONG_ANSWERS = "no_wrong_answers"
    # Every wrong student gave the same wrong answer (True/False, or a question
    # with one overwhelming distractor): agreement there is guaranteed, not
    # informative.
    SINGLE_DISTRACTOR = "single_distractor"
    # Too few wrong answers to estimate how the class spreads across distractors.
    TOO_FEW_WRONG = "too_few_wrong"


@dataclass(frozen=True)
class PatternMember:
    """One student in a flagged pair."""

    student_id: str
    student_name: str
    source_pdf: str

    @property
    def label(self) -> str:
        """A human handle for this student, preferring the name."""
        return self.student_name or self.student_id or self.source_pdf or "unknown"


@dataclass(frozen=True)
class ObjectiveResponse:
    """One student's answer to one rule-gradable question.

    The single input shape of this engine, built by
    :func:`objective_responses_from_documents` (or by a test, or by a future
    front end). ``answer`` is already normalized the way grading normalizes a
    choice response, so "b", "B." and "B)" are one answer here exactly as they
    are one answer to the auto-grader. A blank response is never turned into an
    ``ObjectiveResponse``: two students both leaving a question empty is not a
    shared wrong answer.
    """

    member: PatternMember
    question_id: str
    assignment_version: str | None
    answer: str
    correct: bool
    # What the answer key accepted, for display beside the shared wrong answer.
    expected_answer: str = ""


@dataclass(frozen=True)
class SharedWrongAnswer:
    """One question where both students gave the *same* incorrect answer."""

    question_id: str
    answer: str
    expected_answer: str
    # The cohort-estimated chance two independent wrong students match here.
    # Printed because it is what makes one shared answer surprising and another
    # unremarkable, and it is the whole of the arithmetic behind the z-score.
    match_probability: float


@dataclass(frozen=True)
class AnswerPatternFlag:
    """One pair of students whose incorrect answers agree implausibly often.

    Pairs, not clusters, are the unit here — unlike :mod:`similarity`, where a
    ring of copiers is genuinely one artifact. The evidence *is* pairwise ("these
    two share seven wrong answers"), and unioning pairs into a component would
    hide which pair carried the finding behind a list of names.
    """

    assignment_version: str | None
    kind: PatternKind
    severity: PatternSeverity
    a: PatternMember
    b: PatternMember
    shared_wrong: tuple[SharedWrongAnswer, ...]
    # How many identical wrong answers were expected from the cohort's own
    # distractor spread, and how far above that the pair actually landed.
    expected_shared: float
    z_score: float
    # Questions both got wrong (the population the expectation is over) and
    # questions both answered but differently (Harpp-Hogan's denominator).
    both_wrong_count: int
    difference_count: int
    # Every eligible question at least one of them answered — the size of the
    # exam this finding rests on.
    compared_questions: int

    @property
    def members(self) -> tuple[PatternMember, PatternMember]:
        return (self.a, self.b)

    @property
    def shared_wrong_count(self) -> int:
        return len(self.shared_wrong)

    @property
    def harpp_hogan_index(self) -> float | None:
        """Identical wrong answers per question answered differently.

        ``None`` when the two never differed on an analyzed question — the ratio
        is undefined there, and reporting it as infinity would put a number on
        what is really "these two papers are the same paper". The z-score still
        decides the flag either way.
        """
        if self.difference_count <= 0:
            return None
        return self.shared_wrong_count / self.difference_count

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK[self.severity]


@dataclass(frozen=True)
class QuestionExclusion:
    """An objective question the analysis could not use, and why.

    Reported rather than dropped: a report that silently analyzed 4 of an exam's
    50 questions and found nothing reads as a clean bill of health, which is the
    same dishonesty ``scan_check`` exists to avoid at the other end of the
    pipeline.
    """

    question_id: str
    assignment_version: str | None
    reason: ExclusionReason
    wrong_count: int
    distinct_wrong_answers: int


@dataclass(frozen=True)
class AnswerPatternReport:
    """Flagged pairs plus everything needed to judge how much they mean."""

    flags: tuple[AnswerPatternFlag, ...] = ()
    # Question ids (deduplicated across versions) the statistic actually ran on.
    analyzed_questions: tuple[str, ...] = ()
    excluded_questions: tuple[QuestionExclusion, ...] = ()
    # Pairs of students who got at least one analyzed question wrong together —
    # i.e. pairs a test was actually performed on, and the multiple-comparison
    # denominator the threshold has to survive.
    pairs_compared: int = 0
    threshold: float = DEFAULT_PATTERN_THRESHOLD
    min_shared_wrong: int = DEFAULT_MIN_SHARED_WRONG

    @property
    def expected_by_chance(self) -> float:
        """Roughly how many of these flags a clean cohort would produce anyway.

        ``pairs_compared`` × the normal tail above ``threshold``. It is an
        approximation (the per-pair count is Poisson-binomial, not normal, and
        the pairs are not independent), so it is offered as the order of
        magnitude a reader should hold the list against — not as a p-value.
        """
        if self.pairs_compared <= 0:
            return 0.0
        return self.pairs_compared * _normal_tail(self.threshold)

    @property
    def analyzed(self) -> bool:
        """Whether any question survived the eligibility guards.

        ``False`` means the audit ran and had nothing to run *on* — an all
        free-response exam, or one nobody got wrong twice the same way — which a
        caller must report differently from "checked, nothing found".
        """
        return bool(self.analyzed_questions)


def _normal_tail(z: float) -> float:
    """P(Z > z) for a standard normal, from stdlib ``erfc``.

    Used only to express the threshold as an expected number of chance flags, so
    an approximation is appropriate and a SciPy dependency is not.
    """
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def objective_responses_from_documents(
    documents: Sequence[StudentSubmissionDocument],
    qa_doc: QADocument,
) -> list[ObjectiveResponse]:
    """Turn a cohort plus its answer key into the engine's input records.

    The one place this feature reads a QA file, kept as an adapter (like
    ``grader_agreement.answers_from_journal``) so the statistic itself stays a
    pure function of prepared records and unit-tests with no answer key at all.

    Correctness comes from ``grading/solutions.objective_correctness`` — the same
    rule the auto-grader applies — so "wrong" means here exactly what it means
    when a grade is written. An answer that rule cannot settle (a free response,
    a numeric miss, an unmatched select-all) yields no record: it is not known to
    be wrong, and guessing would put honest students in an integrity report.
    """
    responses: list[ObjectiveResponse] = []
    for document in documents:
        member = PatternMember(
            student_id=document.student_id.strip(),
            student_name=document.student_name.strip(),
            source_pdf=document.source_pdf,
        )
        entries = solution_entries_for_submission(qa_doc, document)
        for question in document.questions:
            entry = entries.get(question.question_id)
            if entry is None:
                continue
            raw = question.response_text or ""
            answer = normalize_choice_response(raw)
            if not answer:
                # An unanswered question is not a shared wrong answer, and a
                # cohort of blanks would otherwise look like mass collusion.
                continue
            correct = objective_correctness(entry, raw, question)
            if correct is None:
                continue
            responses.append(
                ObjectiveResponse(
                    member=member,
                    question_id=question.question_id,
                    assignment_version=document.assignment_version,
                    answer=answer,
                    correct=correct,
                    expected_answer=", ".join(entry.expected_answers),
                )
            )
    return responses


def _member_sort_key(member: PatternMember) -> tuple[str, str, str]:
    return (member.student_name.lower(), member.student_id, member.source_pdf)


def match_probability(wrong_answers: Sequence[str]) -> float:
    """Chance two of these wrong answers, drawn as a pair, are identical.

    ``Σ n_c(n_c-1) / (N(N-1))`` over the distinct wrong answers ``c``. The
    without-replacement form is deliberate: the pair under test is drawn *from*
    this very list, so the naive ``Σ (n_c/N)²`` counts each student as a possible
    partner for themselves and is biased upward — which would inflate every
    expectation and quietly suppress real findings.
    """
    total = len(wrong_answers)
    if total < 2:
        return 0.0
    counts: dict[str, int] = {}
    for answer in wrong_answers:
        counts[answer] = counts.get(answer, 0) + 1
    pairs_matching = sum(count * (count - 1) for count in counts.values())
    return pairs_matching / (total * (total - 1))


@dataclass(frozen=True)
class _EligibleQuestion:
    """One analyzable question and the probability model derived from it."""

    question_id: str
    expected_answer: str
    match_probability: float


def _bit_indices(mask: int) -> Iterable[int]:
    """Yield the set bit positions of ``mask``, lowest first."""
    while mask:
        lowest = mask & -mask
        yield lowest.bit_length() - 1
        mask ^= lowest


def _partition_by_version(
    responses: Sequence[ObjectiveResponse],
) -> dict[str | None, list[ObjectiveResponse]]:
    groups: dict[str | None, list[ObjectiveResponse]] = {}
    for response in responses:
        groups.setdefault(response.assignment_version, []).append(response)
    return groups


def _eligible_questions(
    responses: Sequence[ObjectiveResponse],
    version: str | None,
) -> tuple[list[_EligibleQuestion], list[QuestionExclusion]]:
    """Split one version's questions into the analyzable and the excluded.

    Question order follows first appearance in ``responses`` so a report reads in
    the order the exam is laid out, and so two runs over the same folder produce
    the same bit positions and therefore the same output.
    """
    wrong_by_question: dict[str, list[str]] = {}
    expected_by_question: dict[str, str] = {}
    order: list[str] = []
    for response in responses:
        if response.question_id not in wrong_by_question:
            wrong_by_question[response.question_id] = []
            order.append(response.question_id)
        expected_by_question.setdefault(response.question_id, response.expected_answer)
        if not response.correct:
            wrong_by_question[response.question_id].append(response.answer)

    eligible: list[_EligibleQuestion] = []
    excluded: list[QuestionExclusion] = []
    for question_id in order:
        wrong = wrong_by_question[question_id]
        distinct = len(set(wrong))
        reason: ExclusionReason | None = None
        if not wrong:
            reason = ExclusionReason.NO_WRONG_ANSWERS
        elif distinct < MIN_DISTINCT_WRONG_ANSWERS:
            reason = ExclusionReason.SINGLE_DISTRACTOR
        elif len(wrong) < MIN_WRONG_RESPONDENTS:
            reason = ExclusionReason.TOO_FEW_WRONG
        if reason is not None:
            excluded.append(
                QuestionExclusion(
                    question_id=question_id,
                    assignment_version=version,
                    reason=reason,
                    wrong_count=len(wrong),
                    distinct_wrong_answers=distinct,
                )
            )
            continue
        eligible.append(
            _EligibleQuestion(
                question_id=question_id,
                expected_answer=expected_by_question.get(question_id, ""),
                match_probability=match_probability(wrong),
            )
        )
    return eligible, excluded


@dataclass
class _StudentProfile:
    """One student's answers to one version, as bitmasks over eligible questions.

    Bitmasks rather than sets because every pairwise quantity the statistic needs
    — how many questions both got wrong, how many both answered, how many both
    got right — is one integer AND plus a popcount, which is what keeps a
    200-student class (19,900 pairs) a sub-second report instead of a nested
    Python loop over every question of every pair.
    """

    member: PatternMember
    answered_mask: int = 0
    wrong_mask: int = 0
    correct_mask: int = 0
    # Question bit -> the wrong answer given, for the shared-answer index below.
    wrong_answers: dict[int, str] = field(default_factory=dict)


def _build_profiles(
    responses: Sequence[ObjectiveResponse],
    eligible: Sequence[_EligibleQuestion],
) -> list[_StudentProfile]:
    """Collapse one version's responses into one profile per student.

    Students are keyed by their whole identity triple (id, name, source PDF)
    rather than by student id alone, because an unidentified submission has no
    id and merging every such paper into one phantom student would invent
    matches out of nothing.
    """
    bit_of = {question.question_id: index for index, question in enumerate(eligible)}
    profiles: dict[PatternMember, _StudentProfile] = {}
    for response in responses:
        bit = bit_of.get(response.question_id)
        if bit is None:
            continue
        profile = profiles.get(response.member)
        if profile is None:
            profile = _StudentProfile(member=response.member)
            profiles[response.member] = profile
        flag = 1 << bit
        profile.answered_mask |= flag
        if response.correct:
            profile.correct_mask |= flag
        else:
            profile.wrong_mask |= flag
            profile.wrong_answers[bit] = response.answer
    # Sorted so the pair ordering — and therefore the whole report — is stable
    # across runs regardless of the order the submissions were read from disk.
    return sorted(profiles.values(), key=lambda profile: _member_sort_key(profile.member))


def _shared_wrong_index(
    profiles: Sequence[_StudentProfile],
    eligible: Sequence[_EligibleQuestion],
) -> dict[tuple[int, int], list[int]]:
    """Map each student pair to the question bits where they agreed on a wrong answer.

    Built by inverting each question (answer -> the students who gave it) rather
    than by comparing every pair on every question: only students who actually
    chose the same distractor are ever paired up, so the work is proportional to
    the agreements that exist rather than to the size of the class squared.
    """
    shared: dict[tuple[int, int], list[int]] = {}
    for bit in range(len(eligible)):
        by_answer: dict[str, list[int]] = {}
        for index, profile in enumerate(profiles):
            answer = profile.wrong_answers.get(bit)
            if answer is not None:
                by_answer.setdefault(answer, []).append(index)
        for indices in by_answer.values():
            if len(indices) < 2:
                continue
            for first, second in combinations(indices, 2):
                shared.setdefault((first, second), []).append(bit)
    return shared


def _flags_for_version(
    version: str | None,
    responses: Sequence[ObjectiveResponse],
    *,
    threshold: float,
    min_shared_wrong: int,
) -> tuple[list[AnswerPatternFlag], list[_EligibleQuestion], list[QuestionExclusion], int]:
    """Run the statistic over one assignment version.

    Returns the flags, the questions it was able to use, the ones it could not,
    and how many student pairs a test was actually performed on.
    """
    eligible, excluded = _eligible_questions(responses, version)
    if not eligible:
        return [], [], excluded, 0

    profiles = _build_profiles(responses, eligible)
    if len(profiles) < 2:
        return [], eligible, excluded, 0

    # Pairs a test was performed on: both students got at least one analyzed
    # question wrong, which is the only situation in which a shared wrong answer
    # was even possible. This is the denominator `expected_by_chance` divides
    # the threshold's tail across, so it has to be the tested set and not simply
    # every pair in the class.
    pairs_compared = sum(
        1
        for first, second in combinations(range(len(profiles)), 2)
        if profiles[first].wrong_mask & profiles[second].wrong_mask
    )

    probability = [question.match_probability for question in eligible]
    flags: list[AnswerPatternFlag] = []
    for (first, second), bits in _shared_wrong_index(profiles, eligible).items():
        if len(bits) < min_shared_wrong:
            continue
        a, b = profiles[first], profiles[second]

        both_wrong = a.wrong_mask & b.wrong_mask
        expected = 0.0
        variance = 0.0
        both_wrong_count = 0
        for bit in _bit_indices(both_wrong):
            chance = probability[bit]
            expected += chance
            variance += chance * (1.0 - chance)
            both_wrong_count += 1
        if variance <= 0.0:
            # Nothing here could have varied, so there is no surprise to measure.
            # Unreachable for a flagged pair (an eligible question always has
            # 0 <= p < 1, and a matched one has p > 0), kept as the honest
            # refusal rather than a division that would produce infinity.
            continue

        z_score = (len(bits) - expected) / math.sqrt(variance)
        if z_score < threshold:
            continue

        both_answered = a.answered_mask & b.answered_mask
        # Two students who were both right on a multiple-choice question wrote
        # the same thing by definition, so agreement is the shared wrong answers
        # plus the shared correct ones — which keeps Harpp-Hogan's denominator a
        # pair of popcounts instead of another per-question loop.
        agreements = len(bits) + (a.correct_mask & b.correct_mask).bit_count()
        differences = both_answered.bit_count() - agreements

        shared_wrong = tuple(
            SharedWrongAnswer(
                question_id=eligible[bit].question_id,
                answer=a.wrong_answers[bit],
                expected_answer=eligible[bit].expected_answer,
                match_probability=probability[bit],
            )
            # Rarest agreement first: the surprising answers are the reason the
            # pair is in the report, so they lead.
            for bit in sorted(bits, key=lambda index: (probability[index], index))
        )
        severity: PatternSeverity = (
            "high" if z_score >= STRONG_PATTERN_THRESHOLD else "medium"
        )
        # Canonical member order so the same pair reads the same way regardless
        # of the order the submissions happened to be loaded in.
        member_a, member_b = sorted((a.member, b.member), key=_member_sort_key)
        flags.append(
            AnswerPatternFlag(
                assignment_version=version,
                kind=PatternKind.SHARED_WRONG_ANSWERS,
                severity=severity,
                a=member_a,
                b=member_b,
                shared_wrong=shared_wrong,
                expected_shared=expected,
                z_score=z_score,
                both_wrong_count=both_wrong_count,
                difference_count=differences,
                compared_questions=(a.answered_mask | b.answered_mask).bit_count(),
            )
        )
    return flags, eligible, excluded, pairs_compared


def build_answer_pattern_report(
    responses: Sequence[ObjectiveResponse],
    *,
    threshold: float = DEFAULT_PATTERN_THRESHOLD,
    min_shared_wrong: int = DEFAULT_MIN_SHARED_WRONG,
) -> AnswerPatternReport:
    """Find student pairs whose incorrect objective answers agree implausibly.

    For each assignment version independently, questions are screened for
    analyzability (see :data:`MIN_DISTINCT_WRONG_ANSWERS` /
    :data:`MIN_WRONG_RESPONDENTS`), the cohort's own distractor spread gives each
    one a match probability, and every pair of students is scored on how far
    their count of identical wrong answers exceeds the sum of those
    probabilities over the questions they *both* got wrong.

    A pair is flagged only when it clears ``min_shared_wrong`` identical wrong
    answers **and** ``threshold`` standard deviations. Flags are ordered
    severity-first, then by z-score, then deterministically by version and
    student, so the same folder always produces the same report.

    The result is a flag for a human to inspect. It says two papers agree on
    their mistakes more than the class's own answer pattern can explain; it does
    not say why, and the report is deliberately shaped so a reader sees the
    number of comparisons behind it (:attr:`AnswerPatternReport.pairs_compared`,
    :attr:`~AnswerPatternReport.expected_by_chance`) alongside the findings.
    """
    flags: list[AnswerPatternFlag] = []
    analyzed: list[str] = []
    excluded: list[QuestionExclusion] = []
    pairs_compared = 0

    for version, group in _partition_by_version(responses).items():
        version_flags, eligible, version_excluded, tested = _flags_for_version(
            version,
            group,
            threshold=threshold,
            min_shared_wrong=min_shared_wrong,
        )
        flags.extend(version_flags)
        excluded.extend(version_excluded)
        pairs_compared += tested
        for question in eligible:
            if question.question_id not in analyzed:
                analyzed.append(question.question_id)

    flags.sort(
        key=lambda flag: (
            flag.severity_rank,
            -flag.z_score,
            -flag.shared_wrong_count,
            flag.assignment_version or "",
            _member_sort_key(flag.a),
            _member_sort_key(flag.b),
        )
    )
    excluded.sort(
        key=lambda item: (item.assignment_version or "", item.question_id)
    )
    return AnswerPatternReport(
        flags=tuple(flags),
        analyzed_questions=tuple(analyzed),
        excluded_questions=tuple(excluded),
        pairs_compared=pairs_compared,
        threshold=threshold,
        min_shared_wrong=min_shared_wrong,
    )


def build_answer_pattern_report_for_documents(
    documents: Sequence[StudentSubmissionDocument],
    qa_doc: QADocument,
    *,
    threshold: float = DEFAULT_PATTERN_THRESHOLD,
    min_shared_wrong: int = DEFAULT_MIN_SHARED_WRONG,
) -> AnswerPatternReport:
    """Convenience wrapper: adapt a cohort + answer key, then run the statistic.

    The two halves stay separate functions on purpose — every front end builds
    its records the same way through the adapter, while the statistic itself can
    be tested against hand-written responses with no QA file in sight.
    """
    return build_answer_pattern_report(
        objective_responses_from_documents(documents, qa_doc),
        threshold=threshold,
        min_shared_wrong=min_shared_wrong,
    )


# Kept for callers that want to summarize an exclusion without importing the
# enum's spelling into their own formatting code.
EXCLUSION_LABELS: Mapping[ExclusionReason, str] = {
    ExclusionReason.NO_WRONG_ANSWERS: "nobody answered it incorrectly",
    ExclusionReason.SINGLE_DISTRACTOR: "only one wrong answer was ever given",
    ExclusionReason.TOO_FEW_WRONG: "too few wrong answers to model",
}
