"""Telling accounts apart on a shared computer, and what a grader is told
when a workspace cannot be joined."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opengrader.server.peer import peer_uid
from opengrader.server.workspace import client as workspace_client
from opengrader.server.workspace.client import WorkspaceNotShared, join_workspace

# Two ends of one loopback connection as Linux lists them: the server's
# (port 8000, uid 1000) and the caller's (port 51234, uid 1001).
PROC_TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F40 0100007F:C822 01 00000000:00000000 00:00000000 00000000  1000        0 11 1 0 20 4 30 10 -1
   1: 0100007F:C822 0100007F:1F40 01 00000000:00000000 00:00000000 00000000  1001        0 12 1 0 20 4 30 10 -1
"""


@pytest.mark.skipif(os.sys.byteorder != "little", reason="the fixture is little-endian, as x86 and ARM print it")
def test_the_callers_account_is_read_from_the_kernels_connection_list(tmp_path: Path):
    (tmp_path / "tcp").write_text(PROC_TCP)

    assert peer_uid(("127.0.0.1", 51234), ("127.0.0.1", 8000), proc_net=tmp_path) == 1001
    assert peer_uid(("127.0.0.1", 8000), ("127.0.0.1", 51234), proc_net=tmp_path) == 1000
    assert peer_uid(("127.0.0.1", 4), ("127.0.0.1", 8000), proc_net=tmp_path) is None


def test_without_a_connection_list_nobody_is_named(tmp_path: Path):
    assert peer_uid(("127.0.0.1", 1), ("127.0.0.1", 2), proc_net=tmp_path / "missing") is None
    assert peer_uid(None, ("127.0.0.1", 2)) is None


@pytest.fixture
def others(tmp_path: Path, monkeypatch) -> Path:
    from opengrader.core.workspace.store import Workspace

    Workspace.create(tmp_path / "course")
    monkeypatch.setattr(workspace_client, "owned_by_another_account", lambda _root: True)
    return tmp_path / "course"


def test_an_unshared_workspace_says_whose_it_is_and_what_to_ask(others):
    with pytest.raises(WorkspaceNotShared, match="Ask .* to share it"):
        join_workspace(others)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_a_note_for_a_group_this_account_is_not_in(others):
    note = others / ".opengrader" / "connection.json"
    note.write_text("{}")
    note.chmod(0o000)
    try:
        with pytest.raises(WorkspaceNotShared, match="group your account"):
            join_workspace(others)
    finally:
        note.chmod(0o600)


def test_a_note_left_by_a_server_that_stopped(others):
    (others / ".opengrader" / "connection.json").write_text(json.dumps({
        "url": "http://127.0.0.1:9/courses/x", "pid": 1, "workspace_id": "x",
        "token": "t", "owner": "alice",
    }))

    with pytest.raises(WorkspaceNotShared, match="alice's Opengrader is not running"):
        join_workspace(others)
