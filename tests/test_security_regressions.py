"""Regression tests for security boundary behaviors."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydantic_ai_filesystem_sandbox import (
    FileSystemToolset,
    Mount,
    PathNotInSandboxError,
    PathNotWritableError,
    Sandbox,
    SandboxConfig,
    SuffixNotAllowedError,
)


def test_toolset_write_respects_write_allowlist(tmp_path: Path) -> None:
    """Writing must be denied if a derived sandbox only allows reading the prefix."""
    output_root = tmp_path / "output"
    output_root.mkdir()

    config = SandboxConfig(
        mounts=[Mount(host_path=output_root, mount_point="/output", mode="rw")]
    )
    parent = Sandbox(config)
    child = parent.derive(allow_read="/output/readonly")

    toolset = FileSystemToolset(child)
    with pytest.raises((PathNotInSandboxError, PathNotWritableError)):
        toolset.write("/output/readonly/file.txt", "x")


def test_nested_derive_cannot_escalate_to_write(tmp_path: Path) -> None:
    """A derived sandbox must not be able to expand permissions via nested derive."""
    output_root = tmp_path / "output"
    output_root.mkdir()

    config = SandboxConfig(
        mounts=[Mount(host_path=output_root, mount_point="/output", mode="rw")]
    )
    parent = Sandbox(config)
    read_only_child = parent.derive(allow_read="/output/readonly")

    with pytest.raises((PathNotInSandboxError, PathNotWritableError)):
        read_only_child.derive(allow_write="/output/readonly")


def test_suffix_error_does_not_leak_host_path(tmp_path: Path) -> None:
    """SuffixNotAllowedError must use virtual paths, not resolved host paths."""
    output_root = tmp_path / "output"
    output_root.mkdir()
    bad_file = output_root / "bad.bin"
    bad_file.write_text("x", encoding="utf-8")

    config = SandboxConfig(
        mounts=[
            Mount(
                host_path=output_root,
                mount_point="/output",
                mode="ro",
                suffixes=[".txt"],
            )
        ]
    )
    sb = Sandbox(config)

    _, resolved, mount = sb.get_path_config("/output/bad.bin", op="read")
    assert resolved == bad_file.resolve()

    with pytest.raises(SuffixNotAllowedError) as exc_info:
        sb.check_suffix(resolved, mount, virtual_path="/output/bad.bin")
    msg = str(exc_info.value)
    assert str(tmp_path) not in msg
    assert "/output/bad.bin" in msg


def test_mount_host_path_overlap_is_disallowed(tmp_path: Path) -> None:
    """Two mounts must not overlap on the host filesystem."""
    root = tmp_path / "root"
    child = root / "child"
    root.mkdir()
    child.mkdir()

    config = SandboxConfig(
        mounts=[
            Mount(host_path=root, mount_point="/a", mode="ro"),
            Mount(host_path=child, mount_point="/b", mode="ro"),
        ]
    )
    with pytest.raises(ValueError, match="overlap"):
        Sandbox(config)
