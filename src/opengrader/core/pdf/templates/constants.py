from enum import Enum
from typing import TypeAlias

PAGE_EDGE_MARGIN = 24
# Horizontal bounds for instructions, questions, and continuation headers.
CONTENT_SIDE_MARGIN = PAGE_EDGE_MARGIN + 50
# Printable-area inset used for footer page numbers and continuation headers.
PRINT_SAFE_MARGIN = 36

BOX_LINE = 0.75

# 8.5x11 inch page
LETTER_W, LETTER_H = 612, 792  # points

MIN_QUESTION_BOX_HEIGHT = 120
QUESTION_GAP = 8

QUESTION_HEADER_HEIGHT = 30
QUESTION_INNER_PADDING = 10
SUBQUESTION_GAP = 6
SUBQUESTION_MIN_HEIGHT = 68

ColorType: TypeAlias = tuple[float, float, float]
COLOR_BLACK: ColorType = (0.0, 0.0, 0.0)
COLOR_QUESTION_TEXT: ColorType = (0.2, 0.2, 0.2)
COLOR_SUBQUESTION_TEXT: ColorType = (0.25, 0.25, 0.25)
COLOR_FIELD_LABEL: ColorType = (0.15, 0.15, 0.15)
