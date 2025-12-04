"""ApprovableFileSystemToolset: File I/O tools with approval integration.

This module provides ApprovableFileSystemToolset, which extends FileSystemToolset
with approval protocol support for use with ApprovalToolset from
pydantic-ai-blocking-approval.

Example:
    from pydantic_ai_filesystem_sandbox import Sandbox, SandboxConfig, PathConfig
    from pydantic_ai_filesystem_sandbox.approval_toolset import ApprovableFileSystemToolset
    from pydantic_ai_blocking_approval import ApprovalToolset, ApprovalController

    # Create sandbox and toolset
    config = SandboxConfig(paths={
        "output": PathConfig(root="./output", mode="rw", write_approval=True),
    })
    sandbox = Sandbox(config)
    toolset = ApprovableFileSystemToolset(sandbox)

    # Wrap with approval
    controller = ApprovalController(mode="interactive")
    approved_toolset = ApprovalToolset(
        inner=toolset,
        approval_callback=controller.approval_callback,
        memory=controller.memory,
    )

    agent = Agent(..., toolsets=[approved_toolset])
"""
from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai_blocking_approval import ApprovalResult

from .sandbox import PathNotInSandboxError
from .toolset import FileSystemToolset


class ApprovableFileSystemToolset(FileSystemToolset):
    """FileSystemToolset with approval protocol support.

    Extends FileSystemToolset with needs_approval() and get_approval_description()
    methods for use with ApprovalToolset from pydantic-ai-blocking-approval.

    Example:
        from pydantic_ai_filesystem_sandbox import Sandbox, SandboxConfig, PathConfig
        from pydantic_ai_filesystem_sandbox.approval_toolset import ApprovableFileSystemToolset
        from pydantic_ai_blocking_approval import ApprovalToolset

        sandbox = Sandbox(SandboxConfig(paths={
            "output": PathConfig(root="./output", mode="rw", write_approval=True),
        }))
        toolset = ApprovableFileSystemToolset(sandbox)
        approved = ApprovalToolset(inner=toolset, approval_callback=my_callback)
    """

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
