"""The browser's outline flow end to end on a real class scan.

Writes a contact sheet of every crop to $OPENGRADER_ARTIFACTS (or the test's
temporary directory) so a reviewer can check that each region lands on the
student's writing.
"""

import csv
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import fitz
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from opengrader.server.workspace.app import create_app

SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "cs101-quiz5"


def import_roster(client: TestClient) -> None:
    data = (SAMPLE / "roster.csv").read_bytes()
    preview = client.post("/api/roster/import/preview", content=data, headers={"content-type": "text/csv"})
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["added"]) == 10
    imported = client.post(
        "/api/roster/import",
        params={"expected": preview.json()["fingerprint"]},
        content=data,
        headers={"content-type": "text/csv"},
    )
    assert imported.status_code == 200, imported.text


def contact_sheet(rows: list[tuple[str, list[Image.Image]]], path: Path) -> None:
    width = 360
    scaled = [
        (label, [image.resize((width, max(1, round(image.height * width / image.width)))) for image in images])
        for label, images in rows
    ]
    height = sum(max((image.height for image in images), default=0) + 24 for _, images in scaled)
    columns = max(len(images) for _, images in scaled)
    sheet = Image.new("RGB", (columns * (width + 8) + 8, height + 8), "white")
    draw = ImageDraw.Draw(sheet)
    top = 4
    for label, images in scaled:
        draw.text((8, top), label, fill="black")
        left = 8
        for image in images:
            sheet.paste(image, (left, top + 18))
            draw.rectangle([left - 1, top + 17, left + image.width, top + 18 + image.height], outline="gray")
            left += width + 8
        top += max((image.height for image in images), default=0) + 24
    sheet.save(path)


def test_outline_crops_a_class_scan_into_named_gradable_answers(tmp_path, monkeypatch):
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(max_workers=1))
    artifacts = Path(os.environ.get("OPENGRADER_ARTIFACTS", tmp_path / "artifacts")) / "region-map-flow"
    artifacts.mkdir(parents=True, exist_ok=True)
    truth = json.loads((SAMPLE / "ground-truth.json").read_text())["assignments"][0]["submissions"]

    app = create_app(tmp_path / "course")
    with TestClient(app) as client:
        created = client.post("/api/assignments", json={"qa_markdown": (SAMPLE / "qa.md").read_text()})
        assert created.status_code == 201, created.text
        assignment = created.json()["id"]
        api = f"/api/assignments/{assignment}"

        leaves = {}
        for version in ("A", "B"):
            generated = client.post(f"{api}/template/generate", params={"version": version})
            assert generated.status_code == 200, generated.text
            outline = generated.json()
            assert outline["template"]["page_count"] == 2
            assert outline["outline"]["name_box"] and outline["outline"]["id_box"]
            leaves[version] = [
                leaf for question in outline["outline"]["questions"] for leaf in question["parts"] or [question]
            ]
            assert leaves[version] and all(leaf["boxes"] for leaf in leaves[version])
        source = client.get(f"{api}/source").json()["qa_markdown"]
        added = client.put(api, json={"qa_markdown": source + "\n- (1 points) One more question\n", "expected_qa_markdown": source})
        assert added.status_code == 409
        assert "Outline page" in added.json()["detail"]
        reworded = source.replace("- (10 points) What is 9 plus 6?", "- (10 points) What is 9 plus 7?", 1)
        assert client.put(api, json={"qa_markdown": reworded, "expected_qa_markdown": source}).status_code == 200

        import_roster(client)
        scan = client.post(f"{api}/scans", params={"filename": "submissions.pdf"}, content=(SAMPLE / "submissions.pdf").read_bytes())
        assert scan.status_code == 200, scan.text
        batch = scan.json()
        analyzed = client.post(f"{api}/scans/{batch['id']}/analyze", json={"expected_revision": batch["revision"], "mode": "fixed", "pages_per_submission": 2})
        assert analyzed.status_code == 202
        app.state.scan_jobs.executor.submit(lambda: None).result(timeout=30)
        batch = next(item for item in client.get(f"{api}/scans").json()["batches"] if item["id"] == batch["id"])
        assert [len(group["pages"]) for group in batch["groups"]] == [2] * 8 + [1]

        groups = [[{"number": page} for page in student["scan_pages"]] for student in truth]
        # What a grader fixes in the split review: one student's pages were
        # scanned in reverse and another's first page went in upside down.
        groups[5].reverse()
        groups[7][0]["rotation"] = 180
        reviewed = client.put(
            f"{api}/scans/{batch['id']}/groups",
            json={"expected_revision": batch["revision"], "groups": groups},
        )
        assert reviewed.status_code == 200, reviewed.text
        batch = reviewed.json()
        unversioned = client.post(f"{api}/scans/{batch['id']}/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True})
        assert unversioned.status_code == 422
        assert unversioned.json()["detail"] == "Choose the assignment version for this submission."
        processed = client.post(
            f"{api}/scans/{batch['id']}/process",
            json={
                "expected_revision": batch["revision"],
                "acknowledge_findings": True,
                "group_versions": {group["key"]: student["assignment_version"] for group, student in zip(batch["groups"], truth)},
            },
        )
        assert processed.status_code == 202, processed.text
        app.state.scan_jobs.executor.submit(lambda: None).result(timeout=120)
        batch = next(item for item in client.get(f"{api}/scans").json()["batches"] if item["id"] == batch["id"])
        assert batch["status"] == "ready", batch

        submissions = client.get(f"{api}/submissions").json()
        assert [item["version"] for item in submissions] == [student["assignment_version"] for student in truth]
        rows: dict[str, list[Image.Image]] = {"name": []}
        summary = []
        roster_ids = {
            f"{row['First Name']} {row['Last Name']}": row["SID"]
            for row in csv.DictReader((SAMPLE / "roster.csv").read_text().splitlines())
        }
        assert [(item["student_name"], item["student_id"]) for item in submissions] == [
            (student["student"]["name"], roster_ids[student["student"]["name"]]) for student in truth
        ]
        assert all(item["match_percent"] >= 60 for item in submissions)
        for submission, student in zip(submissions, truth):
            name = student["student"]["name"]
            assert submission["name_image_url"] and submission["id_image_url"]
            rows["name"].append(Image.open(_bytes(client, submission["name_image_url"])))
            crops = {}
            for leaf in leaves[submission["version"]]:
                answer = client.get(f"{api}/submissions/{submission['id']}/answers/{leaf['id']}").json()
                assert answer["response_text"] == ""
                assert answer["response_image_url"], (submission["id"], leaf["id"])
                rows.setdefault(f"{submission['version']} {leaf['id']}", []).append(
                    Image.open(_bytes(client, answer["response_image_url"]))
                )
                crops[leaf["id"]] = [answer["response_image_width"], answer["response_image_height"]]
            summary.append({"submission": submission["id"], "version": submission["version"], "student": name, "crops": crops})

        first = client.get(f"{api}/submissions/1/answers/q1").json()
        assert first["question"]["prompt"] == "What is 9 plus 7?"
        graded = client.put(
            f"{api}/submissions/1/answers/q1",
            json={
                "request_id": str(uuid4()),
                "expected_revision": first["revision"],
                "expected_context_revision": first["context_revision"],
                "rubric_labels": ["Correct"],
                "score_mode": "rubric",
                "points_awarded": None,
                "analysis": "",
            },
        )
        assert graded.status_code == 200, graded.text
        confirmed = client.put(f"{api}/submissions/2/student", json={"student_name": "Marcus  Bell ", "student_id": "583920146"})
        assert (confirmed.json()["student_name"], confirmed.json()["match_percent"]) == ("Marcus Bell", None)
        listed = client.get(f"{api}/submissions").json()
        assert listed[0]["student_name"] == "Priya Shah"
        assert listed[0]["question_scores"]["q1"] == 10
        assert client.get(f"{api}/outline").json()["lock"] == {
            "scope": "questions",
            "reason": "This assignment has submissions, so you can move its boxes but not change its questions.",
        }

        writing = client.get(f"{api}/outline", params={"version": "A"}).json()["writing"]
        assert writing["submissions"] == sum(student["assignment_version"] == "A" for student in truth)
        overlays = tmp_path / "course" / ".opengrader" / "assignments" / assignment / "overlays"
        assert not overlays.exists()
        overlay = client.get(f"{api}/outline/overlay", params={"version": "A", "page": 1})
        assert overlay.status_code == 200, overlay.text
        assert overlay.headers["content-type"] == "image/png"
        (cache,) = overlays.glob(f"*-{writing['revision']}-1.png")
        assert cache.read_bytes() == overlay.content
        cached_at = cache.stat().st_mtime_ns
        assert client.get(f"{api}/outline/overlay", params={"version": "A", "page": 1}).content == overlay.content
        assert cache.stat().st_mtime_ns == cached_at
        assert client.get(f"{api}/outline/overlay", params={"version": "A", "page": 3}).status_code == 404
        heat = Image.open(BytesIO(overlay.content))
        assert heat.mode == "RGBA" and heat.width == 1100
        alpha = heat.getchannel("A")
        outline_a = client.get(f"{api}/outline", params={"version": "A"}).json()["outline"]
        answer_boxes = [box["bbox"] for leaf in leaves["A"] for box in leaf["boxes"] if box["page"] == 1]
        inside = _mean(alpha, answer_boxes)
        outside = _mean(alpha, [[0, 0, 1, 1]], exclude=answer_boxes + [outline_a["name_box"]["bbox"], outline_a["id_box"]["bbox"]])
        template = fitz.open(stream=client.get(f"{api}/template", params={"version": "A"}).content, filetype="pdf")
        pixmap = template[0].get_pixmap(matrix=fitz.Matrix(heat.width / template[0].rect.width, heat.width / template[0].rect.width))
        page = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples).resize(heat.size).convert("RGBA")
        page.alpha_composite(heat)
        draw = ImageDraw.Draw(page)
        for x, y, w, h in answer_boxes + [outline_a["name_box"]["bbox"], outline_a["id_box"]["bbox"]]:
            draw.rectangle([x * heat.width, y * heat.height, (x + w) * heat.width, (y + h) * heat.height], outline="#0369a1", width=2)
        page.convert("RGB").save(artifacts / "overlay-version-a-page-1.png")
        summary.append({"overlay": {"inside_boxes": inside, "outside_boxes": outside}})
        assert inside > 4 * outside, (inside, outside)
        assert _mean(alpha, [outline_a["name_box"]["bbox"]]) > outside

        absent = next(
            row for row in csv.DictReader((SAMPLE / "roster.csv").read_text().splitlines())
            if row["SID"] not in {item["student_id"] for item in client.get(f"{api}/submissions").json()}
        )
        absent_name = f"{absent['First Name']} {absent['Last Name']}"
        late = fitz.open()
        with fitz.open(SAMPLE / "submissions.pdf") as scan:
            for number in truth[0]["scan_pages"]:
                late.insert_pdf(scan, from_page=number - 1, to_page=number - 1)
        version = truth[0]["assignment_version"]
        before = client.get(f"{api}/outline", params={"version": version}).json()["writing"]
        paused = threading.Event()
        app.state.scan_jobs.executor.submit(paused.wait, 60)
        uploaded = client.post(
            f"{api}/scans",
            params={"filename": "late.pdf", "student_id": absent["SID"], "assignment_version": version},
            content=late.tobytes(),
        )
        assert uploaded.status_code == 200, uploaded.text
        group = uploaded.json()["groups"][0]
        assert (group["student"], group["assignment_version"]) == ({"student_id": absent["SID"], "name": absent_name}, version)
        pending = client.post(
            f"{api}/scans",
            params={"filename": "twice.pdf", "student_id": absent["SID"], "assignment_version": version},
            content=(SAMPLE / "template-vA.pdf").read_bytes(),
        )
        assert (pending.status_code, pending.json()["detail"]) == (409, f"An upload for {absent_name} is already on the Scans page.")
        paused.set()
        app.state.scan_jobs.executor.submit(lambda: None).result(timeout=60)
        late_submission = next(item for item in client.get(f"{api}/submissions").json() if item["student_id"] == absent["SID"])
        assert (late_submission["student_name"], late_submission["match_percent"], late_submission["version"]) == (
            absent_name, None, version,
        )
        summary.append({"late_upload": {"submission": late_submission["id"], "student": absent_name, "version": version}})
        again = client.post(
            f"{api}/scans",
            params={"filename": "again.pdf", "student_id": absent["SID"], "assignment_version": version},
            content=(SAMPLE / "template-vA.pdf").read_bytes(),
        )
        assert (again.status_code, again.json()["detail"]) == (422, f"{absent_name} already has a submission.")
        after = client.get(f"{api}/outline", params={"version": version}).json()["writing"]
        assert after["submissions"] == before["submissions"] + 1
        assert after["revision"] != before["revision"]
        assert client.get(f"{api}/outline/overlay", params={"version": version, "page": 1}).status_code == 200
        assert [path.name.split("-")[1] for path in overlays.glob("*-1.png")] == [after["revision"]]

    contact_sheet(list(rows.items()), artifacts / "crops.png")
    (artifacts / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    assert (artifacts / "crops.png").stat().st_size > 0


def _mean(alpha: Image.Image, boxes: list[list[float]], exclude: list[list[float]] = ()) -> float:
    width, height = alpha.size
    mask = Image.new("L", alpha.size, 0)
    draw = ImageDraw.Draw(mask)
    for x, y, w, h in boxes:
        draw.rectangle([x * width, y * height, (x + w) * width, (y + h) * height], fill=255)
    for x, y, w, h in exclude:
        draw.rectangle([x * width, y * height, (x + w) * width, (y + h) * height], fill=0)
    covered = [value for value, keep in zip(alpha.getdata(), mask.getdata()) if keep]
    return sum(covered) / max(len(covered), 1)


def _bytes(client: TestClient, url: str):
    response = client.get(url)
    assert response.status_code == 200
    return BytesIO(response.content)


def test_an_uploaded_template_gains_a_reordered_version(tmp_path, monkeypatch):
    from opengrader.core.pdf.templates import generate_template_pdf
    from opengrader.core.qa_file import parse_qa_markdown, qa_to_template_questions
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(max_workers=1))
    artifacts = Path(os.environ.get("OPENGRADER_ARTIFACTS", tmp_path / "artifacts")) / "region-map-versions"
    artifacts.mkdir(parents=True, exist_ok=True)
    key = parse_qa_markdown((SAMPLE / "qa.md").read_text())
    truth = json.loads((SAMPLE / "ground-truth.json").read_text())["assignments"][0]["submissions"]

    def printed(version: str):
        layout = generate_template_pdf(
            output_path=tmp_path / f"layout-{version}.pdf",
            version=version,
            questions=qa_to_template_questions(key, version=version),
            title=key.title,
            instructions=key.instructions,
        )
        boxes = {question.question_id: question for question in layout.questions}

        def box(question_id):
            return [{"page": boxes[question_id].page, "bbox": list(boxes[question_id].bbox)}]

        return layout, box

    app = create_app(tmp_path / "course")
    with TestClient(app) as client:
        created = client.post(
            "/api/assignments/from-template",
            params={"title": "Quiz 5", "filename": "template-vA.pdf"},
            content=(SAMPLE / "template-vA.pdf").read_bytes(),
        )
        assignment = created.json()["id"]
        api = f"/api/assignments/{assignment}"

        layout, box = printed("A")
        first = client.get(f"{api}/outline").json()
        assert (first["version"], first["versions"]) == ("", [""])
        drawn = client.put(f"{api}/outline", json={
            "version": "",
            "revision": first["revision"],
            "outline": {
                "name_box": {"page": 1, "bbox": list(layout.name_field.bbox)},
                "id_box": {"page": 1, "bbox": list(layout.student_id_field.bbox)},
                "questions": [
                    {
                        "title": question.prompt,
                        "points": question.points,
                        "boxes": [] if question.subquestions else box(question.id),
                        "parts": [
                            {"title": part.prompt, "points": part.points, "boxes": box(part.id)}
                            for part in question.subquestions
                        ],
                    }
                    for question in key.version_questions["A"]
                ],
            },
        })
        assert drawn.status_code == 200, drawn.text

        added = client.post(f"{api}/versions", json={"label": "B", "copy_from": ""})
        assert added.status_code == 422
        added = client.post(f"{api}/versions", json={"label": "B", "copy_from": "A"})
        assert added.status_code == 201, added.text
        second = added.json()
        assert (second["version"], second["versions"], second["template"]) == ("B", ["A", "B"], None)
        assert [question["title"] for question in second["outline"]["questions"]] == [
            question.prompt for question in key.version_questions["A"]
        ]
        kept = client.get(f"{api}/outline", params={"version": "A"}).json()
        assert kept["template"]["filename"] == "template-vA.pdf"
        assert kept["versions_with_regions"] == ["A"]

        uploaded = client.put(
            f"{api}/template",
            params={"filename": "template-vB.pdf", "version": "B"},
            content=(SAMPLE / "template-vB.pdf").read_bytes(),
        )
        assert uploaded.status_code == 200, uploaded.text
        second = uploaded.json()
        copied = {question["title"]: question for question in second["outline"]["questions"]}
        layout, box = printed("B")
        reordered = []
        for question in key.version_questions["B"]:
            row = copied[question.prompt]
            reordered.append({
                **row,
                "boxes": [] if question.subquestions else box(question.id),
                "parts": [
                    {**part_row, "boxes": box(part.id)}
                    for part_row, part in zip(row["parts"], question.subquestions, strict=True)
                ],
            })
        drawn = client.put(f"{api}/outline", json={
            "version": "B",
            "revision": second["revision"],
            "outline": {
                "name_box": {"page": 1, "bbox": list(layout.name_field.bbox)},
                "id_box": {"page": 1, "bbox": list(layout.student_id_field.bbox)},
                "questions": reordered,
            },
        })
        assert drawn.status_code == 200, drawn.text
        assert drawn.json()["versions_with_regions"] == ["A", "B"]

        spare = client.post(f"{api}/versions", json={"label": "C", "copy_from": None})
        assert spare.status_code == 201
        removed = client.delete(f"{api}/versions/C")
        assert removed.status_code == 200
        assert removed.json()["versions"] == ["A", "B"]
        assert removed.json()["versions_with_regions"] == ["A", "B"]

        batch = client.post(f"{api}/scans", params={"filename": "submissions.pdf"}, content=(SAMPLE / "submissions.pdf").read_bytes()).json()
        client.post(f"{api}/scans/{batch['id']}/analyze", json={"expected_revision": batch["revision"], "mode": "fixed", "pages_per_submission": 2})
        app.state.scan_jobs.executor.submit(lambda: None).result(timeout=30)
        batch = next(item for item in client.get(f"{api}/scans").json()["batches"] if item["id"] == batch["id"])
        groups = [[{"number": page} for page in student["scan_pages"]] for student in truth]
        groups[5].reverse()
        groups[7][0]["rotation"] = 180
        batch = client.put(f"{api}/scans/{batch['id']}/groups", json={"expected_revision": batch["revision"], "groups": groups}).json()
        processed = client.post(f"{api}/scans/{batch['id']}/process", json={
            "expected_revision": batch["revision"],
            "acknowledge_findings": True,
            "group_versions": {
                group["key"]: student["assignment_version"]
                for group, student in zip(batch["groups"], truth)
            },
        })
        assert processed.status_code == 202, processed.text
        app.state.scan_jobs.executor.submit(lambda: None).result(timeout=120)
        assert {item["student_name"] for item in client.get(f"{api}/submissions").json()} == {""}
        assert client.post(f"{api}/submissions/match").status_code == 409
        client.put(f"{api}/submissions/3/student", json={"student_name": "Typed by a grader", "student_id": ""})
        import_roster(client)
        matched = client.post(f"{api}/submissions/match")
        assert matched.json() == {"matched": 7, "ambiguous": 0, "unmatched": 0}
        named = {item["id"]: item["student_name"] for item in client.get(f"{api}/submissions").json()}
        assert named[3] == "Typed by a grader"
        assert [named[number] for number in (1, 2, 4, 5, 6, 7, 8)] == [
            truth[index]["student"]["name"] for index in (0, 1, 3, 4, 5, 6, 7)
        ]

        rows: dict[str, list[Image.Image]] = {}
        for submission, student in zip(client.get(f"{api}/submissions").json(), truth):
            version = student["assignment_version"]
            assert submission["version"] == version
            for question in key.version_questions[version]:
                for leaf in question.subquestions or [question]:
                    answer = client.get(f"{api}/submissions/{submission['id']}/answers/{leaf.id}").json()
                    assert answer["question"]["prompt"] == leaf.prompt
                    rows.setdefault(f"{leaf.prompt[:40]}", []).append(
                        Image.open(_bytes(client, answer["response_image_url"]))
                    )
    contact_sheet(list(rows.items()), artifacts / "crops.png")


BOXES_ONLY = "This assignment has submissions, so you can move its boxes but not change its questions."


def _scanned_class(client: TestClient, app, truth: list[dict]) -> str:
    created = client.post("/api/assignments", json={"qa_markdown": (SAMPLE / "qa.md").read_text()})
    assert created.status_code == 201, created.text
    api = f"/api/assignments/{created.json()['id']}"
    for version in ("A", "B"):
        assert client.post(f"{api}/template/generate", params={"version": version}).status_code == 200
    import_roster(client)
    batch = client.post(f"{api}/scans", params={"filename": "submissions.pdf"}, content=(SAMPLE / "submissions.pdf").read_bytes()).json()
    client.post(f"{api}/scans/{batch['id']}/analyze", json={"expected_revision": batch["revision"], "mode": "fixed", "pages_per_submission": 2})
    app.state.scan_jobs.executor.submit(lambda: None).result(timeout=30)
    batch = next(item for item in client.get(f"{api}/scans").json()["batches"] if item["id"] == batch["id"])
    groups = [[{"number": page} for page in student["scan_pages"]] for student in truth]
    groups[5].reverse()
    groups[7][0]["rotation"] = 180
    batch = client.put(f"{api}/scans/{batch['id']}/groups", json={"expected_revision": batch["revision"], "groups": groups}).json()
    processed = client.post(f"{api}/scans/{batch['id']}/process", json={
        "expected_revision": batch["revision"],
        "acknowledge_findings": True,
        "group_versions": {group["key"]: student["assignment_version"] for group, student in zip(batch["groups"], truth)},
    })
    assert processed.status_code == 202, processed.text
    app.state.scan_jobs.executor.submit(lambda: None).result(timeout=120)
    return api


def _as_sent(outline: dict) -> dict:
    """The outline as the browser sends it: every coordinate rounded to four places."""

    def boxes(items):
        return [{"page": box["page"], "bbox": [round(value, 4) for value in box["bbox"]]} for box in items]

    def row(item):
        return {"id": item["id"], "title": item["title"], "points": item["points"], "boxes": boxes(item["boxes"])}

    return {
        "questions": [{**row(question), "parts": [row(part) for part in question["parts"]]} for question in outline["questions"]],
        "name_box": boxes([outline["name_box"]])[0],
        "id_box": boxes([outline["id_box"]])[0],
    }


def test_moving_boxes_after_scanning_crops_the_class_again(tmp_path, monkeypatch):
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(max_workers=1))
    artifacts = Path(os.environ.get("OPENGRADER_ARTIFACTS", tmp_path / "artifacts")) / "region-map-recut"
    artifacts.mkdir(parents=True, exist_ok=True)
    truth = json.loads((SAMPLE / "ground-truth.json").read_text())["assignments"][0]["submissions"]

    app = create_app(tmp_path / "course")
    with TestClient(app) as client:
        api = _scanned_class(client, app, truth)
        cropped = []
        crop = scan_jobs.crop_submission

        def recorded(pdf, inputs, output):
            cropped.append([question.question_id for question in inputs.regions.questions])
            return crop(pdf, inputs, output)

        monkeypatch.setattr(scan_jobs, "crop_submission", recorded)

        def leaves(version):
            outline = client.get(f"{api}/outline", params={"version": version}).json()["outline"]
            return [leaf["id"] for question in outline["questions"] for leaf in question["parts"] or [question]]

        def answers(submission):
            return {
                leaf: client.get(f"{api}/submissions/{submission['id']}/answers/{leaf}").json()
                for leaf in leaves(submission["version"])
            }

        submissions = client.get(f"{api}/submissions").json()
        in_a = [item for item in submissions if item["version"] == "A"]
        moved_id = leaves("A")[0]
        graded, by_hand = in_a[0]["id"], in_a[1]["id"]
        first = client.get(f"{api}/submissions/{graded}/answers/{moved_id}").json()
        saved_grade = client.put(f"{api}/submissions/{graded}/answers/{moved_id}", json={
            "request_id": str(uuid4()),
            "expected_revision": first["revision"],
            "expected_context_revision": first["context_revision"],
            "rubric_labels": ["Correct"],
            "score_mode": "rubric",
            "points_awarded": None,
            "analysis": "Clear steps.",
        })
        assert saved_grade.status_code == 200, saved_grade.text
        typed = client.put(f"{api}/submissions/{by_hand}/student", json={"student_name": "Typed by a grader", "student_id": ""})
        assert typed.status_code == 200, typed.text
        submissions = client.get(f"{api}/submissions").json()
        before = {item["id"]: answers(item) for item in submissions}

        outline = client.get(f"{api}/outline", params={"version": "A"}).json()
        assert (outline["lock"], outline["recut"]) == ({"scope": "questions", "reason": BOXES_ONLY}, None)
        assert outline["submissions"] == {
            "count": len(in_a),
            "rematchable": len(in_a) - 1,
            "graded": {moved_id: 1},
        }
        for change in ("title", "points"):
            edited = _as_sent(outline["outline"])
            edited["questions"][0][change] = "Retitled" if change == "title" else 99
            refused = client.put(f"{api}/outline", json={"version": "A", "revision": outline["revision"], "outline": edited})
            assert (refused.status_code, refused.json()["detail"]) == (422, BOXES_ONLY)

        moved = _as_sent(outline["outline"])
        target = next(leaf for question in moved["questions"] for leaf in question["parts"] or [question] if leaf["id"] == moved_id)
        x, y, width, height = target["boxes"][0]["bbox"]
        target["boxes"][0]["bbox"] = [x, y, width, round(height / 2, 4)]
        name_x, name_y, name_width, name_height = moved["name_box"]["bbox"]
        moved["name_box"]["bbox"] = [name_x, name_y, round(name_width + 0.02, 4), name_height]
        paused = threading.Event()
        app.state.scan_jobs.executor.submit(paused.wait, 60)
        try:
            saved = client.put(f"{api}/outline", json={"version": "A", "revision": outline["revision"], "outline": moved})
            assert saved.status_code == 200, saved.text
            assert saved.json()["lock"]["scope"] == "outline"
            assert saved.json()["recut"] == {"done": 0, "remaining": len(in_a), "failures": []}
            again = client.put(f"{api}/outline", json={"version": "A", "revision": saved.json()["revision"], "outline": moved})
            assert (again.status_code, again.json()["detail"]) == (
                409,
                "Wait for answers to be cropped from the new boxes before changing the outline.",
            )
        finally:
            paused.set()
        app.state.scan_jobs.executor.submit(lambda: None).result(timeout=120)

        assert cropped == [[moved_id]] * len(in_a)
        finished = client.get(f"{api}/outline", params={"version": "A"}).json()
        assert (finished["lock"]["scope"], finished["recut"]) == ("questions", None)
        after_list = {item["id"]: item for item in client.get(f"{api}/submissions").json()}
        rows: dict[str, list[Image.Image]] = {}
        summary = []

        def unimaged(answer):
            return {key: value for key, value in answer.items() if not key.startswith("response_image")}

        for submission in submissions:
            old, new = before[submission["id"]], answers(submission)
            listed = after_list[submission["id"]]
            if submission["version"] == "B":
                assert (new, listed) == (old, submission)
                continue
            assert {key: value for key, value in new.items() if key != moved_id} == {
                key: value for key, value in old.items() if key != moved_id
            }
            assert unimaged(new[moved_id]) == unimaged(old[moved_id])
            assert new[moved_id]["response_image_url"] != old[moved_id]["response_image_url"]
            assert new[moved_id]["response_image_width"] == old[moved_id]["response_image_width"]
            expected = old[moved_id]["response_image_height"] * round(height / 2, 4) / height
            assert abs(new[moved_id]["response_image_height"] - expected) <= 2
            assert listed["name_image_url"] != submission["name_image_url"]
            if submission["id"] == by_hand:
                assert (listed["student_name"], listed["match_percent"]) == ("Typed by a grader", None)
            else:
                assert listed["student_name"] == submission["student_name"]
                assert listed["match_percent"] is not None
            for label, image in (
                (f"{moved_id} before", old[moved_id]["response_image_url"]),
                (f"{moved_id} after", new[moved_id]["response_image_url"]),
                ("name before", submission["name_image_url"]),
                ("name after", listed["name_image_url"]),
            ):
                rows.setdefault(label, []).append(Image.open(_bytes(client, image)))
            summary.append({
                "submission": submission["id"],
                "student": listed["student_name"],
                "match_percent": listed["match_percent"],
                f"{moved_id}_before": [old[moved_id]["response_image_width"], old[moved_id]["response_image_height"]],
                f"{moved_id}_after": [new[moved_id]["response_image_width"], new[moved_id]["response_image_height"]],
            })
        assert unimaged(answers(after_list[graded])[moved_id]) == unimaged(before[graded][moved_id])
        assert after_list[graded]["question_scores"][moved_id] == 10
        jobs = [job for job in client.get("/api/jobs").json() if api.endswith(job["assignment_id"])]
        assert [(job["submission_id"], job["generation"]) for job in jobs] == [(graded, 2)]

    contact_sheet(list(rows.items()), artifacts / "crops.png")
    (artifacts / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    assert (artifacts / "crops.png").stat().st_size > 0
