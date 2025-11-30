"""File sandbox implementation with LLM-friendly errors.

This module provides a standalone, reusable filesystem sandbox for PydanticAI:
- FileSandboxConfig and PathConfig for configuration
- FileSandboxError classes with LLM-friendly messages
- FileSandboxImpl implementation as a PydanticAI AbstractToolset
- Built-in approval checking support (optional, via ApprovalConfigurable protocol)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.tools import RunContext, ToolDefinition


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class PathConfig(BaseModel):
    """Configuration for a single path in the sandbox."""

    root: str = Field(description="Root directory path")
    mode: Literal["ro", "rw"] = Field(
        default="ro", description="Access mode: 'ro' (read-only) or 'rw' (read-write)"
    )
    suffixes: Optional[list[str]] = Field(
        default=None,
        description="Allowed file suffixes (e.g., ['.md', '.txt']). None means all allowed.",
    )
    max_file_bytes: Optional[int] = Field(
        default=None, description="Maximum file size in bytes. None means no limit."
    )
    # Approval settings
    write_approval: bool = Field(
        default=True,
        description="Whether writes to this path require approval",
    )
    read_approval: bool = Field(
        default=False,
        description="Whether reads from this path require approval",
    )


class FileSandboxConfig(BaseModel):
    """Configuration for a file sandbox."""

    paths: dict[str, PathConfig] = Field(
        default_factory=dict,
        description="Named paths with their configurations",
    )


# ---------------------------------------------------------------------------
# LLM-Friendly Errors
# ---------------------------------------------------------------------------


class FileSandboxError(Exception):
    """Base class for sandbox errors with LLM-friendly messages.

    All sandbox errors include guidance on what IS allowed,
    helping the LLM correct its behavior.
    """

    pass


class PathNotInSandboxError(FileSandboxError):
    """Raised when a path is outside all sandbox boundaries."""

    def __init__(self, path: str, readable_roots: list[str]):
        self.path = path
        self.readable_roots = readable_roots
        roots_str = ", ".join(readable_roots) if readable_roots else "none"
        self.message = (
            f"Cannot access '{path}': path is outside sandbox.\n"
            f"Readable paths: {roots_str}"
        )
        super().__init__(self.message)


class PathNotWritableError(FileSandboxError):
    """Raised when trying to write to a read-only path."""

    def __init__(self, path: str, writable_roots: list[str]):
        self.path = path
        self.writable_roots = writable_roots
        roots_str = ", ".join(writable_roots) if writable_roots else "none"
        self.message = (
            f"Cannot write to '{path}': path is read-only.\n"
            f"Writable paths: {roots_str}"
        )
        super().__init__(self.message)


class SuffixNotAllowedError(FileSandboxError):
    """Raised when file suffix is not in the allowed list."""

    def __init__(self, path: str, suffix: str, allowed: list[str]):
        self.path = path
        self.suffix = suffix
        self.allowed = allowed
        allowed_str = ", ".join(allowed) if allowed else "any"
        self.message = (
            f"Cannot access '{path}': suffix '{suffix}' not allowed.\n"
            f"Allowed suffixes: {allowed_str}"
        )
        super().__init__(self.message)


class FileTooLargeError(FileSandboxError):
    """Raised when file exceeds size limit."""

    def __init__(self, path: str, size: int, limit: int):
        self.path = path
        self.size = size
        self.limit = limit
        self.message = (
            f"Cannot read '{path}': file too large ({size:,} bytes).\n"
            f"Maximum allowed: {limit:,} bytes"
        )
        super().__init__(self.message)


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
# Implementation
# ---------------------------------------------------------------------------


class FileSandboxImpl(AbstractToolset[Any]):
    """File sandbox implementation as a PydanticAI AbstractToolset.

    Implements both the FileSandbox protocol and AbstractToolset interface.
    Provides read_file, write_file, and list_files tools.
    """

    def __init__(
        self,
        config: FileSandboxConfig,
        base_path: Optional[Path] = None,
        id: Optional[str] = None,
        max_retries: int = 1,
    ):
        """Initialize the file sandbox toolset.

        Args:
            config: Sandbox configuration
            base_path: Base path for resolving relative roots (defaults to cwd)
            id: Optional toolset ID for durable execution
            max_retries: Maximum number of retries for tool calls (default: 1)
        """
        self.config = config
        self._base_path = base_path or Path.cwd()
        self._toolset_id = id
        self._max_retries = max_retries
        self._paths: dict[str, tuple[Path, PathConfig]] = {}
        self._setup_paths()

    def _setup_paths(self) -> None:
        """Resolve and validate configured paths."""
        for name, path_config in self.config.paths.items():
            root = Path(path_config.root)
            if not root.is_absolute():
                root = (self._base_path / root).resolve()
            else:
                root = root.resolve()
            # Create directory if it doesn't exist
            root.mkdir(parents=True, exist_ok=True)
            self._paths[name] = (root, path_config)

    @property
    def readable_roots(self) -> list[str]:
        """List of readable path roots (for error messages)."""
        return [name for name in self._paths.keys()]

    @property
    def writable_roots(self) -> list[str]:
        """List of writable path roots (for error messages)."""
        return [
            name
            for name, (_, config) in self._paths.items()
            if config.mode == "rw"
        ]

    def _find_path_for(self, path: str) -> tuple[str, Path, PathConfig]:
        """Find which sandbox path contains the given path.

        Args:
            path: Path to look up (can be "sandbox_name/relative" or absolute)

        Returns:
            Tuple of (sandbox_name, resolved_path, path_config)

        Raises:
            PathNotInSandboxError: If path is not in any sandbox
        """
        # Handle "sandbox_name/relative/path" format
        if "/" in path and not path.startswith("/"):
            parts = path.split("/", 1)
            sandbox_name = parts[0]
            if sandbox_name in self._paths:
                root, config = self._paths[sandbox_name]
                relative = parts[1] if len(parts) > 1 else ""
                resolved = self._resolve_within(root, relative)
                return (sandbox_name, resolved, config)

        # Handle "sandbox_name:relative/path" format
        if ":" in path:
            parts = path.split(":", 1)
            sandbox_name = parts[0]
            if sandbox_name in self._paths:
                root, config = self._paths[sandbox_name]
                relative = parts[1].lstrip("/") if len(parts) > 1 else ""
                resolved = self._resolve_within(root, relative)
                return (sandbox_name, resolved, config)

        # Try to find path in any sandbox
        check_path = Path(path)
        if check_path.is_absolute():
            check_path = check_path.resolve()
            for name, (root, config) in self._paths.items():
                try:
                    check_path.relative_to(root)
                    return (name, check_path, config)
                except ValueError:
                    continue

        raise PathNotInSandboxError(path, self.readable_roots)

    def _resolve_within(self, root: Path, relative: str) -> Path:
        """Resolve a relative path within a root, preventing escapes.

        Args:
            root: The sandbox root directory
            relative: Relative path within the sandbox

        Returns:
            Resolved absolute path

        Raises:
            PathNotInSandboxError: If resolved path escapes the root
        """
        relative = relative.lstrip("/")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise PathNotInSandboxError(
                relative, self.readable_roots
            )
        return candidate

    def can_read(self, path: str) -> bool:
        """Check if path is readable within sandbox boundaries."""
        try:
            self._find_path_for(path)
            return True
        except FileSandboxError:
            return False

    def can_write(self, path: str) -> bool:
        """Check if path is writable within sandbox boundaries."""
        try:
            _, _, config = self._find_path_for(path)
            return config.mode == "rw"
        except FileSandboxError:
            return False

    def resolve(self, path: str) -> Path:
        """Resolve path within sandbox.

        Args:
            path: Relative or absolute path to resolve

        Returns:
            Resolved absolute Path

        Raises:
            PathNotInSandboxError: If path is outside sandbox boundaries
        """
        _, resolved, _ = self._find_path_for(path)
        return resolved

    def _check_suffix(self, path: Path, config: PathConfig) -> None:
        """Check if file suffix is allowed.

        Raises:
            SuffixNotAllowedError: If suffix is not in allowed list
        """
        if config.suffixes is not None:
            suffix = path.suffix.lower()
            allowed = [s.lower() for s in config.suffixes]
            if suffix not in allowed:
                raise SuffixNotAllowedError(str(path), suffix, config.suffixes)

    def _check_size(self, path: Path, config: PathConfig) -> None:
        """Check if file size is within limit.

        Raises:
            FileTooLargeError: If file exceeds size limit
        """
        if config.max_file_bytes is not None and path.exists():
            size = path.stat().st_size
            if size > config.max_file_bytes:
                raise FileTooLargeError(str(path), size, config.max_file_bytes)

    # ---------------------------------------------------------------------------
    # Approval Interface (ApprovalConfigurable protocol)
    # ---------------------------------------------------------------------------

    def needs_approval(
        self, tool_name: str, args: dict[str, Any]
    ) -> Union[bool, dict[str, Any]]:
        """Check if the tool call requires approval.

        Implements the ApprovalConfigurable protocol:
        - False: No approval needed
        - True: Approval needed with default presentation
        - dict: Approval needed with custom description

        Path validation is also performed here, raising PermissionError
        for blocked operations.

        Args:
            tool_name: Name of the tool being called
            args: Tool arguments

        Returns:
            False if no approval needed, or dict with description if needed

        Raises:
            PermissionError: If operation is blocked entirely (path not in sandbox, etc.)
        """
        path = args.get("path", "")

        if tool_name == "write_file":
            try:
                sandbox_name, resolved, config = self._find_path_for(path)
            except PathNotInSandboxError:
                raise PermissionError(f"Path not in any sandbox: {path}")

            if config.mode != "rw":
                raise PermissionError(f"Path is read-only: {path}")

            if not config.write_approval:
                return False

            # Approval needed - return custom description
            return {"description": f"Write to {sandbox_name}/{path}"}

        elif tool_name == "read_file":
            try:
                sandbox_name, resolved, config = self._find_path_for(path)
            except PathNotInSandboxError:
                raise PermissionError(f"Path not in any sandbox: {path}")

            if not config.read_approval:
                return False

            # Approval needed - return custom description
            return {"description": f"Read from {sandbox_name}/{path}"}

        # list_files doesn't require approval
        return False

    # ---------------------------------------------------------------------------
    # File Operations
    # ---------------------------------------------------------------------------

    def read(self, path: str, max_chars: int = DEFAULT_MAX_READ_CHARS, offset: int = 0) -> ReadResult:
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
        name, resolved, config = self._find_path_for(path)

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not resolved.is_file():
            raise IsADirectoryError(f"Not a file: {path}")

        self._check_suffix(resolved, config)
        self._check_size(resolved, config)

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
        name, resolved, config = self._find_path_for(path)

        if config.mode != "rw":
            raise PathNotWritableError(path, self.writable_roots)

        self._check_suffix(resolved, config)

        # Check content size against limit
        if config.max_file_bytes is not None:
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > config.max_file_bytes:
                raise FileTooLargeError(path, content_bytes, config.max_file_bytes)

        # Create parent directories if needed
        resolved.parent.mkdir(parents=True, exist_ok=True)

        resolved.write_text(content, encoding="utf-8")
        return f"Written {len(content)} characters to {name}/{resolved.relative_to(self._paths[name][0])}"

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
            for name, (root, _) in self._paths.items():
                for match in root.glob(pattern):
                    if match.is_file():
                        try:
                            rel = match.relative_to(root)
                            results.append(f"{name}/{rel}")
                        except ValueError:
                            continue
            return sorted(results)

        # Otherwise, find the specific path
        try:
            name, resolved, _ = self._find_path_for(path)
        except PathNotInSandboxError:
            # Path might be just a sandbox name
            if path in self._paths:
                name = path
                resolved, _ = self._paths[name]
            else:
                raise

        root, _ = self._paths[name]
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

        else:
            raise ValueError(f"Unknown tool: {name}")
