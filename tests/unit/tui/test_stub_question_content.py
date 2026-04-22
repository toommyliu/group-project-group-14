from opengrader.tui.data.stub_question_content import StubQuestionContent

from .fakes import DEFAULT_IMAGE_PATH


def test_hw_1_uses_question_split_output_pdfs() -> None:
    content = StubQuestionContent(DEFAULT_IMAGE_PATH)

    seeds = content.build_question_seeds(assignment_title="HW 1")

    assert [seed.question_id for seed in seeds] == [
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Q6",
        "Q7",
        "Q8",
        "Q9",
        "Q10",
    ]
    assert all("question_split_images" in seed.image_path for seed in seeds)


def test_non_hw_1_uses_default_placeholder_question() -> None:
    content = StubQuestionContent(DEFAULT_IMAGE_PATH)

    seeds = content.build_question_seeds(assignment_title="HW 2")

    assert [seed.question_id for seed in seeds] == ["Q1"]
    assert seeds[0].image_path == DEFAULT_IMAGE_PATH
