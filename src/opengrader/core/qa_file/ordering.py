"""Move complete question blocks while retaining their Markdown formatting."""

from typing import TYPE_CHECKING

from .markdown import QADocument, _BulletItem, _OUTCOME_SECTION_RE, _normalize_section_name, _parse_bullet_items, _parse_frontmatter, _split_version_sections

if TYPE_CHECKING:
    from .editing import DocumentEdit, QuestionEdit


def reorder_questions(source: str, baseline: QADocument, edits: list["QuestionEdit"], metadata: "DocumentEdit") -> str:
    # Match surviving baseline identities to the edited source before moving any
    # blocks. Parsing bullets here also works while rubric values are incomplete.
    lines = source.splitlines(keepends=True)
    _, start = _parse_frontmatter(source.splitlines())
    sections, _ = _split_version_sections(source.splitlines()[start:], start_line=start + 1)
    roots = {label or "": _parse_bullet_items(content, start_line=line) for label, content, line in sections}
    removed = {edit.source_line for edit in edits if edit.remove}
    groups: dict[tuple[str, int | None], dict[int, _BulletItem]] = {}
    allowed: dict[tuple[str, int | None], set[int]] = {}
    versions = [(version.key, version.original_label, version.label.strip()) for version in metadata.versions or []]
    if metadata.versions is None:
        versions = [(label, label, label) for label in baseline.versions or [""]]
    for key, original_label, label in versions:
        original = ([] if original_label is None else baseline.version_questions.get(original_label, baseline.questions if original_label == "" else []))
        original_ids = [question.source_line for question in original]
        additions = [edit.source_line for edit in edits if edit.source_line < 0 and (edit.version or "") == key]
        identities = [identity for identity in [*original_ids, *additions] if identity not in removed]
        nodes = roots.get(label, [])
        if len(nodes) != len(identities):
            raise ValueError("Question order no longer matches the draft.")
        groups[key, None] = dict(zip(identities, nodes, strict=True))
        allowed[key, None] = set(original_ids + additions)
        for question in original:
            if question.source_line in removed or not question.subquestions:
                continue
            parent = groups[key, None][question.source_line]
            sections = {"correct", "answer", "answers", "choices", "type", "image", "rubric", "expected", "partial", "wrong", "ungraded"}
            children = [child for child in parent.children if _normalize_section_name(child.text) not in sections and not _OUTCOME_SECTION_RE.match(child.text.strip())]
            identities = [child.source_line for child in question.subquestions if child.source_line not in removed]
            if len(children) != len(identities):
                raise ValueError("Subquestion order no longer matches the draft.")
            groups[key, question.source_line] = dict(zip(identities, children, strict=True))
            allowed[key, question.source_line] = {child.source_line for child in question.subquestions}

    def end_of(node: _BulletItem) -> int:
        end = node.line_no
        while end < len(lines):
            text = lines[end]
            prefix = text[:len(text) - len(text.lstrip(" \t"))]
            if text.strip() and len(prefix.expandtabs(4)) <= node.indent:
                break
            end += 1
        return end

    # Fixed line slots let a parent move include already-reordered children.
    ranges = {node.line_no: (node.line_no - 1, end_of(node)) for group in groups.values() for node in group.values()}
    seen: set[tuple[str, int | None]] = set()
    for order in sorted(metadata.question_orders or [], key=lambda order: order.parent_source_line is None):
        key = (order.version, order.parent_source_line)
        if key in seen:
            raise ValueError("Specify each question order once.")
        seen.add(key)
        group = groups.get(key)
        if group is None:
            if order.parent_source_line in removed or order.version not in {version[0] for version in versions}:
                continue
            raise ValueError("Choose an existing question group to reorder.")
        if len(set(order.source_lines)) != len(order.source_lines) or not set(order.source_lines) <= allowed[key]:
            raise ValueError("Reorder questions only within their version or parent question.")
        identities = [identity for identity in order.source_lines if identity in group]
        identities.extend(identity for identity in group if identity not in identities)
        if identities == list(group):
            continue
        blocks = {}
        for identity, node in group.items():
            first, end = ranges[node.line_no]
            blocks[identity] = "".join(lines[first:end])
        newline = "\r\n" if "\r\n" in source else "\n"
        for node, identity in zip(group.values(), identities, strict=True):
            first, end = ranges[node.line_no]
            block = blocks[identity]
            if block and not block.endswith("\n") and end < len(lines):
                block += newline
            lines[first:end] = [block, *([""] * (end - first - 1))]
    return "".join(lines)
