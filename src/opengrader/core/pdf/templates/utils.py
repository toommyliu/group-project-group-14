def default_subquestion_label(index: int) -> str:
    if 0 <= index < 26:
        return chr(ord("a") + index)
    return str(index + 1)