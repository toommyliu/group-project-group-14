import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from opengrader.core import browser_app
from opengrader.core.browser_app import BrowserPreferences, ToolStatus, WebBuildError


def _web_sources(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "package.json").write_text("{}")
    (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    return root


def _tool(bin_dir: Path, name: str, body: str) -> Path:
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def only_these_tools(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """PATH holds nothing but the fake tools a test puts in it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(browser_app, "_EXTRA_TOOL_DIRS", ())
    return bin_dir


def test_the_browser_app_is_off_until_the_user_opts_in() -> None:
    assert browser_app.load_browser_preferences() == BrowserPreferences()
    assert not browser_app.load_browser_preferences().enabled


def test_preferences_round_trip_and_survive_a_malformed_file(tmp_path) -> None:  # noqa: ANN001
    store = tmp_path / "browser.json"
    browser_app.save_browser_preferences(
        BrowserPreferences(enabled=True, last_workspace="/courses/cs101"), store
    )
    assert browser_app.load_browser_preferences(store) == BrowserPreferences(
        enabled=True, last_workspace="/courses/cs101"
    )

    store.write_text("{not json")
    assert browser_app.load_browser_preferences(store) == BrowserPreferences()
    store.write_text('{"enabled": "yes"}')
    assert not browser_app.load_browser_preferences(store).enabled


def test_the_preferences_live_beside_the_ai_settings() -> None:
    from opengrader.core.app_config import settings_path

    assert browser_app.browser_preferences_path().parent == settings_path().parent


def test_the_web_sources_are_the_folder_the_service_serves_from() -> None:
    import opengrader.server.workspace.app as service_app

    assert browser_app.web_source_dir() == Path(service_app.__file__).parents[2] / "web"


def test_dist_ready_and_buildable_are_read_from_the_folder(tmp_path) -> None:  # noqa: ANN001
    source = tmp_path / "web"
    assert not browser_app.web_buildable(source)
    _web_sources(source)
    assert browser_app.web_buildable(source)
    assert not browser_app.web_dist_ready(source)
    (source / "dist").mkdir()
    (source / "dist" / "index.html").write_text("<html></html>")
    assert browser_app.web_dist_ready(source)


def test_pnpm_is_preferred_and_corepack_is_the_fallback(only_these_tools) -> None:  # noqa: ANN001
    assert browser_app.pnpm_command() is None
    corepack = _tool(only_these_tools, "corepack", "exit 0\n")
    assert browser_app.pnpm_command() == [str(corepack), "pnpm"]
    pnpm = _tool(only_these_tools, "pnpm", "exit 0\n")
    assert browser_app.pnpm_command() == [str(pnpm)]


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_build_installs_then_bundles_and_logs_both_steps(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    source = _web_sources(tmp_path / "web")
    _tool(
        only_these_tools,
        "pnpm",
        'echo "pnpm $*"\n'
        'if [ "$1" = exec ]; then /bin/mkdir -p dist && echo ok > dist/index.html; fi\n',
    )
    lines: list[str] = []
    log = tmp_path / "build.log"

    browser_app.build_web(source, on_line=lines.append, log=log)

    assert browser_app.web_dist_ready(source)
    assert "pnpm install --frozen-lockfile" in lines
    assert "pnpm exec vite build" in lines
    assert "install --frozen-lockfile" in log.read_text()


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_a_failed_step_is_reported_with_the_end_of_its_output(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    source = _web_sources(tmp_path / "web")
    _tool(only_these_tools, "pnpm", 'echo "ERR_PNPM_FETCH no network"\nexit 1\n')

    with pytest.raises(WebBuildError) as caught:
        browser_app.build_web(source, log=tmp_path / "build.log")

    assert "Installing packages failed" in str(caught.value)
    assert "ERR_PNPM_FETCH no network" in caught.value.tail
    assert not browser_app.web_dist_ready(source)


def test_no_pnpm_says_what_to_install(tmp_path, only_these_tools) -> None:  # noqa: ANN001
    source = _web_sources(tmp_path / "web")
    with pytest.raises(WebBuildError, match="Node.js"):
        browser_app.build_web(source, log=tmp_path / "build.log")


def test_a_copy_without_sources_is_refused(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(WebBuildError, match="no browser app sources"):
        browser_app.build_web(tmp_path / "missing", log=tmp_path / "build.log")


# -- stale detection ------------------------------------------------------


def test_fingerprint_changes_with_a_source_file_and_ignores_node_modules_and_dist(
    tmp_path,  # noqa: ANN001
) -> None:
    source = _web_sources(tmp_path / "web")
    (source / "src").mkdir()
    (source / "src" / "main.tsx").write_text("console.log('a')")

    before = browser_app.source_fingerprint(source)
    assert before == browser_app.source_fingerprint(source)  # deterministic

    (source / "src" / "main.tsx").write_text("console.log('b')")
    after_edit = browser_app.source_fingerprint(source)
    assert after_edit != before

    (source / "node_modules" / "pkg").mkdir(parents=True)
    (source / "node_modules" / "pkg" / "index.js").write_text("module.exports = {}")
    (source / "dist").mkdir()
    (source / "dist" / "index.html").write_text("<html></html>")
    assert browser_app.source_fingerprint(source) == after_edit


def test_build_writes_a_stamp_with_the_fingerprint(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    source = _web_sources(tmp_path / "web")
    _tool(
        only_these_tools,
        "pnpm",
        'if [ "$1" = exec ]; then /bin/mkdir -p dist && echo ok > dist/index.html; fi\n',
    )

    browser_app.build_web(source, log=tmp_path / "build.log")

    stamp_path = source / "dist" / browser_app.BUILD_STAMP_FILENAME
    stamp = json.loads(stamp_path.read_text())
    assert stamp["fingerprint"] == browser_app.source_fingerprint(source)
    assert stamp["built_at"]
    assert browser_app.web_dist_state(source) == "ready"


def test_dist_state_is_missing_without_an_index_html(tmp_path) -> None:  # noqa: ANN001
    source = _web_sources(tmp_path / "web")
    assert browser_app.web_dist_state(source) == "missing"


def test_dist_state_follows_a_matching_or_mismatched_stamp(tmp_path) -> None:  # noqa: ANN001
    source = _web_sources(tmp_path / "web")
    dist = source / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>")

    (dist / browser_app.BUILD_STAMP_FILENAME).write_text(
        json.dumps({"version": 1, "fingerprint": browser_app.source_fingerprint(source)})
    )
    assert browser_app.web_dist_state(source) == "ready"

    (dist / browser_app.BUILD_STAMP_FILENAME).write_text(
        json.dumps({"version": 1, "fingerprint": "not-the-real-one"})
    )
    assert browser_app.web_dist_state(source) == "stale"


def test_dist_state_falls_back_to_mtimes_with_no_stamp(tmp_path) -> None:  # noqa: ANN001
    source = _web_sources(tmp_path / "web")
    dist = source / "dist"
    dist.mkdir()
    index_html = dist / "index.html"
    index_html.write_text("<html></html>")

    now = time.time()
    os.utime(source / "package.json", (now - 10, now - 10))
    os.utime(index_html, (now, now))
    assert browser_app.web_dist_state(source) == "ready"

    os.utime(source / "package.json", (now + 10, now + 10))
    assert browser_app.web_dist_state(source) == "stale"


def test_only_one_build_runs_at_a_time(tmp_path, only_these_tools) -> None:  # noqa: ANN001
    source = _web_sources(tmp_path / "web")
    _tool(
        only_these_tools,
        "pnpm",
        'sleep 0.3\n'
        'if [ "$1" = exec ]; then /bin/mkdir -p dist && echo ok > dist/index.html; fi\n',
    )
    errors: list[Exception] = []

    def _second_build() -> None:
        time.sleep(0.05)
        try:
            browser_app.build_web(source, log=tmp_path / "build2.log")
        except WebBuildError as exc:
            errors.append(exc)

    thread = threading.Thread(target=_second_build)
    thread.start()
    browser_app.build_web(source, log=tmp_path / "build1.log")
    thread.join()

    assert len(errors) == 1
    assert "already being built" in str(errors[0])


# -- tool_status ------------------------------------------------------------


def test_tool_status_is_none_when_nothing_is_installed(only_these_tools) -> None:  # noqa: ANN001
    assert browser_app.tool_status() == ToolStatus(node=None, pnpm=None)


def test_tool_status_reports_node_and_pnpm_paths_and_versions(only_these_tools) -> None:  # noqa: ANN001
    node = _tool(only_these_tools, "node", 'echo "v22.22.2"\n')
    pnpm = _tool(only_these_tools, "pnpm", 'echo "12.6.0"\n')

    status = browser_app.tool_status()

    assert status.node == browser_app.ToolInfo(path=str(node), version="v22.22.2")
    assert status.pnpm == browser_app.ToolInfo(path=str(pnpm), version="12.6.0")


def test_tool_status_reports_pnpm_via_corepack_when_pnpm_itself_is_absent(
    only_these_tools,  # noqa: ANN001
) -> None:
    _tool(only_these_tools, "corepack", 'echo "12.6.0"\n')

    status = browser_app.tool_status()

    assert status.pnpm == browser_app.ToolInfo(path="via corepack", version="12.6.0")


def test_tool_status_is_none_when_the_version_call_fails(only_these_tools) -> None:  # noqa: ANN001
    _tool(only_these_tools, "node", "exit 1\n")

    assert browser_app.tool_status().node is None
