from __future__ import annotations

import json

from opengrader.core.pipeline_settings import (
    FolderPipelineSettings,
    MachinePipelineSettings,
    folder_settings_path,
    load_folder_settings,
    load_machine_settings,
    machine_settings_path,
    save_folder_settings,
    save_machine_settings,
)


def test_a_folder_with_nothing_saved_reads_as_none(tmp_path) -> None:
    assert load_folder_settings(tmp_path) is None


def test_folder_settings_round_trip(tmp_path) -> None:
    settings = FolderPipelineSettings(
        scan=str(tmp_path / "elsewhere.pdf"),
        qa_file="/keys/qa.md",
        pages_per_submission=6,
        use_roster=True,
        roster=str(tmp_path / "roster.tsv"),
        use_region_map=True,
        region_map=str(tmp_path / "midterm.regions.json"),
        use_section_rosters=True,
        section_rosters=("/rosters/mw.tsv=MW", "/rosters/tth.tsv"),
        reorder_pages=True,
        draft_rubrics=True,
    )
    save_folder_settings(tmp_path, settings)

    assert load_folder_settings(tmp_path) == settings


def test_paths_inside_the_folder_are_stored_relative_so_it_can_move(tmp_path) -> None:
    old = tmp_path / "quiz-1"
    save_folder_settings(
        old,
        FolderPipelineSettings(roster=str(old / "roster.tsv"), qa_file="/keys/qa.md"),
    )
    stored = json.loads(folder_settings_path(old).read_text(encoding="utf-8"))
    assert stored["roster"] == "roster.tsv"
    # One outside the folder has nothing to be relative to.
    assert stored["qa_file"] == "/keys/qa.md"

    moved = tmp_path / "renamed"
    old.rename(moved)
    assert load_folder_settings(moved).roster == str(moved / "roster.tsv")


def test_one_shot_overrides_are_not_settings() -> None:
    # A remembered "ignore scan warnings" would silently grade the next bad scan.
    fields = set(FolderPipelineSettings.__dataclass_fields__) | set(
        MachinePipelineSettings.__dataclass_fields__
    )
    assert not fields & {"force", "fresh"}


def test_a_malformed_file_reads_as_nothing_saved(tmp_path) -> None:
    path = folder_settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_folder_settings(tmp_path) is None

    path.write_text('["a list"]', encoding="utf-8")
    assert load_folder_settings(tmp_path) is None


def test_wrongly_typed_values_fall_back_to_the_defaults(tmp_path) -> None:
    path = folder_settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "pages_per_submission": True,
                "use_roster": "yes",
                "roster": 7,
                "section_rosters": ["ok.tsv", 3, "  "],
            }
        ),
        encoding="utf-8",
    )
    assert load_folder_settings(tmp_path) == FolderPipelineSettings(
        section_rosters=("ok.tsv",)
    )


def test_machine_settings_default_when_nothing_is_saved() -> None:
    assert load_machine_settings() == MachinePipelineSettings(
        grade_as_ready=True, jobs=None, last_class_folder=""
    )


def test_machine_settings_round_trip_beside_the_ai_settings() -> None:
    from opengrader.core import app_config

    settings = MachinePipelineSettings(
        grade_as_ready=False, jobs=4, last_class_folder="/classes/quiz-1"
    )
    path = save_machine_settings(settings)

    assert path == machine_settings_path()
    assert path.parent == app_config.settings_path().parent
    assert load_machine_settings() == settings
