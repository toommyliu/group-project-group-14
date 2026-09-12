from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

_COMMENTS_DIRNAME = ".opengrader"
_COMMENTS_FILENAME = "comments.json"


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _clean_use_counts(raw: Any) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    cleaned: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            continue
        cleaned[key] = value
    return cleaned


@dataclass(frozen=True)
class CommentSnippet:
    text: str
    uses: int = 0
    uses_by_question: Mapping[str, int] = field(default_factory=dict)

    def uses_on(self, question_id: str) -> int:
        return self.uses_by_question.get(question_id, 0)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"text": self.text, "uses": self.uses}
        if self.uses_by_question:
            data["uses_by_question"] = dict(sorted(self.uses_by_question.items()))
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommentSnippet | None":
        raw_text = data.get("text")
        if not isinstance(raw_text, str):
            return None
        text = _normalize_text(raw_text)
        if not text:
            return None
        raw_uses = data.get("uses", 0)
        uses = raw_uses if isinstance(raw_uses, int) and not isinstance(raw_uses, bool) and raw_uses >= 0 else 0
        return cls(
            text=text,
            uses=uses,
            uses_by_question=_clean_use_counts(data.get("uses_by_question")),
        )


@dataclass(frozen=True)
class CommentLibrary:
    snippets: tuple[CommentSnippet, ...] = ()

    def ordered(self, question_id: str | None = None) -> tuple[CommentSnippet, ...]:
        qid = (question_id or "").strip()
        if qid:
            def sort_key(pair: tuple[int, CommentSnippet]) -> tuple[int, int, int]:
                index, snippet = pair
                return (-snippet.uses_on(qid), -snippet.uses, index)
        else:
            def sort_key(pair: tuple[int, CommentSnippet]) -> tuple[int, int, int]:
                index, snippet = pair
                return (0, -snippet.uses, index)

        ranked = sorted(enumerate(self.snippets), key=sort_key)
        return tuple(snippet for _, snippet in ranked)

    def _index_of(self, text: str) -> int | None:
        target = _normalize_text(text)
        for index, snippet in enumerate(self.snippets):
            if snippet.text == target:
                return index
        return None

    def with_snippet(self, text: str) -> "CommentLibrary":
        normalized = _normalize_text(text)
        if not normalized or self._index_of(normalized) is not None:
            return self
        return CommentLibrary(self.snippets + (CommentSnippet(text=normalized),))

    def without(self, text: str) -> "CommentLibrary":
        index = self._index_of(text)
        if index is None:
            return self
        remaining = self.snippets[:index] + self.snippets[index + 1 :]
        return CommentLibrary(remaining)

    def record_use(
        self, text: str, question_id: str | None = None
    ) -> "CommentLibrary":
        index = self._index_of(text)
        if index is None:
            return self
        snippet = self.snippets[index]
        by_question = dict(snippet.uses_by_question)
        qid = (question_id or "").strip()
        if qid:
            by_question[qid] = by_question.get(qid, 0) + 1
        bumped = replace(
            snippet, uses=snippet.uses + 1, uses_by_question=by_question
        )
        updated = self.snippets[:index] + (bumped,) + self.snippets[index + 1 :]
        return CommentLibrary(updated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "snippets": [snippet.to_dict() for snippet in self.snippets],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommentLibrary":
        raw_snippets = data.get("snippets")
        if not isinstance(raw_snippets, list):
            return cls()
        snippets: list[CommentSnippet] = []
        seen: set[str] = set()
        for entry in raw_snippets:
            if not isinstance(entry, dict):
                continue
            snippet = CommentSnippet.from_dict(entry)
            if snippet is None or snippet.text in seen:
                continue
            seen.add(snippet.text)
            snippets.append(snippet)
        return cls(tuple(snippets))


def resolve_comments_path(submissions_path: str | Path) -> Path:
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _COMMENTS_DIRNAME / _COMMENTS_FILENAME


def read_comments(submissions_path: str | Path) -> CommentLibrary:
    path = resolve_comments_path(submissions_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return CommentLibrary()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return CommentLibrary()
    if not isinstance(data, dict):
        return CommentLibrary()
    return CommentLibrary.from_dict(data)


def write_comments(
    submissions_path: str | Path, library: CommentLibrary
) -> Path:
    path = resolve_comments_path(submissions_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(library.to_dict(), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
    return path
