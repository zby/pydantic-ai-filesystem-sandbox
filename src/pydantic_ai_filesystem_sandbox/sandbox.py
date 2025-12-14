"""Sandbox: Permission checking and path resolution with LLM-friendly errors.

This module provides the security boundary for filesystem access:
- PathConfig and SandboxConfig for configuration
- Sandbox class for permission checking and path resolution
- LLM-friendly error classes

The Sandbox is a pure policy/validation layer - it doesn't perform file I/O.
For file operations, use FileSystemToolset which wraps a Sandbox.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


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


class RootSandboxConfig(BaseModel):
    """Configuration for a single-root sandbox.

    The host directory defined by `root` becomes the virtual `/`.
    """

    root: Path = Field(description="Root directory path for virtual '/'")
    readonly: bool = Field(
        default=False, description="If true, no writes anywhere in the sandbox"
    )
    suffixes: Optional[list[str]] = Field(
        default=None,
        description="Allowed file suffixes (e.g., ['.md', '.txt']). None means all allowed.",
    )
    max_file_bytes: Optional[int] = Field(
        default=None, description="Maximum file size in bytes. None means no limit."
    )


class SandboxConfig(BaseModel):
    """Configuration for a sandbox."""

    root: Optional[RootSandboxConfig] = Field(
        default=None,
        description="Single-root sandbox configuration (virtual '/')",
    )
    paths: Optional[dict[str, PathConfig]] = Field(
        default=None,
        description="Named paths with their configurations",
    )

    @model_validator(mode="after")
    def _xor_root_paths(self) -> "SandboxConfig":
        if self.root is None and self.paths is None:
            raise ValueError("SandboxConfig requires exactly one of 'root' or 'paths'.")
        if self.root is not None and self.paths is not None:
            raise ValueError("SandboxConfig cannot set both 'root' and 'paths'.")
        return self


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
        *,
        _parent: Optional["Sandbox"] = None,
        _allowed_read: Optional[list[tuple[str, Path]]] = None,
        _allowed_write: Optional[list[tuple[str, Path]]] = None,
        _readable_root_labels: Optional[list[str]] = None,
        _writable_root_labels: Optional[list[str]] = None,
    ):
        """Initialize the sandbox.

        Args:
            config: Sandbox configuration
            base_path: Base path for resolving relative roots (defaults to cwd)
        """
        self.config = config
        self._base_path = base_path or Path.cwd()
        self._paths: dict[str, tuple[Path, PathConfig]] = {}
        self._is_root_mode: bool = False
        self._root_path: Optional[Path] = None
        self._root_path_config: Optional[PathConfig] = None

        self._parent: Optional[Sandbox] = _parent
        self._allowed_read: Optional[list[tuple[str, Path]]] = _allowed_read
        self._allowed_write: Optional[list[tuple[str, Path]]] = _allowed_write
        self._readable_root_labels: Optional[list[str]] = _readable_root_labels
        self._writable_root_labels: Optional[list[str]] = _writable_root_labels

        if self._parent is None:
            self._setup_paths()

    def _setup_paths(self) -> None:
        """Resolve and validate configured paths."""
        if self.config.root is not None:
            self._is_root_mode = True
            root_cfg = self.config.root
            root_path = Path(root_cfg.root)
            if not root_path.is_absolute():
                root_path = (self._base_path / root_path).resolve()
            else:
                root_path = root_path.resolve()
            root_path.mkdir(parents=True, exist_ok=True)
            mode: Literal["ro", "rw"] = "ro" if root_cfg.readonly else "rw"
            # Root-mode uses PathConfig defaults for approvals.
            path_cfg = PathConfig(
                root=str(root_path),
                mode=mode,
                suffixes=root_cfg.suffixes,
                max_file_bytes=root_cfg.max_file_bytes,
            )
            self._root_path = root_path
            self._root_path_config = path_cfg
            self._paths["/"] = (root_path, path_cfg)
            return

        paths = self.config.paths or {}
        for name, path_config in paths.items():
            root = Path(path_config.root)
            if not root.is_absolute():
                root = (self._base_path / root).resolve()
            else:
                root = root.resolve()
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
        _, resolved, _ = self.get_path_config(path)
        return resolved

    def _find_path_for_boundary(self, path: str) -> tuple[str, Path, PathConfig]:
        """Resolve a path to its sandbox root without applying allowlists."""
        if self._parent is not None:
            return self._parent._find_path_for_boundary(path)

        if self._is_root_mode:
            if self._root_path is None or self._root_path_config is None:
                raise RuntimeError("Root sandbox not initialized.")
            return self._find_root_mode_boundary(path)

        return self._find_multi_path_boundary(path)

    def _find_multi_path_boundary(self, path: str) -> tuple[str, Path, PathConfig]:
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

    def _find_root_mode_boundary(self, path: str) -> tuple[str, Path, PathConfig]:
        """Resolve a sandbox path inside a root-mode sandbox."""
        assert self._root_path is not None
        assert self._root_path_config is not None

        normalized = path.replace("\\", "/").strip()
        if not normalized:
            normalized = "/"

        if normalized.startswith("~") or ":" in normalized:
            raise PathNotInSandboxError(path, self.readable_roots)
        if re.match(r"^[A-Za-z]:", normalized):
            raise PathNotInSandboxError(path, self.readable_roots)

        # Disallow traversal segments entirely in root-mode.
        parts = Path(normalized.lstrip("/")).parts
        if ".." in parts:
            raise PathNotInSandboxError(path, self.readable_roots)

        relative = normalized.lstrip("/")
        resolved = self._resolve_within(self._root_path, relative)
        return ("/", resolved, self._root_path_config)

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
            name, resolved, _ = self._find_path_for_boundary(path)
        except SandboxError:
            return False

        if self._parent is not None and not self._parent.can_read(path):
            return False

        return self._is_allowed_for_read(name, resolved)

    def can_write(self, path: str) -> bool:
        """Check if path is writable within sandbox boundaries."""
        try:
            name, resolved, config = self._find_path_for_boundary(path)
        except SandboxError:
            return False

        if config.mode != "rw":
            return False
        if self._parent is not None and not self._parent.can_write(path):
            return False

        return self._is_allowed_for_write(name, resolved)

    def needs_read_approval(self, path: str) -> bool:
        """Check if reading this path requires approval."""
        try:
            _, _, config = self.get_path_config(path)
        except SandboxError:
            return False
        return config.read_approval if self.can_read(path) else False

    def needs_write_approval(self, path: str) -> bool:
        """Check if writing this path requires approval."""
        try:
            _, _, config = self.get_path_config(path)
        except SandboxError:
            return False
        return config.write_approval if self.can_write(path) else False

    # ---------------------------------------------------------------------------
    # Boundary Info
    # ---------------------------------------------------------------------------

    @property
    def readable_roots(self) -> list[str]:
        """List of readable path roots (for error messages)."""
        if self._parent is not None:
            if self._readable_root_labels is None:
                return self._parent.readable_roots
            return self._readable_root_labels
        if self._is_root_mode:
            return ["/"]
        return [name for name in self._paths.keys() if name != "/"]

    @property
    def writable_roots(self) -> list[str]:
        """List of writable path roots (for error messages)."""
        if self._parent is not None:
            if self._writable_root_labels is None:
                return self._parent.writable_roots
            return self._writable_root_labels
        if self._is_root_mode:
            cfg = self._root_path_config
            if cfg is None or cfg.mode != "rw":
                return []
            return ["/"]
        return [
            name
            for name, (_, config) in self._paths.items()
            if config.mode == "rw"
        ]

    # ---------------------------------------------------------------------------
    # Derivation
    # ---------------------------------------------------------------------------

    def derive(
        self,
        *,
        allow_read: str | list[str] | None = None,
        allow_write: str | list[str] | None = None,
        readonly: bool | None = None,
        inherit: bool = False,
    ) -> "Sandbox":
        """Derive a child sandbox using allowlists.

        The child keeps the same path namespace as the parent but can only
        access paths allowed by the provided prefixes. By default (`inherit=False`
        and no allowlists), the child has no access.
        """
        if readonly is False and not self._has_any_writable_area():
            raise SandboxPermissionEscalationError(
                "Cannot create child sandbox with readonly=False: parent sandbox is readonly."
            )

        read_entries = self._normalize_allowlist(allow_read)
        write_entries = self._normalize_allowlist(allow_write)

        if write_entries is not None and read_entries is None:
            read_entries = write_entries
        if read_entries is not None and write_entries is None:
            write_entries = []

        if read_entries is None and write_entries is None and not inherit:
            read_entries = []
            write_entries = []

        allowed_read, readable_labels = self._resolve_allowlist_entries(read_entries)
        allowed_write, writable_labels = self._resolve_allowlist_entries(write_entries)

        if readonly:
            allowed_write = []
            writable_labels = []

        if read_entries is None and inherit:
            readable_labels = None
            allowed_read = None
        if write_entries is None and inherit and not readonly:
            writable_labels = None
            allowed_write = None

        return Sandbox(
            self.config,
            base_path=self._base_path,
            _parent=self,
            _allowed_read=allowed_read,
            _allowed_write=allowed_write,
            _readable_root_labels=readable_labels,
            _writable_root_labels=writable_labels,
        )

    def _normalize_allowlist(
        self, value: str | list[str] | None
    ) -> Optional[list[str]]:
        if value is None:
            return None
        if isinstance(value, str):
            return [value]
        return list(value)

    def _resolve_allowlist_entries(
        self, entries: Optional[list[str]]
    ) -> tuple[Optional[list[tuple[str, Path]]], Optional[list[str]]]:
        if entries is None:
            return None, None
        allowed: list[tuple[str, Path]] = []
        labels: list[str] = []
        for entry in entries:
            name, prefix_path, label = self._resolve_allow_prefix(entry)
            allowed.append((name, prefix_path))
            labels.append(label)
        # Deduplicate labels while preserving order.
        seen: set[str] = set()
        unique_labels = [l for l in labels if not (l in seen or seen.add(l))]
        return allowed, unique_labels

    def _resolve_allow_prefix(self, entry: str) -> tuple[str, Path, str]:
        if self._is_root_mode:
            normalized = entry.replace("\\", "/").strip()
            if not normalized.startswith("/"):
                raise ValueError(
                    f"Root-mode allowlist entry must start with '/': {entry!r}"
                )
            if ".." in Path(normalized).parts:
                raise ValueError(
                    f"Root-mode allowlist entry must not contain '..': {entry!r}"
                )
            resolved = self.resolve(normalized)
            if resolved.exists() and resolved.is_file():
                resolved = resolved.parent
            label = normalized.rstrip("/") or "/"
            return "/", resolved, label

        name, resolved, _ = self.get_path_config(entry)
        if resolved.exists() and resolved.is_file():
            resolved = resolved.parent
        root_path = self._find_multi_path_boundary(name)[1]
        try:
            rel = resolved.relative_to(root_path)
            rel_str = rel.as_posix()
        except ValueError:
            rel_str = ""
        label = name if not rel_str or rel_str == "." else f"{name}/{rel_str}"
        return name, resolved, label

    def _has_any_writable_area(self) -> bool:
        if self._parent is not None:
            return self._parent._has_any_writable_area()
        if self._is_root_mode:
            cfg = self._root_path_config
            return cfg is not None and cfg.mode == "rw"
        return any(config.mode == "rw" for _, config in self._paths.values())

    def _matches_prefix(self, name: str, path: Path, prefix: tuple[str, Path]) -> bool:
        prefix_name, prefix_path = prefix
        if prefix_name != name:
            return False
        try:
            path.relative_to(prefix_path)
            return True
        except ValueError:
            return False

    def _is_allowed_for_any(self, name: str, path: Path) -> bool:
        if self._allowed_read is None and self._allowed_write is None:
            return True
        prefixes: list[tuple[str, Path]] = []
        if self._allowed_read is not None:
            prefixes.extend(self._allowed_read)
        if self._allowed_write is not None:
            prefixes.extend(self._allowed_write)
        for prefix in prefixes:
            prefix_name, prefix_path = prefix
            if prefix_name != name:
                continue
            # Allow descendants and ancestors of the allowed prefix.
            if self._matches_prefix(name, path, prefix):
                return True
            try:
                prefix_path.relative_to(path)
                return True
            except ValueError:
                continue
        return False

    def _is_allowed_for_read(self, name: str, path: Path) -> bool:
        if self._allowed_read is None:
            return True
        return any(self._matches_prefix(name, path, p) for p in self._allowed_read)

    def _is_allowed_for_write(self, name: str, path: Path) -> bool:
        if self._allowed_write is None:
            return True
        return any(self._matches_prefix(name, path, p) for p in self._allowed_write)

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
        if self._parent is not None:
            name, resolved, config = self._parent.get_path_config(path)
            if not self._is_allowed_for_any(name, resolved):
                raise PathNotInSandboxError(path, self.readable_roots)
            return name, resolved, config

        name, resolved, config = self._find_path_for_boundary(path)
        return name, resolved, config


class SandboxPermissionEscalationError(SandboxError):
    """Raised when a child sandbox derivation would expand permissions."""

    pass
