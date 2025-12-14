"""Tests for root-mount sandboxes and derive allowlists."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydantic_ai_filesystem_sandbox import (
    Mount,
    PathNotInSandboxError,
    Sandbox,
    SandboxConfig,
    SandboxPermissionEscalationError,
)


class TestRootMountSandbox:
    def test_root_mount_resolves_relative_and_absolute(self, tmp_path: Path) -> None:
        root = tmp_path / "proj"
        (root / "src").mkdir(parents=True)
        file_path = root / "src" / "a.txt"
        file_path.write_text("hi", encoding="utf-8")

        cfg = SandboxConfig(mounts=[Mount(host_path=root, mount_point="/", mode="rw")])
        sb = Sandbox(cfg, base_path=tmp_path)

        assert sb.resolve("src/a.txt") == file_path.resolve()
        assert sb.resolve("/src/a.txt") == file_path.resolve()
        assert sb.can_read("src/a.txt")
        assert sb.can_write("src/a.txt")
        assert sb.readable_roots == ["/"]
        assert sb.writable_roots == ["/"]

    def test_root_mount_blocks_traversal_and_symlink_escape(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "proj"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("nope", encoding="utf-8")
        (root / "inside.txt").write_text("ok", encoding="utf-8")

        cfg = SandboxConfig(mounts=[Mount(host_path=root, mount_point="/", mode="rw")])
        sb = Sandbox(cfg, base_path=tmp_path)

        with pytest.raises(PathNotInSandboxError):
            sb.resolve("../outside/secret.txt")

        link = root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not supported")

        with pytest.raises(PathNotInSandboxError):
            sb.resolve("link/secret.txt")


class TestDeriveAllowlistsMultiPath:
    def test_derive_default_is_empty(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        output_root = tmp_path / "output"
        data_root.mkdir()
        output_root.mkdir()

        cfg = SandboxConfig(
            mounts=[
                Mount(host_path=data_root, mount_point="/data", mode="ro"),
                Mount(host_path=output_root, mount_point="/output", mode="rw"),
            ]
        )
        parent = Sandbox(cfg)
        child = parent.derive()

        assert not child.can_read("/data/file.txt")
        assert not child.can_write("/output/file.txt")
        assert child.readable_roots == []
        assert child.writable_roots == []
        with pytest.raises(PathNotInSandboxError):
            child.resolve("/data/file.txt")

    def test_derive_allow_read_narrows_access(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        (data_root / "sub").mkdir(parents=True)
        (data_root / "sub" / "a.txt").write_text("a", encoding="utf-8")
        (data_root / "b.txt").write_text("b", encoding="utf-8")

        cfg = SandboxConfig(mounts=[Mount(host_path=data_root, mount_point="/data", mode="ro")])
        parent = Sandbox(cfg)
        child = parent.derive(allow_read="/data/sub")

        assert child.can_read("/data/sub/a.txt")
        assert not child.can_read("/data/b.txt")
        assert not child.can_write("/data/sub/a.txt")
        assert child.readable_roots == ["/data/sub"]
        assert child.writable_roots == []
        with pytest.raises(PathNotInSandboxError):
            child.resolve("/data/b.txt")

    def test_allow_write_implies_read(self, tmp_path: Path) -> None:
        output_root = tmp_path / "output"
        output_root.mkdir()

        cfg = SandboxConfig(mounts=[Mount(host_path=output_root, mount_point="/output", mode="rw")])
        parent = Sandbox(cfg)
        child = parent.derive(allow_write="/output")

        assert child.can_read("/output/x.txt")
        assert child.can_write("/output/x.txt")
        assert child.readable_roots == ["/output"]
        assert child.writable_roots == ["/output"]

    def test_inherit_true_keeps_parent_access(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        output_root = tmp_path / "output"
        data_root.mkdir()
        output_root.mkdir()

        cfg = SandboxConfig(
            mounts=[
                Mount(host_path=data_root, mount_point="/data", mode="ro"),
                Mount(host_path=output_root, mount_point="/output", mode="rw"),
            ]
        )
        parent = Sandbox(cfg)
        child = parent.derive(inherit=True)

        assert child.can_read("/data/a.txt")
        assert child.can_write("/output/a.txt")
        assert child.readable_roots == parent.readable_roots
        assert child.writable_roots == parent.writable_roots

    def test_readonly_false_escalation_blocked(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()

        cfg = SandboxConfig(mounts=[Mount(host_path=data_root, mount_point="/data", mode="ro")])
        parent = Sandbox(cfg)

        with pytest.raises(SandboxPermissionEscalationError):
            parent.derive(inherit=True, readonly=False)


class TestDeriveAllowlistsRootMount:
    def test_derive_allow_read_root_mount(self, tmp_path: Path) -> None:
        root = tmp_path / "proj"
        (root / "src").mkdir(parents=True)
        (root / "docs").mkdir(parents=True)
        (root / "src" / "a.py").write_text("a", encoding="utf-8")
        (root / "docs" / "b.md").write_text("b", encoding="utf-8")

        cfg = SandboxConfig(mounts=[Mount(host_path=root, mount_point="/", mode="rw")])
        parent = Sandbox(cfg)
        child = parent.derive(allow_read="/src")

        assert child.can_read("/src/a.py")
        assert not child.can_read("/docs/b.md")
        with pytest.raises(PathNotInSandboxError):
            child.resolve("/docs/b.md")


class TestNestedDerivation:
    """Tests for deriving from already-derived sandboxes."""

    def test_nested_derive_multi_path(self, tmp_path: Path) -> None:
        """Child of derived sandbox can further narrow access."""
        data_root = tmp_path / "data"
        (data_root / "sub" / "deep").mkdir(parents=True)
        (data_root / "sub" / "a.txt").write_text("a", encoding="utf-8")
        (data_root / "sub" / "deep" / "b.txt").write_text("b", encoding="utf-8")
        (data_root / "other.txt").write_text("other", encoding="utf-8")

        cfg = SandboxConfig(mounts=[Mount(host_path=data_root, mount_point="/data", mode="rw")])
        parent = Sandbox(cfg)

        # First derivation: allow /data/sub
        child = parent.derive(allow_read="/data/sub")
        assert child.can_read("/data/sub/a.txt")
        assert child.can_read("/data/sub/deep/b.txt")
        assert not child.can_read("/data/other.txt")

        # Second derivation: further narrow to /data/sub/deep
        grandchild = child.derive(allow_read="/data/sub/deep")
        assert not grandchild.can_read("/data/sub/a.txt")
        assert grandchild.can_read("/data/sub/deep/b.txt")
        assert not grandchild.can_read("/data/other.txt")

    def test_nested_derive_root_mount(self, tmp_path: Path) -> None:
        """Child of derived root-mount sandbox can further narrow access."""
        root = tmp_path / "proj"
        (root / "src" / "core").mkdir(parents=True)
        (root / "src" / "a.py").write_text("a", encoding="utf-8")
        (root / "src" / "core" / "b.py").write_text("b", encoding="utf-8")
        (root / "docs").mkdir()
        (root / "docs" / "readme.md").write_text("readme", encoding="utf-8")

        cfg = SandboxConfig(mounts=[Mount(host_path=root, mount_point="/", mode="rw")])
        parent = Sandbox(cfg)

        # First derivation: allow /src
        child = parent.derive(allow_read="/src")
        assert child.can_read("/src/a.py")
        assert child.can_read("/src/core/b.py")
        assert not child.can_read("/docs/readme.md")

        # Second derivation: further narrow to /src/core
        grandchild = child.derive(allow_read="/src/core")
        assert not grandchild.can_read("/src/a.py")
        assert grandchild.can_read("/src/core/b.py")
        assert not grandchild.can_read("/docs/readme.md")

    def test_nested_derive_write_permission(self, tmp_path: Path) -> None:
        """Write permissions can be further narrowed in nested derivation."""
        output_root = tmp_path / "output"
        (output_root / "dir1").mkdir(parents=True)
        (output_root / "dir2").mkdir()

        cfg = SandboxConfig(mounts=[Mount(host_path=output_root, mount_point="/output", mode="rw")])
        parent = Sandbox(cfg)

        child = parent.derive(allow_write="/output/dir1")
        assert child.can_write("/output/dir1/file.txt")
        assert not child.can_write("/output/dir2/file.txt")

        # Grandchild cannot escalate to dir2
        grandchild = child.derive(allow_write="/output/dir1")
        assert grandchild.can_write("/output/dir1/file.txt")
        assert not grandchild.can_write("/output/dir2/file.txt")
