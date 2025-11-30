"""Approval integration for filesystem sandbox.

This module provides FileSandboxApprovalToolset, a pre-built subclass of
ApprovalToolset that implements approval logic for filesystem operations.

Requires: pydantic-ai-blocking-approval>=0.4.0
Install with: pip install pydantic-ai-filesystem-sandbox[approval]
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic_ai_blocking_approval import (
    ApprovalDecision,
    ApprovalMemory,
    ApprovalRequest,
    ApprovalToolset,
)

from .sandbox import FileSandboxImpl, PathNotInSandboxError


class FileSandboxApprovalToolset(ApprovalToolset):
    """ApprovalToolset subclass with filesystem-aware approval logic.

    This wrapper intercepts file operations and checks approval based on
    the PathConfig settings (write_approval, read_approval) in the inner
    FileSandboxImpl.

    Example:
        from pydantic_ai_filesystem_sandbox import FileSandboxImpl, FileSandboxConfig, PathConfig
        from pydantic_ai_filesystem_sandbox.approval import FileSandboxApprovalToolset

        config = FileSandboxConfig(paths={
            "data": PathConfig(root="./data", mode="rw", write_approval=True),
        })
        sandbox = FileSandboxImpl(config)

        def my_callback(request):
            print(f"Approve {request.tool_name}? {request.description}")
            return ApprovalDecision(approved=input("[y/n]: ").lower() == "y")

        approved_sandbox = FileSandboxApprovalToolset(
            inner=sandbox,
            approval_callback=my_callback,
        )
        agent = Agent(..., toolsets=[approved_sandbox])
    """

    def __init__(
        self,
        inner: FileSandboxImpl,
        approval_callback: Callable[[ApprovalRequest], ApprovalDecision],
        memory: Optional[ApprovalMemory] = None,
        config: Optional[dict[str, dict[str, Any]]] = None,
    ):
        """Initialize the filesystem approval wrapper.

        Args:
            inner: The FileSandboxImpl to wrap
            approval_callback: Callback to request user approval
            memory: Session cache for "approve for session" (created if None)
            config: Optional per-tool config. The sandbox's PathConfig settings
                (write_approval, read_approval) are used by default.
        """
        super().__init__(
            inner=inner,
            approval_callback=approval_callback,
            memory=memory,
            config=config,
        )
        self._sandbox = inner

    def needs_approval(
        self, name: str, tool_args: dict[str, Any]
    ) -> bool | dict[str, Any]:
        """Determine if this tool call needs approval.

        Uses the PathConfig settings from the inner FileSandboxImpl:
        - write_approval: for write_file and edit_file
        - read_approval: for read_file
        - list_files: never requires approval

        Also validates paths and raises PermissionError for blocked operations.

        Args:
            name: Tool name being called
            tool_args: Arguments passed to the tool

        Returns:
            False: no approval needed
            dict: approval needed with custom description

        Raises:
            PermissionError: If operation is blocked (path not in sandbox, etc.)
        """
        # Check if tool is pre-approved via config first
        tool_config = self.config.get(name, {})
        if tool_config.get("pre_approved"):
            return False

        # Delegate to inner sandbox's approval logic
        return self._sandbox.needs_approval(name, tool_args)
