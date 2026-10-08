import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from opengrader.server.workspace.app import create_app

SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "cs101-quiz5"
PAGE_FLAGS = {"repeated_page", "interleaved", "missing_page", "mixed_versions", "extra_work"}


def test_scan_checks_flag_every_misassembled_variant_and_nothing_else(tmp_path, monkeypatch):
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(max_workers=1))
    artifacts = Path(os.environ.get("OPENGRADER_ARTIFACTS", tmp_path / "artifacts")) / "scan-page-checks"
    artifacts.mkdir(parents=True, exist_ok=True)

    app = create_app(tmp_path / "course")
    with TestClient(app) as client:
        created = client.post("/api/assignments", json={"qa_markdown": (SAMPLE / "qa.md").read_text()})
        assert created.status_code == 201, created.text
        api = f"/api/assignments/{created.json()['id']}"
        for version in ("A", "B"):
            assert client.post(f"{api}/template/generate", params={"version": version}).status_code == 200

        report = {}
        for variant in sorted(path for path in (SAMPLE / "variants").iterdir() if path.is_dir()):
            truth = json.loads((variant / "ground-truth.json").read_text())
            uploaded = client.post(f"{api}/scans", params={"filename": f"{variant.name}.pdf"}, content=(variant / "submissions.pdf").read_bytes())
            assert uploaded.status_code == 200, uploaded.text
            batch = uploaded.json()
            analyzed = client.post(
                f"{api}/scans/{batch['id']}/analyze",
                json={"expected_revision": batch["revision"], "mode": "fixed", "pages_per_submission": 2},
            )
            assert analyzed.status_code == 202, analyzed.text
            app.state.scan_jobs.executor.submit(lambda: None).result(timeout=60)
            batch = next(item for item in client.get(f"{api}/scans").json()["batches"] if item["id"] == batch["id"])
            groups = [
                {"pages": [page["number"] for page in group["pages"]], "version": group["matched_version"], "warnings": group["scan_warnings"]}
                for group in batch["groups"]
            ]
            report[variant.name] = groups
            (artifacts / "variants.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

            flags = [flag for flag in truth["expected_flags"] if flag["kind"] in PAGE_FLAGS]
            for flag in flags:
                assert any(set(group["pages"]) & set(flag["scan_pages"]) and group["warnings"] for group in groups), (variant.name, flag)

            pages = {page["scan_page"]: page for page in truth["scan_pages"]}
            flagged = {page for flag in flags for page in flag["scan_pages"]}
            for group in groups:
                student = {pages[number]["student"] for number in group["pages"]}
                printed = [pages[number]["printed_page"] for number in group["pages"]]
                if len(student) == 1 and printed == [1, 2] and not flagged & set(group["pages"]):
                    reordered = [f"Pages were reordered to match Version {group['version']}."]
                    assert group["warnings"] in ([], reordered), (variant.name, group)
                    assert group["version"] == pages[group["pages"][0]]["version"], (variant.name, group)
