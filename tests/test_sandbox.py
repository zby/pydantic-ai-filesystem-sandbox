"""Tests for filesystem sandbox functionality."""
from __future__ import annotations

import pytest

from pydantic_ai_filesystem_sandbox import (
    EditError,
    FileSystemToolset,
    PathConfig,
    PathNotInSandboxError,
    PathNotWritableError,
    ReadResult,
    Sandbox,
    SandboxConfig,
    SuffixNotAllowedError,
)


class TestSandboxRead:
    """Tests for FileSystemToolset.read() functionality."""

    def test_read_text_rejects_binary_suffix(self, tmp_path):
        """Sandbox should refuse to read files with disallowed suffixes."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        binary_file = sandbox_root / "photo.png"
        binary_file.write_bytes(b"not actually an image")

        config = SandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    suffixes=[".txt"],
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(SuffixNotAllowedError, match="suffix '.png' not allowed"):
            sandbox.read("input/photo.png")

    def test_read_returns_read_result(self, tmp_path):
        """FileSystemToolset.read() returns ReadResult with content and metadata."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        text_file = sandbox_root / "doc.txt"
        text_file.write_text("Hello, World!", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.read("input/doc.txt")
        assert isinstance(result, ReadResult)
        assert result.content == "Hello, World!"
        assert result.truncated is False
        assert result.total_chars == 13
        assert result.offset == 0
        assert result.chars_read == 13

    def test_read_truncates_large_content(self, tmp_path):
        """FileSystemToolset.read() truncates content when exceeding max_chars."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        text_file = sandbox_root / "large.txt"
        content = "x" * 100
        text_file.write_text(content, encoding="utf-8")

        config = SandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.read("input/large.txt", max_chars=30)
        assert result.content == "x" * 30
        assert result.truncated is True
        assert result.total_chars == 100
        assert result.offset == 0
        assert result.chars_read == 30

    def test_read_with_offset(self, tmp_path):
        """FileSystemToolset.read() respects offset parameter."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        text_file = sandbox_root / "doc.txt"
        text_file.write_text("0123456789ABCDEF", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.read("input/doc.txt", offset=10)
        assert result.content == "ABCDEF"
        assert result.truncated is False
        assert result.total_chars == 16
        assert result.offset == 10
        assert result.chars_read == 6

    def test_read_with_offset_and_max_chars(self, tmp_path):
        """FileSystemToolset.read() respects both offset and max_chars."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        text_file = sandbox_root / "doc.txt"
        text_file.write_text("0123456789ABCDEF", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.read("input/doc.txt", max_chars=4, offset=10)
        assert result.content == "ABCD"
        assert result.truncated is True
        assert result.total_chars == 16
        assert result.offset == 10
        assert result.chars_read == 4


class TestSandboxWrite:
    """Tests for FileSystemToolset.write() functionality."""

    def test_write_creates_file(self, tmp_path):
        """FileSystemToolset.write() creates a new file."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.write("output/new.txt", "Hello!")
        assert "Written 6 characters" in result
        assert (sandbox_root / "new.txt").read_text() == "Hello!"

    def test_write_to_readonly_raises(self, tmp_path):
        """FileSystemToolset.write() raises for read-only paths."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "input": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(PathNotWritableError, match="read-only"):
            sandbox.write("input/test.txt", "content")


class TestSandboxListFiles:
    """Tests for FileSystemToolset.list_files() functionality."""

    def test_list_files_returns_files(self, tmp_path):
        """FileSystemToolset.list_files() returns matching files."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()
        (sandbox_root / "a.txt").write_text("a")
        (sandbox_root / "b.txt").write_text("b")
        (sandbox_root / "sub").mkdir()
        (sandbox_root / "sub" / "c.txt").write_text("c")

        config = SandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        files = sandbox.list_files("data")
        assert "data/a.txt" in files
        assert "data/b.txt" in files
        assert "data/sub/c.txt" in files

    def test_list_files_with_pattern(self, tmp_path):
        """FileSystemToolset.list_files() respects glob pattern."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()
        (sandbox_root / "a.txt").write_text("a")
        (sandbox_root / "b.md").write_text("b")

        config = SandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        files = sandbox.list_files("data", pattern="*.txt")
        assert "data/a.txt" in files
        assert "data/b.md" not in files


class TestSandboxPathValidation:
    """Tests for path validation and security."""

    def test_path_escape_blocked(self, tmp_path):
        """Sandbox blocks path traversal attempts."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(PathNotInSandboxError):
            sandbox.read("data/../../../etc/passwd")

    def test_unknown_sandbox_rejected(self, tmp_path):
        """Sandbox rejects unknown sandbox names."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(PathNotInSandboxError, match="outside sandbox"):
            sandbox.read("unknown/file.txt")


class TestSandboxEdit:
    """Tests for FileSystemToolset.edit() functionality."""

    def test_edit_replaces_text(self, tmp_path):
        """FileSystemToolset.edit() replaces old_text with new_text."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        test_file = sandbox_root / "test.txt"
        test_file.write_text("Hello World!", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.edit("output/test.txt", "World", "Python")
        assert "Edited" in result
        assert test_file.read_text() == "Hello Python!"

    def test_edit_multiline(self, tmp_path):
        """FileSystemToolset.edit() handles multiline replacements."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        test_file = sandbox_root / "test.txt"
        test_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.edit("output/test.txt", "line2\nline3", "replaced")
        assert test_file.read_text() == "line1\nreplaced\n"

    def test_edit_text_not_found(self, tmp_path):
        """FileSystemToolset.edit() raises EditError when text not found."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        test_file = sandbox_root / "test.txt"
        test_file.write_text("Hello World!", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(EditError, match="text not found"):
            sandbox.edit("output/test.txt", "nonexistent", "replacement")

    def test_edit_multiple_matches(self, tmp_path):
        """FileSystemToolset.edit() raises EditError when text appears multiple times."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        test_file = sandbox_root / "test.txt"
        test_file.write_text("foo bar foo baz foo", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(EditError, match="found 3 times"):
            sandbox.edit("output/test.txt", "foo", "qux")

    def test_edit_readonly_raises(self, tmp_path):
        """FileSystemToolset.edit() raises for read-only paths."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        test_file = sandbox_root / "test.txt"
        test_file.write_text("Hello World!", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "input": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(PathNotWritableError, match="read-only"):
            sandbox.edit("input/test.txt", "World", "Python")

    def test_edit_file_not_found(self, tmp_path):
        """FileSystemToolset.edit() raises when file doesn't exist."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(FileNotFoundError, match="not found"):
            sandbox.edit("output/nonexistent.txt", "old", "new")


class TestSandboxDelete:
    """Tests for FileSystemToolset.delete() functionality."""

    def test_delete_removes_file(self, tmp_path):
        """FileSystemToolset.delete() removes the file."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        test_file = sandbox_root / "test.txt"
        test_file.write_text("content", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.delete("output/test.txt")
        assert "Deleted" in result
        assert not test_file.exists()

    def test_delete_readonly_raises(self, tmp_path):
        """FileSystemToolset.delete() raises for read-only paths."""
        sandbox_root = tmp_path / "input"
        sandbox_root.mkdir()
        test_file = sandbox_root / "test.txt"
        test_file.write_text("content", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "input": PathConfig(root=str(sandbox_root), mode="ro")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(PathNotWritableError, match="read-only"):
            sandbox.delete("input/test.txt")

    def test_delete_file_not_found(self, tmp_path):
        """FileSystemToolset.delete() raises when file doesn't exist."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(FileNotFoundError, match="not found"):
            sandbox.delete("output/nonexistent.txt")


class TestSandboxMove:
    """Tests for FileSystemToolset.move() functionality."""

    def test_move_renames_file(self, tmp_path):
        """FileSystemToolset.move() renames a file."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        src_file = sandbox_root / "old.txt"
        src_file.write_text("content", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.move("output/old.txt", "output/new.txt")
        assert "Moved" in result
        assert not src_file.exists()
        assert (sandbox_root / "new.txt").read_text() == "content"

    def test_move_creates_parent_directories(self, tmp_path):
        """FileSystemToolset.move() creates parent directories for destination."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        src_file = sandbox_root / "file.txt"
        src_file.write_text("content", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.move("output/file.txt", "output/subdir/nested/file.txt")
        assert not src_file.exists()
        assert (sandbox_root / "subdir" / "nested" / "file.txt").read_text() == "content"

    def test_move_destination_exists_raises(self, tmp_path):
        """FileSystemToolset.move() raises when destination exists."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        (sandbox_root / "src.txt").write_text("source", encoding="utf-8")
        (sandbox_root / "dst.txt").write_text("dest", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(FileExistsError, match="already exists"):
            sandbox.move("output/src.txt", "output/dst.txt")

    def test_move_readonly_source_raises(self, tmp_path):
        """FileSystemToolset.move() raises for read-only source."""
        input_root = tmp_path / "input"
        input_root.mkdir()
        (input_root / "file.txt").write_text("content", encoding="utf-8")

        output_root = tmp_path / "output"
        output_root.mkdir()

        config = SandboxConfig(
            paths={
                "input": PathConfig(root=str(input_root), mode="ro"),
                "output": PathConfig(root=str(output_root), mode="rw"),
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(PathNotWritableError, match="read-only"):
            sandbox.move("input/file.txt", "output/file.txt")


class TestSandboxCopy:
    """Tests for FileSystemToolset.copy() functionality."""

    def test_copy_duplicates_file(self, tmp_path):
        """FileSystemToolset.copy() copies a file."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        src_file = sandbox_root / "original.txt"
        src_file.write_text("content", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.copy("output/original.txt", "output/copy.txt")
        assert "Copied" in result
        assert src_file.exists()  # Source still exists
        assert (sandbox_root / "copy.txt").read_text() == "content"

    def test_copy_from_readonly_to_writable(self, tmp_path):
        """FileSystemToolset.copy() can copy from read-only to writable."""
        input_root = tmp_path / "input"
        input_root.mkdir()
        (input_root / "file.txt").write_text("content", encoding="utf-8")

        output_root = tmp_path / "output"
        output_root.mkdir()

        config = SandboxConfig(
            paths={
                "input": PathConfig(root=str(input_root), mode="ro"),
                "output": PathConfig(root=str(output_root), mode="rw"),
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.copy("input/file.txt", "output/file.txt")
        assert (output_root / "file.txt").read_text() == "content"

    def test_copy_creates_parent_directories(self, tmp_path):
        """FileSystemToolset.copy() creates parent directories for destination."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        (sandbox_root / "file.txt").write_text("content", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        result = sandbox.copy("output/file.txt", "output/deep/nested/copy.txt")
        assert (sandbox_root / "deep" / "nested" / "copy.txt").read_text() == "content"

    def test_copy_destination_exists_raises(self, tmp_path):
        """FileSystemToolset.copy() raises when destination exists."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()
        (sandbox_root / "src.txt").write_text("source", encoding="utf-8")
        (sandbox_root / "dst.txt").write_text("dest", encoding="utf-8")

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(FileExistsError, match="already exists"):
            sandbox.copy("output/src.txt", "output/dst.txt")

    def test_copy_to_readonly_raises(self, tmp_path):
        """FileSystemToolset.copy() raises for read-only destination."""
        input_root = tmp_path / "input"
        input_root.mkdir()
        (input_root / "file.txt").write_text("content", encoding="utf-8")

        readonly_root = tmp_path / "readonly"
        readonly_root.mkdir()

        config = SandboxConfig(
            paths={
                "input": PathConfig(root=str(input_root), mode="ro"),
                "readonly": PathConfig(root=str(readonly_root), mode="ro"),
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        with pytest.raises(PathNotWritableError, match="read-only"):
            sandbox.copy("input/file.txt", "readonly/file.txt")
