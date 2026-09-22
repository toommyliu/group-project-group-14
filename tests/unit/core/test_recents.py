from __future__ import annotations

import json
from pathlib import Path

from opengrader.core import recents
from opengrader.core.app_config import settings_path


def test_the_list_lives_beside_ai_json_not_in_an_assignment_folder() -> None:
    assert recents.recents_path() == settings_path().parent / "recent.json"


def test_nothing_recorded_is_an_empty_list() -> None:
    assert recents.load_recents() == []


def test_newest_first_one_entry_per_folder_and_capped(tmp_path: Path) -> None:
    for index in range(recents.MAX_RECENTS + 3):
        recents.record_recent("folder", tmp_path / f"class{index}", title=f"Class {index}")
    recents.record_recent("folder", tmp_path / "class0", title="Class 0 again")

    items = recents.load_recents()

    assert len(items) == recents.MAX_RECENTS
    assert items[0].title == "Class 0 again"
    assert [i.path for i in items].count(str((tmp_path / "class0").resolve())) == 1


def test_one_folder_with_two_answer_keys_is_two_entries(tmp_path: Path) -> None:
    recents.record_recent("folder", tmp_path, qa_file=tmp_path / "a.md")
    recents.record_recent("folder", tmp_path, qa_file=tmp_path / "b.md")

    assert len(recents.load_recents()) == 2


def test_forget_removes_only_that_entry(tmp_path: Path) -> None:
    recents.record_recent("folder", tmp_path / "keep")
    recents.record_recent("workspace", tmp_path / "drop")
    [drop, keep] = recents.load_recents()

    recents.forget_recent(drop)

    assert recents.load_recents() == [keep]


def test_a_malformed_file_costs_the_list_never_the_app() -> None:
    target = recents.recents_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    for body in ("{not json", json.dumps([1, 2]), json.dumps({"recent": [{"kind": "x"}, 5]})):
        target.write_text(body, encoding="utf-8")
        assert recents.load_recents() == []


def test_an_unwritable_store_is_ignored(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")

    recents.record_recent("folder", tmp_path, store=blocker / "recent.json")  # no raise


def test_exists_follows_what_the_entry_needs(tmp_path: Path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("", encoding="utf-8")
    recents.record_recent("folder", tmp_path, qa_file=qa, submissions=tmp_path)
    recents.record_recent("workspace", tmp_path / "ws")
    [workspace, folder] = recents.load_recents()

    assert folder.exists() and not workspace.exists()
    qa.unlink()
    assert not folder.exists()
