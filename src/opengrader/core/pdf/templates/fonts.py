from dataclasses import dataclass
from pathlib import Path

import fitz

DEFAULT_FONT = "times-roman"
DEFAULT_FONT_BOLD = "times-bold"


def _default_cmu_assets_dir() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "assets" / "fonts" / "cmu"
        if candidate.exists():
            return candidate
    return current.parents[5] / "assets" / "fonts" / "cmu"


CMU_ASSETS_DIR = _default_cmu_assets_dir()
CMU_REGULAR_FILE = CMU_ASSETS_DIR / "cmunrm.ttf"
CMU_BOLD_FILE = CMU_ASSETS_DIR / "cmunbx.ttf"


@dataclass
class ResolvedTemplateFonts:
    regular_name: str
    bold_name: str
    regular_file: str | None
    bold_file: str | None
    regular_metrics: fitz.Font
    bold_metrics: fitz.Font
    warning: str | None = None


def resolve_template_fonts(
    regular_file: str | Path | None = None,
    bold_file: str | Path | None = None,
) -> ResolvedTemplateFonts:
    reg_candidate = Path(regular_file) if regular_file else CMU_REGULAR_FILE
    bold_candidate = Path(bold_file) if bold_file else CMU_BOLD_FILE

    reg_exists = reg_candidate.exists()
    bold_exists = bold_candidate.exists()

    regular_name = "lg_cmu_serif_regular"
    bold_name = "lg_cmu_serif_bold"

    regular_path = str(reg_candidate) if reg_exists else None
    bold_path = str(bold_candidate) if bold_exists else None

    regular_metrics = (
        fitz.Font(fontfile=regular_path)
        if regular_path is not None
        else fitz.Font(fontname=DEFAULT_FONT)
    )
    bold_metrics = (
        fitz.Font(fontfile=bold_path)
        if bold_path is not None
        else fitz.Font(fontname=DEFAULT_FONT_BOLD)
    )

    resolved_regular_name = regular_name if regular_path is not None else DEFAULT_FONT
    resolved_bold_name = bold_name if bold_path is not None else DEFAULT_FONT_BOLD

    warning = None
    if regular_path is None or bold_path is None:
        missing = []
        if regular_path is None:
            missing.append(str(reg_candidate))
        if bold_path is None:
            missing.append(str(bold_candidate))
        warning = (
            "Warning: CMU font asset(s) missing; falling back to built-in Times font(s). "
            f"Missing: {', '.join(missing)}"
        )

    return ResolvedTemplateFonts(
        regular_name=resolved_regular_name,
        bold_name=resolved_bold_name,
        regular_file=regular_path,
        bold_file=bold_path,
        regular_metrics=regular_metrics,
        bold_metrics=bold_metrics,
        warning=warning,
    )


def register_page_fonts(page: fitz.Page, fonts: ResolvedTemplateFonts) -> None:
    if fonts.regular_file is not None:
        page.insert_font(fontname=fonts.regular_name, fontfile=fonts.regular_file)
    if fonts.bold_file is not None:
        page.insert_font(fontname=fonts.bold_name, fontfile=fonts.bold_file)


def measure_text_width(
    text: str, fontsize: float, *, bold: bool, fonts: ResolvedTemplateFonts
) -> float:
    metric_font = fonts.bold_metrics if bold else fonts.regular_metrics
    return metric_font.text_length(text, fontsize=fontsize)
