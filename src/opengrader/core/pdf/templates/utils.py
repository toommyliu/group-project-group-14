from typing import Any

# A raw template question, in either shorthand form. Lives here (not in
# ``generate.py``) so it stays importable without pulling in fitz: it's a bare
# type alias with no dependencies, but ``generate.py`` imports fitz at module
# scope, and ``qa_file/markdown.py`` needs this name purely for annotations.
QuestionInput = tuple[str, int, str] | dict[str, Any]


def default_subquestion_label(index: int) -> str:
    if 0 <= index < 26:
        return chr(ord("a") + index)
    return str(index + 1)
