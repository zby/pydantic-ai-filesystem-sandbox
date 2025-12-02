"""Sandbox: Permission checking and path resolution with LLM-friendly errors.

This module provides the security boundary for filesystem access:
- PathConfig and SandboxConfig for configuration
- Sandbox class for permission checking and path resolution
- LLM-friendly error classes

The Sandbox is a pure policy/validation layer - it doesn't perform file I/O.
For file operations, use FileSystemToolset which wraps a Sandbox.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


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


class SandboxConfig(BaseModel):
    """Configuration for a sandbox."""

    paths: dict[str, PathConfig] = Field(
        default_factory=dict,
        description="Named paths with their configurations",
    )


# ---------------------------------------------------------------------------
# LLM-Friendly Errors
# ---------------------------------------------------------------------------


class SandboxError(Exception):
    """Base class for sandbox errors with LLM-friendly messages.

    All sandbox errors include guidance on what IS allowed,
    helping the LLM correct its behavior.
    """

    pass


class PathNotInSandboxError(SandboxError):
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


class PathNotWritableError(SandboxError):
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


class SuffixNotAllowedError(SandboxError):
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


class FileTooLargeError(SandboxError):
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


class EditError(SandboxError):
    """Raised when edit operation fails."""

    def __init__(self, path: str, reason: str, old_text: str):
        self.path = path
        self.reason = reason
        self.old_text = old_text
        # Show a preview of what was being searched for
        preview = old_text[:100] + "..." if len(old_text) > 100 else old_text
        preview = preview.replace("\n", "\\n")
        self.message = (
            f"Cannot edit '{path}': {reason}.\n"
            f"Searched for: {preview!r}"
        )
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Sandbox Implementation
# ---------------------------------------------------------------------------


class Sandbox:
    """Security boundary for file access validation.

    The Sandbox is responsible for:
    - Path resolution (relative → absolute within boundaries)
    - Permission checking (can_read, can_write)
    - Approval requirements (needs_read_approval, needs_write_approval)
    - Boundary enforcement (readable_roots, writable_roots)

    This is a pure policy/validation layer - it doesn't perform file I/O.
    For file operations, use FileSystemToolset which wraps a Sandbox.

    Example:
        config = SandboxConfig(paths={
            "input": PathConfig(root="./input", mode="ro"),
            "output": PathConfig(root="./output", mode="rw"),
        })
        sandbox = Sandbox(config)

        # Query permissions
        if sandbox.can_write("output/file.txt"):
            resolved = sandbox.resolve("output/file.txt")
            # ... perform write operation
    """

    def __init__(
        self,
        config: SandboxConfig,
        base_path: Optional[Path] = None,
    ):
        """Initialize the sandbox.

        Args:
            config: Sandbox configuration
            base_path: Base path for resolving relative roots (defaults to cwd)
        """
        self.config = config
        self._base_path = base_path or Path.cwd()
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

    # ---------------------------------------------------------------------------
    # Path Resolution
    # ---------------------------------------------------------------------------

    def resolve(self, path: str) -> Path:
        """Resolve path within sandbox boundaries.

        Args:
            path: Relative or absolute path to resolve

        Returns:
            Resolved absolute Path

        Raises:
            PathNotInSandboxError: If path is outside sandbox boundaries
        """
        _, resolved, _ = self._find_path_for(path)
        return resolved

    def _find_path_for(self, path: str) -> tuple[str, Path, PathConfig]:
        """Find which sandbox path contains the given path.

        Args:
            path: Path to look up (can be "sandbox_name", "sandbox_name/relative" or absolute)

        Returns:
            Tuple of (sandbox_name, resolved_path, path_config)

        Raises:
            PathNotInSandboxError: If path is not in any sandbox
        """
        # Handle bare sandbox name (e.g., "output" -> returns sandbox root)
        if path in self._paths:
            root, config = self._paths[path]
            return (path, root, config)

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
            raise PathNotInSandboxError(relative, self.readable_roots)
        return candidate

    # ---------------------------------------------------------------------------
    # Permission Checking
    # ---------------------------------------------------------------------------

    def can_read(self, path: str) -> bool:
        """Check if path is readable within sandbox boundaries."""
        try:
            self._find_path_for(path)
            return True
        except SandboxError:
            return False

    def can_write(self, path: str) -> bool:
        """Check if path is writable within sandbox boundaries."""
        try:
            _, _, config = self._find_path_for(path)
            return config.mode == "rw"
        except SandboxError:
            return False

    def needs_read_approval(self, path: str) -> bool:
        """Check if reading this path requires approval."""
        try:
            _, _, config = self._find_path_for(path)
            return config.read_approval
        except SandboxError:
            return False

    def needs_write_approval(self, path: str) -> bool:
        """Check if writing this path requires approval."""
        try:
            _, _, config = self._find_path_for(path)
            return config.write_approval
        except SandboxError:
            return False

    # ---------------------------------------------------------------------------
    # Boundary Info
    # ---------------------------------------------------------------------------

    @property
    def readable_roots(self) -> list[str]:
        """List of readable path roots (for error messages)."""
        return list(self._paths.keys())

    @property
    def writable_roots(self) -> list[str]:
        """List of writable path roots (for error messages)."""
        return [
            name
            for name, (_, config) in self._paths.items()
            if config.mode == "rw"
        ]

    # ---------------------------------------------------------------------------
    # Validation Helpers
    # ---------------------------------------------------------------------------

    def check_suffix(self, path: Path, config: PathConfig) -> None:
        """Check if file suffix is allowed.

        Raises:
            SuffixNotAllowedError: If suffix is not in allowed list
        """
        if config.suffixes is not None:
            suffix = path.suffix.lower()
            allowed = [s.lower() for s in config.suffixes]
            if suffix not in allowed:
                raise SuffixNotAllowedError(str(path), suffix, config.suffixes)

    def check_size(self, path: Path, config: PathConfig) -> None:
        """Check if file size is within limit.

        Raises:
            FileTooLargeError: If file exceeds size limit
        """
        if config.max_file_bytes is not None and path.exists():
            size = path.stat().st_size
            if size > config.max_file_bytes:
                raise FileTooLargeError(str(path), size, config.max_file_bytes)

    def get_path_config(self, path: str) -> tuple[str, Path, PathConfig]:
        """Get sandbox name, resolved path, and config for a path.

        This is useful for toolsets that need full path info.

        Args:
            path: Path to look up

        Returns:
            Tuple of (sandbox_name, resolved_path, path_config)

        Raises:
            PathNotInSandboxError: If path is not in any sandbox
        """
        return self._find_path_for(path)
