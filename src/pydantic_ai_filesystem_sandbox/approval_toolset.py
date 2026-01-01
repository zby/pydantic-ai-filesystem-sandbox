"""ApprovableFileSystemToolset: File I/O tools with approval integration.

This module provides ApprovableFileSystemToolset, which extends FileSystemToolset
with approval protocol support for use with ApprovalToolset from
pydantic-ai-blocking-approval.

Example:
    from pydantic_ai_filesystem_sandbox import Sandbox, SandboxConfig, Mount
    from pydantic_ai_filesystem_sandbox.approval_toolset import ApprovableFileSystemToolset
    from pydantic_ai_blocking_approval import ApprovalToolset, ApprovalController

    # Create sandbox and toolset
    config = SandboxConfig(mounts=[
        Mount(host_path="./output", mount_point="/output", mode="rw", write_approval=True),
    ])
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
from pydantic_ai_blocking_approval import (
    ApprovalConfig,
    ApprovalResult,
    needs_approval_from_config,
)

from .sandbox import PathNotInSandboxError, PathNotWritableError
from .toolset import FileSystemToolset


class ApprovableFileSystemToolset(FileSystemToolset):
    """FileSystemToolset with approval protocol support.

    Extends FileSystemToolset with needs_approval() and get_approval_description()
    methods for use with ApprovalToolset from pydantic-ai-blocking-approval.

    Example:
        from pydantic_ai_filesystem_sandbox import Sandbox, SandboxConfig, Mount
        from pydantic_ai_filesystem_sandbox.approval_toolset import ApprovableFileSystemToolset
        from pydantic_ai_blocking_approval import ApprovalToolset

        sandbox = Sandbox(SandboxConfig(mounts=[
            Mount(host_path="./output", mount_point="/output", mode="rw", write_approval=True),
        ]))
        toolset = ApprovableFileSystemToolset(sandbox)
        approved = ApprovalToolset(inner=toolset, approval_callback=my_callback)
    """

    def needs_approval(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        config: ApprovalConfig | None = None,
    ) -> ApprovalResult:
        """Check if the tool call requires approval.

        Called by ApprovalToolset to decide if approval is needed.

        Args:
            name: Tool name being called
            tool_args: Arguments passed to the tool
            ctx: PydanticAI run context (includes deps, model, usage, etc.)
            config: Optional per-tool config from ApprovalToolset

        Returns:
            ApprovalResult with status: blocked, pre_approved, or needs_approval
        """
        # Check config-based policy first
        base = needs_approval_from_config(name, config)
        if base.is_pre_approved:
            return base

        # Tools that require 'path' argument
        path_required_tools = {"write_file", "read_file", "edit_file", "delete_file"}
        if name in path_required_tools and "path" not in tool_args:
            return ApprovalResult.blocked(f"Missing required 'path' argument for {name}")

        path = tool_args.get("path", "/")

        if name == "write_file":
            try:
                _, _, config = self._sandbox.get_path_config(path, op="write")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Path not in any mount: {path}")
            except PathNotWritableError:
                return ApprovalResult.blocked(f"Path is read-only: {path}")

            if not config.write_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

        elif name == "read_file":
            try:
                _, _, config = self._sandbox.get_path_config(path, op="read")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Path not in any mount: {path}")

            if not config.read_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

        elif name == "edit_file":
            try:
                _, _, config = self._sandbox.get_path_config(path, op="write")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Path not in any mount: {path}")
            except PathNotWritableError:
                return ApprovalResult.blocked(f"Path is read-only: {path}")

            if not config.write_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

        elif name == "list_files":
            list_path = tool_args.get("path", "/")
            if list_path in ("/", ".", ""):
                for root_virtual in self._sandbox.readable_roots:
                    try:
                        _, _, config = self._sandbox.get_path_config(
                            root_virtual, op="read"
                        )
                    except PathNotInSandboxError:
                        continue
                    if config.read_approval:
                        return ApprovalResult.needs_approval()
                return ApprovalResult.pre_approved()

            try:
                _, _, config = self._sandbox.get_path_config(list_path, op="read")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Path not in any mount: {list_path}")

            if config.read_approval:
                return ApprovalResult.needs_approval()
            return ApprovalResult.pre_approved()

        elif name == "delete_file":
            try:
                _, _, config = self._sandbox.get_path_config(path, op="write")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Path not in any mount: {path}")
            except PathNotWritableError:
                return ApprovalResult.blocked(f"Path is read-only: {path}")

            if not config.write_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

        elif name == "move_file":
            if "source" not in tool_args:
                return ApprovalResult.blocked("Missing required 'source' argument for move_file")
            if "destination" not in tool_args:
                return ApprovalResult.blocked("Missing required 'destination' argument for move_file")
            source = tool_args["source"]
            destination = tool_args["destination"]

            # Check source
            try:
                _, _, src_config = self._sandbox.get_path_config(source, op="write")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Source not in any mount: {source}")
            except PathNotWritableError:
                return ApprovalResult.blocked(f"Source is read-only: {source}")

            # Check destination
            try:
                _, _, dst_config = self._sandbox.get_path_config(destination, op="write")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Destination not in any mount: {destination}")
            except PathNotWritableError:
                return ApprovalResult.blocked(f"Destination is read-only: {destination}")

            if not src_config.write_approval and not dst_config.write_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

        elif name == "copy_file":
            if "source" not in tool_args:
                return ApprovalResult.blocked("Missing required 'source' argument for copy_file")
            if "destination" not in tool_args:
                return ApprovalResult.blocked("Missing required 'destination' argument for copy_file")
            source = tool_args["source"]
            destination = tool_args["destination"]

            # Check source (only needs read)
            try:
                _, _, src_config = self._sandbox.get_path_config(source, op="read")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Source not in any mount: {source}")

            # Check destination
            try:
                _, _, dst_config = self._sandbox.get_path_config(destination, op="write")
            except PathNotInSandboxError:
                return ApprovalResult.blocked(f"Destination not in any mount: {destination}")
            except PathNotWritableError:
                return ApprovalResult.blocked(f"Destination is read-only: {destination}")

            if not src_config.read_approval and not dst_config.write_approval:
                return ApprovalResult.pre_approved()

            return ApprovalResult.needs_approval()

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
        path = tool_args.get("path", "/")

        if name == "write_file":
            content = tool_args.get("content", "")
            char_count = len(content)
            return f"Write {char_count} chars to {path}"

        elif name == "read_file":
            return f"Read from {path}"

        elif name == "edit_file":
            old_text = tool_args.get("old_text", "")
            new_text = tool_args.get("new_text", "")
            return f"Edit {path}: replace {len(old_text)} chars with {len(new_text)} chars"

        elif name == "list_files":
            pattern = tool_args.get("pattern", "**/*")
            return f"List file paths in {path} matching {pattern}"

        elif name == "delete_file":
            return f"Delete {path}"

        elif name == "move_file":
            source = tool_args.get("source", "")
            destination = tool_args.get("destination", "")
            return f"Move {source} to {destination}"

        elif name == "copy_file":
            source = tool_args.get("source", "")
            destination = tool_args.get("destination", "")
            return f"Copy {source} to {destination}"

        return f"{name}({path})"
