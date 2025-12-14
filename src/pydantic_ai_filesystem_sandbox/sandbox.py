"""Sandbox: Permission checking and path resolution with LLM-friendly errors.

This module provides the security boundary for filesystem access:
- Mount and SandboxConfig for configuration
- Sandbox class for permission checking and path resolution
- LLM-friendly error classes

The Sandbox is a pure policy/validation layer - it doesn't perform file I/O.
For file operations, use FileSystemToolset which wraps a Sandbox.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class Mount(BaseModel):
    """Mount a host directory into the virtual filesystem.

    Similar to Docker volume mounts, this maps a host directory to a path
    in the sandbox's virtual filesystem.

    Example:
        Mount(host_path="/home/user/docs", mount_point="/docs", mode="ro")
        # Host /home/user/docs/file.txt -> sandbox /docs/file.txt
    """

    host_path: Path = Field(description="Host directory path to mount")
    mount_point: str = Field(
        description="Where to mount in virtual filesystem (e.g., '/docs', '/data')"
    )
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
    write_approval: bool = Field(
        default=True,
        description="Whether writes to this mount require approval",
    )
    read_approval: bool = Field(
        default=False,
        description="Whether reads from this mount require approval",
    )

    @model_validator(mode="after")
    def _validate_mount_point(self) -> "Mount":
        if not self.mount_point.startswith("/"):
            raise ValueError(f"mount_point must start with '/': {self.mount_point!r}")
        if self.mount_point != "/" and self.mount_point.endswith("/"):
            raise ValueError(f"mount_point must not end with '/': {self.mount_point!r}")
        return self


# ---------------------------------------------------------------------------
# Deprecated Configuration (for backwards compatibility)
# ---------------------------------------------------------------------------


class PathConfig(BaseModel):
    """Configuration for a single path in the sandbox.

    DEPRECATED: Use Mount instead. Will be removed in a future version.
    """

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

    DEPRECATED: Use Mount with mount_point="/" instead. Will be removed in a future version.
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
    """Configuration for a sandbox.

    Preferred usage (new API):
        config = SandboxConfig(mounts=[
            Mount(host_path="./docs", mount_point="/docs", mode="ro"),
            Mount(host_path="./data", mount_point="/data", mode="rw"),
        ])

    Deprecated usage (old API, still supported):
        # Multi-path mode
        config = SandboxConfig(paths={
            "docs": PathConfig(root="./docs", mode="ro"),
        })
        # Root mode
        config = SandboxConfig(root=RootSandboxConfig(root="./project"))
    """

    mounts: Optional[list[Mount]] = Field(
        default=None,
        description="List of directory mounts (preferred API)",
    )
    # Deprecated fields
    root: Optional[RootSandboxConfig] = Field(
        default=None,
        description="DEPRECATED: Use mounts with mount_point='/' instead",
    )
    paths: Optional[dict[str, PathConfig]] = Field(
        default=None,
        description="DEPRECATED: Use mounts instead",
    )

    @model_validator(mode="after")
    def _validate_config(self) -> "SandboxConfig":
        has_mounts = self.mounts is not None
        has_root = self.root is not None
        has_paths = self.paths is not None

        # Must have exactly one configuration style
        if not has_mounts and not has_root and not has_paths:
            raise ValueError(
                "SandboxConfig requires 'mounts' (or deprecated 'root'/'paths')."
            )
        if has_mounts and (has_root or has_paths):
            raise ValueError(
                "SandboxConfig cannot mix 'mounts' with deprecated 'root'/'paths'."
            )
        if has_root and has_paths:
            raise ValueError("SandboxConfig cannot set both 'root' and 'paths'.")

        # Emit deprecation warnings
        if has_root:
            warnings.warn(
                "SandboxConfig(root=...) is deprecated. Use mounts=[Mount(mount_point='/')] instead.",
                DeprecationWarning,
                stacklevel=3,
            )
        if has_paths:
            warnings.warn(
                "SandboxConfig(paths=...) is deprecated. Use mounts=[Mount(...)] instead.",
                DeprecationWarning,
                stacklevel=3,
            )

        return self

    def get_mounts(self) -> list[Mount]:
        """Get mounts, converting from deprecated config if needed."""
        if self.mounts is not None:
            return self.mounts

        # Convert deprecated root config
        if self.root is not None:
            return [
                Mount(
                    host_path=self.root.root,
                    mount_point="/",
                    mode="ro" if self.root.readonly else "rw",
                    suffixes=self.root.suffixes,
                    max_file_bytes=self.root.max_file_bytes,
                )
            ]

        # Convert deprecated paths config
        if self.paths is not None:
            return [
                Mount(
                    host_path=Path(cfg.root),
                    mount_point=f"/{name}",
                    mode=cfg.mode,
                    suffixes=cfg.suffixes,
                    max_file_bytes=cfg.max_file_bytes,
                    write_approval=cfg.write_approval,
                    read_approval=cfg.read_approval,
                )
                for name, cfg in self.paths.items()
            ]

        return []


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
    - Path resolution (virtual path → host path)
    - Permission checking (can_read, can_write)
    - Approval requirements (needs_read_approval, needs_write_approval)
    - Boundary enforcement (readable_roots, writable_roots)

    This is a pure policy/validation layer - it doesn't perform file I/O.
    For file operations, use FileSystemToolset which wraps a Sandbox.

    Example:
        config = SandboxConfig(mounts=[
            Mount(host_path="./input", mount_point="/input", mode="ro"),
            Mount(host_path="./output", mount_point="/output", mode="rw"),
        ])
        sandbox = Sandbox(config)

        # Query permissions
        if sandbox.can_write("/output/file.txt"):
            resolved = sandbox.resolve("/output/file.txt")
            # ... perform write operation
    """

    def __init__(
        self,
        config: SandboxConfig,
        base_path: Optional[Path] = None,
        *,
        _parent: Optional["Sandbox"] = None,
        _allowed_read: Optional[list[tuple[str, Path, str]]] = None,
        _allowed_write: Optional[list[tuple[str, Path, str]]] = None,
    ):
        """Initialize the sandbox.

        Args:
            config: Sandbox configuration
            base_path: Base path for resolving relative host paths (defaults to cwd)
        """
        self.config = config
        self._base_path = base_path or Path.cwd()
        # List of (mount_point, resolved_host_path, Mount)
        self._mounts: list[tuple[str, Path, Mount]] = []

        self._parent: Optional[Sandbox] = _parent
        # Allowlists: list of (mount_point, host_path, label) tuples
        # None = inherit from parent (or allow all if root sandbox)
        # [] = no access
        self._allowed_read: Optional[list[tuple[str, Path, str]]] = _allowed_read
        self._allowed_write: Optional[list[tuple[str, Path, str]]] = _allowed_write

        if self._parent is None:
            self._setup_mounts()
        else:
            # Inherit mount configuration from parent for nested derivation
            self._mounts = self._parent._mounts

    def _setup_mounts(self) -> None:
        """Resolve and validate configured mounts."""
        mounts = self.config.get_mounts()

        # Check for duplicate mount points (nested mounts are allowed)
        mount_points = [m.mount_point for m in mounts]
        seen = set()
        for mp in mount_points:
            if mp in seen:
                raise ValueError(f"Duplicate mount point: {mp!r}")
            seen.add(mp)

        # Resolve and create mount directories
        for mount in mounts:
            host_path = Path(mount.host_path)
            if not host_path.is_absolute():
                host_path = (self._base_path / host_path).resolve()
            else:
                host_path = host_path.resolve()
            host_path.mkdir(parents=True, exist_ok=True)
            self._mounts.append((mount.mount_point, host_path, mount))

        # Sort by mount_point length descending (longest prefix first)
        self._mounts.sort(key=lambda x: len(x[0]), reverse=True)

    # ---------------------------------------------------------------------------
    # Path Resolution
    # ---------------------------------------------------------------------------

    def _normalize_path(self, path: str) -> str:
        """Normalize a virtual path."""
        normalized = path.replace("\\", "/").strip()
        if not normalized:
            return "/"
        # Reject dangerous patterns
        if normalized.startswith("~"):
            raise PathNotInSandboxError(path, self.readable_roots)
        # Handle Windows drive letters
        if len(normalized) >= 2 and normalized[1] == ":":
            raise PathNotInSandboxError(path, self.readable_roots)
        # Ensure path starts with /
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        return normalized

    def _find_mount(self, path: str) -> tuple[str, Path, Mount]:
        """Find the mount that contains this path.

        Args:
            path: Virtual path (e.g., "/docs/file.txt")

        Returns:
            Tuple of (mount_point, host_path, mount_config)

        Raises:
            PathNotInSandboxError: If path is not in any mount
        """
        if self._parent is not None:
            return self._parent._find_mount(path)

        normalized = self._normalize_path(path)

        # Find the most specific (longest) matching mount point
        best_match: tuple[str, Path, Mount] | None = None
        best_length = -1

        for mount_point, host_path, mount in self._mounts:
            if mount_point == "/":
                # Root mount matches everything, but with lowest priority
                if best_match is None:
                    best_match = (mount_point, host_path, mount)
                    best_length = 0
            elif normalized == mount_point or normalized.startswith(mount_point + "/"):
                # More specific mount takes precedence
                if len(mount_point) > best_length:
                    best_match = (mount_point, host_path, mount)
                    best_length = len(mount_point)

        if best_match is not None:
            return best_match

        raise PathNotInSandboxError(path, self.readable_roots)

    def _resolve_within(self, host_path: Path, relative: str) -> Path:
        """Resolve a relative path within a host path, preventing escapes.

        Args:
            host_path: The host directory
            relative: Relative path within the mount

        Returns:
            Resolved absolute path

        Raises:
            PathNotInSandboxError: If resolved path escapes the host_path
        """
        relative = relative.lstrip("/")
        if not relative:
            return host_path
        candidate = (host_path / relative).resolve()
        try:
            candidate.relative_to(host_path)
        except ValueError:
            raise PathNotInSandboxError(relative, self.readable_roots)
        return candidate

    def resolve(self, path: str) -> Path:
        """Resolve virtual path to host path within sandbox boundaries.

        Args:
            path: Virtual path (e.g., "/docs/file.txt")

        Returns:
            Resolved absolute host Path

        Raises:
            PathNotInSandboxError: If path is outside sandbox boundaries or
                not in derived sandbox's allowlist
        """
        _, resolved, _ = self.get_path_config(path)
        return resolved

    def get_mount_root(self, mount_point: str) -> Path:
        """Get the host path for a mount point.

        Unlike resolve(), this doesn't check derived sandbox allowlists.
        Used internally for path formatting in list_files().

        Args:
            mount_point: Mount point (e.g., "/data", "/")

        Returns:
            Host path for the mount

        Raises:
            PathNotInSandboxError: If mount_point is not a valid mount
        """
        for mp, host_path, _ in self._mounts:
            if mp == mount_point:
                return host_path
        raise PathNotInSandboxError(mount_point, self.readable_roots)

    def get_path_config(self, path: str) -> tuple[str, Path, Mount]:
        """Get mount point, resolved path, and config for a path.

        This is useful for toolsets that need full path info.

        Args:
            path: Virtual path to look up

        Returns:
            Tuple of (mount_point, resolved_host_path, mount_config)

        Raises:
            PathNotInSandboxError: If path is not in any mount
        """
        mount_point, host_path, mount = self._find_mount(path)
        normalized = self._normalize_path(path)

        # Extract relative part
        if mount_point == "/":
            relative = normalized[1:]
        else:
            relative = normalized[len(mount_point) :]

        resolved = self._resolve_within(host_path, relative)

        # Check allowlists for derived sandboxes
        if self._parent is not None:
            if not self._is_allowed_for_any(mount_point, resolved):
                raise PathNotInSandboxError(path, self.readable_roots)

        return mount_point, resolved, mount

    # ---------------------------------------------------------------------------
    # Permission Checking
    # ---------------------------------------------------------------------------

    def can_read(self, path: str) -> bool:
        """Check if path is readable within sandbox boundaries."""
        try:
            mount_point, resolved, _ = self.get_path_config(path)
        except SandboxError:
            return False

        if self._parent is not None and not self._parent.can_read(path):
            return False

        return self._is_allowed_for_read(mount_point, resolved)

    def can_write(self, path: str) -> bool:
        """Check if path is writable within sandbox boundaries."""
        try:
            mount_point, resolved, mount = self.get_path_config(path)
        except SandboxError:
            return False

        if mount.mode != "rw":
            return False
        if self._parent is not None and not self._parent.can_write(path):
            return False

        return self._is_allowed_for_write(mount_point, resolved)

    def needs_read_approval(self, path: str) -> bool:
        """Check if reading this path requires approval."""
        try:
            _, _, mount = self.get_path_config(path)
        except SandboxError:
            return False
        return mount.read_approval if self.can_read(path) else False

    def needs_write_approval(self, path: str) -> bool:
        """Check if writing this path requires approval."""
        try:
            _, _, mount = self.get_path_config(path)
        except SandboxError:
            return False
        return mount.write_approval if self.can_write(path) else False

    # ---------------------------------------------------------------------------
    # Boundary Info
    # ---------------------------------------------------------------------------

    @property
    def readable_roots(self) -> list[str]:
        """List of readable paths (for error messages)."""
        if self._parent is not None:
            if self._allowed_read is None:
                return self._parent.readable_roots
            # Extract unique labels from allowlist, preserving order
            return list(dict.fromkeys(lbl for _, _, lbl in self._allowed_read))
        return [mount_point for mount_point, _, _ in self._mounts]

    @property
    def writable_roots(self) -> list[str]:
        """List of writable paths (for error messages)."""
        if self._parent is not None:
            if self._allowed_write is None:
                return self._parent.writable_roots
            # Extract unique labels from allowlist, preserving order
            return list(dict.fromkeys(lbl for _, _, lbl in self._allowed_write))
        return [
            mount_point
            for mount_point, _, mount in self._mounts
            if mount.mode == "rw"
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

        The child keeps the same mount namespace as the parent but can only
        access paths allowed by the provided prefixes. By default (`inherit=False`
        and no allowlists), the child has no access.

        Args:
            allow_read: Path(s) to allow reading (e.g., "/docs", "/data/sub")
            allow_write: Path(s) to allow writing
            readonly: If True, child cannot write anywhere
            inherit: If True and no allowlists given, inherit parent permissions
        """
        if readonly is False and not self._has_any_writable_area():
            raise SandboxPermissionEscalationError(
                "Cannot create child sandbox with readonly=False: parent sandbox is readonly."
            )

        read_entries = self._normalize_allowlist(allow_read)
        write_entries = self._normalize_allowlist(allow_write)

        # allow_write implies allow_read for the same paths
        if write_entries is not None and read_entries is None:
            read_entries = write_entries
        if read_entries is not None and write_entries is None:
            write_entries = []

        # Default (no inherit, no allowlists) = no access
        if read_entries is None and write_entries is None and not inherit:
            read_entries = []
            write_entries = []

        # Resolve entries to (mount_point, host_path, label) tuples
        allowed_read = self._resolve_allowlist_entries(read_entries)
        allowed_write = self._resolve_allowlist_entries(write_entries)

        if readonly:
            allowed_write = []

        # inherit=True with no explicit allowlist = None (inherit from parent)
        if read_entries is None and inherit:
            allowed_read = None
        if write_entries is None and inherit and not readonly:
            allowed_write = None

        return Sandbox(
            self.config,
            base_path=self._base_path,
            _parent=self,
            _allowed_read=allowed_read,
            _allowed_write=allowed_write,
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
    ) -> Optional[list[tuple[str, Path, str]]]:
        """Resolve allowlist entries to (mount_point, host_path, label) tuples."""
        if entries is None:
            return None
        return [self._resolve_allow_prefix(entry) for entry in entries]

    def _resolve_allow_prefix(self, entry: str) -> tuple[str, Path, str]:
        """Resolve an allowlist entry to (mount_point, host_path, label).

        Args:
            entry: A virtual path to allow (must be a directory, not a file)

        Raises:
            ValueError: If entry contains '..' or points to a file
        """
        normalized = self._normalize_path(entry)
        if ".." in Path(normalized).parts:
            raise ValueError(f"Allowlist entry must not contain '..': {entry!r}")

        resolved = self.resolve(normalized)
        if resolved.exists() and resolved.is_file():
            # Suggest the normalized parent path
            parent_path = str(Path(normalized).parent)
            if parent_path == ".":
                parent_path = "/"
            raise ValueError(
                f"Allowlist entry must be a directory, not a file: {entry!r}. "
                f"Use the parent directory instead: '{parent_path}'"
            )

        mount_point, _, _ = self._find_mount(entry)
        label = normalized.rstrip("/") or "/"
        return mount_point, resolved, label

    def _has_any_writable_area(self) -> bool:
        if self._parent is not None:
            return self._parent._has_any_writable_area()
        return any(mount.mode == "rw" for _, _, mount in self._mounts)

    def _matches_prefix(
        self, mount_point: str, path: Path, prefix: tuple[str, Path, str]
    ) -> bool:
        """Check if path matches an allowlist prefix entry."""
        prefix_mount, prefix_path, _ = prefix  # Ignore label
        if prefix_mount != mount_point:
            return False
        try:
            path.relative_to(prefix_path)
            return True
        except ValueError:
            return False

    def _is_allowed_for_any(self, mount_point: str, path: Path) -> bool:
        """Check if path is allowed by any read or write allowlist entry.

        Only allows paths that are descendants of (or equal to) an allowed prefix.
        When inheriting (allowlists are None), delegates to parent.
        """
        if self._allowed_read is None and self._allowed_write is None:
            # Inheriting from parent - check parent's permissions
            if self._parent is not None:
                return self._parent._is_allowed_for_any(mount_point, path)
            return True  # Root sandbox with no restrictions
        prefixes: list[tuple[str, Path, str]] = []
        if self._allowed_read is not None:
            prefixes.extend(self._allowed_read)
        if self._allowed_write is not None:
            prefixes.extend(self._allowed_write)
        return any(
            self._matches_prefix(mount_point, path, prefix) for prefix in prefixes
        )

    def _is_allowed_for_read(self, mount_point: str, path: Path) -> bool:
        if self._allowed_read is None:
            # Inheriting from parent - check parent's permissions
            if self._parent is not None:
                return self._parent._is_allowed_for_read(mount_point, path)
            return True  # Root sandbox with no restrictions
        return any(
            self._matches_prefix(mount_point, path, p) for p in self._allowed_read
        )

    def _is_allowed_for_write(self, mount_point: str, path: Path) -> bool:
        if self._allowed_write is None:
            # Inheriting from parent - check parent's permissions
            if self._parent is not None:
                return self._parent._is_allowed_for_write(mount_point, path)
            return True  # Root sandbox with no restrictions
        return any(
            self._matches_prefix(mount_point, path, p) for p in self._allowed_write
        )

    # ---------------------------------------------------------------------------
    # Validation Helpers
    # ---------------------------------------------------------------------------

    def check_suffix(
        self, path: Path, mount: Mount, display_path: Optional[str] = None
    ) -> None:
        """Check if file suffix is allowed.

        Args:
            path: Resolved host path
            mount: Mount configuration
            display_path: Virtual path for error messages (defaults to path if not provided)

        Raises:
            SuffixNotAllowedError: If suffix is not in allowed list
        """
        if mount.suffixes is not None:
            suffix = path.suffix.lower()
            allowed = [s.lower() for s in mount.suffixes]
            if suffix not in allowed:
                raise SuffixNotAllowedError(
                    display_path or str(path), suffix, mount.suffixes
                )

    def check_size(
        self, path: Path, mount: Mount, display_path: Optional[str] = None
    ) -> None:
        """Check if file size is within limit.

        Args:
            path: Resolved host path
            mount: Mount configuration
            display_path: Virtual path for error messages (defaults to path if not provided)

        Raises:
            FileTooLargeError: If file exceeds size limit
        """
        if mount.max_file_bytes is not None and path.exists():
            size = path.stat().st_size
            if size > mount.max_file_bytes:
                raise FileTooLargeError(
                    display_path or str(path), size, mount.max_file_bytes
                )


class SandboxPermissionEscalationError(SandboxError):
    """Raised when a child sandbox derivation would expand permissions."""

    pass
