"""Apply visual edits to selected QA sections without normalizing the whole file."""

from dataclasses import dataclass

from .markdown import (
    QARubricItem,
    QAChoice,
    QAQuestion,
    _BulletItem,
    _append_bullet,
    _normalize_section_name,
    _parse_bullet_items,
    _parse_frontmatter,
    _render_rubric,
    _render_frontmatter_field,
    _render_choices,
    _render_question,
    _split_version_sections,
    parse_qa_markdown,
)


@dataclass
class QuestionEdit:
    source_line: int
    prompt: str
    points: int
    answers: list[str]
    rubric: list[QARubricItem]
    choices: list[QAChoice] | None = None
    correct_choice_labels: list[str] | None = None
    version: str | None = None
    remove: bool = False


@dataclass
class VersionEdit:
    key: str
    original_label: str | None
    label: str


@dataclass
class QuestionOrder:
    version: str
    source_lines: list[int]
    parent_source_line: int | None = None


@dataclass
class DocumentEdit:
    title: str
    instructions: str
    versions: list[VersionEdit] | None = None
    question_orders: list[QuestionOrder] | None = None


def edit_qa_markdown(source: str, edits: list[QuestionEdit], metadata: DocumentEdit | None = None) -> str:
    """Render changed sections against a valid baseline, allowing incomplete drafts.

    Source lines refer to the baseline, so edits to multiple questions cannot shift
    one another's targets. Validation of the resulting draft belongs to the caller.
    """
    document = parse_qa_markdown(source)
    questions = {
        question.source_line: question
        for version in (document.version_questions.values() or [document.questions])
        for parent in version
        for question in [parent, *parent.subquestions]
    }
    lines = source.splitlines(keepends=True)
    _, start = _parse_frontmatter(source.splitlines())
    sections, _ = _split_version_sections(source.splitlines()[start:], start_line=start + 1)
    nodes: dict[int, _BulletItem] = {}

    def visit(items: list[_BulletItem]) -> None:
        for item in items:
            nodes[item.line_no] = item
            visit(item.children)

    for _, content, line in sections:
        visit(_parse_bullet_items(content, start_line=line))

    newline = "\r\n" if "\r\n" in source else "\n"
    patches: list[tuple[int, int, str]] = []
    seen: set[int] = set()
    additions_by_position: dict[int, list[str]] = {}
    version_edits = metadata.versions if metadata else None
    removed_sections: list[tuple[int, int]] = []
    new_version_content: dict[str, list[str]] = {}

    def end_of(node: _BulletItem) -> int:
        end = node.line_no
        while end < len(lines):
            text = lines[end]
            prefix = text[:len(text) - len(text.lstrip(" \t"))]
            if text.strip() and len(prefix.expandtabs(4)) <= node.indent:
                break
            end += 1
        while end > node.line_no and not lines[end - 1].strip():
            end -= 1
        return end

    def replacement(start: int, end: int, rendered: list[str]) -> None:
        text = newline.join(rendered)
        if text and (end < len(lines) or not lines or lines[end - 1].endswith("\n")):
            text += newline
        patches.append((start, end, text))

    removed = [nodes[edit.source_line] for edit in edits if edit.remove and edit.source_line in questions]

    def replace_section(node: _BulletItem, names: set[str], rendered: list[str]) -> None:
        children = [child for child in node.children if _normalize_section_name(child.text) in names]
        if children:
            for index, child in enumerate(children):
                replacement(child.line_no - 1, end_of(child), rendered if index == 0 else [])
        elif rendered:
            additions_by_position.setdefault(end_of(node), []).extend(rendered)

    if version_edits is not None:
        if not version_edits or len({version.key for version in version_edits}) != len(version_edits):
            raise ValueError("Keep at least one version, with a unique key for each version.")
        labels = [version.label.strip() for version in version_edits]
        if len(set(labels)) != len(labels) or (len(labels) > 1 and not all(labels)) or any("\n" in label or "\r" in label for label in labels):
            raise ValueError("Give each version a different, nonempty name.")
        originals = [version.original_label for version in version_edits if version.original_label is not None]
        available = document.versions or [""]
        if len(set(originals)) != len(originals) or any(label not in available for label in originals):
            raise ValueError("A version must refer to a different existing version or be new.")
        for label, content, line in sections:
            original_label = label or ""
            if original_label not in available:
                continue
            version = next((version for version in version_edits if version.original_label == original_label), None)
            if version is None:
                first = line - 2 if label is not None else line - 1
                end = line - 1 + len(content)
                removed_sections.append((first, end))
                patches.append((first, end, ""))
            elif version.label.strip() != original_label:
                heading = [f"## Version {version.label.strip()}"] if version.label.strip() else []
                if label is None:
                    patches.append((line - 1, line - 1, newline.join([*heading, "", ""])))
                else:
                    replacement(line - 2, line - 1, heading)

    if metadata is not None:
        additions: list[str] = []
        for name in ("title", "instructions"):
            value = getattr(metadata, name)
            if value == (getattr(document, name) or ""):
                continue
            rendered: list[str] = []
            if value:
                _render_frontmatter_field(rendered, name, value)
            existing = next((index for index in range(1, start - 1) if lines[index].startswith(name + ":")), None)
            if existing is None:
                additions.extend(rendered)
            else:
                end = existing + 1
                while end < start - 1 and (not lines[end].strip() or lines[end][0].isspace()):
                    end += 1
                while end > existing + 1 and not lines[end - 1].strip():
                    end -= 1
                replacement(existing, end, rendered)
        if additions:
            if start:
                patches.append((start - 1, start - 1, newline.join(additions) + newline))
            else:
                patches.append((0, 0, newline.join(["---", *additions, "---", "", ""])))

    for edit in edits:
        if edit.source_line in seen:
            raise ValueError("Each edit must identify a different question in the source.")
        seen.add(edit.source_line)
        if edit.source_line < 0:
            if edit.remove:
                continue
            target = next((version for version in version_edits or [] if version.key == (edit.version or "")), None)
            if version_edits is not None and target is None:
                continue
            original_label = (target.original_label or None) if target else edit.version
            rendered: list[str] = []
            _render_question(rendered, QAQuestion(
                id="new", prompt=edit.prompt, points=edit.points, answers=edit.answers,
                rubric=edit.rubric, choices=edit.choices or [],
                correct_choice_labels=tuple(edit.correct_choice_labels or []),
            ))
            if target and target.original_label is None:
                new_version_content.setdefault(target.key, []).extend([*rendered, ""])
            else:
                section = next(((content, line) for label, content, line in sections if label == original_label), None)
                if section is None:
                    raise ValueError("Choose an existing version for the new question.")
                content, line = section
                insertion = line - 1 + len(content)
                additions_by_position.setdefault(insertion, []).extend([*rendered, ""])
            continue
        original = questions.get(edit.source_line)
        if original is None:
            raise ValueError("Each edit must identify a different question in the source.")
        node = nodes[edit.source_line]
        if any(first <= node.line_no - 1 < end for first, end in removed_sections):
            continue
        if any(parent.line_no < node.line_no <= end_of(parent) for parent in removed):
            continue
        if edit.remove:
            replacement(node.line_no - 1, end_of(node), [])
            continue
        if edit.prompt != original.prompt or edit.points != original.points:
            rendered: list[str] = []
            _append_bullet(rendered, indent=node.indent, text=f"({edit.points} points) {edit.prompt}")
            end = node.children[0].line_no - 1 if node.children else end_of(node)
            while end > node.line_no and not lines[end - 1].strip():
                end -= 1
            replacement(node.line_no - 1, end, rendered)

        if edit.rubric != original.rubric:
            rendered = []
            _render_rubric(rendered, edit.rubric, indent=node.indent + 2)
            replace_section(node, {"rubric"}, rendered)
        choices = edit.choices if edit.choices is not None else original.choices
        if choices != original.choices:
            rendered = []
            _render_choices(rendered, choices, indent=node.indent + 2)
            replace_section(node, {"choices"}, rendered)
            replace_section(node, {"type"}, [])
        original_labels = list(original.correct_choice_labels or ((original.correct_choice_label,) if original.correct_choice_label else ()))
        labels = edit.correct_choice_labels if edit.correct_choice_labels is not None else original_labels
        if edit.answers != original.answers or labels != original_labels or bool(choices) != bool(original.choices):
            values = labels if choices else edit.answers
            rendered = []
            if values:
                _append_bullet(rendered, indent=node.indent + 2, text="**Correct**" if choices else "**Answer**")
                for value in values:
                    _append_bullet(rendered, indent=node.indent + 4, text=value)
            replace_section(node, {"correct", "answer", "answers"}, rendered)

    for version in version_edits or []:
        if version.original_label is None:
            rendered = ["", f"## Version {version.label.strip()}", ""] if version.label.strip() else [""]
            rendered.extend(new_version_content.get(version.key, []))
            additions_by_position.setdefault(len(lines), []).extend(rendered)

    for insertion, rendered in additions_by_position.items():
        text = newline.join(rendered) + newline
        if insertion and not lines[insertion - 1].endswith("\n"):
            text = newline + text
        patches.append((insertion, insertion, text))

    for start, end, text in sorted(patches, key=lambda patch: (patch[0], patch[1]), reverse=True):
        lines[start:end] = [text]
    result = "".join(lines)
    if metadata and metadata.question_orders:
        from .ordering import reorder_questions

        result = reorder_questions(result, document, edits, metadata)
    return result
