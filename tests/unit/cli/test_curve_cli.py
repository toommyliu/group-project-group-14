from pathlib import Path

import pytest

from opengrader.cli.curve import run_cli
from opengrader.core.curve import Curve, read_curve
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)


def _question(qid: str, possible: float, awarded: float | None) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=qid,
        prompt="",
        possible_points=possible,
        response_image_path=None,
        response_text="",
        points_awarded=awarded,
    )


def _write(path: Path, sid: str, awarded: float) -> None:
    doc = StudentSubmissionDocument(
        student_name="X",
        student_id=sid,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf=f"{path.stem}.pdf",
        questions=[_question("q1", 100.0, awarded)],
    )
    path.write_text(render_student_submission_markdown(doc), encoding="utf-8")


def _seed(subs: Path) -> None:
    _write(subs / "a.submission.md", "s1", 36.0)
    _write(subs / "b.submission.md", "s2", 49.0)


def test_curve_set_persists_and_previews(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed(subs)

    run_cli(["set", "--submissions", str(subs), "--method", "sqrt"])

    assert read_curve(subs) == Curve("sqrt", 0.0, cap_at_max=True)
    out = capsys.readouterr().out
    assert "square-root curve" in out
    # sqrt raises both 36→60 and 49→70; mean 42.5 → 65
    assert "65" in out


def test_curve_set_bonus_requires_value(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed(subs)

    with pytest.raises(SystemExit) as exc:
        run_cli(["set", "--submissions", str(subs), "--method", "bonus"])
    assert exc.value.code == 1
    assert "requires --value" in capsys.readouterr().err


def test_curve_suggest_previews_without_applying(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed(subs)  # raw scores 36 and 49 out of 100 → mean 42.5%

    run_cli(
        [
            "suggest", "--submissions", str(subs),
            "--method", "bonus", "--target-mean", "60",
        ]
    )

    out = capsys.readouterr().out
    assert "Suggested curve: bonus: +17.5 points" in out  # +17.5 lifts 42.5→60
    assert "preview only" in out
    # Preview only: nothing written.
    assert read_curve(subs) == Curve()


def test_curve_suggest_apply_persists(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed(subs)

    run_cli(
        [
            "suggest", "--submissions", str(subs),
            "--method", "scale", "--target-mean", "85", "--apply",
        ]
    )

    curve = read_curve(subs)
    assert curve.method == "scale"
    assert curve.value == pytest.approx(2.0, abs=1e-2)  # 42.5% × 2 = 85%


def test_curve_suggest_requires_a_target(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed(subs)

    with pytest.raises(SystemExit) as exc:
        run_cli(["suggest", "--submissions", str(subs), "--method", "bonus"])
    assert exc.value.code == 1
    assert "target" in capsys.readouterr().err


def test_curve_suggest_unreachable_target_errors(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write(subs / "z.submission.md", "s0", 0.0)  # a zero-scorer
    _write(subs / "a.submission.md", "s1", 50.0)

    with pytest.raises(SystemExit) as exc:
        run_cli(
            [
                "suggest", "--submissions", str(subs),
                "--method", "scale", "--target-mean", "100",
            ]
        )
    assert exc.value.code == 1
    assert "Could not find" in capsys.readouterr().err


def test_curve_show_and_clear(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed(subs)

    run_cli(["set", "--submissions", str(subs), "--method", "bonus", "--value", "10"])
    capsys.readouterr()

    run_cli(["show", "--submissions", str(subs)])
    assert "bonus: +10 points" in capsys.readouterr().out

    run_cli(["clear", "--submissions", str(subs)])
    assert "restored" in capsys.readouterr().out
    assert read_curve(subs) == Curve()
