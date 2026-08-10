"""LLM-assisted grading logic (provider-agnostic).

Only the model-facing pieces are re-exported here. ``solutions`` (the answer-key
model and the rule-based grading decisions) and ``evaluation`` (the ``llm-eval``
harness) are imported from their submodules instead, deliberately: both sit
alongside ``core.submissions``, which imports *this* package for the AI analysis
tag, and re-exporting them would turn that into an import cycle.
"""

from .llm_grader import (
    AiAnalysisTag,
    GradeSuggestion,
    RubricChoice,
    ai_analysis_confidence,
    ai_analysis_is_confirmed,
    ai_analysis_is_overridden,
    ai_analysis_is_reviewed,
    ai_analysis_model,
    build_grading_prompt,
    confirm_ai_analysis,
    format_ai_analysis,
    override_ai_analysis,
    parse_ai_analysis,
    suggest_free_response_grade,
)

__all__ = [
    "AiAnalysisTag",
    "GradeSuggestion",
    "RubricChoice",
    "ai_analysis_confidence",
    "ai_analysis_is_confirmed",
    "ai_analysis_is_overridden",
    "ai_analysis_is_reviewed",
    "ai_analysis_model",
    "build_grading_prompt",
    "confirm_ai_analysis",
    "format_ai_analysis",
    "override_ai_analysis",
    "parse_ai_analysis",
    "suggest_free_response_grade",
]
