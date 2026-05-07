# Glossary

This glossary defines common terms used throughout the project.

## Assignment Setup

**QA file**: A Markdown question-and-answer source file used to describe an
assignment, including title, instructions, questions, expected answers, choices,
versions, and rubrics. See [QA_file.md](QA_file.md) for the full format.

**Template**: A PDF generated from a QA file that students complete for an
assignment.

**Assignment version**: A version marker such as `A` or `B` used when an
assignment has multiple variants. Versions let the grading workflow match each
student submission to the correct set of questions and answers.

## Submission Processing

**Completed template**: A template after one student has filled in their
answers.

**Scanned submissions PDF**: A scanned PDF containing multiple students'
completed templates. Typically, scanning in bulk is more efficient than scanning each submission separately.

**Split submission PDF**: A per-student PDF produced from a scanned submissions PDF.

**Student submission file**: A `.submission.md` file representing one student's
processed submission. It stores metadata, response text, response images,
grading fields, and source PDF references.

**Field crop**: A cropped image of a metadata field such as name, student ID, or
assignment title.

## Grading

**Question**: A top-level prompt from the QA file. A question may be graded
directly or may contain graded subquestions.

**Subquestion**: A graded child part under a question, represented with IDs such
as `q5a`.

**Question ID**: A stable identifier such as `q1`, `q2`, or `q5a` used to match
QA file questions to student submission responses.

**Question type**: A classification for the type of question, such as
`Free Response` or `Multiple Choice`.

**Prompt**: The question text.

**Possible points**: The maximum points available for a question or subquestion.

**Points**: The awarded score for a question or subquestion.

**Rubric**: Scoring options defined in the QA file and selected during grading.

**Rubric label**: A saved label for a selected rubric option, such as `Correct`,
`Incorrect`, or `Partially Correct - Minor issue`.

**Analysis**: Grader or automated feedback text saved with a student response.

**Annotated PDF**: A derived PDF containing grader comments synchronized back to
response regions.
