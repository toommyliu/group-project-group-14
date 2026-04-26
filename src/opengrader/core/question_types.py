from __future__ import annotations

from typing import Literal

FREE_RESPONSE = "free_response"
MULTIPLE_CHOICE = "multiple_choice"

QuestionType = Literal[FREE_RESPONSE, MULTIPLE_CHOICE]
