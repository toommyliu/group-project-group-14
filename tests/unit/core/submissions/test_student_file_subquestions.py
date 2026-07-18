from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from opengrader.core.ocr.extractor import (
    FieldExtractionResult,
    OcrConfig,
    OcrResult,
    QuestionExtractionResult,
    SubmissionResult,
)
from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig, TitleMetadataResult
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE
from opengrader.core.submissions import student_file


def test_parse_assignment_metadata_extracts_compact_suffix_version() -> None:
    metadata = student_file.parse_assignment_metadata_from_title("cmpe 30 quiz 2_5a")

    assert metadata.course_code == "CMPE30"
    assert metadata.assignment_title == "Quiz 2"
    assert metadata.assignment_version == "5A"


def test_parse_assignment_metadata_extracts_colon_compact_suffix_version() -> None:
    metadata = student_file.parse_assignment_metadata_from_title("CMPE 30: Quiz 2_5a")

    assert metadata.course_code == "CMPE30"
    assert metadata.assignment_title == "Quiz 2"
    assert metadata.assignment_version == "5A"


def test_parse_assignment_metadata_preserves_existing_version_forms() -> None:
    canonical = student_file.parse_assignment_metadata_from_title("CS146: Quiz 1 (A)")
    explicit = student_file.parse_assignment_metadata_from_title("Quiz 1 Version B")

    assert canonical.course_code == "CS146"
    assert canonical.assignment_title == "Quiz 1"
    assert canonical.assignment_version == "A"
    assert explicit.assignment_title == "Quiz 1"
    assert explicit.assignment_version == "B"


def test_assignment_metadata_from_title_result_prefers_compact_raw_parse() -> None:
    metadata = student_file._assignment_metadata_from_title_result(
        TitleMetadataResult(
            course_code=None,
            assignment_title="Quiz 2_5a",
            assignment_version=None,
            raw_title="cmpe 30 quiz 2_5a",
        )
    )

    assert metadata.course_code == "CMPE30"
    assert metadata.assignment_title == "Quiz 2"
    assert metadata.assignment_version == "5A"


def test_assignment_metadata_from_title_result_ignores_assignment_number_as_version() -> None:
    metadata = student_file._assignment_metadata_from_title_result(
        TitleMetadataResult(
            course_code="CS146",
            assignment_title="Quiz",
            assignment_version="1",
            raw_title="CS146: Quiz 1",
        )
    )

    assert metadata.course_code == "CS146"
    assert metadata.assignment_title == "Quiz 1"
    assert metadata.assignment_version is None


def _draw_subquestion_boxes(path: Path) -> None:
    image = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    draw.rectangle((70, 70, 1130, 1520), outline=(0, 0, 0), width=5)
    draw.rectangle((95, 250, 1105, 810), outline=(0, 0, 0), width=5)
    draw.rectangle((95, 860, 1105, 1450), outline=(0, 0, 0), width=5)

    image.save(path)


def test_split_question_response_image_splits_nested_subquestion_boxes(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "q2.png"
    _draw_subquestion_boxes(image_path)

    split = student_file._split_question_response_image(
        question_id="q2",
        image_path=image_path,
    )

    assert [question_id for question_id, _ in split] == ["q2a", "q2b"]
    assert all(path.exists() for _, path in split)


def test_split_question_response_image_falls_back_to_ocr_markers(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "q6.png"
    Image.new("RGB", (1000, 2400), color=(255, 255, 255)).save(image_path)

    def _fake_safe_image_to_data(*_args: object, **_kwargs: object) -> dict[str, list]:
        return {
            "text": ["a)", "b)", "c)", "d)", "ignore"],
            "conf": [96.0, 95.0, 94.0, 93.0, 80.0],
            "left": [120, 120, 120, 120, 800],
            "top": [160, 760, 1360, 1960, 1700],
            "width": [30, 30, 30, 30, 100],
            "height": [28, 28, 28, 28, 30],
            "block_num": [1, 1, 1, 1, 1],
            "par_num": [1, 1, 1, 1, 1],
            "line_num": [1, 2, 3, 4, 5],
        }

    original_detect_boxes = student_file._detect_subquestion_boxes_norm
    original_safe_image_to_data = student_file.safe_image_to_data
    try:
        student_file._detect_subquestion_boxes_norm = lambda _image: []
        student_file.safe_image_to_data = _fake_safe_image_to_data

        split = student_file._split_question_response_image(
            question_id="q6",
            image_path=image_path,
        )

        assert [question_id for question_id, _ in split] == ["q6a", "q6b", "q6c", "q6d"]
        assert all(path.exists() for _, path in split)
    finally:
        student_file._detect_subquestion_boxes_norm = original_detect_boxes
        student_file.safe_image_to_data = original_safe_image_to_data


def test_build_student_submission_document_respects_split_subquestions_flag(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    submission_dir = output_dir / "student-0001"
    response_dir = submission_dir / "responses"
    response_dir.mkdir(parents=True, exist_ok=True)

    q1 = response_dir / "q1.png"
    q1a = response_dir / "q1a.png"
    q1b = response_dir / "q1b.png"

    Image.new("RGB", (200, 120), color=(255, 255, 255)).save(q1)
    Image.new("RGB", (200, 90), color=(255, 255, 255)).save(q1a)
    Image.new("RGB", (200, 90), color=(255, 255, 255)).save(q1b)

    def _fake_process_submission_pdf(
        *_args: object, **_kwargs: object
    ) -> SubmissionResult:
        return SubmissionResult(
            filename="student-0001.pdf",
            version_validated=True,
            fields=[
                FieldExtractionResult(
                    field_id="student_name",
                    ocr_result=OcrResult(
                        text="Jane Student", confidence=99.0, bbox_px=(0, 0, 0, 0)
                    ),
                    validated=True,
                ),
                FieldExtractionResult(
                    field_id="student_id",
                    ocr_result=OcrResult(
                        text="123456789", confidence=99.0, bbox_px=(0, 0, 0, 0)
                    ),
                    validated=True,
                ),
            ],
            questions=[
                QuestionExtractionResult(
                    question_id="q1",
                    page_number=1,
                    question_type=MULTIPLE_CHOICE,
                    output_path=str(q1),
                )
            ],
        )

    split_call_count = {"count": 0}

    def _fake_split_question_response_image(
        *,
        question_id: str,
        image_path: Path,
    ) -> list[student_file._SplitQuestionImage]:
        split_call_count["count"] += 1
        assert question_id == "q1"
        assert image_path == q1
        return [
            student_file._SplitQuestionImage("q1a", q1a, (0.0, 0.0, 1.0, 0.5)),
            student_file._SplitQuestionImage("q1b", q1b, (0.0, 0.5, 1.0, 0.5)),
        ]

    def _fake_classify_question_image_path(image_path: Path) -> str:
        if image_path == q1a:
            return MULTIPLE_CHOICE
        return FREE_RESPONSE

    original_process = student_file.process_submission_pdf
    original_split = student_file._split_question_response_image
    original_classify = student_file._classify_question_image_path
    try:
        student_file.process_submission_pdf = _fake_process_submission_pdf
        student_file._split_question_response_image = (
            _fake_split_question_response_image
        )
        student_file._classify_question_image_path = _fake_classify_question_image_path

        split_result = student_file.build_student_submission_document(
            pdf_path=tmp_path / "student-0001.pdf",
            output_dir=output_dir,
            ocr_config=OcrConfig(),
            split_subquestions=True,
            skip_ollama_qa=True,
        )

        assert [q.question_id for q in split_result.document.questions] == [
            "q1a",
            "q1b",
        ]
        assert [q.question_type for q in split_result.document.questions] == [
            MULTIPLE_CHOICE,
            FREE_RESPONSE,
        ]
        assert split_result.document.source_pdf == "../../student-0001.pdf"
        assert split_result.markdown_path.parent.name == "student-0001"
        assert split_result.responses_dir == (
            split_result.markdown_path.parent / "responses"
        )
        assert (
            split_result.document.questions[0].response_image_path
            == "responses/q1a.png"
        )
        assert split_call_count["count"] == 1

        split_call_count["count"] = 0
        no_split_result = student_file.build_student_submission_document(
            pdf_path=tmp_path / "student-0001.pdf",
            output_dir=output_dir,
            ocr_config=OcrConfig(),
            split_subquestions=False,
            skip_ollama_qa=True,
        )

        assert [q.question_id for q in no_split_result.document.questions] == ["q1"]
        assert no_split_result.document.questions[0].question_type == MULTIPLE_CHOICE
        assert split_call_count["count"] == 0
    finally:
        student_file.process_submission_pdf = original_process
        student_file._split_question_response_image = original_split
        student_file._classify_question_image_path = original_classify


def test_build_student_submission_document_uses_ollama_for_fields_and_qa(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    submission_dir = output_dir / "student-0002"
    response_dir = submission_dir / "responses"
    field_dir = submission_dir / "field_crops"
    response_dir.mkdir(parents=True, exist_ok=True)
    field_dir.mkdir(parents=True, exist_ok=True)

    q1 = response_dir / "q1.png"
    name_crop = field_dir / "name.png"
    sid_crop = field_dir / "sid.png"

    Image.new("RGB", (300, 160), color=(255, 255, 255)).save(q1)
    Image.new("RGB", (500, 120), color=(255, 255, 255)).save(name_crop)
    Image.new("RGB", (500, 120), color=(255, 255, 255)).save(sid_crop)

    def _fake_process_submission_pdf(
        *_args: object, **_kwargs: object
    ) -> SubmissionResult:
        return SubmissionResult(
            filename="student-0002.pdf",
            version_validated=True,
            fields=[
                FieldExtractionResult(
                    field_id="student_name",
                    ocr_result=OcrResult(
                        text="fallback name", confidence=55.0, bbox_px=(0, 0, 0, 0)
                    ),
                    validated=False,
                    crop_path=str(name_crop),
                ),
                FieldExtractionResult(
                    field_id="student_id",
                    ocr_result=OcrResult(
                        text="11111", confidence=55.0, bbox_px=(0, 0, 0, 0)
                    ),
                    validated=False,
                    crop_path=str(sid_crop),
                ),
            ],
            questions=[
                QuestionExtractionResult(
                    question_id="q1",
                    page_number=1,
                    output_path=str(q1),
                )
            ],
        )

    def _fake_extract_field_text(
        *,
        image_path: str | Path,
        field_kind: str,
        config: OllamaVisionConfig,
    ) -> str:
        _ = image_path, config
        if field_kind == "name":
            return "Taylor Student"
        return "987654321"

    def _fake_extract_question_prompt_and_answer(
        *,
        image_path: str | Path,
        config: OllamaVisionConfig,
    ) -> tuple[str, str]:
        _ = image_path, config
        return "State Big-O complexity.", "O(n log n)"

    original_process = student_file.process_submission_pdf
    original_extract_field_text = student_file.extract_field_text
    original_extract_qa = student_file.extract_question_prompt_and_answer
    try:
        student_file.process_submission_pdf = _fake_process_submission_pdf
        student_file.extract_field_text = _fake_extract_field_text
        student_file.extract_question_prompt_and_answer = (
            _fake_extract_question_prompt_and_answer
        )

        result = student_file.build_student_submission_document(
            pdf_path=tmp_path / "student-0002.pdf",
            output_dir=output_dir,
            ocr_config=OcrConfig(),
            split_subquestions=False,
            ollama_config=OllamaVisionConfig(model="custom-model:latest"),
        )

        assert result.document.student_name == "Taylor Student"
        assert result.document.student_id == "987654321"
        assert result.document.questions[0].prompt == "State Big-O complexity."
        assert result.document.questions[0].response_text == "O(n log n)"
    finally:
        student_file.process_submission_pdf = original_process
        student_file.extract_field_text = original_extract_field_text
        student_file.extract_question_prompt_and_answer = original_extract_qa


def test_render_student_submission_markdown_includes_question_type() -> None:
    document = student_file.StudentSubmissionDocument(
        student_name="Taylor Student",
        student_id="123456789",
        course_code="CMPE195",
        assignment_title="Quiz",
        assignment_version="A",
        source_pdf="submission.pdf",
        questions=[
            student_file.StudentSubmissionQuestion(
                question_id="q1",
                question_type=MULTIPLE_CHOICE,
                prompt="Which structure is FIFO?",
                possible_points=None,
                response_image_path=None,
                response_text="B",
            )
        ],
    )

    markdown = student_file.render_student_submission_markdown(document)

    assert "- q1:\n  - Question type: Multiple Choice\n  - Prompt:" in markdown


def test_parse_student_submission_markdown_reads_frontmatter_and_questions(
    tmp_path: Path,
) -> None:
    submission_path = (
        Path(
            "samples/cs146-quiz1/student-submissions/submission-0001/submission-0001.submission.md"
        )
    )
    document = student_file.load_student_submission_markdown(submission_path)

    assert document.student_name == "Tommy Liu"
    assert document.student_id == "123456789"
    assert document.course_code == "CS146"
    assert document.assignment_title == "Quiz 1"
    assert document.assignment_version == "A"
    assert document.questions[0].question_id == "q1"
    # The regenerated samples carry no OCR transcription for responses.
    assert document.questions[0].response_text == ""
    assert document.questions[0].response_image_path is not None
    assert Path(document.questions[0].response_image_path).is_absolute()
    assert Path(document.questions[0].response_image_path).exists()


def test_parse_student_submission_markdown_round_trips_rendered_document(
    tmp_path: Path,
) -> None:
    response_dir = tmp_path / "responses"
    response_dir.mkdir()
    response_image = response_dir / "q1.png"
    Image.new("RGB", (10, 10), color=(255, 255, 255)).save(response_image)

    document = student_file.StudentSubmissionDocument(
        student_name="Taylor Student",
        student_id="123",
        course_code="CMPE195",
        assignment_title="Quiz",
        assignment_version="A",
        source_pdf="submission.pdf",
        questions=[
            student_file.StudentSubmissionQuestion(
                question_id="q1",
                question_type=MULTIPLE_CHOICE,
                prompt="Which structure is FIFO?",
                possible_points=2,
                response_image_path="responses/q1.png",
                response_text="B\nbecause queue",
                points_awarded=1.0,
                analysis="Partial",
                source_segments=(
                    student_file.QuestionSourceSegment(
                        page_number=1,
                        bbox_norm=(0.1, 0.2, 0.7, 0.3),
                    ),
                ),
            )
        ],
    )

    parsed = student_file.parse_student_submission_markdown(
        student_file.render_student_submission_markdown(document),
        base_path=tmp_path,
    )

    assert parsed.student_name == "Taylor Student"
    assert parsed.questions[0].question_id == "q1"
    assert parsed.questions[0].question_type == MULTIPLE_CHOICE
    assert parsed.questions[0].possible_points == 2
    assert parsed.questions[0].source_segments == (
        student_file.QuestionSourceSegment(
            page_number=1,
            bbox_norm=(0.1, 0.2, 0.7, 0.3),
        ),
    )
    assert parsed.questions[0].response_text == "B\nbecause queue"
    assert parsed.questions[0].points_awarded == 1.0
    assert parsed.questions[0].rubric_label == ""
    assert parsed.questions[0].analysis == "Partial"
    assert parsed.questions[0].response_image_path == str(response_image.resolve())


def test_update_student_submission_markdown_updates_grade_fields() -> None:
    content = "\n".join(
        [
            "---",
            'name: "Student"',
            "---",
            "",
            "- q1:",
            "  - Prompt: One",
            "  - Response image: ![q1 response](responses/q1.png)",
            "  - Response text: |",
            "    answer",
            "  - Possible points: ",
            "  - Points: ",
            "  - Analysis: ",
            "",
            "- q2:",
            "  - Prompt: Two",
            "  - Points: ",
            "",
        ]
    )

    updated = student_file.update_student_submission_markdown(
        content,
        student_file.StudentSubmissionGradeUpdate(
            question_id="q1",
            possible_points=5,
            points_awarded=2,
            rubric_labels=("Partially Correct",),
            analysis="Process was close.",
        ),
    )

    assert "  - Response image: ![q1 response](responses/q1.png)" in updated
    assert "  - Possible points: 5" in updated
    assert "  - Points: 2" in updated
    assert "  - Rubric: Partially Correct" in updated
    assert "  - Analysis: Process was close." in updated
    assert "- q2:\n  - Prompt: Two\n  - Points: " in updated


def test_update_student_submission_markdown_supports_multiline_analysis() -> None:
    content = "\n".join(
        [
            "- q1:",
            "  - Prompt: One",
            "  - Points: 1",
            "",
        ]
    )

    updated = student_file.update_student_submission_markdown(
        content,
        student_file.StudentSubmissionGradeUpdate(
            question_id="q1",
            analysis="First line\nSecond line",
        ),
    )

    assert (
        "  - Analysis: |\n"
        "    First line\n"
        "    Second line"
    ) in updated


def test_parse_student_submission_markdown_reads_rubric_label() -> None:
    document = student_file.parse_student_submission_markdown(
        "\n".join(
            [
                "- q1:",
                "  - Prompt: One",
                "  - Points: 1",
                "  - Rubric: Correct",
                "  - Analysis: Looks good.",
                "",
            ]
        )
    )

    assert document.questions[0].rubric_label == "Correct"
    assert document.questions[0].analysis == "Looks good."


def test_parse_student_submission_markdown_reads_incorrect_rubric_label() -> None:
    document = student_file.parse_student_submission_markdown(
        "\n".join(
            [
                "- q1:",
                "  - Prompt: One",
                "  - Rubric: Incorrect",
                "",
            ]
        )
    )

    assert document.questions[0].rubric_labels == ("Incorrect",)


def test_parse_student_submission_markdown_reads_top_level_partial_rubric() -> None:
    document = student_file.parse_student_submission_markdown(
        "\n".join(
            [
                "- q1:",
                "  - Prompt: One",
                "  - Rubric: Partially Correct",
                "",
            ]
        )
    )

    assert document.questions[0].rubric_labels == ("Partially Correct",)


def test_parse_student_submission_markdown_reads_multi_label_rubric() -> None:
    document = student_file.parse_student_submission_markdown(
        "\n".join(
            [
                "- q1:",
                "  - Prompt: One",
                "  - Rubric: Partially Correct",
                "    - A",
                "    - B",
                "",
                "- q2:",
                "  - Prompt: Two",
                "  - Rubric: ",
                "",
            ]
        )
    )

    assert document.questions[0].rubric_labels == (
        "Partially Correct - A",
        "Partially Correct - B",
    )
    assert document.questions[1].rubric_labels == ()


def test_parse_student_submission_markdown_rejects_json_rubric_arrays() -> None:
    with pytest.raises(
        ValueError,
        match="legacy JSON rubric arrays are no longer supported",
    ):
        student_file.parse_student_submission_markdown(
            "\n".join(
                [
                    "- q1:",
                    "  - Prompt: One",
                    '  - Rubric: ["Partially Correct - A","Partially Correct - B"]',
                    "",
                ]
            )
        )


def test_render_student_submission_markdown_formats_multi_label_rubric() -> None:
    document = student_file.StudentSubmissionDocument(
        student_name="Taylor Student",
        student_id="123",
        course_code="CMPE195",
        assignment_title="Quiz",
        assignment_version="A",
        source_pdf="submission.pdf",
        questions=[
            student_file.StudentSubmissionQuestion(
                question_id="q1",
                prompt="One",
                possible_points=None,
                response_image_path="",
                response_text="",
                rubric_labels=("Partially Correct - A",),
            ),
            student_file.StudentSubmissionQuestion(
                question_id="q2",
                prompt="Two",
                possible_points=None,
                response_image_path="",
                response_text="",
                rubric_labels=("Partially Correct - A", "Partially Correct - B"),
            ),
        ],
    )

    rendered = student_file.render_student_submission_markdown(document)

    assert (
        "  - Rubric: Partially Correct\n"
        "    - A"
    ) in rendered
    assert (
        "  - Rubric: Partially Correct\n"
        "    - A\n"
        "    - B"
        in rendered
    )


def test_update_student_submission_markdown_clears_grade_fields() -> None:
    content = "\n".join(
        [
            "- q1:",
            "  - Prompt: One",
            "  - Possible points: 5",
            "  - Points: 3",
            "  - Rubric: Partially Correct",
            "    - A",
            "    - B",
            "",
        ]
    )

    updated = student_file.update_student_submission_markdown(
        content,
        student_file.StudentSubmissionGradeUpdate(
            question_id="q1",
            points_awarded=None,
            rubric_labels=(),
        ),
    )

    assert "  - Points:" in updated
    assert "  - Rubric:" in updated
    assert "  - Points: 3" not in updated
    assert "  - Rubric: Partially Correct" not in updated
    assert "    - A" not in updated
    assert "    - B" not in updated


def test_update_student_submission_markdown_replaces_nested_rubric_children() -> None:
    content = "\n".join(
        [
            "- q1:",
            "  - Prompt: One",
            "  - Points: 3",
            "  - Rubric: Partially Correct",
            "    - A",
            "    - B",
            "  - Analysis: Existing.",
            "",
        ]
    )

    updated = student_file.update_student_submission_markdown(
        content,
        student_file.StudentSubmissionGradeUpdate(
            question_id="q1",
            points_awarded=2,
            rubric_labels=("Partially Correct - C",),
        ),
    )

    assert (
        "  - Rubric: Partially Correct\n"
        "    - C\n"
        "  - Analysis: Existing."
    ) in updated
    assert "    - A" not in updated
    assert "    - B" not in updated


def test_build_student_submission_document_prefers_focus_crop_for_ollama_fields(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    submission_dir = output_dir / "student-0003"
    response_dir = submission_dir / "responses"
    field_dir = submission_dir / "field_crops"
    response_dir.mkdir(parents=True, exist_ok=True)
    field_dir.mkdir(parents=True, exist_ok=True)

    q1 = response_dir / "q1.png"
    name_crop = field_dir / "name_p1.png"
    sid_crop = field_dir / "sid_p1.png"

    Image.new("RGB", (300, 160), color=(255, 255, 255)).save(q1)
    Image.new("RGB", (500, 120), color=(255, 255, 255)).save(name_crop)
    Image.new("RGB", (500, 120), color=(255, 255, 255)).save(sid_crop)

    def _fake_process_submission_pdf(
        *_args: object, **_kwargs: object
    ) -> SubmissionResult:
        return SubmissionResult(
            filename="student-0003.pdf",
            version_validated=True,
            fields=[
                FieldExtractionResult(
                    field_id="student_name",
                    ocr_result=OcrResult(text="", confidence=0.0, bbox_px=(0, 0, 0, 0)),
                    validated=True,
                    crop_path=str(name_crop),
                ),
                FieldExtractionResult(
                    field_id="student_id",
                    ocr_result=OcrResult(text="", confidence=0.0, bbox_px=(0, 0, 0, 0)),
                    validated=True,
                    crop_path=str(sid_crop),
                ),
            ],
            questions=[
                QuestionExtractionResult(
                    question_id="q1",
                    page_number=1,
                    output_path=str(q1),
                )
            ],
        )

    def _fake_extract_field_text(
        *,
        image_path: str | Path,
        field_kind: str,
        config: OllamaVisionConfig,
    ) -> str:
        _ = field_kind, config
        if Path(image_path) == name_crop:
            return "Taylor Student"
        return "name"

    original_process = student_file.process_submission_pdf
    original_extract_field_text = student_file.extract_field_text
    try:
        student_file.process_submission_pdf = _fake_process_submission_pdf
        student_file.extract_field_text = _fake_extract_field_text

        result = student_file.build_student_submission_document(
            pdf_path=tmp_path / "student-0003.pdf",
            output_dir=output_dir,
            ocr_config=OcrConfig(),
            ollama_config=OllamaVisionConfig(model="custom-model:latest"),
            skip_ollama_qa=True,
        )

        assert result.document.student_name == "Taylor Student"
    finally:
        student_file.process_submission_pdf = original_process
        student_file.extract_field_text = original_extract_field_text


def test_build_student_submission_document_uses_structured_title_metadata(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    submission_dir = output_dir / "student-0004"
    response_dir = submission_dir / "responses"
    field_dir = submission_dir / "field_crops"
    response_dir.mkdir(parents=True, exist_ok=True)
    field_dir.mkdir(parents=True, exist_ok=True)

    q1 = response_dir / "q1.png"
    title_crop = field_dir / "title_p1.png"
    Image.new("RGB", (300, 160), color=(255, 255, 255)).save(q1)
    Image.new("RGB", (500, 120), color=(255, 255, 255)).save(title_crop)

    def _fake_process_submission_pdf(
        *_args: object, **_kwargs: object
    ) -> SubmissionResult:
        return SubmissionResult(
            filename="student-0004.pdf",
            version_validated=True,
            title_crop_path=str(title_crop),
            fields=[],
            questions=[
                QuestionExtractionResult(
                    question_id="q1",
                    page_number=1,
                    output_path=str(q1),
                )
            ],
        )

    def _fake_extract_title_metadata(
        *,
        image_path: str | Path,
        config: OllamaVisionConfig,
    ) -> TitleMetadataResult:
        _ = image_path, config
        return TitleMetadataResult(
            course_code="CS146",
            assignment_title="Quiz 1",
            assignment_version="A",
            raw_title="CS146: Quiz 1 (A)",
        )

    original_process = student_file.process_submission_pdf
    original_extract_title_metadata = student_file.extract_title_metadata
    progress: list[str] = []
    try:
        student_file.process_submission_pdf = _fake_process_submission_pdf
        student_file.extract_title_metadata = _fake_extract_title_metadata

        result = student_file.build_student_submission_document(
            pdf_path=tmp_path / "student-0004.pdf",
            output_dir=output_dir,
            ocr_config=OcrConfig(),
            ollama_config=OllamaVisionConfig(model="custom-model:latest"),
            skip_ollama_qa=True,
            progress_callback=progress.append,
        )

        assert not [message for message in progress if "title OCR failed" in message]
        assert result.document.course_code == "CS146"
        assert result.document.assignment_title == "Quiz 1"
        assert result.document.assignment_version == "A"
    finally:
        student_file.process_submission_pdf = original_process
        student_file.extract_title_metadata = original_extract_title_metadata
