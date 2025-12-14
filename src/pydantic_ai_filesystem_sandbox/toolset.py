"""FileSystemToolset: File I/O tools for PydanticAI agents.

This module provides the FileSystemToolset, a PydanticAI AbstractToolset that
provides file operations (read, write, edit, list) within sandbox boundaries.

The toolset uses a Sandbox for permission checking and path resolution,
keeping concerns cleanly separated.

For approval integration, see approval_toolset.py which provides
ApprovableFileSystemToolset (requires pydantic-ai-blocking-approval).

Example:
    from pydantic_ai_filesystem_sandbox import FileSystemToolset, Sandbox, SandboxConfig, PathConfig

    # Create sandbox (policy layer)
    config = SandboxConfig(paths={
        "data": PathConfig(root="./data", mode="rw"),
    })
    sandbox = Sandbox(config)

    # Create toolset (file I/O layer)
    toolset = FileSystemToolset(sandbox)

    # Use with PydanticAI agent
    agent = Agent(..., toolsets=[toolset])
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

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

        # Custom sandbox
        sandbox = Sandbox(SandboxConfig(paths={
            "input": PathConfig(root="./input", mode="ro"),
            "output": PathConfig(root="./output", mode="rw"),
        }))
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
        mode: str = "rw",
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
            mounts=[Mount(host_path=Path(root), mount_point="/", mode=mode)]  # type: ignore
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
            max_chars: Maximum characters to read
            offset: Character position to start reading from (default: 0)

        Returns:
            ReadResult with content, truncation info, and metadata

        Raises:
            PathNotInSandboxError: If path outside sandbox
            SuffixNotAllowedError: If suffix not allowed
            FileTooLargeError: If file too large
            FileNotFoundError: If file doesn't exist
        """
        mount_point, resolved, mount = self._sandbox.get_path_config(path)

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not resolved.is_file():
            raise IsADirectoryError(f"Not a file: {path}")

        self._sandbox.check_suffix(resolved, mount)
        self._sandbox.check_size(resolved, mount)

        text = resolved.read_text(encoding="utf-8")
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
        mount_point, resolved, mount = self._sandbox.get_path_config(path)

        if mount.mode != "rw":
            raise PathNotWritableError(path, self._sandbox.writable_roots)

        self._sandbox.check_suffix(resolved, mount)

        # Check content size against limit
        if mount.max_file_bytes is not None:
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > mount.max_file_bytes:
                raise FileTooLargeError(path, content_bytes, mount.max_file_bytes)

        # Create parent directories if needed
        resolved.parent.mkdir(parents=True, exist_ok=True)

        resolved.write_text(content, encoding="utf-8")

        # Get mount root for relative path calculation
        mount_root = self._sandbox.resolve(mount_point)
        try:
            rel_path = resolved.relative_to(mount_root)
        except ValueError:
            rel_path = resolved.name

        display_path = self._format_result_path(mount_point, rel_path)
        return f"Written {len(content)} characters to {display_path}"

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
        """
        mount_point, resolved, mount = self._sandbox.get_path_config(path)

        if mount.mode != "rw":
            raise PathNotWritableError(path, self._sandbox.writable_roots)

        self._sandbox.check_suffix(resolved, mount)

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

        # Get mount root for relative path calculation
        mount_root = self._sandbox.resolve(mount_point)
        try:
            rel_path = resolved.relative_to(mount_root)
        except ValueError:
            rel_path = resolved.name

        display_path = self._format_result_path(mount_point, rel_path)
        return f"Edited {display_path}: replaced {len(old_text)} chars with {len(new_text)} chars"

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
                root_path = self._sandbox.resolve(mount_point)

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
        mount_point, resolved, _ = self._sandbox.get_path_config(path)

        # Get root for this mount
        root = self._sandbox.resolve(mount_point)

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

        Args:
            path: Virtual path to file (e.g., "/output/file.txt")

        Returns:
            Confirmation message

        Raises:
            PathNotInSandboxError: If path outside sandbox
            PathNotWritableError: If path is read-only
            FileNotFoundError: If file doesn't exist
        """
        mount_point, resolved, mount = self._sandbox.get_path_config(path)

        if mount.mode != "rw":
            raise PathNotWritableError(path, self._sandbox.writable_roots)

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not resolved.is_file():
            raise IsADirectoryError(f"Cannot delete directory with delete_file: {path}")

        resolved.unlink()

        # Get mount root for relative path calculation
        mount_root = self._sandbox.resolve(mount_point)
        try:
            rel_path = resolved.relative_to(mount_root)
        except ValueError:
            rel_path = resolved.name

        display_path = self._format_result_path(mount_point, rel_path)
        return f"Deleted {display_path}"

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
        src_mount, src_resolved, src_mount_cfg = self._sandbox.get_path_config(source)

        if src_mount_cfg.mode != "rw":
            raise PathNotWritableError(source, self._sandbox.writable_roots)

        if not src_resolved.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        if not src_resolved.is_file():
            raise IsADirectoryError(f"Cannot move directory: {source}")

        self._sandbox.check_suffix(src_resolved, src_mount_cfg)

        # Check destination
        dst_mount, dst_resolved, dst_mount_cfg = self._sandbox.get_path_config(destination)

        if dst_mount_cfg.mode != "rw":
            raise PathNotWritableError(destination, self._sandbox.writable_roots)

        if dst_resolved.exists():
            raise FileExistsError(f"Destination already exists: {destination}")

        self._sandbox.check_suffix(dst_resolved, dst_mount_cfg)

        # Create parent directories if needed
        dst_resolved.parent.mkdir(parents=True, exist_ok=True)

        # Move the file
        src_resolved.rename(dst_resolved)

        # Compute relative paths from mount roots for display
        src_root = self._sandbox.resolve(src_mount)
        dst_root = self._sandbox.resolve(dst_mount)
        try:
            src_rel = src_resolved.relative_to(src_root)
        except ValueError:
            src_rel = src_resolved.name
        try:
            dst_rel = dst_resolved.relative_to(dst_root)
        except ValueError:
            dst_rel = dst_resolved.name

        src_display = self._format_result_path(src_mount, src_rel)
        dst_display = self._format_result_path(dst_mount, dst_rel)
        return f"Moved {src_display} to {dst_display}"

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
        import shutil

        # Check source (only needs to be readable)
        src_mount, src_resolved, src_mount_cfg = self._sandbox.get_path_config(source)

        if not src_resolved.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        if not src_resolved.is_file():
            raise IsADirectoryError(f"Cannot copy directory: {source}")

        self._sandbox.check_suffix(src_resolved, src_mount_cfg)
        self._sandbox.check_size(src_resolved, src_mount_cfg)

        # Check destination
        dst_mount, dst_resolved, dst_mount_cfg = self._sandbox.get_path_config(destination)

        if dst_mount_cfg.mode != "rw":
            raise PathNotWritableError(destination, self._sandbox.writable_roots)

        if dst_resolved.exists():
            raise FileExistsError(f"Destination already exists: {destination}")

        self._sandbox.check_suffix(dst_resolved, dst_mount_cfg)

        # Check size limit on destination
        if dst_mount_cfg.max_file_bytes is not None:
            src_size = src_resolved.stat().st_size
            if src_size > dst_mount_cfg.max_file_bytes:
                raise FileTooLargeError(destination, src_size, dst_mount_cfg.max_file_bytes)

        # Create parent directories if needed
        dst_resolved.parent.mkdir(parents=True, exist_ok=True)

        # Copy the file
        shutil.copy2(src_resolved, dst_resolved)

        # Compute relative paths from mount roots for display
        src_root = self._sandbox.resolve(src_mount)
        dst_root = self._sandbox.resolve(dst_mount)
        try:
            src_rel = src_resolved.relative_to(src_root)
        except ValueError:
            src_rel = src_resolved.name
        try:
            dst_rel = dst_resolved.relative_to(dst_root)
        except ValueError:
            dst_rel = dst_resolved.name

        src_display = self._format_result_path(src_mount, src_rel)
        dst_display = self._format_result_path(dst_mount, dst_rel)
        return f"Copied {src_display} to {dst_display}"

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

        # Define tool schemas
        read_file_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Virtual path to file (e.g., '/docs/file.txt')",
                },
                "max_chars": {
                    "type": "integer",
                    "default": DEFAULT_MAX_READ_CHARS,
                    "description": f"Maximum characters to read (default {DEFAULT_MAX_READ_CHARS:,})",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Character position to start reading from (default 0)",
                },
            },
            "required": ["path"],
        }

        write_file_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Virtual path to file (e.g., '/output/file.txt')",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        }

        list_files_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "default": "/",
                    "description": "Virtual path to list (e.g., '/', '/docs', '/docs/sub'). Default: '/'",
                },
                "pattern": {
                    "type": "string",
                    "default": "**/*",
                    "description": "Glob pattern to match (default: '**/*')",
                },
            },
        }

        edit_file_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Virtual path to file (e.g., '/output/file.txt')",
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text to find (must match exactly and be unique)",
                },
                "new_text": {
                    "type": "string",
                    "description": "Text to replace old_text with",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }

        delete_file_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Virtual path to file (e.g., '/output/file.txt')",
                },
            },
            "required": ["path"],
        }

        move_file_schema = {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source virtual path (e.g., '/output/old.txt')",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination virtual path (e.g., '/output/new.txt')",
                },
            },
            "required": ["source", "destination"],
        }

        copy_file_schema = {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source virtual path (e.g., '/input/file.txt')",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination virtual path (e.g., '/output/file.txt')",
                },
            },
            "required": ["source", "destination"],
        }

        # Create ToolsetTool instances
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
                parameters_json_schema=read_file_schema,
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(dict[str, Any]).validator,
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
                parameters_json_schema=write_file_schema,
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(dict[str, Any]).validator,
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
                parameters_json_schema=list_files_schema,
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(dict[str, Any]).validator,
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
                parameters_json_schema=edit_file_schema,
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(dict[str, Any]).validator,
        )

        tools["delete_file"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="delete_file",
                description=(
                    "Delete a file from the sandbox. "
                    "Path format: '/mount/path' (e.g., '/output/file.txt')."
                ),
                parameters_json_schema=delete_file_schema,
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(dict[str, Any]).validator,
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
                parameters_json_schema=move_file_schema,
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(dict[str, Any]).validator,
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
                parameters_json_schema=copy_file_schema,
            ),
            max_retries=self._max_retries,
            args_validator=TypeAdapter(dict[str, Any]).validator,
        )

        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> Any:
        """Call a tool with the given arguments.

        Note: Approval checking is handled by ApprovalToolset via needs_approval().
        This method just executes the operation.
        """
        if name == "read_file":
            path = tool_args["path"]
            max_chars = tool_args.get("max_chars", DEFAULT_MAX_READ_CHARS)
            offset = tool_args.get("offset", 0)
            return self.read(path, max_chars=max_chars, offset=offset)

        elif name == "write_file":
            path = tool_args["path"]
            content = tool_args["content"]
            return self.write(path, content)

        elif name == "list_files":
            path = tool_args.get("path", "/")
            pattern = tool_args.get("pattern", "**/*")
            return self.list_files(path, pattern)

        elif name == "edit_file":
            path = tool_args["path"]
            old_text = tool_args["old_text"]
            new_text = tool_args["new_text"]
            return self.edit(path, old_text, new_text)

        elif name == "delete_file":
            path = tool_args["path"]
            return self.delete(path)

        elif name == "move_file":
            source = tool_args["source"]
            destination = tool_args["destination"]
            return self.move(source, destination)

        elif name == "copy_file":
            source = tool_args["source"]
            destination = tool_args["destination"]
            return self.copy(source, destination)

        else:
            raise ValueError(f"Unknown tool: {name}")
