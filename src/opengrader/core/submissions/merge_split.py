"""Join two submissions the splitter cut apart, after they were read.

The split review (`core/pages/split_review.py`) catches a bad cut before
extraction. This is for one found later, often while grading: a six-page
paper split 5 + 1, its page-6 answers sitting in a one-page "student" of
their own. Joining them means, in this order:

1. the grades both pieces hold, and every reading a person corrected, are
   noted on disk (`merge-grades.json`), so an interruption from here on
   loses nothing — a second attempt finds them;
2. the two split PDFs become one, and the second is removed;
3. that PDF is read again with the command the pipeline used (`--resume`
   reads only what changed) — the extraction is never pieced together from
   the two old ones, because a template-aligned reading of the second piece
   took its page for page 1;
4. the corrected readings are written back, and the second piece's
   extracted folder is removed;
5. the caller writes the grades back (`grades_to_carry`) and calls
   `finish_merge`.

A machine reading is never carried: the joined paper is read again with
all its pages, which is at least as good as either piece's reading.

Only a grade given while looking at the answer is carried: one on a question
whose answer was in that piece. A question marked wrong because its page was
in the other piece starts again. Where both pieces graded a question, the
first piece's grade wins.

Grades are read and written by the caller — the files in a class folder
(`merge_in_folder`, here), the service in a workspace
(`server/workspace/merge.py`) — so this module never needs to know which.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from opengrader.core.submissions.manifest import read_manifest, resolve_manifest_path

EXTRACT_COMMAND_FILENAME = "extract-command.json"
MERGE_GRADES_FILENAME = "merge-grades.json"
# Consent to send to the cloud is given per run, never replayed later.
_ONE_SHOT_FLAGS = frozenset({"--yes-send-to-cloud"})


class MergeUnavailable(RuntimeError):
    """The two submissions cannot be joined from here; the message says why
    and what to do instead."""


# ------------------------------------------------------- the reading command


def _state_dir(submissions_dir: Path) -> Path:
    return resolve_manifest_path(submissions_dir).parent


def record_extract_command(
    submissions_dir: str | Path, input_dir: str | Path, argv: Sequence[str]
) -> None:
    """Remember how the PDFs in `input_dir` were read into `submissions_dir`,
    so one of them can be read again the same way after it is mended."""
    path = _state_dir(Path(submissions_dir)) / EXTRACT_COMMAND_FILENAME
    commands = _read_json(path)
    commands[str(Path(input_dir).resolve())] = [arg for arg in argv if arg not in _ONE_SHOT_FLAGS]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(commands, indent=2) + "\n")


def extract_command(submissions_dir: str | Path, input_dir: str | Path) -> list[str] | None:
    command = _read_json(_state_dir(Path(submissions_dir)) / EXTRACT_COMMAND_FILENAME).get(
        str(Path(input_dir).resolve())
    )
    if isinstance(command, list) and all(isinstance(arg, str) for arg in command):
        return command
    return None


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# ------------------------------------------------------------------ pieces


@dataclass(frozen=True)
class Piece:
    """One extracted submission and the split PDF it was read from."""

    stem: str
    split_pdf: Path
    submission: Path

    @property
    def joined(self) -> bool:
        """Its PDF is gone, already joined onto its neighbour by a join that
        did not finish: joining again finishes it."""
        return not self.split_pdf.exists()


def pieces(submissions_dir: str | Path) -> list[Piece]:
    """Every extracted submission read from a split PDF, in scan order."""
    directory = Path(submissions_dir)
    found: list[Piece] = []
    for key, record in read_manifest(directory).items():
        split_pdf = Path(record.source)
        markdown = next((item for item in record.outputs if item.endswith(".submission.md")), "")
        submission = directory / markdown if markdown else directory / key / f"{key}.submission.md"
        if submission.is_file() and split_pdf.parent.is_dir():
            found.append(Piece(key, split_pdf, submission))
    return sorted(found, key=lambda piece: (str(piece.split_pdf.parent), piece.split_pdf.name))


def recorded_piece(submissions_dir: str | Path, relative: str) -> Piece | None:
    """The piece a submission file was read into, from the manifest alone:
    still found after its files are gone, as a workspace may hold it then."""
    directory = Path(submissions_dir)
    for key, record in read_manifest(directory).items():
        if relative in record.outputs:
            return Piece(key, Path(record.source), directory / relative)
    return None


def piece_for(submissions_dir: str | Path, submission: str | Path) -> Piece | None:
    target = Path(submission).resolve()
    return next((p for p in pieces(submissions_dir) if p.submission.resolve() == target), None)


def neighbours(submissions_dir: str | Path, stem: str) -> tuple[Piece | None, Piece | None]:
    """The submissions scanned just before and just after `stem`'s, from the
    same scan: the only ones it can be joined with."""
    every = pieces(submissions_dir)
    here = next((piece for piece in every if piece.stem == stem), None)
    if here is None:
        return None, None
    same_scan = [piece for piece in every if piece.split_pdf.parent == here.split_pdf.parent]
    index = same_scan.index(here)
    before = same_scan[index - 1] if index > 0 else None
    after = same_scan[index + 1] if index + 1 < len(same_scan) else None
    return before, after


def page_count(piece: Piece) -> int:
    from pypdf import PdfReader

    return 0 if piece.joined else len(PdfReader(str(piece.split_pdf)).pages)


# ------------------------------------------------------------------ grades


@dataclass(frozen=True)
class Grade:
    question_id: str
    points_awarded: float | None
    rubric_labels: tuple[str, ...]
    analysis: str


@dataclass(frozen=True)
class AnswerSeen:
    """What one piece holds for one question."""

    question_id: str
    # The piece had this answer on one of its pages.
    had_answer: bool
    grade: Grade | None


def answers_in_file(path: str | Path) -> list[AnswerSeen]:
    from opengrader.core.submissions import load_student_submission_markdown

    document = load_student_submission_markdown(Path(path))
    seen: list[AnswerSeen] = []
    for question in document.questions:
        graded = (
            question.points_awarded is not None
            or bool(question.rubric_labels)
            or bool(question.analysis.strip())
        )
        seen.append(
            AnswerSeen(
                question_id=question.question_id,
                had_answer=bool(question.source_segments) or bool(question.response_image_path),
                grade=Grade(
                    question.question_id,
                    question.points_awarded,
                    tuple(question.rubric_labels),
                    question.analysis,
                )
                if graded
                else None,
            )
        )
    return seen


def grades_to_carry(*pieces_answers: Sequence[AnswerSeen]) -> dict[str, Grade]:
    """Each question's grade from the first piece that graded it while
    holding its answer."""
    carried: dict[str, Grade] = {}
    for answers in pieces_answers:
        for answer in answers:
            if answer.grade is not None and answer.had_answer:
                carried.setdefault(answer.question_id, answer.grade)
    return carried


@dataclass(frozen=True)
class Carried:
    """What a join keeps from the two pieces."""

    grades: dict[str, Grade]
    # A reading a person corrected, by question. A machine reading is never
    # carried: the joined paper is read again, now with all its pages.
    readings: dict[str, str]


def readings_to_carry(*submissions: Path) -> dict[str, str]:
    """Each question's corrected reading from the first piece that held its
    answer and had it corrected by a person."""
    from opengrader.core.submissions import SOURCE_HUMAN, load_student_submission_markdown

    carried: dict[str, str] = {}
    for path in submissions:
        if not path.is_file():
            continue
        for question in load_student_submission_markdown(path).questions:
            held = bool(question.source_segments) or bool(question.response_image_path)
            if held and question.response_source == SOURCE_HUMAN:
                carried.setdefault(question.question_id, question.response_text)
    return carried


def save_carried(submissions_dir: str | Path, stem: str, carried: Carried) -> None:
    path = _state_dir(Path(submissions_dir)) / MERGE_GRADES_FILENAME
    pending = _read_json(path)
    pending[stem] = {
        "grades": [asdict(grade) for grade in carried.grades.values()],
        "readings": dict(carried.readings),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending, indent=2) + "\n")


def pending_carried(submissions_dir: str | Path, stem: str) -> Carried | None:
    """What a join of `stem` that has not finished noted, if any."""
    entry = _read_json(_state_dir(Path(submissions_dir)) / MERGE_GRADES_FILENAME).get(stem)
    if not isinstance(entry, dict):
        return None
    grades: dict[str, Grade] = {}
    for item in entry.get("grades", []):
        try:
            grade = Grade(
                question_id=str(item["question_id"]),
                points_awarded=None if item["points_awarded"] is None else float(item["points_awarded"]),
                rubric_labels=tuple(str(label) for label in item["rubric_labels"]),
                analysis=str(item["analysis"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        grades[grade.question_id] = grade
    readings = entry.get("readings", {})
    return Carried(
        grades,
        {str(k): str(v) for k, v in readings.items()} if isinstance(readings, dict) else {},
    )


def finish_merge(submissions_dir: str | Path, stem: str) -> None:
    path = _state_dir(Path(submissions_dir)) / MERGE_GRADES_FILENAME
    pending = _read_json(path)
    if pending.pop(stem, None) is not None:
        path.write_text(json.dumps(pending, indent=2) + "\n")


@dataclass(frozen=True)
class JoinResult:
    path: Path
    grades: int
    readings: int


# ----------------------------------------------------------------- joining


def join_and_reread(
    submissions_dir: str | Path,
    first: Piece,
    second: Piece,
    *,
    readings: Mapping[str, str] | None = None,
    actor: str | None = None,
    status: Callable[[str], None] | None = None,
    prompt_cloud_send: bool = True,
    send_to_cloud_confirmed: bool = False,
) -> tuple[Path, int]:
    """Steps 2–4: join the PDFs, read the result again, write the corrected
    readings back into it, remove the second piece's folder. Returns the
    joined submission's file and how many readings were carried. The caller
    has already noted what to carry (`save_carried`).

    `send_to_cloud_confirmed` is the person's yes for this one reading, when
    the assignment is read by a cloud AI; without it the extractor asks (in a
    terminal) or refuses (`prompt_cloud_send=False`)."""
    from opengrader.cli import submission_extractor
    from opengrader.core.grading.files import FileGradingSession
    from opengrader.core.history import GradeJournal
    from opengrader.core.history.journal import resolve_history_dir
    from opengrader.core.pages.split_review import merge_split_pdfs
    from opengrader.core.pipeline import rerecord_split

    directory = Path(submissions_dir)
    split_dir = first.split_pdf.parent
    command = extract_command(directory, split_dir)
    if command is None:
        raise MergeUnavailable(
            "Opengrader does not know how these scans were read: they were read "
            "before it started recording that, or by hand. Run the pipeline on "
            "this folder once more (it reuses everything already done), then "
            "join them again."
        )
    say = status or (lambda _message: None)
    if first.joined:
        raise MergeUnavailable(f"{first.stem}.pdf is missing from {split_dir}.")
    if "--resume" not in command:
        command = [*command, "--resume"]
    if not second.joined:
        # Before anything changes: every other submission is recorded under
        # settings of its own, so the next ordinary run still reuses it
        # instead of reading the whole class again over its grades. This
        # pass reads nothing, so it never needs a cloud yes.
        say("Checking the other submissions are kept as they are…")
        submission_extractor.run_cli(
            [*command, "--adopt-only"], status_callback=say, prompt_cloud_send=prompt_cloud_send
        )
        say(f"Joining {second.stem}.pdf onto {first.stem}.pdf…")
        merge_split_pdfs(split_dir, [first.stem, second.stem])
        # The pipeline's run ledger, when this folder has one, must not split again.
        rerecord_split(directory.parent, split_dir)
    say(f"Reading {first.stem} again…")
    # Only the joined PDF: the page catalog beside it is found all the same.
    joined = list(command)
    joined[joined.index("--input") + 1] = str(first.split_pdf)
    if send_to_cloud_confirmed:
        joined.append("--yes-send-to-cloud")
    submission_extractor.run_cli(
        joined, status_callback=say, prompt_cloud_send=prompt_cloud_send
    )
    if not first.submission.is_file():
        raise MergeUnavailable(f"Reading {first.stem} again did not write {first.submission}.")
    carried = 0
    if readings:
        from opengrader.core.submissions import load_student_submission_markdown

        present = {q.question_id for q in load_student_submission_markdown(first.submission).questions}
        session = FileGradingSession(
            first.submission,
            submissions_path=directory,
            journal=GradeJournal(resolve_history_dir(directory), actor=actor),
        )
        for question_id, text in readings.items():
            if question_id in present:
                # Saved as a person's reading again: confidence 100, journalled.
                session.save_transcription(question_id=question_id, response_text=text)
                carried += 1
    folder = second.submission.parent.resolve()
    # Only ever the second piece's own folder inside this submissions folder.
    if folder.name == second.stem and folder.parent == directory.resolve() and folder.is_dir():
        shutil.rmtree(folder)
    return first.submission, carried


def merge_in_folder(
    submissions_dir: str | Path,
    first: Piece,
    second: Piece,
    *,
    history_dir: str | Path,
    actor: str | None = None,
    status: Callable[[str], None] | None = None,
    prompt_cloud_send: bool = True,
    send_to_cloud_confirmed: bool = False,
) -> JoinResult:
    """The whole join for a class folder, where the files hold the grades."""
    from opengrader.core.grading.files import FileGradingSession
    from opengrader.core.history import GradeJournal
    from opengrader.core.submissions import load_student_submission_markdown

    directory = Path(submissions_dir)
    carried = pending_carried(directory, first.stem)
    if carried is None:
        carried = Carried(
            grades_to_carry(answers_in_file(first.submission), answers_in_file(second.submission)),
            readings_to_carry(first.submission, second.submission),
        )
        save_carried(directory, first.stem, carried)
    path, readings = join_and_reread(
        directory,
        first,
        second,
        readings=carried.readings,
        actor=actor,
        status=status,
        prompt_cloud_send=prompt_cloud_send,
        send_to_cloud_confirmed=send_to_cloud_confirmed,
    )
    session = FileGradingSession(
        path, submissions_path=directory, journal=GradeJournal(history_dir, actor=actor)
    )
    possible = {
        question.question_id: question.possible_points
        for question in load_student_submission_markdown(path).questions
    }
    grades = 0
    for grade in carried.grades.values():
        if grade.question_id not in possible:
            continue
        session.save_grade(
            question_id=grade.question_id,
            possible_points=possible[grade.question_id] or 0.0,
            points_awarded=grade.points_awarded,
            rubric_labels=grade.rubric_labels,
            analysis=grade.analysis,
        )
        grades += 1
    finish_merge(directory, first.stem)
    return JoinResult(path, grades, readings)
