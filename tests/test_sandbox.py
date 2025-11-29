"""Tests for filesystem sandbox functionality."""
from __future__ import annotations

import pytest

from pydantic_ai_filesystem_sandbox import (
    FileSandboxConfig,
    FileSandboxImpl,
    PathConfig,
    PathNotInSandboxError,
    PathNotWritableError,
    ReadResult,
    SuffixNotAllowedError,
)


class TestSandboxRead:
    """Tests for FileSandboxImpl.read() functionality."""

    def test_read_text_rejects_binary_suffix(self, tmp_path):
        """Sandbox should refuse to read files with disallowed suffixes."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        binary_file = sandbox_root / "photo.png"
        binary_file.write_bytes(b"not actually an image")

        config = FileSandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    suffixes=[".txt"],
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        with pytest.raises(SuffixNotAllowedError, match="suffix '.png' not allowed"):
            sandbox.read("input/photo.png")

    def test_read_returns_read_result(self, tmp_path):
        """FileSandboxImpl.read() returns ReadResult with content and metadata."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        text_file = sandbox_root / "doc.txt"
        text_file.write_text("Hello, World!", encoding="utf-8")

        config = FileSandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.read("input/doc.txt")
        assert isinstance(result, ReadResult)
        assert result.content == "Hello, World!"
        assert result.truncated is False
        assert result.total_chars == 13
        assert result.offset == 0
        assert result.chars_read == 13

    def test_read_truncates_large_content(self, tmp_path):
        """FileSandboxImpl.read() truncates content when exceeding max_chars."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        text_file = sandbox_root / "large.txt"
        content = "x" * 100
        text_file.write_text(content, encoding="utf-8")

        config = FileSandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.read("input/large.txt", max_chars=30)
        assert result.content == "x" * 30
        assert result.truncated is True
        assert result.total_chars == 100
        assert result.offset == 0
        assert result.chars_read == 30

    def test_read_with_offset(self, tmp_path):
        """FileSandboxImpl.read() respects offset parameter."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        text_file = sandbox_root / "doc.txt"
        text_file.write_text("0123456789ABCDEF", encoding="utf-8")

        config = FileSandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.read("input/doc.txt", offset=10)
        assert result.content == "ABCDEF"
        assert result.truncated is False
        assert result.total_chars == 16
        assert result.offset == 10
        assert result.chars_read == 6

    def test_read_with_offset_and_max_chars(self, tmp_path):
        """FileSandboxImpl.read() respects both offset and max_chars."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        text_file = sandbox_root / "doc.txt"
        text_file.write_text("0123456789ABCDEF", encoding="utf-8")

        config = FileSandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.read("input/doc.txt", max_chars=4, offset=10)
        assert result.content == "ABCD"
        assert result.truncated is True
        assert result.total_chars == 16
        assert result.offset == 10
        assert result.chars_read == 4


class TestSandboxWrite:
    """Tests for FileSandboxImpl.write() functionality."""

    def test_write_creates_file(self, tmp_path):
        """FileSandboxImpl.write() creates a new file."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.write("output/new.txt", "Hello!")
        assert "Written 6 characters" in result
        assert (sandbox_root / "new.txt").read_text() == "Hello!"

    def test_write_to_readonly_raises(self, tmp_path):
        """FileSandboxImpl.write() raises for read-only paths."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        with pytest.raises(PathNotWritableError, match="read-only"):
            sandbox.write("input/test.txt", "content")


class TestSandboxListFiles:
    """Tests for FileSandboxImpl.list_files() functionality."""

    def test_list_files_returns_files(self, tmp_path):
        """FileSandboxImpl.list_files() returns matching files."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()
        (sandbox_root / "a.txt").write_text("a")
        (sandbox_root / "b.txt").write_text("b")
        (sandbox_root / "sub").mkdir()
        (sandbox_root / "sub" / "c.txt").write_text("c")

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSandboxImpl(config)

        files = sandbox.list_files("data")
        assert "data/a.txt" in files
        assert "data/b.txt" in files
        assert "data/sub/c.txt" in files

    def test_list_files_with_pattern(self, tmp_path):
        """FileSandboxImpl.list_files() respects glob pattern."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()
        (sandbox_root / "a.txt").write_text("a")
        (sandbox_root / "b.md").write_text("b")

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSandboxImpl(config)

        files = sandbox.list_files("data", pattern="*.txt")
        assert "data/a.txt" in files
        assert "data/b.md" not in files


class TestSandboxPathValidation:
    """Tests for path validation and security."""

    def test_path_escape_blocked(self, tmp_path):
        """Sandbox blocks path traversal attempts."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSandboxImpl(config)

        with pytest.raises(PathNotInSandboxError):
            sandbox.read("data/../../../etc/passwd")

    def test_unknown_sandbox_rejected(self, tmp_path):
        """Sandbox rejects unknown sandbox names."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSandboxImpl(config)

        with pytest.raises(PathNotInSandboxError, match="outside sandbox"):
            sandbox.read("unknown/file.txt")
