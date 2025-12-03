"""FileSystemToolset: File I/O tools for PydanticAI agents.

This module provides the FileSystemToolset, a PydanticAI AbstractToolset that
provides file operations (read, write, edit, list) within sandbox boundaries.

The toolset uses a Sandbox for permission checking and path resolution,
keeping concerns cleanly separated.

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

    # Or wrap with approval
    from pydantic_ai_blocking_approval import ApprovalToolset
    approved = ApprovalToolset(inner=toolset, approval_callback=my_callback)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, TypeAdapter
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai_blocking_approval import ApprovalResult

from .sandbox import (
    EditError,
    FileTooLargeError,
    PathConfig,
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

    Provides read_file, write_file, edit_file, and list_files tools.
    Uses a Sandbox for permission checking and path resolution.

    Implements needs_approval() for use with ApprovalToolset from
    pydantic-ai-blocking-approval.

    Example:
        # Simple usage with default sandbox
        toolset = FileSystemToolset.create_default("./data")

        # Custom sandbox
        sandbox = Sandbox(SandboxConfig(paths={
            "input": PathConfig(root="./input", mode="ro"),
            "output": PathConfig(root="./output", mode="rw"),
        }))
        toolset = FileSystemToolset(sandbox)

        # With approval
        from pydantic_ai_blocking_approval import ApprovalToolset
        approved = ApprovalToolset(inner=toolset, approval_callback=cb)
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

    @classmethod
    def create_default(
        cls,
        root: str | Path,
        mode: str = "rw",
        id: Optional[str] = None,
    ) -> "FileSystemToolset":
        """Create a toolset with a single default sandbox path.

        Convenience factory for simple use cases.

        Args:
            root: Root directory for the sandbox
            mode: Access mode ("ro" or "rw", default "rw")
            id: Optional toolset ID

        Returns:
            FileSystemToolset with a single "data" path
        """
        config = SandboxConfig(
            paths={"data": PathConfig(root=str(root), mode=mode)}  # type: ignore
        )
        sandbox = Sandbox(config)
        return cls(sandbox, id=id)

    @property
    def sandbox(self) -> Sandbox:
        """Access the underlying sandbox for permission queries."""
        return self._sandbox

    # ---------------------------------------------------------------------------
    # Approval Protocol (for ApprovalToolset)
    # ---------------------------------------------------------------------------

    def needs_approval(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any]
    ) -> ApprovalResult:
        """Check if the tool call requires approval.

        Called by ApprovalToolset to decide if approval is needed.

        Args:
            name: Tool name being called
            tool_args: Arguments passed to the tool
            ctx: PydanticAI run context (includes deps, model, usage, etc.)

        Returns:
            ApprovalResult with status: blocked, pre_approved, or needs_approval
        """
        path = tool_args.get("path", "")

        if name == "write_file":
            try:
                sandbox_name, resolved, config = self._sandbox.get_path_config(path)
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Path not in any sandbox: {path}")

            if config.mode != "rw":
                return ApprovalResult.blocked(f"Path is read-only: {path}")

            if not config.write_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

        elif name == "read_file":
            try:
                sandbox_name, resolved, config = self._sandbox.get_path_config(path)
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Path not in any sandbox: {path}")

            if not config.read_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

        elif name == "edit_file":
            try:
                sandbox_name, resolved, config = self._sandbox.get_path_config(path)
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Path not in any sandbox: {path}")

            if config.mode != "rw":
                return ApprovalResult.blocked(f"Path is read-only: {path}")

            if not config.write_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

        elif name == "list_files":
            return ApprovalResult.pre_approved()

        # Unknown tool - require approval
        return ApprovalResult.needs_approval()

    def get_approval_description(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any]
    ) -> str:
        """Return human-readable description for approval prompt.

        Called by ApprovalToolset when needs_approval() returns needs_approval.

        Args:
            name: Tool name being called
            tool_args: Arguments passed to the tool
            ctx: PydanticAI run context

        Returns:
            Description string to show user
        """
        path = tool_args.get("path", "")

        # Get sandbox name for display
        try:
            sandbox_name, _, _ = self._sandbox.get_path_config(path)
            display_path = f"{sandbox_name}/{path}"
        except PathNotInSandboxError:
            display_path = path

        if name == "write_file":
            content = tool_args.get("content", "")
            char_count = len(content)
            return f"Write {char_count} chars to {display_path}"

        elif name == "read_file":
            return f"Read from {display_path}"

        elif name == "edit_file":
            old_text = tool_args.get("old_text", "")
            new_text = tool_args.get("new_text", "")
            return f"Edit {display_path}: replace {len(old_text)} chars with {len(new_text)} chars"

        elif name == "list_files":
            pattern = tool_args.get("pattern", "**/*")
            return f"List files in {display_path} matching {pattern}"

        return f"{name}({path})"

    # ---------------------------------------------------------------------------
    # File Operations
    # ---------------------------------------------------------------------------

    def read(
        self, path: str, max_chars: int = DEFAULT_MAX_READ_CHARS, offset: int = 0
    ) -> ReadResult:
        """Read text file from sandbox.

        Args:
            path: Path to file (relative to sandbox)
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
        name, resolved, config = self._sandbox.get_path_config(path)

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not resolved.is_file():
            raise IsADirectoryError(f"Not a file: {path}")

        self._sandbox.check_suffix(resolved, config)
        self._sandbox.check_size(resolved, config)

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

        Args:
            path: Path to file (relative to sandbox)
            content: Content to write

        Returns:
            Confirmation message

        Raises:
            PathNotInSandboxError: If path outside sandbox
            PathNotWritableError: If path is read-only
            SuffixNotAllowedError: If suffix not allowed
        """
        name, resolved, config = self._sandbox.get_path_config(path)

        if config.mode != "rw":
            raise PathNotWritableError(path, self._sandbox.writable_roots)

        self._sandbox.check_suffix(resolved, config)

        # Check content size against limit
        if config.max_file_bytes is not None:
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > config.max_file_bytes:
                raise FileTooLargeError(path, content_bytes, config.max_file_bytes)

        # Create parent directories if needed
        resolved.parent.mkdir(parents=True, exist_ok=True)

        resolved.write_text(content, encoding="utf-8")

        # Get sandbox root for relative path calculation
        sandbox_root = self._sandbox.resolve(name)
        try:
            rel_path = resolved.relative_to(sandbox_root)
        except ValueError:
            rel_path = resolved.name

        return f"Written {len(content)} characters to {name}/{rel_path}"

    def edit(self, path: str, old_text: str, new_text: str) -> str:
        """Edit a file by replacing old_text with new_text.

        This is a search/replace operation that requires an exact match.
        The old_text must appear exactly once in the file.

        Args:
            path: Path to file (relative to sandbox)
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
        name, resolved, config = self._sandbox.get_path_config(path)

        if config.mode != "rw":
            raise PathNotWritableError(path, self._sandbox.writable_roots)

        self._sandbox.check_suffix(resolved, config)

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
        if config.max_file_bytes is not None:
            content_bytes = len(new_content.encode("utf-8"))
            if content_bytes > config.max_file_bytes:
                raise FileTooLargeError(path, content_bytes, config.max_file_bytes)

        resolved.write_text(new_content, encoding="utf-8")

        # Get sandbox root for relative path calculation
        sandbox_root = self._sandbox.resolve(name)
        try:
            rel_path = resolved.relative_to(sandbox_root)
        except ValueError:
            rel_path = resolved.name

        return f"Edited {name}/{rel_path}: replaced {len(old_text)} chars with {len(new_text)} chars"

    def list_files(self, path: str = ".", pattern: str = "**/*") -> list[str]:
        """List files matching pattern within sandbox.

        Args:
            path: Base path to search from (sandbox name or sandbox_name/subdir)
            pattern: Glob pattern to match

        Returns:
            List of matching file paths (as sandbox_name/relative format)
        """
        # If path is "." or empty, list all sandboxes
        if path in (".", ""):
            results = []
            for name in self._sandbox.readable_roots:
                root_path = self._sandbox.resolve(name)

                for match in root_path.glob(pattern):
                    if match.is_file():
                        try:
                            rel = match.relative_to(root_path)
                            results.append(f"{name}/{rel}")
                        except ValueError:
                            continue
            return sorted(results)

        # Get the resolved path and sandbox name
        name, resolved, _ = self._sandbox.get_path_config(path)

        # Get root for this sandbox
        root = self._sandbox.resolve(name)

        results = []
        for match in resolved.glob(pattern):
            if match.is_file():
                try:
                    rel = match.relative_to(root)
                    results.append(f"{name}/{rel}")
                except ValueError:
                    continue
        return sorted(results)

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
                    "description": "Path format: 'sandbox_name/relative/path'",
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
                    "description": "Path format: 'sandbox_name/relative/path'",
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
                    "default": ".",
                    "description": "Path format: 'sandbox_name' or 'sandbox_name/subdir' (default: '.')",
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
                    "description": "Path format: 'sandbox_name/relative/path'",
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

        # Create ToolsetTool instances
        tools["read_file"] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(
                name="read_file",
                description=(
                    "Read a text file from the sandbox. "
                    "Path format: 'sandbox_name/relative/path'. "
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
                    "Path format: 'sandbox_name/relative/path'."
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
                    "Path format: 'sandbox_name' or 'sandbox_name/subdir'. "
                    "Use '.' to list all sandboxes."
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
                    "Path format: 'sandbox_name/relative/path'."
                ),
                parameters_json_schema=edit_file_schema,
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
            path = tool_args.get("path", ".")
            pattern = tool_args.get("pattern", "**/*")
            return self.list_files(path, pattern)

        elif name == "edit_file":
            path = tool_args["path"]
            old_text = tool_args["old_text"]
            new_text = tool_args["new_text"]
            return self.edit(path, old_text, new_text)

        else:
            raise ValueError(f"Unknown tool: {name}")
