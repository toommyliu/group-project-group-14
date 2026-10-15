"""Exercise the built React app against the real API and SQLite persistence."""

import csv
import io
import re

from playwright.sync_api import expect


def score(page):
    return page.get_by_role("spinbutton", name="Score", exact=True)


def feedback(page):
    return page.get_by_role("textbox", name=re.compile(r"^Feedback"))


def saved(page):
    panel = page.get_by_role("region", name="Rubric", exact=True)
    expect(panel.get_by_role("status")).to_have_text("Saved")


def test_upload_pdf_scan_and_reload(page, browser_app, scan_pdf):
    page.goto(f"{browser_app.assignment_url}/scans")
    page.get_by_label("Upload PDF scans", exact=True).set_input_files(
        {"name": "student-scan.pdf", "mimeType": "application/pdf", "buffer": scan_pdf}
    )
    card = page.get_by_role("article").filter(has_text="student-scan.pdf")
    expect(card).to_have_count(1)
    expect(card).to_contain_text("Not split yet")
    page.reload()
    expect(card).to_have_count(1)
    response = page.request.get(
        f"{browser_app.course_url}/api/assignments/{browser_app.assignment_id}/scans"
    )
    assert response.ok
    (batch,) = response.json()["batches"]
    assert batch["filename"] == "student-scan.pdf"
    assert batch["page_count"] == 1
    original = page.request.get(
        f"{browser_app.course_url}/api/assignments/"
        f"{browser_app.assignment_id}/scans/{batch['id']}/pdf"
    )
    assert original.ok
    assert original.body() == scan_pdf


def test_import_answer_key_and_create_assignment(page, browser_app, answer_key):
    page.goto(f"{browser_app.course_url}/assignments/new?write=true")
    source = answer_key.replace("Browser quiz", "Imported browser quiz")
    page.get_by_label("Import answer key", exact=True).set_input_files(
        {"name": "quiz.md", "mimeType": "text/markdown", "buffer": source.encode()}
    )
    page.get_by_role("button", name="Create assignment", exact=True).click()
    expect(page).to_have_url(re.compile(r"/assignments/[^/]+/outline$"))
    expect(page.get_by_role("heading", name="Outline", exact=True)).to_be_visible()

    response = page.request.get(f"{browser_app.course_url}/api/assignments")
    assert response.ok
    created = next(
        item for item in response.json() if item["title"] == "Imported browser quiz"
    )
    page.goto(f"{browser_app.course_url}/assignments/{created['id']}")
    page.get_by_role("tab", name="Markdown", exact=True).click()
    expect(page.get_by_label("QA markdown", exact=True)).to_contain_text(
        "Explain your reasoning."
    )


def test_decimal_score_and_feedback_survive_navigation_and_reload(page, browser_app):
    page.goto(browser_app.grade_url)
    score(page).fill("1.50")
    feedback(page).fill("Explain the missing step.")
    # Navigation must finish any pending save before changing the response.
    page.get_by_role("button", name="Next", exact=True).click()
    expect(page).to_have_url(re.compile(r"submission=2"))
    expect(score(page)).to_have_value("")
    page.get_by_role("button", name="Previous", exact=True).click()
    expect(page).to_have_url(re.compile(r"submission=1"))
    page.get_by_role("button", name="Next question", exact=True).click()
    expect(page).to_have_url(re.compile(r"question=q2"))
    expect(score(page)).to_have_value("")
    expect(feedback(page)).to_have_value("")
    score(page).fill("2")
    page.get_by_role("button", name="Previous question", exact=True).click()
    expect(page).to_have_url(re.compile(r"question=q1"))
    page.reload()
    expect(score(page)).to_have_value("1.5")
    expect(feedback(page)).to_have_value("Explain the missing step.")
    response = page.request.get(browser_app.answer_url)
    assert response.ok
    assert response.json()["points_awarded"] == 1.5
    assert response.json()["analysis"] == "Explain the missing step."


def test_saved_grade_updates_another_tab_without_reloading(page, browser_app):
    page.goto(browser_app.grade_url)
    other = page.context.new_page()
    other.goto(browser_app.grade_url)
    expect(score(other)).to_have_value("")
    page.get_by_role("button", name=re.compile(r"^Partially Correct")).click()
    saved(page)
    expect(score(other)).to_have_value("6")
    expect(
        other.get_by_role("button", name=re.compile(r"^Partially Correct"))
    ).to_have_attribute("aria-pressed", "true")


def test_failed_save_preserves_draft_and_conflict_can_use_saved_answer(page, browser_app):
    page.goto(browser_app.grade_url)
    other = page.context.new_page()
    other.goto(browser_app.grade_url)
    answer_pattern = "**/api/assignments/*/submissions/1/answers/q1"

    def disconnect_save(route):
        if route.request.method == "PUT":
            route.abort("failed")
        else:
            route.continue_()

    other.route(answer_pattern, disconnect_save)
    feedback(other).fill("My unsaved feedback")
    expect(other.get_by_role("button", name="Retry save", exact=True)).to_be_visible()
    score(page).fill("4")
    feedback(page).fill("Saved by the other grader")
    saved(page)
    expect(feedback(other)).to_have_value("My unsaved feedback")

    other.unroute(answer_pattern, disconnect_save)
    other.get_by_role("button", name="Retry save", exact=True).click()
    dialog = other.get_by_role("alertdialog")
    expect(dialog.get_by_role("heading", name="This answer changed")).to_be_visible()
    dialog.get_by_role("button", name="Use saved answer", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(score(other)).to_have_value("4")
    expect(feedback(other)).to_have_value("Saved by the other grader")
    response = other.request.get(browser_app.answer_url)
    assert response.ok
    assert response.json()["analysis"] == "Saved by the other grader"


def test_review_csv_contains_the_saved_grade(page, browser_app):
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name=re.compile(r"^Partially Correct")).click()
    saved(page)
    page.goto(f"{browser_app.assignment_url}/review")
    with page.expect_download() as pending:
        page.get_by_role("button", name="Download CSV", exact=True).click()
    download = pending.value
    assert download.suggested_filename == "Browser quiz.csv"
    rows = list(
        csv.DictReader(io.StringIO(download.path().read_text(encoding="utf-8-sig")))
    )
    assert len(rows) == 2
    alice = next(row for row in rows if row["Student ID"] == "0001")
    assert alice["Student name"] == "Alice Example"
    assert alice["Q1 (10)"] == "6"
    assert alice["Status"] == "In progress"
