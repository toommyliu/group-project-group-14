from opengrader.core.submissions.student_file import parse_student_submission_markdown

LEGACY_INK_SUBMISSION = """---
student_name: ""
student_id: ""
course_code: null
assignment_title: "Test"
assignment_version: null
section: null
source_pdf: "scan.pdf"
student_score: ""
name_image: null
id_image: null
---


- q1:
  - Question type: Free Response
  - Prompt: (1 points) Write anywhere in the box.
  - Response image: ![q1 response](responses/q1.png)
  - Ink image: ![q1 student ink](responses/q1.ink.png)
  - Source segments: [{"page":1,"bbox":[0.08,0.2,0.84,0.3]}]
  - Ink fragments: [{"page":1,"bbox":[0.1,0.2,0.08,0.03],"text":"left","confidence":98.4,"source":"vision-model"}]
  - Read by: vision-model (ollama)
  - Response text: |
    left
    right
  - Possible points: 1
  - Points: 
  - Rubric:
  - Analysis: 
"""


def test_a_submission_written_with_ink_lines_still_loads() -> None:
    question = parse_student_submission_markdown(LEGACY_INK_SUBMISSION).questions[0]

    assert question.response_image_path == "responses/q1.png"
    assert question.response_text == "left\nright"
