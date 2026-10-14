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
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}')
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


def test_npm_command_is_found_on_path(only_these_tools) -> None:  # noqa: ANN001
    assert browser_app.npm_command() is None
    npm = _tool(only_these_tools, "npm", "exit 0\n")
    assert browser_app.npm_command() == [str(npm)]


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_build_installs_then_bundles_and_logs_both_steps(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    source = _web_sources(tmp_path / "web")
    _tool(
        only_these_tools,
        "npm",
        'echo "npm $*"\n'
        'if [ "$1" = exec ]; then /bin/mkdir -p dist && echo ok > dist/index.html; fi\n',
    )
    lines: list[str] = []
    log = tmp_path / "build.log"

    browser_app.build_web(source, on_line=lines.append, log=log)

    assert browser_app.web_dist_ready(source)
    assert "npm ci --no-audit --no-fund" in lines
    assert "npm exec -- vite build" in lines
    assert "npm ci --no-audit --no-fund" in log.read_text()


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_a_failed_step_is_reported_with_the_end_of_its_output(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    source = _web_sources(tmp_path / "web")
    _tool(only_these_tools, "npm", 'echo "npm error network no network"\nexit 1\n')

    with pytest.raises(WebBuildError) as caught:
        browser_app.build_web(source, log=tmp_path / "build.log")

    assert "Installing packages failed" in str(caught.value)
    assert "npm error network no network" in caught.value.tail
    assert not browser_app.web_dist_ready(source)


def test_no_npm_says_what_to_install(tmp_path, only_these_tools) -> None:  # noqa: ANN001
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
        "npm",
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
        "npm",
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
    assert browser_app.tool_status() == ToolStatus(node=None, npm=None)


def test_tool_status_reports_node_and_npm_paths_and_versions(only_these_tools) -> None:  # noqa: ANN001
    node = _tool(only_these_tools, "node", 'echo "v22.22.2"\n')
    npm = _tool(only_these_tools, "npm", 'echo "10.9.0"\n')

    status = browser_app.tool_status()

    assert status.node == browser_app.ToolInfo(path=str(node), version="v22.22.2")
    assert status.npm == browser_app.ToolInfo(path=str(npm), version="10.9.0")


def test_tool_status_is_none_when_the_version_call_fails(only_these_tools) -> None:  # noqa: ANN001
    _tool(only_these_tools, "node", "exit 1\n")

    assert browser_app.tool_status().node is None


# -- installing Node.js ----------------------------------------------


def _fake_brew(
    bin_dir: Path, *, installs: bool = True, exit_code: int = 0, skip: str = ""
) -> Path:
    """A stand-in `brew` that records its arguments and, when `installs`,
    leaves a runnable tool behind for each formula it was asked for (`node`
    brings `npm`) — except `skip`, the one whose download "failed"."""
    create = (
        'for f in "$@"; do\n'
        '  [ "$f" = install ] && continue\n'
        f'  [ "$f" = "{skip}" ] && continue\n'
        '  for t in "$f" $( [ "$f" = node ] && echo npm ); do\n'
        f'    printf \'#!/bin/sh\\necho 1.0\\n\' > "{bin_dir}/$t"\n'
        f'    /bin/chmod +x "{bin_dir}/$t"\n'
        "  done\n"
        "done\n"
        if installs
        else ""
    )
    return _tool(
        bin_dir,
        "brew",
        f'echo "$*" >> "{bin_dir}/brew-calls"\n'
        'echo "==> Pouring $*"\n'
        f"{create}"
        f"exit {exit_code}\n",
    )


def _brew_calls(bin_dir: Path) -> list[str]:
    record = bin_dir / "brew-calls"
    return record.read_text().splitlines() if record.exists() else []


def test_missing_tools_name_the_formulae_that_supply_them() -> None:
    node = browser_app.ToolInfo(path="/bin/node", version="v22")
    npm = browser_app.ToolInfo(path="/bin/npm", version="10")

    assert browser_app.missing_tool_formulae(ToolStatus(None, None)) == ("node",)
    assert browser_app.missing_tool_formulae(ToolStatus(node, None)) == ("node",)
    assert browser_app.missing_tool_formulae(ToolStatus(None, npm)) == ("node",)
    assert browser_app.missing_tool_formulae(ToolStatus(node, npm)) == ()


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_install_tools_asks_homebrew_for_node_and_reports_its_output(
    only_these_tools,  # noqa: ANN001
) -> None:
    brew = _fake_brew(only_these_tools)
    lines: list[str] = []
    started: list[object] = []

    browser_app.install_tools(str(brew), on_line=lines.append, on_start=started.append)

    assert _brew_calls(only_these_tools) == ["install node"]
    assert lines == ["==> Pouring install node"]
    assert len(started) == 1
    status = browser_app.tool_status()
    assert status.node is not None and status.npm is not None
    assert browser_app.npm_command() == [str(only_these_tools / "npm")]


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_install_tools_leaves_homebrew_alone_when_nothing_is_missing(
    only_these_tools,  # noqa: ANN001
) -> None:
    _tool(only_these_tools, "node", 'echo "v22.22.2"\n')
    _tool(only_these_tools, "npm", 'echo "10.9.0"\n')
    brew = _fake_brew(only_these_tools)

    browser_app.install_tools(str(brew))

    assert _brew_calls(only_these_tools) == []


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_a_failed_tool_install_is_reported_with_the_end_of_homebrews_output(
    only_these_tools,  # noqa: ANN001
) -> None:
    brew = _fake_brew(only_these_tools, installs=False, exit_code=1)

    with pytest.raises(WebBuildError) as caught:
        browser_app.install_tools(str(brew))

    assert str(caught.value).startswith("Node.js was not installed.")
    assert "exit 1" in str(caught.value)
    assert caught.value.tail == "==> Pouring install node"


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_a_clean_homebrew_exit_that_left_no_tools_is_still_a_failure(
    only_these_tools,  # noqa: ANN001
) -> None:
    brew = _fake_brew(only_these_tools, installs=False)

    with pytest.raises(WebBuildError, match="still cannot be run"):
        browser_app.install_tools(str(brew))


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_a_homebrew_that_complains_but_leaves_both_tools_working_has_not_failed(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    brew = _fake_brew(only_these_tools, exit_code=1)

    browser_app.install_tools(str(brew), log=tmp_path / "install.log")

    status = browser_app.tool_status()
    assert status.node is not None and status.npm is not None


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_a_new_tool_that_fails_its_first_start_is_asked_again(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    # What a just-installed `node` can do: nothing the first time it is run.
    started = only_these_tools / "node-started"
    flaky_node = (
        f'if [ ! -e "{started}" ]; then : > "{started}"; exit 1; fi\\necho v22.22.2\\n'
    )
    brew = _tool(
        only_these_tools,
        "brew",
        f'printf \'#!/bin/sh\\n{flaky_node}\' > "{only_these_tools}/node"\n'
        f'printf \'#!/bin/sh\\necho 10.9.0\\n\' > "{only_these_tools}/npm"\n'
        f'/bin/chmod +x "{only_these_tools}/node" "{only_these_tools}/npm"\n',
    )

    browser_app.install_tools(str(brew), log=tmp_path / "install.log")

    assert started.exists()
    assert browser_app.tool_status().node is not None


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_the_check_after_an_install_waits_longer_than_the_panels(
    tmp_path, only_these_tools, monkeypatch  # noqa: ANN001
) -> None:
    brew = _fake_brew(only_these_tools)
    real = browser_app.tool_status
    timeouts: list[float] = []

    def _status(*, timeout: float = browser_app._TOOL_CHECK_SECONDS) -> ToolStatus:
        timeouts.append(timeout)
        return real(timeout=timeout)

    monkeypatch.setattr(browser_app, "tool_status", _status)

    browser_app.install_tools(str(brew), log=tmp_path / "install.log")

    assert timeouts == [
        browser_app._TOOL_CHECK_SECONDS,
        browser_app._FIRST_RUN_CHECK_SECONDS,
    ]
    assert browser_app._FIRST_RUN_CHECK_SECONDS > browser_app._TOOL_CHECK_SECONDS


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_the_install_log_keeps_a_failed_try_after_the_retry_that_worked(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    log = tmp_path / "install.log"
    with pytest.raises(WebBuildError):
        browser_app.install_tools(
            str(_fake_brew(only_these_tools, exit_code=1, skip="node")), log=log
        )

    browser_app.install_tools(str(_fake_brew(only_these_tools)), log=log)

    text = log.read_text()
    assert "install node" in text and "failed (exit 1)" in text
    assert "Node.js: cannot be run" in text
    assert _brew_calls(only_these_tools) == ["install node", "install node"]
    assert text.rstrip().endswith("npm: 1.0 (" + str(only_these_tools / "npm") + ")")


def test_the_install_log_lives_beside_the_build_log() -> None:
    assert browser_app.install_log_path().parent == browser_app.build_log_path().parent
    assert browser_app.install_log_path() != browser_app.build_log_path()


@pytest.mark.skipif(os.name == "nt", reason="fake tools are shell scripts")
def test_a_log_that_cannot_be_written_does_not_stop_the_install(
    tmp_path, only_these_tools  # noqa: ANN001
) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")

    browser_app.install_tools(
        str(_fake_brew(only_these_tools)), log=blocker / "install.log"
    )

    assert browser_app.tool_status().node is not None
