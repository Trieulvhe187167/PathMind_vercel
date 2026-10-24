"""Workspace paths survive renaming or moving the installation folder."""

from __future__ import annotations

from pathmind.services.workspace.relocate import relocate_stale_path


def test_moved_data_path_is_reanchored(tmp_path):
    data = tmp_path / "PathMind" / "data"
    target = data / "users" / "u1" / "workspaces" / "ws_1"
    target.mkdir(parents=True)
    old_windows = r"C:\DeepTutor\data\users\u1\workspaces\ws_1"
    old_posix = "/srv/deeptutor/data/users/u1/workspaces/ws_1"
    assert relocate_stale_path(old_windows, data_root=data) == str(target.resolve())
    assert relocate_stale_path(old_posix, data_root=data) == str(target.resolve())


def test_existing_or_unknown_paths_are_left_alone(tmp_path):
    data = tmp_path / "data"
    (data / "users").mkdir(parents=True)
    existing = tmp_path / "custom"
    existing.mkdir()
    assert relocate_stale_path(str(existing), data_root=data) is None
    assert relocate_stale_path(r"D:\Elsewhere\folder", data_root=data) is None
    assert relocate_stale_path(r"C:\Old\data\users\missing", data_root=data) is None
    assert relocate_stale_path(r"C:\Old\data\..\..\etc", data_root=data) is None
    assert relocate_stale_path("", data_root=data) is None
