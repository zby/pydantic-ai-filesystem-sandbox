"""Filesystem sandbox toolset for PydanticAI agents with LLM-friendly errors.

This package provides a standalone, reusable filesystem sandbox for PydanticAI:
- FileSandboxConfig and PathConfig for configuration
- FileSandboxError classes with LLM-friendly messages
- FileSandboxImpl implementation as a PydanticAI AbstractToolset
- FileSandboxApprovalToolset for optional approval support

Usage (standalone - no approval):
    from pydantic_ai_filesystem_sandbox import FileSandboxImpl, FileSandboxConfig, PathConfig

    config = FileSandboxConfig(paths={
        "data": PathConfig(root="./data", mode="rw"),
    })
    sandbox = FileSandboxImpl(config)
    agent = Agent(..., toolsets=[sandbox])

Usage (with approval - requires pydantic-ai-blocking-approval):
    pip install pydantic-ai-filesystem-sandbox[approval]

    from pydantic_ai_filesystem_sandbox import FileSandboxImpl, FileSandboxConfig, PathConfig
    from pydantic_ai_filesystem_sandbox.approval import FileSandboxApprovalToolset

    config = FileSandboxConfig(paths={
        "data": PathConfig(root="./data", mode="rw", write_approval=True),
    })
    sandbox = FileSandboxImpl(config)
    approved_sandbox = FileSandboxApprovalToolset(
        inner=sandbox,
        approval_callback=my_prompt_fn,
    )
    agent = Agent(..., toolsets=[approved_sandbox])
"""

from .sandbox import (
    DEFAULT_MAX_READ_CHARS,
    EditError,
    FileSandboxConfig,
    FileSandboxError,
    FileSandboxImpl,
    FileTooLargeError,
    PathConfig,
    PathNotInSandboxError,
    PathNotWritableError,
    ReadResult,
    SuffixNotAllowedError,
)

__version__ = "0.5.0"

__all__ = [
    "DEFAULT_MAX_READ_CHARS",
    "EditError",
    "FileSandboxConfig",
    "FileSandboxError",
    "FileSandboxImpl",
    "FileTooLargeError",
    "PathConfig",
    "PathNotInSandboxError",
    "PathNotWritableError",
    "ReadResult",
    "SuffixNotAllowedError",
]
