from typing import Any

__all__ = [
    "generate_template_pdf",
    "generate_submission_pdf",
    "generate_personalized_batch",
    "CMU_REGULAR_FILE",
    "CMU_BOLD_FILE",
]

_GENERATE_NAMES = {
    "generate_template_pdf",
    "generate_submission_pdf",
    "generate_personalized_batch",
}
_FONTS_NAMES = {
    "CMU_REGULAR_FILE",
    "CMU_BOLD_FILE",
}


def __getattr__(name: str) -> Any:
    if name in _GENERATE_NAMES:
        from . import generate

        return getattr(generate, name)
    if name in _FONTS_NAMES:
        from . import fonts

        return getattr(fonts, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
