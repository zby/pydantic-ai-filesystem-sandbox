"""FileSystemToolset: File I/O tools for PydanticAI agents.

This module provides the FileSystemToolset, a PydanticAI AbstractToolset that
provides file operations (read, write, edit, list) within sandbox boundaries.

The toolset uses a Sandbox for permission checking and path resolution,
keeping concerns cleanly separated.

For approval integration, see approval_toolset.py which provides
ApprovableFileSystemToolset (requires pydantic-ai-blocking-approval).

Example:
    from pydantic_ai_filesystem_sandbox import FileSystemToolset, Sandbox, SandboxConfig, Mount

    # Create sandbox (policy layer)
    config = SandboxConfig(mounts=[
        Mount(host_path="./data", mount_point="/data", mode="rw"),
    ])
    sandbox = Sandbox(config)

    # Create toolset (file I/O layer)
    toolset = FileSystemToolset(sandbox)

    # Use with PydanticAI agent
    agent = Agent(..., toolsets=[toolset])
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, TypeAdapter
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.tools import RunContext, ToolDefinition

from .sandbox import (
    EditError,
    FileTooLargeError,
    Mount,
    PathNotInSandboxError,
    PathNotWritableError,
    Sandbox,
    SandboxConfig,
    SandboxError,
)


# ---------------------------------------------------------------------------
# Read Result
# ---------------------------------------------------------------------------


DEFAULT_MAX_READ_CHARS = 20_000
"""Default maximum characters to read from a file."""


class ReadResult(BaseModel):
    """Result of reading a file from the sandbox."""

    content: str = Field(description="The file content read")
    truncated: bool = Field(description="True if more content exists after this chunk")
    total_chars: int = Field(description="Total file size in characters")
    offset: int = Field(description="Starting character position used")
    chars_read: int = Field(description="Number of characters actually returned")


# ---------------------------------------------------------------------------
# Tool Argument Models
# ---------------------------------------------------------------------------


class ReadFileArgs(BaseModel):
    """Arguments for read_file tool."""

    path: str = Field(description="Virtual path to file (e.g., '/docs/file.txt')")
    max_chars: int = Field(
        default=DEFAULT_MAX_READ_CHARS,
        description=f"Maximum characters to read (default {DEFAULT_MAX_READ_CHARS:,})",
    )
    offset: int = Field(
        default=0,
        description="Character position to start reading from (default 0)",
    )


class WriteFileArgs(BaseModel):
    """Arguments for write_file tool."""

    path: str = Field(description="Virtual path to file (e.g., '/output/file.txt')")
    content: str = Field(description="Content to write to the file")


class ListFilesArgs(BaseModel):
    """Arguments for list_files tool."""

    path: str = Field(
        default="/",
        description="Virtual path to list (e.g., '/', '/docs', '/docs/sub'). Default: '/'",
    )
    pattern: str = Field(
        default="**/*",
        description="Glob pattern to match (default: '**/*')",
    )


class EditFileArgs(BaseModel):
    """Arguments for edit_file tool."""

    path: str = Field(description="Virtual path to file (e.g., '/output/file.txt')")
    old_text: str = Field(
        description="Exact text to find (must match exactly and be unique)"
    )
    new_text: str = Field(description="Text to replace old_text with")


class DeleteFileArgs(BaseModel):
    """Arguments for delete_file tool."""

    path: str = Field(description="Virtual path to file (e.g., '/output/file.txt')")


class MoveFileArgs(BaseModel):
    """Arguments for move_file tool."""

    source: str = Field(description="Source virtual path (e.g., '/output/old.txt')")
    destination: str = Field(
        description="Destination virtual path (e.g., '/output/new.txt')"
    )


class CopyFileArgs(BaseModel):
    """Arguments for copy_file tool."""

    source: str = Field(description="Source virtual path (e.g., '/input/file.txt')")
    destination: str = Field(
        description="Destination virtual path (e.g., '/output/file.txt')"
    )


# ---------------------------------------------------------------------------
# FileSystemToolset Implementation
# ---------------------------------------------------------------------------


class FileSystemToolset(AbstractToolset[Any]):
    """File I/O toolset for PydanticAI agents.

    Provides file operation tools: read_file, write_file, edit_file, delete_file,
    move_file, copy_file, and list_files.
    Uses a Sandbox for permission checking and path resolution.

    For approval integration, use ApprovableFileSystemToolset from
    the approval_toolset module (requires pydantic-ai-blocking-approval).

    Example:
        # Simple usage with default sandbox
        toolset = FileSystemToolset.create_default("./data")

        # Custom sandbox with mounts
        sandbox = Sandbox(SandboxConfig(mounts=[
            Mount(host_path="./input", mount_point="/input", mode="ro"),
            Mount(host_path="./output", mount_point="/output", mode="rw"),
        ]))
        toolset = FileSystemToolset(sandbox)
    """

    def __init__(
        self,
        sandbox: Sandbox,
        id: Optional[str] = None,
        max_retries: int = 1,
    ):
        """Initialize the file system toolset.

        Args:
            sandbox: Sandbox for permission checking and path resolution
            id: Optional toolset ID for durable execution
            max_retries: Maximum number of retries for tool calls (default: 1)
        """
        self._sandbox = sandbox
        self._toolset_id = id
        self._max_retries = max_retries

    @staticmethod
    def _format_result_path(mount_point: str, rel: str | Path) -> str:
        """Format a result path from mount point and relative path.

        Always returns paths in /mount/relative format.
        """
        rel_str = rel.as_posix() if isinstance(rel, Path) else str(rel)
        if mount_point == "/":
            if not rel_str or rel_str == ".":
                return "/"
            return f"/{rel_str.lstrip('/')}"
        if not rel_str or rel_str == ".":
            return mount_point
        return f"{mount_point}/{rel_str.lstrip('/')}"

    @classmethod
    def create_default(
        cls,
        root: str | Path,
        mode: Literal["ro", "rw"] = "rw",
        id: Optional[str] = None,
    ) -> "FileSystemToolset":
        """Create a toolset with a single root mount.

        Convenience factory for simple use cases.

        Args:
            root: Root directory for the sandbox
            mode: Access mode ("ro" or "rw", default "rw")
            id: Optional toolset ID

        Returns:
            FileSystemToolset with root mounted at "/"
        """
        config = SandboxConfig(
            mounts=[Mount(host_path=Path(root), mount_point="/", mode=mode)]
        )
        sandbox = Sandbox(config)
        return cls(sandbox, id=id)

    @property
    def sandbox(self) -> Sandbox:
        """Access the underlying sandbox for permission queries."""
        return self._sandbox

    # ---------------------------------------------------------------------------
    # File Operations
    # ---------------------------------------------------------------------------

    def read(
        self, path: str, max_chars: int = DEFAULT_MAX_READ_CHARS, offset: int = 0
    ) -> ReadResult:
        """Read text file from sandbox.

        Args:
            path: Virtual path to file (e.g., "/docs/file.txt")
            max_chars: Maximum characters to read (must be >= 0)
            offset: Character position to start reading from (must be >= 0, default: 0)

        Returns:
            ReadResult with content, truncation info, and metadata

        Raises:
            PathNotInSandboxError: If path outside sandbox
            SuffixNotAllowedError: If suffix not allowed
            FileTooLargeError: If file too large
            FileNotFoundError: If file doesn't exist
            ValueError: If offset or max_chars is negative
        """
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset}")
        if max_chars < 0:
            raise ValueError(f"max_chars must be >= 0, got {max_chars}")

        _, resolved, mount = self._sandbox.get_path_config(path, op="read")

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not resolved.is_file():
            raise IsADirectoryError(f"Not a file: {path}")

        self._sandbox.check_suffix(resolved, mount, virtual_path=path)
        self._sandbox.check_size(resolved, mount, virtual_path=path)

        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise SandboxError(
                f"Cannot read '{path}': file appears to be binary or not UTF-8 encoded.\n"
                "This tool only reads text files. For binary files, pass them as attachments."
            )
        total_chars = len(text)

        # Apply offset
        if offset > 0:
            text = text[offset:]

        # Apply max_chars limit
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]

        return ReadResult(
            content=text,
            truncated=truncated,
            total_chars=total_chars,
            offset=offset,
            chars_read=len(text),
        )

    def write(self, path: str, content: str) -> str:
        """Write text file to sandbox.

        Parent directories are created automatically if they don't exist.

        Args:
            path: Virtual path to file (e.g., "/output/file.txt")
            content: Content to write

        Returns:
            Confirmation message

        Raises:
            PathNotInSandboxError: If path outside sandbox
            PathNotWritableError: If path is read-only
            SuffixNotAllowedError: If suffix not allowed
        """
        _, resolved, mount = self._sandbox.get_path_config(path, op="write")

        self._sandbox.check_suffix(resolved, mount, virtual_path=path)

        # Check content size against limit
        if mount.max_file_bytes is not None:
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > mount.max_file_bytes:
                raise FileTooLargeError(path, content_bytes, mount.max_file_bytes)

        # Create parent directories if needed
        resolved.parent.mkdir(parents=True, exist_ok=True)

        resolved.write_text(content, encoding="utf-8")

        return f"Written {len(content)} characters to {path}"

    def edit(self, path: str, old_text: str, new_text: str) -> str:
        """Edit a file by replacing old_text with new_text.

        This is a search/replace operation that requires an exact match.
        The old_text must appear exactly once in the file.

        Args:
            path: Virtual path to file (e.g., "/output/file.txt")
            old_text: Exact text to find and replace
            new_text: Text to replace it with

        Returns:
            Confirmation message

        Raises:
            PathNotInSandboxError: If path outside sandbox
            PathNotWritableError: If path is read-only
            SuffixNotAllowedError: If suffix not allowed
            EditError: If old_text not found or found multiple times
            FileNotFoundError: If file doesn't exist
            FileTooLargeError: If edited content exceeds mount's max_file_bytes
        """
        _, resolved, mount = self._sandbox.get_path_config(path, op="write")

        self._sandbox.check_suffix(resolved, mount, virtual_path=path)

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        # Read current content
        content = resolved.read_text(encoding="utf-8")

        # Count occurrences
        count = content.count(old_text)

        if count == 0:
            raise EditError(path, "text not found in file", old_text)
        if count > 1:
            raise EditError(
                path, f"text found {count} times (must be unique)", old_text
            )

        # Perform the replacement
        new_content = content.replace(old_text, new_text, 1)

        # Check content size against limit
        if mount.max_file_bytes is not None:
            content_bytes = len(new_content.encode("utf-8"))
            if content_bytes > mount.max_file_bytes:
                raise FileTooLargeError(path, content_bytes, mount.max_file_bytes)

        resolved.write_text(new_content, encoding="utf-8")

        return f"Edited {path}: replaced {len(old_text)} chars with {len(new_text)} chars"

    def list_files(self, path: str = "/", pattern: str = "**/*") -> list[str]:
        """List files matching pattern within sandbox.

        Args:
            path: Virtual path to search from (e.g., "/", "/docs", "/docs/sub")
            pattern: Glob pattern to match

        Returns:
            List of matching file paths (as /mount/relative format)
        """
        # If path is "/" or "." or empty, list all mounts
        if path in ("/", ".", ""):
            results = []
            for mount_point in self._sandbox.readable_roots:
                root_path = self._sandbox.get_mount_root(mount_point)

                for match in root_path.glob(pattern):
                    if match.is_file():
                        try:
                            rel = match.relative_to(root_path)
                            result_path = self._format_result_path(mount_point, rel)
                            # Filter by read permission (respects derived sandbox allowlists)
                            if self._sandbox.can_read(result_path):
                                results.append(result_path)
                        except ValueError:
                            continue
            return sorted(results)

        # Get the resolved path and mount point
        mount_point, resolved, _ = self._sandbox.get_path_config(path, op="read")

        # Get mount root for relative path calculation (doesn't check allowlists)
        root = self._sandbox.get_mount_root(mount_point)

        results = []
        for match in resolved.glob(pattern):
            if match.is_file():
                try:
                    rel = match.relative_to(root)
                    result_path = self._format_result_path(mount_point, rel)
                    # Filter by read permission (respects derived sandbox allowlists)
                    if self._sandbox.can_read(result_path):
                        results.append(result_path)
                except ValueError:
                    continue
        return sorted(results)

    def delete(self, path: str) -> str:
        """Delete a file from the sandbox.

        Only files can be deleted; directories cannot be deleted with this method.

        Args:
            path: Virtual path to file (e.g., "/output/file.txt")

        Returns:
            Confirmation message

        Raises:
            PathNotInSandboxError: If path outside sandbox
            PathNotWritableError: If path is read-only
            FileNotFoundError: If file doesn't exist
            IsADirectoryError: If path is a directory
        """
        _, resolved, _ = self._sandbox.get_path_config(path, op="write")

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not resolved.is_file():
            raise IsADirectoryError(f"Cannot delete directory with delete_file: {path}")

        resolved.unlink()

        return f"Deleted {path}"

    def move(self, source: str, destination: str) -> str:
        """Move or rename a file within the sandbox.

        Parent directories of destination are created automatically.

        Args:
            source: Source virtual path (e.g., "/output/old.txt")
            destination: Destination virtual path (e.g., "/output/new.txt")

        Returns:
            Confirmation message

        Raises:
            PathNotInSandboxError: If path outside sandbox
            PathNotWritableError: If source or destination is read-only
            FileNotFoundError: If source doesn't exist
            FileExistsError: If destination already exists
        """
        # Check source
        _, src_resolved, src_mount_cfg = self._sandbox.get_path_config(
            source, op="write"
        )

        if not src_resolved.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        if not src_resolved.is_file():
            raise IsADirectoryError(f"Cannot move directory: {source}")

        self._sandbox.check_suffix(src_resolved, src_mount_cfg, virtual_path=source)

        # Check destination
        _, dst_resolved, dst_mount_cfg = self._sandbox.get_path_config(
            destination, op="write"
        )

        if dst_resolved.exists():
            raise FileExistsError(f"Destination already exists: {destination}")

        self._sandbox.check_suffix(dst_resolved, dst_mount_cfg, virtual_path=destination)

        # Create parent directories if needed
        dst_resolved.parent.mkdir(parents=True, exist_ok=True)

        # Move the file
        src_resolved.rename(dst_resolved)

        return f"Moved {source} to {destination}"

    def copy(self, source: str, destination: str) -> str:
        """Copy a file within the sandbox.

        Parent directories of destination are created automatically.

        Args:
            source: Source virtual path (e.g., "/input/file.txt")
            destination: Destination virtual path (e.g., "/output/file.txt")

        Returns:
            Confirmation message

        Raises:
            PathNotInSandboxError: If path outside sandbox
            PathNotWritableError: If destination is read-only
            FileNotFoundError: If source doesn't exist
            FileExistsError: If destination already exists
        """
        # Check source (only needs to be readable)
        _, src_resolved, src_mount_cfg = self._sandbox.get_path_config(
            source, op="read"
        )

        if not src_resolved.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        if not src_resolved.is_file():
            raise IsADirectoryError(f"Cannot copy directory: {source}")

        self._sandbox.check_suffix(src_resolved, src_mount_cfg, virtual_path=source)
        self._sandbox.check_size(src_resolved, src_mount_cfg, virtual_path=source)

        # Check destination
        _, dst_resolved, dst_mount_cfg = self._sandbox.get_path_config(
            destination, op="write"
        )

        if dst_resolved.exists():
            raise FileExistsError(f"Destination already exists: {destination}")

        self._sandbox.check_suffix(dst_resolved, dst_mount_cfg, virtual_path=destination)

        # Check size limit on destination
        if dst_mount_cfg.max_file_bytes is not None:
            src_size = src_resolved.stat().st_size
            if src_size > dst_mount_cfg.max_file_bytes:
                raise FileTooLargeError(destination, src_size, dst_mount_cfg.max_file_bytes)

        # Create parent directories if needed
        dst_resolved.parent.mkdir(parents=True, exist_ok=True)

        # Copy the file
        shutil.copy2(src_resolved, dst_resolved)

        return f"Copied {source} to {destination}"

    # ---------------------------------------------------------------------------
    # AbstractToolset Implementation
    # ---------------------------------------------------------------------------

    @property
    def id(self) -> str | None:
        """Unique identifier for this toolset."""
        return self._toolset_id

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        """Return the tools provided by this toolset."""
        tools = {}

        tools["read_file"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="read_file",
                description=(
                    "Read a text file from the sandbox. "
                    "Path format: '/mount/path' (e.g., '/docs/file.txt'). "
                    "Do not use this on binary files (PDFs, images, etc) - "
                    "pass them as attachments instead."
                ),
                parameters_json_schema=ReadFileArgs.model_json_schema(),
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(ReadFileArgs).validator,
        )

        tools["write_file"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="write_file",
                description=(
                    "Write a text file to the sandbox. "
                    "Parent directories are created automatically. "
                    "Path format: '/mount/path' (e.g., '/output/file.txt')."
                ),
                parameters_json_schema=WriteFileArgs.model_json_schema(),
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(WriteFileArgs).validator,
        )

        tools["list_files"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="list_files",
                description=(
                    "List files in the sandbox matching a glob pattern. "
                    "Path format: '/mount' or '/mount/subdir'. "
                    "Use '/' to list all mounts."
                ),
                parameters_json_schema=ListFilesArgs.model_json_schema(),
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(ListFilesArgs).validator,
        )

        tools["edit_file"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="edit_file",
                description=(
                    "Edit a file by replacing exact text. "
                    "The old_text must match exactly and appear only once. "
                    "Path format: '/mount/path' (e.g., '/output/file.txt')."
                ),
                parameters_json_schema=EditFileArgs.model_json_schema(),
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(EditFileArgs).validator,
        )

        tools["delete_file"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="delete_file",
                description=(
                    "Delete a file from the sandbox. "
                    "Path format: '/mount/path' (e.g., '/output/file.txt')."
                ),
                parameters_json_schema=DeleteFileArgs.model_json_schema(),
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(DeleteFileArgs).validator,
        )

        tools["move_file"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="move_file",
                description=(
                    "Move or rename a file within the sandbox. "
                    "Parent directories of destination are created automatically. "
                    "Path format: '/mount/path' (e.g., '/output/file.txt')."
                ),
                parameters_json_schema=MoveFileArgs.model_json_schema(),
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(MoveFileArgs).validator,
        )

        tools["copy_file"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="copy_file",
                description=(
                    "Copy a file within the sandbox. "
                    "Parent directories of destination are created automatically. "
                    "Path format: '/mount/path' (e.g., '/output/file.txt')."
                ),
                parameters_json_schema=CopyFileArgs.model_json_schema(),
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(CopyFileArgs).validator,
        )

        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: Any,
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        """Call a tool with the given arguments.

        Args:
            name: Tool name
            tool_args: Either a validated model instance or a dict (when called via ApprovalToolset)
            ctx: PydanticAI run context
            tool: ToolsetTool instance

        Note: Approval checking is handled by ApprovalToolset via needs_approval().
        This method just executes the operation.
        """
        if name == "read_file":
            args = tool_args if isinstance(tool_args, ReadFileArgs) else ReadFileArgs(**tool_args)
            return self.read(args.path, max_chars=args.max_chars, offset=args.offset)

        elif name == "write_file":
            args = tool_args if isinstance(tool_args, WriteFileArgs) else WriteFileArgs(**tool_args)
            return self.write(args.path, args.content)

        elif name == "list_files":
            args = tool_args if isinstance(tool_args, ListFilesArgs) else ListFilesArgs(**tool_args)
            return self.list_files(args.path, args.pattern)

        elif name == "edit_file":
            args = tool_args if isinstance(tool_args, EditFileArgs) else EditFileArgs(**tool_args)
            return self.edit(args.path, args.old_text, args.new_text)

        elif name == "delete_file":
            args = tool_args if isinstance(tool_args, DeleteFileArgs) else DeleteFileArgs(**tool_args)
            return self.delete(args.path)

        elif name == "move_file":
            args = tool_args if isinstance(tool_args, MoveFileArgs) else MoveFileArgs(**tool_args)
            return self.move(args.source, args.destination)

        elif name == "copy_file":
            args = tool_args if isinstance(tool_args, CopyFileArgs) else CopyFileArgs(**tool_args)
            return self.copy(args.source, args.destination)

        else:
            raise ValueError(f"Unknown tool: {name}")
