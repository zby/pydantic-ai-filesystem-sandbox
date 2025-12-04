"""Integration tests with PydanticAI Agent and TestModel for filesystem sandbox."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext

from pydantic_ai_blocking_approval import (
    ApprovalController,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ApprovalToolset,
)

from pydantic_ai_filesystem_sandbox import (
    ApprovableFileSystemToolset,
    FileSystemToolset,
    PathConfig,
    Sandbox,
    SandboxConfig,
)


class TestFileSandboxStandalone:
    """Integration tests for FileSystemToolset without approval wrapping."""

    def test_sandbox_toolset_registers_with_agent(self, tmp_path):
        """Test that FileSystemToolset can be registered as a toolset with Agent."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "data": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        # Agent should accept the toolset without error
        agent = Agent(
            model=TestModel(),
            toolsets=[sandbox],
        )

        assert agent is not None

    def test_sandbox_provides_tools(self, tmp_path):
        """Test that FileSystemToolset provides expected tools."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        # Get tools from the toolset
        ctx = MagicMock(spec=RunContext)
        tools = asyncio.run(sandbox.get_tools(ctx))

        assert "read_file" in tools
        assert "write_file" in tools
        assert "list_files" in tools

    def test_agent_can_call_list_files(self, tmp_path):
        """Test that agent can call list_files tool (doesn't validate paths strictly)."""
        sandbox_root = tmp_path / "files"
        sandbox_root.mkdir()
        (sandbox_root / "a.txt").write_text("a")

        config = SandboxConfig(
            paths={
                "files": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSystemToolset(Sandbox(config))

        agent = Agent(
            model=TestModel(),
            toolsets=[sandbox],
        )

        # list_files with "." path works even with TestModel
        result = asyncio.run(
            agent.run(
                "List all files",
                model=TestModel(call_tools=["list_files"]),
            )
        )

        assert result is not None


class TestApprovalToolsetIntegration:
    """Integration tests for ApprovableFileSystemToolset wrapped with ApprovalToolset.

    These tests call the toolset methods directly to test the approval flow,
    since TestModel generates placeholder arguments that may not match sandbox paths.
    """

    def test_write_requires_approval_and_denied(self, tmp_path):
        """Test that write with approval=True raises PermissionError when denied."""
        approval_requests: list[ApprovalRequest] = []

        def deny_callback(request: ApprovalRequest) -> ApprovalDecision:
            approval_requests.append(request)
            return ApprovalDecision(approved=False, note="User denied write")

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=deny_callback,
        )

        # Call the toolset method directly with valid args
        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        with pytest.raises(PermissionError) as exc_info:
            asyncio.run(
                approved_sandbox.call_tool(
                    "write_file",
                    {"path": "output/test.txt", "content": "test content"},
                    ctx,
                    tool,
                )
            )

        assert len(approval_requests) == 1
        assert approval_requests[0].tool_name == "write_file"
        assert "User denied write" in str(exc_info.value)

    def test_write_requires_approval_and_approved(self, tmp_path):
        """Test that write with approval=True succeeds when approved."""
        approval_requests: list[ApprovalRequest] = []

        def approve_callback(request: ApprovalRequest) -> ApprovalDecision:
            approval_requests.append(request)
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=approve_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        result = asyncio.run(
            approved_sandbox.call_tool(
                "write_file",
                {"path": "output/test.txt", "content": "test content"},
                ctx,
                tool,
            )
        )

        assert len(approval_requests) == 1
        assert approval_requests[0].tool_name == "write_file"
        # File should have been written
        assert (sandbox_root / "test.txt").read_text() == "test content"

    def test_read_requires_approval_and_denied(self, tmp_path):
        """Test that read with read_approval=True raises PermissionError when denied."""
        approval_requests: list[ApprovalRequest] = []

        def deny_callback(request: ApprovalRequest) -> ApprovalDecision:
            approval_requests.append(request)
            return ApprovalDecision(approved=False, note="User denied read")

        sandbox_root = tmp_path / "sensitive"
        sandbox_root.mkdir()
        (sandbox_root / "secret.txt").write_text("secret data")

        config = SandboxConfig(
            paths={
                "sensitive": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    read_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=deny_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        with pytest.raises(PermissionError) as exc_info:
            asyncio.run(
                approved_sandbox.call_tool(
                    "read_file",
                    {"path": "sensitive/secret.txt"},
                    ctx,
                    tool,
                )
            )

        assert len(approval_requests) == 1
        assert approval_requests[0].tool_name == "read_file"
        assert "User denied read" in str(exc_info.value)

    def test_read_requires_approval_and_approved(self, tmp_path):
        """Test that read with read_approval=True succeeds when approved."""
        approval_requests: list[ApprovalRequest] = []

        def approve_callback(request: ApprovalRequest) -> ApprovalDecision:
            approval_requests.append(request)
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "sensitive"
        sandbox_root.mkdir()
        (sandbox_root / "secret.txt").write_text("secret data")

        config = SandboxConfig(
            paths={
                "sensitive": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    read_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=approve_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        result = asyncio.run(
            approved_sandbox.call_tool(
                "read_file",
                {"path": "sensitive/secret.txt"},
                ctx,
                tool,
            )
        )

        assert len(approval_requests) == 1
        assert result.content == "secret data"

    def test_no_approval_needed_when_disabled(self, tmp_path):
        """Test that no approval is prompted when write_approval=False."""
        callback_called = False

        def should_not_be_called(request: ApprovalRequest) -> ApprovalDecision:
            nonlocal callback_called
            callback_called = True
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=False,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=should_not_be_called,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        result = asyncio.run(
            approved_sandbox.call_tool(
                "write_file",
                {"path": "output/test.txt", "content": "test"},
                ctx,
                tool,
            )
        )

        assert not callback_called
        assert (sandbox_root / "test.txt").read_text() == "test"

    def test_approval_toolset_directly_with_approvable_toolset(self, tmp_path):
        """Test using ApprovalToolset directly with ApprovableFileSystemToolset.

        This is the recommended approach: configure approval via PathConfig,
        then wrap with ApprovalToolset.
        """
        approval_requests: list[ApprovalRequest] = []

        def capture_callback(request: ApprovalRequest) -> ApprovalDecision:
            approval_requests.append(request)
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        # Use ApprovalToolset directly (recommended approach)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=capture_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        result = asyncio.run(
            approved_sandbox.call_tool(
                "write_file",
                {"path": "output/test.txt", "content": "test"},
                ctx,
                tool,
            )
        )

        # ApprovalToolset delegates to inner.needs_approval()
        assert len(approval_requests) == 1
        assert approval_requests[0].tool_name == "write_file"
        assert (sandbox_root / "test.txt").read_text() == "test"

    def test_list_files_never_needs_approval(self, tmp_path):
        """Test that list_files never requires approval even with read_approval=True."""
        callback_called = False

        def should_not_be_called(request: ApprovalRequest) -> ApprovalDecision:
            nonlocal callback_called
            callback_called = True
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()
        (sandbox_root / "file.txt").write_text("content")

        config = SandboxConfig(
            paths={
                "data": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    read_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=should_not_be_called,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        result = asyncio.run(
            approved_sandbox.call_tool(
                "list_files",
                {"path": "data"},
                ctx,
                tool,
            )
        )

        # list_files returns pre_approved from needs_approval
        assert not callback_called
        assert "data/file.txt" in result


class TestApprovalControllerIntegration:
    """Integration tests using ApprovalController modes."""

    def test_approve_all_mode(self, tmp_path):
        """Test that approve_all mode auto-approves without prompting."""
        controller = ApprovalController(mode="approve_all")

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=controller.approval_callback,
            memory=controller.memory,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        result = asyncio.run(
            approved_sandbox.call_tool(
                "write_file",
                {"path": "output/test.txt", "content": "approved content"},
                ctx,
                tool,
            )
        )

        # Should succeed without interactive prompting
        assert (sandbox_root / "test.txt").read_text() == "approved content"

    def test_strict_mode(self, tmp_path):
        """Test that strict mode auto-denies all requests with PermissionError."""
        controller = ApprovalController(mode="strict")

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=controller.approval_callback,
            memory=controller.memory,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        with pytest.raises(PermissionError) as exc_info:
            asyncio.run(
                approved_sandbox.call_tool(
                    "write_file",
                    {"path": "output/test.txt", "content": "should fail"},
                    ctx,
                    tool,
                )
            )

        assert "Strict mode" in str(exc_info.value)


class TestFileSandboxPathValidation:
    """Integration tests for path validation with approval."""

    def test_path_outside_sandbox_blocked(self, tmp_path):
        """Test that paths outside sandbox return blocked ApprovalResult."""
        sandbox_root = tmp_path / "safe"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "safe": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        # Directly test - paths outside sandbox should return blocked result
        ctx = MagicMock(spec=RunContext)
        result = sandbox.needs_approval("write_file", {"path": "unknown/file.txt"}, ctx)

        assert result.is_blocked
        assert "not in any sandbox" in result.block_reason

    def test_readonly_path_blocked(self, tmp_path):
        """Test that writes to readonly paths return blocked ApprovalResult."""
        sandbox_root = tmp_path / "readonly"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "readonly": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",  # Read-only
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        # Writes to readonly should return blocked result
        ctx = MagicMock(spec=RunContext)
        result = sandbox.needs_approval("write_file", {"path": "readonly/file.txt"}, ctx)

        assert result.is_blocked
        assert "read-only" in result.block_reason


class TestNeedsApprovalProtocol:
    """Tests for the ApprovalConfigurable protocol implementation."""

    def test_needs_approval_returns_pre_approved_when_disabled(self, tmp_path):
        """Test needs_approval returns pre_approved when approval is disabled."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=False,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        ctx = MagicMock(spec=RunContext)
        result = sandbox.needs_approval("write_file", {"path": "output/test.txt"}, ctx)
        assert result.is_pre_approved

    def test_needs_approval_returns_needs_approval_when_enabled(self, tmp_path):
        """Test needs_approval returns needs_approval when approval is enabled."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        ctx = MagicMock(spec=RunContext)
        result = sandbox.needs_approval("write_file", {"path": "output/test.txt"}, ctx)
        assert result.is_needs_approval

    def test_needs_approval_for_list_files_always_pre_approved(self, tmp_path):
        """Test that list_files never requires approval."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "data": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    read_approval=True,  # Even with read_approval
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        ctx = MagicMock(spec=RunContext)
        result = sandbox.needs_approval("list_files", {"path": "data"}, ctx)
        assert result.is_pre_approved


class TestGetApprovalDescription:
    """Tests for get_approval_description() method."""

    def test_get_approval_description_write(self, tmp_path):
        """Test get_approval_description returns nice description for writes."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw", write_approval=True)
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        ctx = MagicMock(spec=RunContext)
        desc = sandbox.get_approval_description(
            "write_file", {"path": "output/test.txt", "content": "data"}, ctx
        )

        assert "Write" in desc
        assert "4 chars" in desc  # len("data") = 4
        assert "output" in desc

    def test_get_approval_description_read(self, tmp_path):
        """Test get_approval_description returns nice description for reads."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro", read_approval=True)
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        ctx = MagicMock(spec=RunContext)
        desc = sandbox.get_approval_description(
            "read_file", {"path": "data/test.txt"}, ctx
        )

        assert "Read from" in desc
        assert "data" in desc

    def test_get_approval_description_edit(self, tmp_path):
        """Test get_approval_description returns nice description for edits."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw", write_approval=True)
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))

        ctx = MagicMock(spec=RunContext)
        desc = sandbox.get_approval_description(
            "edit_file", {"path": "output/test.txt", "old_text": "old", "new_text": "new"}, ctx
        )

        assert "Edit" in desc
        assert "3 chars" in desc  # len("old") = len("new") = 3
        assert "output" in desc

    def test_approval_uses_get_approval_description(self, tmp_path):
        """Test that ApprovalToolset uses get_approval_description for nice descriptions."""
        approval_requests: list[ApprovalRequest] = []

        def capture_callback(request: ApprovalRequest) -> ApprovalDecision:
            approval_requests.append(request)
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = SandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = ApprovableFileSystemToolset(Sandbox(config))
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=capture_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()

        asyncio.run(
            approved_sandbox.call_tool(
                "write_file",
                {"path": "output/test.txt", "content": "test"},
                ctx,
                tool,
            )
        )

        # Check that the approval request has nice description from get_approval_description
        assert len(approval_requests) == 1
        assert "Write" in approval_requests[0].description
        assert "4 chars" in approval_requests[0].description
        assert approval_requests[0].tool_args["path"] == "output/test.txt"


