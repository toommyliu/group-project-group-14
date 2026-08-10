"""Display-formatting helpers shared across TUI screens."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.text import Text

from opengrader.core.question_types import humanize_question_label

if TYPE_CHECKING:
    # Import for typing only: at runtime this module must stay dependency-free
    # so the widgets package can import it without a state<->widgets cycle.
    from opengrader.core.analytics import (
        ActorGradingStats,
        DistractorTally,
        GraderAgreementReport,
        GraderStats,
        GradingSessionReport,
        ItemAnalysis,
        OutcomeMastery,
        OutcomeReport,
        PairAgreement,
    )
    from opengrader.core.comments import CommentSnippet
    from opengrader.core.curve import Curve, CurveEffect
    from opengrader.core.environment import EnvironmentCheck, EnvironmentReport
    from opengrader.core.release import (
        GradeChange,
        GradeRelease,
        ReadinessIssue,
        ReadinessReport,
        ReleaseComparison,
        StudentTotalChange,
    )
    from opengrader.core.history.journal import GradeJournalEntry
    from opengrader.core.submissions.page_context import PageContextEntry
    from opengrader.tui.data.models import (
        AnswerPatternMember,
        AnswerPatternReportItem,
        AnswerPatternReportView,
        ConsistencyGradeVariant,
        ConsistencyMember,
        ConsistencyReportItem,
        RegradeReportItem,
        ReviewItem,
        SharedWrongAnswerRow,
        SimilarityReportItem,
        SimilarityReportMember,
        SimilarityReportPair,
    )
    from opengrader.tui.state.grading_log import GradingLogEntry

# Matches the key highlight used by the status bar so every hotkey legend
# in the app reads the same.
HOTKEY_STYLE = "bold #EBCB8B"

def format_question_label(question_id: str) -> str:
    """Humanize a QA question id for display ("q5a" -> "Question 5a").

    Thin alias for :func:`opengrader.core.question_types.humanize_question_label`
    — the shared source of truth, so TUI screens and the headless PDF/report
    generators label a question identically.
    """
    return humanize_question_label(question_id)


_QUESTION_TYPE_LABELS = {
    "free_response": "Free Response",
    "multiple_choice": "Multiple Choice",
    "multiple_answer": "Select all that apply",
    "numeric": "Numeric",
}


def format_question_type(question_type: str) -> str:
    """Humanize a QuestionType literal ("multiple_choice" -> "Multiple Choice").

    Unknown or empty values return "" so callers can simply skip the label.
    """
    return _QUESTION_TYPE_LABELS.get(question_type.strip().lower(), "")


# App palette markers for the grading log: grade saves in green, comments in
# the hotkey yellow, rubric edits in the accent blue.
_LOG_KIND_MARKERS = {
    "grade": ("✓", "bold #A3BE8C"),
    "comment": ("✎", HOTKEY_STYLE),
    "rubric": ("±", "bold #81A1C1"),
    "identity": ("⌂", "bold #B48EAD"),
    "transcription": ("⌨", "bold #D08770"),
}


def format_grading_log_row(entry: GradingLogEntry, *, teacher_mode: bool) -> Text:
    """Render one grading-log entry as a single ellipsized line.

    Teacher mode shows student names; anonymous mode falls back to the
    student display id, matching the app-wide anonymity rules.
    """
    marker, marker_style = _LOG_KIND_MARKERS.get(entry.kind, ("•", "dim"))
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(entry.timestamp.strftime("%H:%M"), style="dim")
    text.append(f" {marker} ", style=marker_style)
    text.append(format_question_label(entry.question_id))
    student_label = (
        entry.student_name
        if teacher_mode and entry.student_name
        else entry.student_display_id
    )
    if student_label:
        text.append(" · ", style="dim")
        text.append(student_label)
    if entry.summary:
        text.append(" · ", style="dim")
        text.append(entry.summary, style="dim" if entry.kind == "comment" else None)
    return text


# Needs-review severity → (glyph, style). High in red, medium in the hotkey
# yellow, low in the accent blue, matching the app palette.
_REVIEW_SEVERITY_MARKERS = {
    "high": ("●", "bold #BF616A"),
    "medium": ("◐", HOTKEY_STYLE),
    "low": ("○", "bold #81A1C1"),
}

# Human labels for review reasons (the enum values are machine-readable).
_REVIEW_REASON_LABELS = {
    "missing_student_id": "Missing student ID",
    "malformed_student_id": "Suspect student ID",
    "missing_student_name": "Missing student name",
    "empty_response": "Empty response",
    "unreadable_response": "Unreadable answer",
    "low_confidence_transcription": "Low-confidence OCR",
    "low_confidence_identity": "Unsure whose paper this is",
    "missing_source_segments": "No source region",
    "low_confidence_ai_grade": "Low-confidence AI grade",
    "zero_score_sweep": "All answers scored 0",
    "ungraded": "Not graded",
    "missing_submission": "No submission",
    "open_regrade_request": "Regrade requested",
    "graded_before_rubric_change": "Graded on the old rubric",
}


def format_review_reason(reason: str) -> str:
    """Humanize a ReviewReason value ("empty_response" -> "Empty response")."""
    return _REVIEW_REASON_LABELS.get(reason, reason.replace("_", " ").capitalize())


def format_review_row(item: "ReviewItem", *, teacher_mode: bool) -> Text:
    """Render one needs-review item as a single ellipsized line.

    Anonymity-aware: teacher mode shows the student name, otherwise the display
    id — matching the app-wide grading anonymity rules.
    """
    severity = getattr(item.severity, "value", item.severity)
    reason = getattr(item.reason, "value", item.reason)
    marker, marker_style = _REVIEW_SEVERITY_MARKERS.get(severity, ("•", "dim"))
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{marker} ", style=marker_style)
    text.append(format_review_reason(reason))
    student_label = (
        item.student_name
        if teacher_mode and item.student_name
        else item.student_display_id
    )
    if student_label:
        text.append(" · ", style="dim")
        text.append(student_label)
    if item.question_id:
        text.append(" · ", style="dim")
        text.append(format_question_label(item.question_id))
    source_pdf_name = getattr(item, "source_pdf_name", "")
    if source_pdf_name:
        text.append(" · ", style="dim")
        text.append(source_pdf_name, style="dim italic")
    if item.detail:
        text.append("  ")
        text.append(item.detail, style="dim")
    return text


def format_history_row(entry: "GradeJournalEntry", *, teacher_mode: bool) -> Text:
    """Render one audit-journal entry as a single ellipsized line.

    Shows who (actor) changed what, when. Anonymity-aware for the student
    label; the actor is always shown (accountability is the point).
    """
    marker, marker_style = _LOG_KIND_MARKERS.get(entry.kind, ("•", "dim"))
    text = Text(no_wrap=True, overflow="ellipsis")
    timestamp = entry.timestamp
    stamp = timestamp[:16].replace("T", " ") if len(timestamp) >= 16 else timestamp
    text.append(stamp, style="dim")
    text.append(f" {marker} ", style=marker_style)
    if entry.actor:
        text.append(entry.actor, style="bold #81A1C1")
    student_label = (
        entry.student_name
        if teacher_mode and entry.student_name
        else entry.student_id
    )
    if student_label:
        text.append(" · ", style="dim")
        text.append(student_label)
    if entry.question_id:
        text.append(" · ", style="dim")
        text.append(format_question_label(entry.question_id))
    if entry.summary:
        text.append(" · ", style="dim")
        text.append(entry.summary, style="dim")
    return text


# Consistency-audit kind → the one-line "what's wrong" summary.
_CONSISTENCY_KIND_LABELS = {
    "points_divergence": "Different scores, same answer",
    "rubric_divergence": "Same score, rubric differs",
}


def _member_label(member: "ConsistencyMember", *, teacher_mode: bool) -> str:
    """A student handle for a consistency member, anonymity-aware.

    Teacher mode shows the name; anonymous mode falls back to the student id
    (then the source PDF), matching the app-wide grading anonymity rules — so
    the audit never leaks names while anonymous.
    """
    if teacher_mode and member.student_name:
        return member.student_name
    return member.student_id or member.source_pdf_name or "unknown"


def format_consistency_headline(flag: "ConsistencyReportItem") -> Text:
    """Render the header line for one inconsistent answer group."""
    severity = getattr(flag.severity, "value", flag.severity)
    kind = getattr(flag.kind, "value", flag.kind)
    marker, marker_style = _REVIEW_SEVERITY_MARKERS.get(severity, ("•", "dim"))
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{marker} ", style=marker_style)
    text.append(format_question_label(flag.question_id), style="bold")
    if flag.assignment_version:
        text.append(f"  (version {flag.assignment_version})", style="dim")
    text.append("  ·  ", style="dim")
    text.append(_CONSISTENCY_KIND_LABELS.get(kind, kind))
    text.append(f"  ·  {flag.member_count} students", style="dim")
    return text


def format_consistency_answer(flag: "ConsistencyReportItem") -> Text:
    """Render the shared-answer line for one inconsistent answer group."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append("answer: ", style="dim")
    if flag.is_blank:
        text.append("(blank)", style="dim italic")
    else:
        text.append("“")
        text.append(" ".join(flag.sample_response_text.split()))
        text.append("”")
    return text


def format_consistency_variant_grade(variant: "ConsistencyGradeVariant") -> Text:
    """Render the grade sub-header for one outcome (the students list below it)."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{variant.points_awarded:g} pts", style="bold")
    labels = ", ".join(variant.rubric_labels) if variant.rubric_labels else "no rubric"
    text.append(f"  [{labels}]", style="dim")
    return text


def format_consistency_member(member: "ConsistencyMember", *, teacher_mode: bool) -> Text:
    """Render one clickable student row under a grade sub-header.

    A navigable member gets an arrow (it opens on the grade screen); a
    non-navigable one (no mapped submission) is dimmed and marked inert.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    if member.is_navigable:
        text.append("→ ", style="dim")
        text.append(_member_label(member, teacher_mode=teacher_mode))
    else:
        text.append("· ", style="dim")
        text.append(_member_label(member, teacher_mode=teacher_mode), style="dim")
        text.append("  (can't open)", style="dim italic")
    return text


# --- Similarity audit -----------------------------------------------------
# Answer-similarity kind → the one-line "what's wrong" summary (one kind today).
_SIMILARITY_KIND_LABELS = {
    "near_duplicate": "Near-identical answers",
}


def format_similarity_headline(item: "SimilarityReportItem") -> Text:
    """Render the header line for one similar-answer cluster."""
    severity = getattr(item.severity, "value", item.severity)
    kind = getattr(item.kind, "value", item.kind)
    marker, marker_style = _REVIEW_SEVERITY_MARKERS.get(severity, ("•", "dim"))
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{marker} ", style=marker_style)
    text.append(format_question_label(item.question_id), style="bold")
    if item.assignment_version:
        text.append(f"  (version {item.assignment_version})", style="dim")
    text.append("  ·  ", style="dim")
    text.append(_SIMILARITY_KIND_LABELS.get(kind, kind))
    text.append(
        f"  ·  {item.member_count} students · up to {item.max_score * 100:.0f}% alike",
        style="dim",
    )
    return text


def format_similarity_pair(pair: "SimilarityReportPair", *, teacher_mode: bool) -> Text:
    """Render one pairwise-match line ("96%  Ann ↔ Bob")."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{pair.score * 100:.0f}%", style="bold")
    text.append("  ")
    text.append(_member_label(pair.a, teacher_mode=teacher_mode))
    text.append(" ↔ ", style="dim")
    text.append(_member_label(pair.b, teacher_mode=teacher_mode))
    return text


def format_similarity_member(
    member: "SimilarityReportMember", *, teacher_mode: bool
) -> Text:
    """Render one clickable student row with a snippet of their answer.

    A navigable member gets an arrow (it opens on the grade screen); a
    non-navigable one (no mapped submission) is dimmed and marked inert.
    """
    snippet = " ".join(member.response_text.split())
    if len(snippet) > 60:
        snippet = snippet[:59] + "…"
    text = Text(no_wrap=True, overflow="ellipsis")
    if member.is_navigable:
        text.append("→ ", style="dim")
        text.append(_member_label(member, teacher_mode=teacher_mode))
    else:
        text.append("· ", style="dim")
        text.append(_member_label(member, teacher_mode=teacher_mode), style="dim")
        text.append("  (can't open)", style="dim italic")
    if snippet:
        text.append(f"  — {snippet}", style="dim italic")
    return text


# --- Objective answer patterns --------------------------------------------
# The other half of the similarity modal: multiple-choice answers, where a text
# diff says nothing and identical *wrong* answers say everything.
_PATTERN_KIND_LABELS = {
    "shared_wrong_answers": "Identical wrong answers",
}


def format_answer_pattern_scope(view: "AnswerPatternReportView") -> Text:
    """Render the one-line context above the objective findings.

    Always shown, and always *before* any verdict, because "nothing found" over
    two analyzable questions and "nothing found" over fifty look identical
    otherwise — and only one of them is reassuring.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    total = view.analyzed_question_count + view.excluded_question_count
    if not view.analyzed:
        text.append("No objective question could be analyzed", style="bold")
        text.append(
            "  ·  needs a rule-graded question with a spread of wrong answers"
            " (True/False can never qualify).",
            style="dim",
        )
        return text
    text.append(
        f"{view.analyzed_question_count} of {total} rule-graded question(s) analyzed",
        style="bold",
    )
    text.append(
        f"  ·  {view.pairs_compared} student pair(s) compared  ·  "
        f"~{view.expected_by_chance:.2f} flag(s) expected from chance at "
        f"z ≥ {view.threshold:g}",
        style="dim",
    )
    return text


def format_answer_pattern_headline(item: "AnswerPatternReportItem", *, teacher_mode: bool) -> Text:
    """Render the header line for one flagged pair (who, and how surprising)."""
    severity = getattr(item.severity, "value", item.severity)
    kind = getattr(item.kind, "value", item.kind)
    marker, marker_style = _REVIEW_SEVERITY_MARKERS.get(severity, ("•", "dim"))
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{marker} ", style=marker_style)
    text.append(_member_label(item.a, teacher_mode=teacher_mode), style="bold")
    text.append(" ↔ ", style="dim")
    text.append(_member_label(item.b, teacher_mode=teacher_mode), style="bold")
    if item.assignment_version:
        text.append(f"  (version {item.assignment_version})", style="dim")
    text.append("  ·  ", style="dim")
    text.append(_PATTERN_KIND_LABELS.get(kind, kind))
    text.append(
        f"  ·  {item.shared_wrong_count} shared "
        f"({item.expected_shared:.1f} expected) · z = {item.z_score:.1f}",
        style="dim",
    )
    return text


def format_answer_pattern_detail(item: "AnswerPatternReportItem") -> Text:
    """Render the sub-line carrying the pair's supporting counts.

    The Harpp-Hogan index rides here rather than in the headline: it is the
    number the original literature is written around and a reader can check by
    hand, but the flag is decided on the z-score, and putting it beside the
    decision would read as if it were the decision.
    """
    index = item.harpp_hogan_index
    index_text = "n/a (never differed)" if index is None else f"{index:.2f}"
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(
        f"both wrong on {item.both_wrong_count} · differed on "
        f"{item.difference_count} · Harpp-Hogan index {index_text}",
        style="dim",
    )
    return text


def format_answer_pattern_shared(shared: "SharedWrongAnswerRow") -> Text:
    """Render one shared wrong answer, with what made it surprising."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{format_question_label(shared.question_id)}: ", style="dim")
    text.append(f"both “{shared.answer}”")
    if shared.expected_answer:
        text.append(f"  (correct: {shared.expected_answer})", style="dim")
    text.append(
        f"  — {shared.match_probability * 100:.0f}% of wrong pairs agree here",
        style="dim italic",
    )
    return text


def format_answer_pattern_member(member: "AnswerPatternMember", *, teacher_mode: bool) -> Text:
    """Render one clickable student row for a flagged pair.

    Mirrors :func:`format_similarity_member` minus the answer snippet: an
    objective flag is a pattern across questions, so there is no single answer
    worth quoting on the row.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    if member.is_navigable:
        text.append("→ ", style="dim")
        text.append(_member_label(member, teacher_mode=teacher_mode))
    else:
        text.append("· ", style="dim")
        text.append(_member_label(member, teacher_mode=teacher_mode), style="dim")
        text.append("  (can't open)", style="dim italic")
    return text


# --- Regrade requests -----------------------------------------------------


def _regrade_student_label(item: "RegradeReportItem", *, teacher_mode: bool) -> str:
    """A student handle for a regrade dispute, anonymity-aware.

    Teacher mode shows the name; anonymous mode falls back to the disputing
    student's id — matching the app-wide grading anonymity rules.
    """
    if teacher_mode and item.student_name:
        return item.student_name
    return item.student_id or item.source_pdf_name or "unknown"


def format_regrade_headline(item: "RegradeReportItem", *, teacher_mode: bool) -> Text:
    """Render the header line for one open regrade dispute.

    Regrade requests are always high severity, so they carry the red marker.
    """
    marker, marker_style = _REVIEW_SEVERITY_MARKERS["high"]
    text = Text(no_wrap=True, overflow="ellipsis")
    if item.is_navigable:
        text.append("→ ", style="dim")
    else:
        text.append("· ", style="dim")
    text.append(f"{marker} ", style=marker_style)
    text.append(_regrade_student_label(item, teacher_mode=teacher_mode), style="bold")
    text.append("  ·  ", style="dim")
    if item.question_id:
        text.append(format_question_label(item.question_id))
    else:
        text.append("whole assignment", style="italic")
    if not item.is_navigable:
        text.append("  (no submission)", style="dim italic")
    return text


def format_regrade_reason(item: "RegradeReportItem") -> Text:
    """Render the student's stated reason for a dispute (the sub-line)."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append("reason: ", style="dim")
    reason = " ".join(item.reason.split())
    if reason:
        text.append("“")
        text.append(reason)
        text.append("”")
    else:
        text.append("(none given)", style="dim italic")
    return text


# --- Transcription confidence ---------------------------------------------
# Confidence bands for the grade-screen chip: green healthy, amber caution, red
# verify. Thresholds mirror the review engine's OCR floor (60) plus a comfortable
# "trust it" line at 80.
_CONFIDENCE_HIGH = 80.0
_CONFIDENCE_LOW = 60.0


def format_confidence_chip(confidence: float | None, *, has_response: bool) -> Text | None:
    """Render the response's transcription-confidence chip, or ``None`` to hide it.

    - a number ≥80 → green "OCR confidence 94%";
    - 60–80 → amber; below 60 → red with a "verify" nudge (this is what the
      needs-review queue flags);
    - ``None`` *with* a response → a dim "AI transcription · unverified" (a VLM
      read has no calibrated confidence — honest, no fake number);
    - ``None`` with no response → hidden (there's nothing to rate).
    """
    if confidence is None:
        if not has_response:
            return None
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append("Transcription: ", style="dim")
        text.append("AI · unverified", style="dim italic")
        return text

    if confidence >= _CONFIDENCE_HIGH:
        style = "#A3BE8C"  # green
        suffix = ""
    elif confidence >= _CONFIDENCE_LOW:
        style = HOTKEY_STYLE  # amber
        suffix = ""
    else:
        style = "bold #BF616A"  # red
        suffix = " · verify"
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append("OCR confidence: ", style="dim")
    text.append(f"{confidence:.0f}%{suffix}", style=style)
    return text


def format_page_context_label(
    entry: "PageContextEntry",
    index: int,
    total: int,
    *,
    redacted: bool = False,
) -> Text:
    """Caption for the whole-page view: which page is shown and why.

    The distinction matters to a grader mid-decision: a *source* page is the
    sheet this answer was cropped from (so the highlight shows the crop's
    boundary and anything outside it is context the crop dropped), while an
    *extra work* page belongs to no question at all — it is the student's
    scratch/overflow sheet, offered from every question because nothing ties it
    to one. ``redacted`` names the black bar over the sheet's identity header so
    anonymous grading reads as deliberate rather than as a broken render. The
    position and paging keys are appended so the view is operable without
    consulting the Hotkeys card.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"Page {entry.page_number}", style="bold")
    if entry.kind == "extra":
        text.append(" · extra work page", style="#D08770")
    else:
        text.append(" · answer region highlighted", style="dim")
    if redacted:
        text.append(" · identity redacted", style="#EBCB8B")
    text.append(f"  ({index + 1}/{total})", style="dim")
    text.append("  w", style=HOTKEY_STYLE)
    text.append("/", style="dim")
    text.append("W", style=HOTKEY_STYLE)
    text.append(" to page", style="dim")
    return text


# --- Item analysis --------------------------------------------------------
# Difficulty runs cool→warm (easy is green, hard is red); the healthy middle is
# the accent blue. Separation runs good→bad (green→red), negative in alarm red.
_DIFFICULTY_STYLE = {
    "very_easy": "#A3BE8C",
    "easy": "#8FBCBB",
    "moderate": "#81A1C1",
    "hard": "#D08770",
    "very_hard": "#BF616A",
}
_DIFFICULTY_LABEL = {
    "very_easy": "very easy",
    "easy": "easy",
    "moderate": "moderate",
    "hard": "hard",
    "very_hard": "very hard",
}
_SEPARATION_STYLE = {
    "excellent": "#A3BE8C",
    "good": "#8FBCBB",
    "fair": "#EBCB8B",
    "weak": "#D08770",
    "negative": "#BF616A",
    "insufficient_data": "#6B7280",
}
_SEPARATION_LABEL = {
    "excellent": "excellent",
    "good": "good",
    "fair": "fair",
    "weak": "weak",
    "negative": "negative",
    "insufficient_data": "n/a",
}


def _band_value(band: object) -> str:
    return str(getattr(band, "value", band) or "")


def _meter(fraction: float, width: int, style: str) -> Text:
    """A solid/▁ track bar, `fraction` (0..1) filled in `style`."""
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    text = Text()
    text.append("█" * filled, style=style)
    text.append("░" * (width - filled), style="#3B424A")
    return text


def _chip(label: str, style: str) -> Text:
    """A small pill-like colored band label."""
    text = Text()
    text.append(f" {label} ", style=f"bold #2E3440 on {style}")
    return text


def format_item_headline(item: "ItemAnalysis") -> Text:
    """Header line for one item card: flag, question, type, response count."""
    text = Text(no_wrap=True, overflow="ellipsis")
    if item.needs_attention:
        text.append("⚠ ", style="bold #BF616A")
    text.append(format_question_label(item.question_id), style="bold")
    if item.assignment_version:
        text.append(f"  (version {item.assignment_version})", style="dim")
    type_label = format_question_type(getattr(item.question_type, "value", item.question_type))
    if type_label:
        text.append(f"  ·  {type_label}", style="dim")
    text.append(f"  ·  {item.responses_count} responses", style="dim")
    return text


def format_item_difficulty(item: "ItemAnalysis", *, bar_width: int = 16) -> Text:
    """Difficulty row: label, meter, percent, band chip."""
    text = Text(no_wrap=True)
    text.append(f"{'Difficulty':<11}", style="dim")
    if item.difficulty is None:
        text.append("—  ", style="dim")
        text.append("no point value", style="dim italic")
        return text
    band = _band_value(item.difficulty_band)
    style = _DIFFICULTY_STYLE.get(band, "#81A1C1")
    text.append_text(_meter(item.difficulty, bar_width, style))
    text.append(f"  {item.difficulty * 100:>3.0f}%  ")
    text.append_text(_chip(_DIFFICULTY_LABEL.get(band, band), style))
    return text


def format_item_separation(item: "ItemAnalysis", *, bar_width: int = 16) -> Text:
    """Separation row: label, magnitude meter (sign-aware), value, band chip."""
    text = Text(no_wrap=True)
    text.append(f"{'Separation':<11}", style="dim")
    band = _band_value(item.separation_band)
    style = _SEPARATION_STYLE.get(band, "#6B7280")
    if item.separation is None:
        text.append("░" * bar_width, style="#3B424A")
        text.append("   —   ")
        text.append_text(_chip(_SEPARATION_LABEL.get(band, band), style))
        text.append("  need ≥8 responses", style="dim italic")
        return text
    # Bar shows magnitude; the value carries the sign.
    text.append_text(_meter(abs(item.separation), bar_width, style))
    text.append(f"  {item.separation:+.2f}  ")
    text.append_text(_chip(_SEPARATION_LABEL.get(band, band), style))
    return text


def format_item_distractor(
    distractor: "DistractorTally",
    *,
    max_count: int,
    bar_width: int = 12,
) -> Text:
    """One multiple-choice option row: choice, count bar, share, key, choosers."""
    text = Text(no_wrap=True, overflow="ellipsis")
    is_key = distractor.is_likely_key
    choice_style = "bold #A3BE8C" if is_key else None
    text.append(f"{distractor.choice[:16]:<16}", style=choice_style)
    fraction = (distractor.count / max_count) if max_count > 0 else 0.0
    bar_style = "#A3BE8C" if is_key else "#81A1C1"
    text.append_text(_meter(fraction, bar_width, bar_style))
    text.append(f"  {distractor.count:>2} ({distractor.share * 100:>3.0f}%)", style="dim")
    if distractor.mean_chooser_total_percent is not None:
        text.append(f"  choosers {distractor.mean_chooser_total_percent:>3.0f}%", style="dim")
    if is_key:
        text.append("  ← key", style="#A3BE8C")
    return text


def format_comment_snippet_row(
    snippet: "CommentSnippet",
    *,
    hotkey: int | None = None,
    uses: int | None = None,
) -> Text:
    """One comment-library row: optional insert-key, the phrase, and use count.

    ``hotkey`` is the 1-9 digit that inserts this snippet (the first nine rows);
    later rows pass ``None`` and are reached by arrow keys or a click. ``uses``
    overrides the count shown — the per-question view passes that question's
    count so the badge matches the ordering; global view uses the total.
    """
    count = snippet.uses if uses is None else uses
    text = Text(no_wrap=True, overflow="ellipsis")
    if hotkey is not None:
        text.append(f"{hotkey} ", style=HOTKEY_STYLE)
    else:
        text.append("  ")
    text.append(snippet.text)
    if count:
        text.append(f"  · {count}×", style="dim")
    return text


def format_hotkey_reference(
    rows: list[tuple[str, str]],
    *,
    key_width: int = 9,
) -> Text:
    """Render a key/description legend with status-bar key styling.

    Rows with an empty key render as continuation lines for the row above.
    """
    text = Text()
    for index, (key, description) in enumerate(rows):
        if index:
            text.append("\n")
        text.append(f"{key:<{key_width}}", style=HOTKEY_STYLE if key else None)
        text.append(description)
    return text


def format_grading_duration(seconds: float) -> str:
    """Humanize a span of seconds ("1h 20m" / "12m 30s" / "45s")."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_grading_rate(rate: float | None, unit: str) -> str:
    return f"{rate:.1f} {unit}" if rate is not None else "—"


def _format_grading_cadence(seconds: float | None) -> str:
    return f"{seconds:.0f}s" if seconds is not None else "—"


def format_grading_session_overview(report: "GradingSessionReport") -> str:
    """Rich-markup summary block for the grading-session analytics modal."""
    if report.total_events == 0:
        return "[dim]No grading activity has been recorded yet.[/dim]"
    lines = [
        f"[b]{report.total_events}[/b] action(s) by [b]{report.distinct_actors}[/b] "
        f"grader(s) across [b]{report.sessions}[/b] session(s).",
        (
            f"Grades [b]{report.grades}[/b]  ·  Comments [b]{report.comments}[/b]  ·  "
            f"Rubric edits [b]{report.rubric_edits}[/b]  ·  ID assigns "
            f"[b]{report.identity_assignments}[/b]"
        ),
        f"Students touched: [b]{report.distinct_students}[/b]",
        f"Active grading time: [b]{format_grading_duration(report.active_seconds)}[/b]",
        (
            "Throughput: "
            f"[b]{_format_grading_rate(report.grades_per_active_hour, 'grades/hr')}[/b]"
            f"  ·  {_format_grading_rate(report.events_per_active_hour, 'actions/hr')}"
        ),
        (
            "Typical time per action: "
            f"[b]{_format_grading_cadence(report.median_seconds_between_events)}[/b]"
            f"   [dim](break threshold "
            f"{format_grading_duration(report.idle_gap_seconds)})[/dim]"
        ),
    ]
    return "\n".join(lines)


def format_curve_label(curve: "Curve") -> str:
    """Short human description of a curve ("bonus +5 pts", "×1.1", "√ curve").

    Delegates to `core.curve.describe_curve`: the CLI preview and the release
    report need the same sentence, so the phrasing lives in core and this stays
    the TUI's name for it.
    """
    from opengrader.core.curve import describe_curve

    return describe_curve(curve)


def _curve_number(value: float | None) -> str:
    return f"{value:g}" if value is not None else "—"


def format_curve_effect(effect: "CurveEffect") -> str:
    """Rich-markup before→after stats block for the curve modal / preview."""
    if effect.count == 0:
        return "[dim]No graded submissions to preview yet.[/dim]"
    poss = f" / {_curve_number(effect.possible)}" if effect.possible is not None else ""
    lines = [
        f"Students: [b]{effect.count}[/b]",
        (
            f"Mean:   [b]{_curve_number(effect.raw_mean)}[/b]{poss}"
            f"  →  [b]{_curve_number(effect.curved_mean)}[/b]{poss}"
        ),
        (
            f"Median: [b]{_curve_number(effect.raw_median)}[/b]{poss}"
            f"  →  [b]{_curve_number(effect.curved_median)}[/b]{poss}"
        ),
        (
            f"Raised [b]{effect.students_raised}[/b]  ·  "
            f"Lowered [b]{effect.students_lowered}[/b]  ·  "
            f"At max after [b]{effect.curved_at_max}[/b]"
        ),
    ]
    return "\n".join(lines)


def format_grading_session_actor(actor: "ActorGradingStats") -> str:
    """Rich-markup two-line block for one grader in the analytics modal."""
    header = (
        f"[b]{actor.actor}[/b]  —  {actor.total_events} action(s), "
        f"{actor.sessions} session(s)"
    )
    detail = (
        f"  [dim]{actor.grades} grade / {actor.comments} comment / "
        f"{actor.rubric_edits} rubric / {actor.identity_assignments} id  ·  "
        f"active {format_grading_duration(actor.active_seconds)}  ·  "
        f"{_format_grading_rate(actor.grades_per_active_hour, 'grades/hr')}  ·  "
        f"~{_format_grading_cadence(actor.median_seconds_between_events)}/action  ·  "
        f"{actor.students_touched} student(s)[/dim]"
    )
    return f"{header}\n{detail}"


# ── inter-grader agreement (leniency, drift, calibration) ───────────────────


def _format_residual(value: float | None) -> str:
    """A leniency/drift figure in points per 100, always signed.

    The sign is the whole message ("+6" = more generous than the rest of the
    team marked the same questions), so it is never dropped, and an unmeasurable
    figure renders as an em dash rather than as 0.0 — which would read as
    "measured, and even".
    """
    return "—" if value is None else f"{value:+.1f}"


def format_grader_agreement_overview(report: "GraderAgreementReport") -> str:
    """Rich-markup summary block for the inter-grader agreement modal."""
    if not report.graders:
        return "[dim]No graded answers have been recorded yet.[/dim]"

    lines = [
        f"[b]{report.total_answers}[/b] graded answer(s) by "
        f"[b]{report.distinct_graders}[/b] grader(s) across "
        f"[b]{len(report.questions)}[/b] question(s).",
    ]
    if report.unweighted_answers:
        # Without this the headline count and the per-grader rows disagree with
        # no explanation, which reads as a bug in the report rather than as a
        # property of the data. An answer to a question worth no points cannot
        # be marked generously or harshly, so it is in no mean.
        lines.append(
            f"[dim]{report.unweighted_answers} excluded — their question "
            "records no point value, so there is no share to be generous "
            "with.[/dim]"
        )
    if report.has_overlap:
        pairs = report.same_work_pairs or report.same_answer_pairs
        basis = (
            "the same students' answers"
            if report.same_work_pairs
            else "the same answers from different students"
        )
        lines.append(f"[dim]Compared on {basis} — {len(pairs)} grader pair(s).[/dim]")
    else:
        # The ordinary state of a cleanly divided cohort, and the reason
        # calibration exists. Saying so beats showing residuals that are near
        # zero purely because each grader is their own only reference.
        lines.append(
            "[dim]No two graders marked comparable work, so they cannot be "
            "compared. Run [b]calibrate pack[/b] to have everyone mark the same "
            "sample.[/dim]"
        )
    return "\n".join(lines)


def format_grader_stats(grader: "GraderStats") -> str:
    """Rich-markup two-line block for one grader in the agreement modal."""
    header = (
        f"[b]{grader.actor}[/b]  —  {grader.answers} answer(s), "
        f"{grader.students} student(s)"
    )
    drift = "—" if grader.drift is None else _format_residual(grader.drift.delta)
    mean = "—" if grader.mean_percent is None else f"{grader.mean_percent:.1f}%"
    detail = (
        f"  [dim]mean {mean}  ·  "
        f"vs others {_format_residual(grader.mean_residual)} pts/100  ·  "
        f"drift {drift}  ·  {grader.questions} question(s)[/dim]"
    )
    return f"{header}\n{detail}"


def format_grader_pair(pair: "PairAgreement") -> str:
    """Rich-markup two-line block for one grader pair's agreement."""
    provisional = "" if pair.sufficient else "  [dim](provisional — small sample)[/dim]"
    header = (
        f"[b]{pair.a} ↔ {pair.b}[/b]  —  {pair.samples} shared, "
        f"{pair.exact_agreement * 100:.0f}% identical{provisional}"
    )
    if pair.more_generous:
        detail = (
            f"  [dim]{pair.more_generous} marks "
            f"{abs(pair.mean_signed_gap):.1f} pts/100 more generously  ·  "
            f"avg gap {pair.mean_absolute_gap:.1f}  ·  "
            f"worst {pair.max_absolute_gap:.0f}[/dim]"
        )
    else:
        detail = "  [dim]no systematic difference between them[/dim]"
    return f"{header}\n{detail}"


# ── learning-outcome mastery ────────────────────────────────────────────────


def format_outcome_headline(outcome: "OutcomeMastery") -> Text:
    """Header line for one outcome card: name, then who cleared the bar.

    The mastery count leads because it is the number an instructor acts on; the
    colour is the same three-way scale the item cards use, so a red row means
    the same thing in both views.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(outcome.outcome, style="bold")
    rate = outcome.mastery_rate
    if rate is None:
        text.append("  ·  not measured yet", style="dim")
        return text
    style = "#BF616A" if rate < 0.5 else ("#EBCB8B" if rate < 0.8 else "#A3BE8C")
    text.append(
        f"  ·  {outcome.students_mastered}/{outcome.students_measured} mastered",
        style=style,
    )
    text.append(f" ({rate:.0%})", style=style)
    return text


def format_outcome_coverage(outcome: "OutcomeMastery") -> Text:
    """Which questions measured the outcome, and how much of the exam they are.

    Coverage sits on its own line directly under the headline on purpose: a
    100% mastery rate on an outcome carried by 2 of 100 points is not evidence
    the outcome was met, and the two numbers have to be read together.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append("Measured by ", style="dim")
    labels = ", ".join(
        format_question_label(question_id).replace("Question ", "Q")
        for question_id in outcome.question_ids
    )
    text.append(labels or "—")
    text.append(f"  ·  {outcome.possible_points:g} pts", style="dim")
    weight = outcome.assignment_weight
    if weight is not None:
        text.append(f"  ·  {weight:.0%} of the assignment", style="dim")
    return text


def format_outcome_mastery(outcome: "OutcomeMastery") -> Text:
    """The class's average share of the outcome's points."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append("Class average  ", style="dim")
    if outcome.mean_mastery is None:
        text.append("—", style="dim")
        return text
    text.append(f"{outcome.mean_mastery:.0%}", style="bold")
    return text


def format_outcome_summary(report: "OutcomeReport") -> str:
    """Summary line for the outcome view — the coverage caveat comes first.

    A reader who stops at the numbers must already know what fraction of the
    assignment they describe, so the untagged share is stated before anything
    that looks like a verdict.
    """
    if not report.outcomes:
        return (
            "No learning outcomes tagged in this answer key. Add **Outcome** "
            "bullets to your QA questions."
        )
    tagged = report.tagged_share
    caveat = ""
    if tagged is not None and tagged < 1.0:
        caveat = (
            f"  {tagged:.0%} of points carry a tag "
            f"({report.untagged_points:g} untagged)."
        )
    return (
        f"{len(report.outcomes)} outcome(s) over {report.class_size} submission(s).  "
        f"Mastery bar {report.mastery_threshold:.0%}.{caveat}"
    )


# --------------------------------------------------------------------------- #
# Grade release (`core/release.py`)
# --------------------------------------------------------------------------- #

# Deliberately a different marker set from the review queue's high/medium/low:
# a readiness verdict answers "would releasing now be wrong?", not "how much
# does this deserve attention?", and reusing the same glyphs would invite the
# reader to map one onto the other.
_READINESS_MARKERS = {
    "blocking": ("■", "bold #BF616A"),
    "warning": ("▲", HOTKEY_STYLE),
    "note": ("·", "dim"),
}

# What a change did to the student's grade. Red for anything that took points
# away, since that is the one direction a released grade must never move
# quietly.
_CHANGE_LABELS = {
    "raised": ("raised", "#A3BE8C"),
    "lowered": ("lowered", "bold #BF616A"),
    "newly_graded": ("graded since", "#81A1C1"),
    "ungraded": ("un-graded since", "bold #BF616A"),
    "added_question": ("new question", "dim"),
    "removed_question": ("question gone", "bold #BF616A"),
}


def format_readiness_summary(report: "ReadinessReport") -> Text:
    """The one-line headline: how much is graded, and the verdict."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"{report.student_count} student(s)", style="bold")
    text.append("  ·  ", style="dim")
    fraction = report.graded_fraction
    graded = f"{report.graded_answers}/{report.total_answers} answers graded"
    if fraction is not None:
        graded += f" ({fraction * 100:.0f}%)"
    # Amber rather than red for partial work: it is the ordinary state of an
    # assignment being graded, not an error.
    text.append(graded, style="" if fraction == 1.0 else HOTKEY_STYLE)
    text.append("  ·  ", style="dim")
    if report.ready:
        text.append("ready to release", style="bold #A3BE8C")
    else:
        text.append(
            f"{len(report.blocking)} blocking issue(s)", style="bold #BF616A"
        )
    return text


def format_readiness_issue(issue: "ReadinessIssue") -> Text:
    """One readiness issue: marker, summary, then its detail and examples."""
    marker, marker_style = _READINESS_MARKERS.get(issue.severity, ("•", "dim"))
    text = Text()
    text.append(f"{marker} ", style=marker_style)
    text.append(issue.summary)
    if issue.detail:
        text.append(f"\n   {issue.detail}", style="dim")
    if issue.examples:
        shown = ", ".join(issue.examples)
        remaining = issue.example_total - len(issue.examples)
        tail = f" and {remaining} more" if remaining > 0 else ""
        text.append(f"\n   e.g. {shown}{tail}", style="dim")
    return text


def format_release_row(release: "GradeRelease") -> Text:
    """One recorded release, for the history list."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(release.release_id, style="bold")
    text.append("  ")
    # Same "YYYY-MM-DD HH:MM" trim the audit-journal rows use on their ISO
    # timestamps — a release and the journal entries around it should read as
    # one timeline.
    stamp = release.created_at
    text.append(
        stamp[:16].replace("T", " ") if len(stamp) >= 16 else stamp, style="dim"
    )
    text.append(f"  {release.student_count} student(s)")
    if release.label:
        text.append(f"  {release.label}", style="italic")
    return text


def _release_student_label(
    student_id: str, student_name: str, *, teacher_mode: bool
) -> str:
    """Name in teacher mode, id otherwise — the ConsistencyScreen rule.

    A release comparison is about students, so it follows the same anonymity
    convention as the other cohort views rather than being teacher-gated: a TA
    can see that s3's grade moved without a name being revealed.
    """
    if teacher_mode and student_name.strip():
        return student_name.strip()
    return student_id.strip() or "(unidentified)"


def _points(value: float | None) -> str:
    return f"{value:g}" if value is not None else "—"


def format_release_change(change: "GradeChange", *, teacher_mode: bool) -> Text:
    """One question whose grade differs from what the release handed out."""
    label, style = _CHANGE_LABELS.get(change.kind, (change.kind, ""))
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(
        _release_student_label(
            change.student_id, change.student_name, teacher_mode=teacher_mode
        ),
        style="bold",
    )
    text.append("  ·  ", style="dim")
    text.append(format_question_label(change.question_id))
    text.append("  ")
    text.append(label, style=style)
    text.append(f"  {_points(change.before)} → {_points(change.after)}", style="dim")
    return text


def format_release_total_change(
    change: "StudentTotalChange", *, teacher_mode: bool
) -> Text:
    """One student whose overall total is no longer what they were shown."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(
        _release_student_label(
            change.student_id, change.student_name, teacher_mode=teacher_mode
        ),
        style="bold",
    )
    text.append("  ")
    text.append(f"{_points(change.before)} → {_points(change.after)}")
    text.append(
        f"  ({change.delta:+g})",
        style="#A3BE8C" if change.delta > 0 else "bold #BF616A",
    )
    text.append(f"  of {_points(change.possible)}", style="dim")
    return text


def format_release_comparison_summary(comparison: "ReleaseComparison") -> Text:
    """Headline for a comparison — including when there is nothing to report."""
    text = Text(no_wrap=True, overflow="ellipsis")
    if comparison.unchanged:
        text.append("No grade has changed since it was handed back.", style="dim")
        return text
    parts: list[str] = []
    if comparison.changes:
        parts.append(f"{len(comparison.changes)} question grade(s) changed")
    if comparison.totals:
        parts.append(f"{len(comparison.totals)} total(s) differ from what was shown")
    text.append(", ".join(parts), style="bold")
    if comparison.curve_changed:
        text.append("  ·  the curve has changed too", style=HOTKEY_STYLE)
    return text


# --------------------------------------------------------------------------- #
# Environment pre-flight (`core/environment.py`)
# --------------------------------------------------------------------------- #

# Deliberately its own marker set, like the readiness one above. These answer
# "can this machine do the job?", which is a different question from "is this
# submission suspect?" — sharing glyphs would invite the reader to map one scale
# onto the other. Green is used here (and nowhere in the audits) because a
# passing environment check really is a clean bill of health, whereas a clean
# audit only means nothing was flagged.
_ENVIRONMENT_MARKERS = {
    "ready": ("✓", "bold #A3BE8C"),
    "degraded": ("▲", HOTKEY_STYLE),
    "unknown": ("?", "dim"),
    "broken": ("■", "bold #BF616A"),
}

# The one-line verdict per overall status. Phrased as what the user can expect
# to happen, not as a severity word: "degraded" means nothing to an instructor.
_ENVIRONMENT_VERDICTS = {
    "ready": ("Everything Opengrader needs is installed and working.", "bold #A3BE8C"),
    "degraded": (
        "Opengrader will work. Some optional features are unavailable.",
        HOTKEY_STYLE,
    ),
    "unknown": (
        "Opengrader will probably work, but some checks could not run.",
        "dim",
    ),
    "broken": (
        "Opengrader cannot run correctly until the ■ items are fixed.",
        "bold #BF616A",
    ),
}


def format_environment_check(check: "EnvironmentCheck") -> Text:
    """One check: marker, name, what was found, then its impact and fix.

    Impact and fix are only appended when there is something to say — a passing
    check followed by two blank lines is how a diagnostic becomes unreadable.
    """
    marker, marker_style = _ENVIRONMENT_MARKERS.get(check.status, ("•", "dim"))
    text = Text()
    text.append(f"{marker} ", style=marker_style)
    text.append(f"{check.name}: ", style="bold")
    text.append(check.detail)
    if check.impact:
        text.append(f"\n   {check.impact}", style="dim")
    if check.fix:
        text.append("\n   Fix: ", style="dim")
        text.append(check.fix, style=HOTKEY_STYLE)
    return text


def format_environment_verdict(report: "EnvironmentReport") -> Text:
    """The overall verdict line, coloured by the worst finding in the report."""
    message, style = _ENVIRONMENT_VERDICTS.get(
        report.status, ("Environment checked.", "dim")
    )
    return Text(message, style=style)
