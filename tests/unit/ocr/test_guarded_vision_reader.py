"""GuardedVisionReader: the identity pin and the cloud egress log wrapper."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import pytest

from opengrader.core import egress
from opengrader.core.ocr import ollama_vlm
from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig
from opengrader.core.ocr.vision_reader import (
    CloudCallBlocked,
    GuardedVisionReader,
    IdentityFieldBlocked,
    OllamaVisionReader,
    TitleMetadataResult,
    VisionReader,
)

_IDENTITY_OPS = [
    pytest.param("extract_field_text", {"field_kind": "name"}, id="name"),
    pytest.param("extract_field_text", {"field_kind": "sid"}, id="sid"),
    pytest.param("extract_title_metadata", {}, id="title"),
    # reorder.py sends whole pages — page 1 (the cover) among them.
    pytest.param("extract_page_number", {}, id="page-number"),
]

_WORK_OPS = [
    pytest.param("extract_handwriting_only", {"math_latex": False}, id="handwriting"),
    pytest.param("extract_question_prompt_and_answer", {"math_latex": False}, id="qa"),
]

# What ollama_vlm's own function for each identity op would return on a
# successful local read; used to fake a working local Ollama model without
# touching a network.
_LOCAL_SUCCESS = {
    "extract_field_text": lambda **_kwargs: "Grace Hopper",
    "extract_title_metadata": lambda **_kwargs: TitleMetadataResult(None, "HW1", None, "HW1"),
    "extract_page_number": lambda **_kwargs: 1,
}


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        def _call(**kwargs):
            self.calls.append(name)
            return ("p", "a") if name == "extract_question_prompt_and_answer" else "v"

        return _call


def _guard(
    *,
    cloud: bool,
    local_identity_config: OllamaVisionConfig | None = None,
    identity_reads_allowed: bool = True,
) -> tuple[GuardedVisionReader, _Recorder]:
    inner = _Recorder()
    return (
        GuardedVisionReader(
            inner=inner,
            provider="p",
            host="https://h.example" if cloud else "http://127.0.0.1:11434",
            cloud=cloud,
            local_identity_config=local_identity_config,
            identity_reads_allowed=identity_reads_allowed,
        ),
        inner,
    )


def test_the_guard_satisfies_the_protocol() -> None:
    guard, _ = _guard(cloud=False)
    assert isinstance(guard, VisionReader)


@pytest.mark.parametrize("method, kwargs", _IDENTITY_OPS)
def test_identity_operations_never_reach_a_cloud_reader(method, kwargs, tmp_path) -> None:
    """With no local Ollama route configured, an identity op is simply blocked."""
    guard, inner = _guard(cloud=True)

    with egress.recording(tmp_path) as log:
        with pytest.raises(IdentityFieldBlocked):
            getattr(guard, method)(image_path=Path("x.png"), **kwargs)

    assert inner.calls == []
    assert not log.exists()  # nothing was sent, so nothing is logged


@pytest.mark.parametrize("method, kwargs", _IDENTITY_OPS)
def test_identity_operations_run_on_a_local_reader_never_the_cloud_one(
    method, kwargs, monkeypatch, tmp_path
) -> None:
    """A cloud provider's own reader never sees a name, ID, title or page —
    the read happens on a local Ollama reader instead."""
    calls: list[str] = []

    def _fake(**kwargs2):
        calls.append(method)
        return _LOCAL_SUCCESS[method](**kwargs2)

    monkeypatch.setattr(ollama_vlm, method, _fake)
    guard, inner = _guard(cloud=True, local_identity_config=OllamaVisionConfig())

    with egress.recording(tmp_path) as log:
        getattr(guard, method)(image_path=Path("x.png"), **kwargs)

    assert calls == [method]
    assert inner.calls == []  # the cloud reader was never touched
    assert not log.exists()  # a local read is never a cloud egress line


@pytest.mark.parametrize("method, kwargs", _IDENTITY_OPS)
def test_identity_operations_fall_back_when_the_local_reader_fails(
    method, kwargs, monkeypatch, tmp_path
) -> None:
    def _boom(**_kwargs):
        raise ConnectionError("no Ollama server on this machine")

    monkeypatch.setattr(ollama_vlm, method, _boom)
    guard, inner = _guard(cloud=True, local_identity_config=OllamaVisionConfig())

    with egress.recording(tmp_path) as log:
        with pytest.raises(IdentityFieldBlocked):
            getattr(guard, method)(image_path=Path("x.png"), **kwargs)

    assert inner.calls == []
    assert not log.exists()


@pytest.mark.parametrize("method, kwargs", _IDENTITY_OPS)
def test_identity_operations_run_directly_on_a_local_provider(method, kwargs) -> None:
    guard, inner = _guard(cloud=False)
    getattr(guard, method)(image_path=Path("x.png"), **kwargs)
    assert inner.calls == [method]


# ---------------------------------------------------------------------------
# read_identity_locally=False (identity_reads_allowed on the guard): blocks
# only extract_field_text, the one op whose output is a name or ID; the
# other three identity-bearing ops are untouched by the setting.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_kind", ["name", "sid"])
@pytest.mark.parametrize("cloud", [False, True])
def test_extract_field_text_is_blocked_without_touching_any_model_when_disallowed(
    field_kind, cloud, monkeypatch
) -> None:
    guard, inner = _guard(
        cloud=cloud,
        local_identity_config=OllamaVisionConfig() if cloud else None,
        identity_reads_allowed=False,
    )

    def _boom(**_kwargs):
        raise AssertionError("the local Ollama route must not be touched")

    monkeypatch.setattr(ollama_vlm, "extract_field_text", _boom)

    with pytest.raises(IdentityFieldBlocked):
        guard.extract_field_text(image_path=Path("x.png"), field_kind=field_kind)
    assert inner.calls == []  # neither the local nor the cloud inner reader saw it


def test_extract_field_text_disallowed_on_cloud_never_builds_the_local_route(
    monkeypatch,
) -> None:
    """Even the (cheap, network-free) construction of a local reader is skipped."""
    guard, inner = _guard(
        cloud=True, local_identity_config=OllamaVisionConfig(), identity_reads_allowed=False
    )

    def _boom(**_kwargs):
        raise AssertionError("no local reader should be built when the setting is off")

    monkeypatch.setattr(OllamaVisionReader, "extract_field_text", _boom)

    with pytest.raises(IdentityFieldBlocked):
        guard.extract_field_text(image_path=Path("x.png"), field_kind="name")
    assert inner.calls == []


@pytest.mark.parametrize("method, kwargs", _IDENTITY_OPS[2:])  # title, page-number
def test_other_identity_ops_are_unaffected_by_read_identity_locally(method, kwargs) -> None:
    """extract_title_metadata and extract_page_number never return a name or ID, so read_identity_locally=False leaves them alone."""
    guard, inner = _guard(cloud=False, identity_reads_allowed=False)
    getattr(guard, method)(image_path=Path("x.png"), **kwargs)
    assert inner.calls == [method]


@pytest.mark.parametrize("method, kwargs", _WORK_OPS)
def test_answer_operations_reach_a_cloud_reader_and_are_logged(method, kwargs, tmp_path) -> None:
    guard, inner = _guard(cloud=True)

    with egress.recording(tmp_path) as log:
        getattr(guard, method)(image_path=Path("x.png"), **kwargs)

    assert inner.calls == [method]
    (line,) = [json.loads(raw) for raw in log.read_text().splitlines()]
    assert line["category"] == "answer-image"
    assert line["host"] == "https://h.example"


def test_a_failed_cloud_attempt_is_still_logged(tmp_path) -> None:
    class _Boom:
        def extract_handwriting_only(self, **_kwargs):
            raise RuntimeError("503")

    guard = GuardedVisionReader(inner=_Boom(), provider="p", host="https://h.example", cloud=True)
    with egress.recording(tmp_path) as log:
        with pytest.raises(RuntimeError):
            guard.extract_handwriting_only(image_path="x.png")

    assert len(log.read_text().splitlines()) == 1


def test_the_cloud_kill_switch_blocks_non_identity_operations_at_call_time(monkeypatch) -> None:
    guard, inner = _guard(cloud=True)
    monkeypatch.setenv("OPENGRADER_AI_ALLOW_CLOUD", "0")

    with pytest.raises(CloudCallBlocked):
        guard.extract_handwriting_only(image_path="x.png")
    assert inner.calls == []


def test_the_cloud_kill_switch_does_not_affect_identity_routing(monkeypatch) -> None:
    """Identity ops never reach the cloud host, so the cloud kill switch is
    irrelevant to them — they behave exactly the same whether it is set or not."""
    monkeypatch.setenv("OPENGRADER_AI_ALLOW_CLOUD", "0")
    guard, inner = _guard(cloud=True)  # no local reader configured

    with pytest.raises(IdentityFieldBlocked):
        guard.extract_field_text(image_path="x.png", field_kind="name")
    assert inner.calls == []

    local, local_inner = _guard(cloud=False)
    local.extract_field_text(image_path="x.png", field_kind="name")
    assert local_inner.calls == ["extract_field_text"]


def test_monkeypatching_ollama_vlm_after_construction_is_seen_through_the_guard(
    monkeypatch,
) -> None:
    guard = GuardedVisionReader(
        inner=OllamaVisionReader(config=OllamaVisionConfig()),
        provider="ollama",
        host="http://127.0.0.1:11434",
        cloud=False,
    )
    monkeypatch.setattr(
        ollama_vlm, "extract_field_text", lambda *, image_path, field_kind, config: "late"
    )

    assert guard.extract_field_text(image_path="x.png", field_kind="name") == "late"


def test_the_guard_pickles_with_its_inner_reader() -> None:
    guard = GuardedVisionReader(
        inner=OllamaVisionReader(config=OllamaVisionConfig()),
        provider="ollama",
        host="https://remote.example",
        cloud=True,
    )

    restored = pickle.loads(pickle.dumps(guard))

    assert restored == guard
    with pytest.raises(IdentityFieldBlocked):
        restored.extract_page_number(image_path="p.png")


def test_the_guard_pickles_with_identity_reads_allowed() -> None:
    guard = GuardedVisionReader(
        inner=OllamaVisionReader(config=OllamaVisionConfig()),
        provider="ollama",
        host="http://127.0.0.1:11434",
        cloud=False,
        identity_reads_allowed=False,
    )

    restored = pickle.loads(pickle.dumps(guard))

    assert restored == guard
    assert restored.identity_reads_allowed is False
    with pytest.raises(IdentityFieldBlocked):
        restored.extract_field_text(image_path="n.png", field_kind="name")


def test_the_guard_pickles_with_its_local_identity_config() -> None:
    guard = GuardedVisionReader(
        inner=OllamaVisionReader(config=OllamaVisionConfig()),
        provider="acme",
        host="https://remote.example",
        cloud=True,
        local_identity_config=OllamaVisionConfig(model="local-vision"),
    )

    restored = pickle.loads(pickle.dumps(guard))

    assert restored == guard
    assert restored.local_identity_config.model == "local-vision"


@pytest.mark.parametrize("method, kwargs", _IDENTITY_OPS)
@pytest.mark.parametrize(
    "remote_host",
    ["https://ollama.example.com", "http://127.0.0.1@evil.example:11434", "http://8.8.8.8:11434"],
)
def test_the_local_identity_route_is_never_used_when_it_points_off_this_network(
    method, kwargs, remote_host, monkeypatch
) -> None:
    """The redirect for names and IDs is a second route out if its host is
    not actually local; it is checked at the moment of sending."""
    calls: list[str] = []

    def _fake(**kwargs2):
        calls.append(kwargs2["config"].host)
        return _LOCAL_SUCCESS[method](**kwargs2)

    monkeypatch.setattr(ollama_vlm, method, _fake)
    guard, inner = _guard(
        cloud=True, local_identity_config=OllamaVisionConfig(host=remote_host)
    )

    with pytest.raises(IdentityFieldBlocked):
        getattr(guard, method)(image_path=Path("x.png"), **kwargs)

    assert calls == []
    assert inner.calls == []


def test_the_local_identity_route_app_config_builds_is_local(monkeypatch) -> None:
    """Even with a cloud host saved for the built-in Ollama provider, the
    identity route is built from Ollama's own local default."""
    from opengrader.core import app_config

    app_config.save_settings(
        app_config.AISettings(provider="ollama", host="https://ollama.cloud.example")
    )
    config = app_config._local_identity_config()

    assert app_config.host_kind(config.host) == "local"
