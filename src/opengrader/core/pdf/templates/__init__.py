# Lazy re-exports (PEP 562): ``.generate`` and ``.fonts`` both import ``fitz``
# (PyMuPDF, ~110ms cold) at module scope. Python always runs a package's
# ``__init__`` before any of its submodules, so an eager ``from .generate
# import ...`` here would drag fitz onto *every* import of a sibling
# submodule too — including ``opengrader.core.pdf.templates.utils``, which the
# qa_file markdown parser imports for the dependency-free
# ``default_subquestion_label``/``QuestionInput`` and has no other reason to
# need PDF generation at all. Resolving names to their real module only on
# first access keeps that path fitz-free.
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
