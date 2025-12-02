"""Filesystem sandbox toolset for PydanticAI agents with LLM-friendly errors.

This package provides a sandboxed filesystem for PydanticAI agents with:
- Sandbox: Security boundary for permission checking and path resolution
- FileSystemToolset: File I/O tools (read, write, edit, list)
- LLM-friendly error messages that guide correction

Architecture:
    Sandbox handles policy (permissions, boundaries, approval requirements).
    FileSystemToolset handles file I/O and implements needs_approval() for
    integration with ApprovalToolset from pydantic-ai-blocking-approval.

Usage (simple):
    from pydantic_ai_filesystem_sandbox import FileSystemToolset

    toolset = FileSystemToolset.create_default("./data")
    agent = Agent(..., toolsets=[toolset])

Usage (custom sandbox):
    from pydantic_ai_filesystem_sandbox import (
        FileSystemToolset, Sandbox, SandboxConfig, PathConfig
    )

    config = SandboxConfig(paths={
        "input": PathConfig(root="./input", mode="ro"),
        "output": PathConfig(root="./output", mode="rw", write_approval=True),
    })
    sandbox = Sandbox(config)
    toolset = FileSystemToolset(sandbox)
    agent = Agent(..., toolsets=[toolset])

Usage (with approval):
    from pydantic_ai_filesystem_sandbox import FileSystemToolset, Sandbox, SandboxConfig, PathConfig
    from pydantic_ai_blocking_approval import ApprovalToolset

    sandbox = Sandbox(config)
    toolset = FileSystemToolset(sandbox)
    approved = ApprovalToolset(inner=toolset, approval_callback=my_callback)
    agent = Agent(..., toolsets=[approved])
"""

from .sandbox import (
    # Configuration
    PathConfig,
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

__version__ = "0.6.0"

__all__ = [
    # Configuration
    "PathConfig",
    "SandboxConfig",
    # Sandbox (security boundary)
    "Sandbox",
    # Toolset (file I/O)
    "FileSystemToolset",
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
