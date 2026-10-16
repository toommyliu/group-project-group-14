"""Pre-flight: plan_cloud_egress, CloudPreflight, and the CLI confirm helper.

No network: the cloud provider here is a registry record with no real client.
"""

from __future__ import annotations

import pytest

from opengrader.core import app_config
from opengrader.core.app_config import consent
from opengrader.core.cloud_preflight import (
    CloudConfirmationNeeded,
    CloudPreflight,
    cli_confirm_cloud_send,
    plan_cloud_egress,
)
from opengrader.core.pipeline import LlmOptions, PipelineRequest

CLOUD_HOST = "https://api.preflight.example/v1"


def _pdf(path, pages=1) -> None:
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def cloud_spec(monkeypatch):
    original = app_config.registered_providers()
    spec = app_config.ProviderSpec(
        name="preflight-cloud",
        label="Preflight Cloud",
        summary="Test-only cloud provider.",
        default_host=CLOUD_HOST,
        default_vision_model="v",
        default_text_model="t",
        setup_hint="",
        api_key_env="PREFLIGHT_KEY",
        supports_model_listing=False,
        dialect="openai-chat",
        pricing=app_config.ProviderPricing(input_per_mtok=3.0, output_per_mtok=15.0),
    )
    app_config.register_provider(spec)
    try:
        yield spec
    finally:
        app_config._REGISTRY.clear()
        for spec_ in original:
            app_config.register_provider(spec_)


def _consented(spec, *, read_handwriting=True, grade_written=False) -> app_config.ResolvedAI:
    settings = app_config.AISettings(
        provider=spec.name, read_handwriting=read_handwriting, grade_written=grade_written
    )
    resolved = app_config.resolve(settings, environ={})
    settings = consent.grant_cloud_consent(settings, resolved)
    return app_config.resolve(settings, environ={})


def _request(tmp_path, scan, **overrides) -> PipelineRequest:
    base = dict(scan=scan, output_dir=tmp_path / "cohort", qa_file=None)
    base.update(overrides)
    return PipelineRequest(**base)


class TestLocalIsNone:
    def test_a_local_provider_returns_none(self, tmp_path) -> None:
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        request = _request(
            tmp_path, scan, llm=LlmOptions(read_handwriting=True)
        )
        assert plan_cloud_egress(request) is None


class TestCounting:
    def test_no_llm_feature_on_returns_a_preflight_with_no_counts(self, tmp_path, cloud_spec):
        resolved = _consented(cloud_spec, read_handwriting=False)
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        request = _request(tmp_path, scan)
        preflight = plan_cloud_egress(request, resolved)
        assert preflight is not None
        assert preflight.sends_anything is False

    def test_answer_images_counted_from_students_and_qa_questions(self, tmp_path, cloud_spec):
        resolved = _consented(cloud_spec, read_handwriting=True)
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        _pdf(scan / "submission-0002.pdf")
        qa = tmp_path / "qa.md"
        qa.write_text(
            "---\ntitle: T\n---\n\n- (1 points) Q1\n  **Correct** A\n"
            "- (1 points) Q2\n  **Correct** B\n",
            encoding="utf-8",
        )
        request = _request(
            tmp_path, scan, qa_file=qa, llm=LlmOptions(read_handwriting=True)
        )
        preflight = plan_cloud_egress(request, resolved)
        assert preflight is not None
        assert preflight.students == 2
        assert preflight.counts["answer-image"] == 4  # 2 students * 2 questions
        assert preflight.sends_anything is True

    def test_identity_is_never_counted_no_matter_the_settings(self, tmp_path, cloud_spec):
        """There is no override: a name or ID is never sent to a cloud host."""
        resolved = _consented(cloud_spec, read_handwriting=True)
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        request = _request(tmp_path, scan, llm=LlmOptions(read_handwriting=True))
        preflight = plan_cloud_egress(request, resolved)
        assert "identity-image" not in preflight.counts
        assert "title-image" not in preflight.counts
        assert "page-image" not in preflight.counts

    def test_a_reused_extract_stage_sends_nothing_new(self, tmp_path, cloud_spec):
        resolved = _consented(cloud_spec, read_handwriting=True)
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        request = _request(tmp_path, scan, llm=LlmOptions(read_handwriting=True))
        # Fake a reusable extraction ledger entry so the pre-flight sees that
        # every submission was already extracted last time.
        from opengrader.core.pipeline import STAGE_EXTRACT, _stage_ledger_key
        from opengrader.core.submissions.manifest import (
            RUN_LEDGER_FILENAME,
            ExtractionRecord,
            append_record,
            utc_timestamp,
        )

        source, settings_id = _stage_ledger_key(request, STAGE_EXTRACT)
        append_record(
            request.output_dir,
            ExtractionRecord(
                key=STAGE_EXTRACT,
                source=str(request.extract_source),
                source_sha256=source,
                settings_id=settings_id,
                outputs=(),
                completed_at=utc_timestamp(),
                duration_seconds=0.1,
            ),
            filename=RUN_LEDGER_FILENAME,
        )
        preflight = plan_cloud_egress(request, resolved)
        assert preflight.sends_anything is False

    def test_escalate_only_cost_basis_states_the_count_is_a_ceiling(
        self, tmp_path, cloud_spec
    ) -> None:
        """A cloud provider defaults escalate_only to True (only what the
        offline OCR could not read confidently is ever actually sent), so the
        answer-image count here is a ceiling, not a promise — cost_basis must
        say so rather than imply every one of those images will be sent."""
        resolved = _consented(cloud_spec, read_handwriting=True)
        assert resolved.escalate_only is True
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        request = _request(tmp_path, scan, llm=LlmOptions(read_handwriting=True))
        preflight = plan_cloud_egress(request, resolved)
        assert preflight is not None
        assert preflight.counts.get("answer-image", 0) > 0
        assert preflight.cost_basis.startswith(
            f"up to {preflight.counts['answer-image']} answer images"
        )
        assert "escalate-only" in preflight.cost_basis

    def test_grade_written_counts_graded_text(self, tmp_path, cloud_spec):
        resolved = _consented(cloud_spec, read_handwriting=False, grade_written=True)
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        qa = tmp_path / "qa.md"
        qa.write_text("---\ntitle: T\n---\n\n- (1 points) Q1\n  **Correct** A\n", encoding="utf-8")
        request = _request(
            tmp_path, scan, qa_file=qa, llm=LlmOptions(grade_written=True)
        )
        preflight = plan_cloud_egress(request, resolved)
        assert preflight.counts.get("graded-text", 0) > 0
        assert "answer-image" not in preflight.counts


    def test_drafting_rubrics_counts_one_call_per_question_and_version(
        self, tmp_path, cloud_spec
    ):
        # Nothing else is on, so every graded-text call counted is a draft.
        resolved = _consented(cloud_spec, read_handwriting=False, grade_written=False)
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        _pdf(scan / "submission-0002.pdf")
        qa = tmp_path / "qa.md"
        qa.write_text(
            "---\ntitle: T\n---\n\n## Version A\n\n"
            "- (1 points) Q1\n  **Correct** A\n- (1 points) Q2\n  **Correct** B\n\n"
            "## Version B\n\n"
            "- (1 points) Q1\n  **Correct** A\n- (1 points) Q2\n  **Correct** B\n",
            encoding="utf-8",
        )
        drafting = _request(tmp_path, scan, qa_file=qa, draft_rubrics=True, auto_grade=False)
        preflight = plan_cloud_egress(drafting, resolved)
        # Per question, not per student: 2 questions x 2 versions.
        assert preflight.counts == {"graded-text": 4}
        assert preflight.sends_anything is True

        not_drafting = _request(tmp_path, scan, qa_file=qa, auto_grade=False)
        assert plan_cloud_egress(not_drafting, resolved).sends_anything is False

class TestStatementAndDict:
    def test_statement_reports_scale_destination_and_cost(self) -> None:
        preflight = CloudPreflight(
            provider_label="Acme Cloud",
            host="api.acme.example",
            counts={"answer-image": 847, "graded-text": 141},
            students=141,
            estimated_cost=3.40,
            cost_basis="measured from previous calls",
        )
        text = preflight.statement()
        assert "847" in text
        assert "141" in text
        assert "api.acme.example" in text
        assert "$3.40" in text

    def test_nothing_sent_statement(self) -> None:
        preflight = CloudPreflight(provider_label="Acme", host="h", counts={})
        assert preflight.statement() == "Nothing will be sent to h."
        assert preflight.sends_anything is False

    def test_an_unknown_category_gets_a_generic_noun_instead_of_crashing(self) -> None:
        """statement() must not KeyError/IndexError on a counts category that
        isn't in its nouns table — it should fall back to a readable generic
        noun derived from the category name and still name the host."""
        preflight = CloudPreflight(
            provider_label="Acme",
            host="api.acme.example",
            counts={"mystery-call": 3},
        )
        text = preflight.statement()
        assert "3" in text
        assert "mystery call" in text
        assert "api.acme.example" in text

    def test_to_dict_is_json_safe(self) -> None:
        preflight = CloudPreflight(
            provider_label="Acme",
            host="h",
            counts={"answer-image": 1},
            students=1,
        )
        body = preflight.to_dict()
        assert body["counts"] == {"answer-image": 1}
        assert body["sends_anything"] is True
        assert "statement" in body


class TestCliConfirm:
    def test_local_never_prompts_or_exits(self, capsys) -> None:
        cli_confirm_cloud_send(None, None, yes_send_to_cloud=False)
        assert capsys.readouterr().out == ""

    def test_non_interactive_without_flag_exits_2(self, monkeypatch, cloud_spec) -> None:
        resolved = _consented(cloud_spec, read_handwriting=True)
        preflight = CloudPreflight(
            provider_label=resolved.provider.label,
            host=resolved.host,
            counts={"answer-image": 5},
            students=2,
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        messages: list[str] = []
        with pytest.raises(SystemExit) as excinfo:
            cli_confirm_cloud_send(
                resolved,
                preflight,
                yes_send_to_cloud=False,
                emit=messages.append,
            )
        assert excinfo.value.code == 2
        assert any("--yes-send-to-cloud" in m for m in messages)

    def test_flag_confirmed_skips_the_prompt(self, monkeypatch, cloud_spec) -> None:
        resolved = _consented(cloud_spec, read_handwriting=True)
        preflight = CloudPreflight(
            provider_label=resolved.provider.label,
            host=resolved.host,
            counts={"answer-image": 5},
            students=2,
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        cli_confirm_cloud_send(
            resolved, preflight, yes_send_to_cloud=True
        )  # must not raise

    def test_interactive_confirmation(self, monkeypatch, cloud_spec) -> None:
        resolved = _consented(cloud_spec, read_handwriting=True)
        preflight = CloudPreflight(
            provider_label=resolved.provider.label,
            host=resolved.host,
            counts={"answer-image": 5},
            students=2,
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        monkeypatch.setattr("builtins.input", lambda *_a: "no")
        with pytest.raises(SystemExit) as excinfo:
            cli_confirm_cloud_send(resolved, preflight, yes_send_to_cloud=False)
        assert excinfo.value.code == 2

        monkeypatch.setattr("builtins.input", lambda *_a: "y")
        cli_confirm_cloud_send(resolved, preflight, yes_send_to_cloud=False)  # must not raise

    def test_non_interactive_caller_is_never_prompted(self, monkeypatch, cloud_spec) -> None:
        # A TTY is present (the TUI's terminal), but the caller owns it.
        resolved = _consented(cloud_spec, read_handwriting=True)
        preflight = CloudPreflight(
            provider_label=resolved.provider.label,
            host=resolved.host,
            counts={"answer-image": 5},
            students=2,
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        def _no_input(*_a):
            raise AssertionError("input() must not be called")

        monkeypatch.setattr("builtins.input", _no_input)
        messages: list[str] = []
        with pytest.raises(CloudConfirmationNeeded) as excinfo:
            cli_confirm_cloud_send(
                resolved,
                preflight,
                yes_send_to_cloud=False,
                emit=messages.append,
                interactive=False,
            )
        assert excinfo.value.code == 2
        assert excinfo.value.preflight is preflight
        assert excinfo.value.statement().startswith(preflight.statement())
        assert excinfo.value.egress_statement in excinfo.value.statement()
        assert messages == []

        cli_confirm_cloud_send(
            resolved, preflight, yes_send_to_cloud=True, interactive=False
        )  # confirmed: must not raise


class TestNeverUnderstates:
    def test_a_combined_scan_that_is_not_split_yet_is_not_nothing(
        self, tmp_path, cloud_spec
    ) -> None:
        """The usual input is one combined scan PDF: the split stage has not
        run when the pre-flight is asked, so the folder of per-student PDFs
        does not exist yet. That must not read as "Nothing will be sent"."""
        resolved = _consented(cloud_spec, read_handwriting=True)
        scan = tmp_path / "bundle.pdf"
        _pdf(scan, pages=6)
        request = _request(
            tmp_path, scan, pages_per_submission=2, llm=LlmOptions(read_handwriting=True)
        )
        assert not request.extract_source.exists()

        preflight = plan_cloud_egress(request, resolved)

        assert preflight.sends_anything is True
        assert preflight.students == 3
        assert "Nothing will be sent" not in preflight.statement()

    def test_without_an_answer_key_the_count_is_not_presented_as_known(
        self, tmp_path, cloud_spec
    ) -> None:
        resolved = _consented(cloud_spec, read_handwriting=True)
        scan = tmp_path / "split"
        _pdf(scan / "submission-0001.pdf")
        _pdf(scan / "submission-0002.pdf")
        request = _request(tmp_path, scan, llm=LlmOptions(read_handwriting=True))

        text = plan_cloud_egress(request, resolved).statement()

        assert "per answer found in 2 submissions" in text
        assert "not known until the scans are read" in text

    def test_an_uncountable_run_still_says_something_will_be_sent(
        self, tmp_path, cloud_spec
    ) -> None:
        resolved = _consented(cloud_spec, read_handwriting=True)
        scan = tmp_path / "split"
        scan.mkdir()  # no PDFs directly inside: the count is unknown, not zero
        request = _request(tmp_path, scan, llm=LlmOptions(read_handwriting=True))

        preflight = plan_cloud_egress(request, resolved)

        assert preflight.sends_anything is True
        assert preflight.statement().startswith("One answer image per answer found in")

    def test_an_uncounted_statement_is_json_safe(self) -> None:
        preflight = CloudPreflight(
            provider_label="Acme",
            host="h",
            uncounted={"answer-image": "one answer image per question found in the answer key"},
        )
        body = preflight.to_dict()
        assert body["sends_anything"] is True
        assert body["uncounted"] == {
            "answer-image": "one answer image per question found in the answer key"
        }
        assert body["statement"].startswith("One answer image per question")


class TestSectionScans:
    def test_students_are_counted_across_every_section_scan(self, tmp_path, cloud_spec):
        resolved = _consented(cloud_spec)
        scans = tmp_path / "scans"
        _pdf(scans / "section-01.pdf", pages=4)
        _pdf(scans / "section-02.pdf", pages=6)
        request = _request(
            tmp_path,
            scans,
            pages_per_submission=2,
            section_scans=(str(scans / "section-01.pdf"), str(scans / "section-02.pdf")),
            llm=LlmOptions(read_handwriting=True),
        )

        preflight = plan_cloud_egress(request, resolved)

        assert preflight is not None
        # 4 / 2 + 6 / 2: both sections, not just the first.
        assert preflight.students == 5
        assert preflight.sends_anything
