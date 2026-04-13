from enum import Enum

ANCHOR_SIZE = 9
ANCHOR_PADDING = 24
CONTENT_PADDING = ANCHOR_PADDING + 50  # instructions, questions, sequential page header

BOX_LINE = 0.75

# 8.5x11 inch page
LETTER_W, LETTER_H = 612, 792  # points

MIN_QUESTION_BOX_HEIGHT = 120
QUESTION_GAP = 8

QUESTION_HEADER_HEIGHT = 30
QUESTION_INNER_PADDING = 10
SUBQUESTION_GAP = 6
SUBQUESTION_MIN_HEIGHT = 68


class QuestionType(str, Enum):
    FREE_RESPONSE = "free_response"
