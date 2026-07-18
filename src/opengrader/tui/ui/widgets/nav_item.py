from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    item_id: str
    label: str
    meta: str = ""
    display_id: str = ""
    description: str = ""
    # Question items only: QuestionType literal ("free_response" /
    # "multiple_choice"); empty for non-question nav items.
    question_type: str = ""
