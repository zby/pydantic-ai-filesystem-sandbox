"""Filesystem sandbox toolset for PydanticAI agents with LLM-friendly errors.

This package provides a sandboxed filesystem for PydanticAI agents with:
- Sandbox: Security boundary for permission checking and path resolution
- FileSystemToolset: File I/O tools (read, write, edit, list)
- ApprovableFileSystemToolset: FileSystemToolset with approval protocol support
- LLM-friendly error messages that guide correction

Architecture:
    Sandbox handles policy (permissions, boundaries, approval requirements).
    FileSystemToolset handles file I/O.
    ApprovableFileSystemToolset adds needs_approval() for integration with
    ApprovalToolset from pydantic-ai-blocking-approval.

Usage (simple):
    from pydantic_ai_filesystem_sandbox import FileSystemToolset

    toolset = FileSystemToolset.create_default("./data")
    agent = Agent(..., toolsets=[toolset])

Usage (custom sandbox with mounts):
    from pydantic_ai_filesystem_sandbox import (
        FileSystemToolset, Sandbox, SandboxConfig, Mount
    )

    config = SandboxConfig(mounts=[
        Mount(host_path="./input", mount_point="/input", mode="ro"),
        Mount(host_path="./output", mount_point="/output", mode="rw"),
    ])
    sandbox = Sandbox(config)
    toolset = FileSystemToolset(sandbox)
    agent = Agent(..., toolsets=[toolset])

Usage (with approval):
    from pydantic_ai_filesystem_sandbox import (
        ApprovableFileSystemToolset, Sandbox, SandboxConfig, Mount
    )
    from pydantic_ai_blocking_approval import ApprovalToolset

    config = SandboxConfig(mounts=[
        Mount(host_path="./output", mount_point="/output", mode="rw", write_approval=True),
    ])
    sandbox = Sandbox(config)
    toolset = ApprovableFileSystemToolset(sandbox)
    approved = ApprovalToolset(inner=toolset, approval_callback=my_callback)
    agent = Agent(..., toolsets=[approved])
"""

from .sandbox import (
    # Configuration
    Mount,
    SandboxConfig,
    # Sandbox
    Sandbox,
    # Errors
    SandboxError,
    PathNotInSandboxError,
    PathNotWritableError,
    SuffixNotAllowedError,
    FileTooLargeError,
    EditError,
)

from .toolset import (
    # Toolset
    FileSystemToolset,
    # Read result
    ReadResult,
    DEFAULT_MAX_READ_CHARS,
)

from .approval_toolset import (
    # Approvable toolset
    ApprovableFileSystemToolset,
)

__version__ = "0.9.0"

__all__ = [
    # Configuration
    "Mount",
    "SandboxConfig",
    # Sandbox (security boundary)
    "Sandbox",
    # Toolset (file I/O)
    "FileSystemToolset",
    # Approvable toolset (with approval protocol)
    "ApprovableFileSystemToolset",
    # Read result
    "ReadResult",
    "DEFAULT_MAX_READ_CHARS",
    # Errors
    "SandboxError",
    "PathNotInSandboxError",
    "PathNotWritableError",
    "SuffixNotAllowedError",
    "FileTooLargeError",
    "EditError",
]
