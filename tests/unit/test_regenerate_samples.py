import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "samples" / "regenerate.sh"


@pytest.fixture
def sample_run(tmp_path: Path):
    root = tmp_path / "repo with spaces"
    samples = root / "samples"
    samples.mkdir(parents=True)
    script = samples / SCRIPT.name
    shutil.copy2(SCRIPT, script)
    assignment = samples / "quiz"
    assignment.mkdir()
    for name in ("submissions.pdf", "template.pdf", "qa.md"):
        (assignment / name).write_text("source")
    for name in ("split-submissions", "student-submissions", "split", "submissions", ".opengrader"):
        artifact = assignment / name
        artifact.mkdir()
        (artifact / ".stale").write_text("old artifact")
    labels = samples / "labels"
    labels.mkdir()
    (labels / "extraction-ground-truth.json").write_text("ground truth")
    template_only = samples / "template-only"
    template_only.mkdir()
    (template_only / "template.pdf").write_text("blank template")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[2:]\n"
        "with open(os.environ['CALL_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(args) + '\\n')\n"
        "if args[0] == os.environ.get('FAIL_STAGE'):\n"
        "    sys.exit(7)\n"
        "if '--output' in args:\n"
        "    output = pathlib.Path(args[args.index('--output') + 1])\n"
        "    output.mkdir(parents=True)\n"
        "    (output / 'fresh').write_text(args[0])\n"
    )
    uv.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CALL_LOG": str(tmp_path / "calls.jsonl"),
    }
    return script, assignment, env


def test_regenerates_artifacts_and_preserves_sources(sample_run, tmp_path: Path):
    script, assignment, env = sample_run
    result = subprocess.run(["sh", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not list(assignment.rglob(".stale"))
    for name in ("submissions.pdf", "template.pdf", "qa.md"):
        assert (assignment / name).read_text() == "source"
    assert (assignment.parent / "labels/extraction-ground-truth.json").read_text() == "ground truth"
    assert (assignment.parent / "template-only/template.pdf").read_text() == "blank template"
    assert (assignment / "split-submissions/fresh").read_text() == "submission-splitter"
    assert (assignment / "student-submissions/fresh").read_text() == "submission-extractor"
    calls = [json.loads(line) for line in Path(env["CALL_LOG"]).read_text().splitlines()]
    assert [call[0] for call in calls] == ["submission-splitter", "submission-extractor", "automated-grading"]
    assert "--llm-responses" in calls[1]
    assert calls[1][calls[1].index("--blank-template") + 1] == str(assignment / "template.pdf")
    assert calls[2][calls[2].index("--input") + 1] == str(assignment / "student-submissions")


def test_failure_stops_before_extraction_and_grading(sample_run, tmp_path: Path):
    script, assignment, env = sample_run
    result = subprocess.run(
        ["sh", str(script)], cwd=tmp_path,
        env={**env, "FAIL_STAGE": "submission-splitter"}, capture_output=True,
    )
    assert result.returncode == 7
    assert (assignment / "submissions.pdf").read_text() == "source"
    assert not (assignment / "student-submissions").exists()
    calls = Path(env["CALL_LOG"]).read_text().splitlines()
    assert [json.loads(line)[0] for line in calls] == ["submission-splitter"]


def test_help_does_not_clear_artifacts(sample_run, tmp_path: Path):
    script, assignment, env = sample_run
    result = subprocess.run(["sh", str(script), "--help"], cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode == 0
    assert b"Usage: samples/regenerate.sh" in result.stdout
    assert (assignment / "student-submissions/.stale").read_text() == "old artifact"
    assert not Path(env["CALL_LOG"]).exists()
