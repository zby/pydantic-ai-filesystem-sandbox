"""Integration tests with PydanticAI Agent and TestModel for filesystem sandbox."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_blocking_approval import (
    ApprovalController,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalToolset,
)

from pydantic_ai_filesystem_sandbox import (
    FileSandboxConfig,
    FileSandboxImpl,
    PathConfig,
)


class TestFileSandboxStandalone:
    """Integration tests for FileSandboxImpl without approval wrapping."""

    def test_sandbox_toolset_registers_with_agent(self, tmp_path):
        """Test that FileSandboxImpl can be registered as a toolset with Agent."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        # Agent should accept the toolset without error
        agent = Agent(
            model=TestModel(),
            toolsets=[sandbox],
        )

        assert agent is not None

    def test_sandbox_provides_tools(self, tmp_path):
        """Test that FileSandboxImpl provides expected tools."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="rw")
            }
        )
        sandbox = FileSandboxImpl(config)

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

        config = FileSandboxConfig(
            paths={
                "files": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                )
            }
        )
        sandbox = FileSandboxImpl(config)

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
    """Integration tests for FileSandboxImpl wrapped with ApprovalToolset.

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

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=deny_callback,
        )

        # Call the toolset method directly with valid args
        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

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

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=approve_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

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

        config = FileSandboxConfig(
            paths={
                "sensitive": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    read_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=deny_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

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

        config = FileSandboxConfig(
            paths={
                "sensitive": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    read_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=approve_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

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

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=False,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=should_not_be_called,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

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

    def test_pre_approved_tool_skips_approval(self, tmp_path):
        """Test that tools in pre_approved list skip approval entirely."""
        callback_called = False

        def should_not_be_called(request: ApprovalRequest) -> ApprovalDecision:
            nonlocal callback_called
            callback_called = True
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,  # Would require approval
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=should_not_be_called,
            pre_approved=["write_file"],  # write_file in pre_approved
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

        result = asyncio.run(
            approved_sandbox.call_tool(
                "write_file",
                {"path": "output/test.txt", "content": "test"},
                ctx,
                tool,
            )
        )

        # Callback should NOT be called because write_file is pre_approved
        assert not callback_called
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

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    read_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=should_not_be_called,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

        result = asyncio.run(
            approved_sandbox.call_tool(
                "list_files",
                {"path": "data"},
                ctx,
                tool,
            )
        )

        # list_files returns False from needs_approval
        assert not callback_called
        assert "data/file.txt" in result


class TestApprovalControllerIntegration:
    """Integration tests using ApprovalController modes."""

    def test_approve_all_mode(self, tmp_path):
        """Test that approve_all mode auto-approves without prompting."""
        controller = ApprovalController(mode="approve_all")

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=controller.approval_callback,
            memory=controller.memory,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

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

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=controller.approval_callback,
            memory=controller.memory,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

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
        """Test that paths outside sandbox are blocked before approval check."""
        callback_called = False

        def should_not_be_called(request: ApprovalRequest) -> ApprovalDecision:
            nonlocal callback_called
            callback_called = True
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "safe"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "safe": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        # Directly test - paths outside sandbox should raise PermissionError
        # before approval is even checked
        with pytest.raises(PermissionError) as exc_info:
            sandbox.needs_approval("write_file", {"path": "unknown/file.txt"})

        assert "not in any sandbox" in str(exc_info.value)
        assert not callback_called

    def test_readonly_path_blocked(self, tmp_path):
        """Test that writes to readonly paths are blocked before approval check."""
        callback_called = False

        def should_not_be_called(request: ApprovalRequest) -> ApprovalDecision:
            nonlocal callback_called
            callback_called = True
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "readonly"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "readonly": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",  # Read-only
                    write_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        # Writes to readonly should raise PermissionError before approval
        with pytest.raises(PermissionError) as exc_info:
            sandbox.needs_approval("write_file", {"path": "readonly/file.txt"})

        assert "read-only" in str(exc_info.value)
        assert not callback_called


class TestNeedsApprovalProtocol:
    """Tests for the ApprovalConfigurable protocol implementation."""

    def test_needs_approval_returns_false_when_disabled(self, tmp_path):
        """Test needs_approval returns False when approval is disabled."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=False,
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.needs_approval("write_file", {"path": "output/test.txt"})
        assert result is False

    def test_needs_approval_returns_dict_when_enabled(self, tmp_path):
        """Test needs_approval returns dict with presentation when approval is enabled."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.needs_approval("write_file", {"path": "output/test.txt"})
        assert isinstance(result, dict)
        assert "description" in result
        assert "Write to output" in result["description"]
        assert "payload" in result
        assert result["payload"]["sandbox"] == "output"

    def test_needs_approval_for_list_files_always_false(self, tmp_path):
        """Test that list_files never requires approval."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(
                    root=str(sandbox_root),
                    mode="ro",
                    read_approval=True,  # Even with read_approval
                )
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.needs_approval("list_files", {"path": "data"})
        assert result is False


class TestNeedsApprovalPresentation:
    """Tests for presentation returned by needs_approval()."""

    def test_needs_approval_write_presentation(self, tmp_path):
        """Test needs_approval returns nice description for writes."""
        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(root=str(sandbox_root), mode="rw", write_approval=True)
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.needs_approval(
            "write_file", {"path": "output/test.txt", "content": "data"}
        )

        assert isinstance(result, dict)
        assert "description" in result
        assert "Write to output" in result["description"]
        assert "payload" in result
        assert result["payload"]["sandbox"] == "output"

    def test_needs_approval_read_presentation(self, tmp_path):
        """Test needs_approval returns nice description for reads."""
        sandbox_root = tmp_path / "data"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "data": PathConfig(root=str(sandbox_root), mode="ro", read_approval=True)
            }
        )
        sandbox = FileSandboxImpl(config)

        result = sandbox.needs_approval(
            "read_file", {"path": "data/test.txt"}
        )

        assert isinstance(result, dict)
        assert "description" in result
        assert "Read from data" in result["description"]
        assert "payload" in result
        assert result["payload"]["sandbox"] == "data"

    def test_approval_uses_needs_approval_presentation(self, tmp_path):
        """Test that ApprovalToolset uses needs_approval dict for nice descriptions."""
        approval_requests: list[ApprovalRequest] = []

        def capture_callback(request: ApprovalRequest) -> ApprovalDecision:
            approval_requests.append(request)
            return ApprovalDecision(approved=True)

        sandbox_root = tmp_path / "output"
        sandbox_root.mkdir()

        config = FileSandboxConfig(
            paths={
                "output": PathConfig(
                    root=str(sandbox_root),
                    mode="rw",
                    write_approval=True,
                )
            }
        )
        sandbox = FileSandboxImpl(config)
        approved_sandbox = ApprovalToolset(
            inner=sandbox,
            approval_callback=capture_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

        asyncio.run(
            approved_sandbox.call_tool(
                "write_file",
                {"path": "output/test.txt", "content": "test"},
                ctx,
                tool,
            )
        )

        # Check that the approval request has nice description from needs_approval dict
        assert len(approval_requests) == 1
        assert "Write to output" in approval_requests[0].description
        assert approval_requests[0].payload["sandbox"] == "output"


class TestSimpleToolsWithoutNeedsApproval:
    """Tests for simple tools that don't implement needs_approval."""

    def test_toolset_without_needs_approval_always_prompts(self, tmp_path):
        """Test that toolsets without needs_approval always prompt for tools in list.

        This tests the core behavior: when a toolset doesn't implement needs_approval,
        any tool in the require_approval list should always prompt for approval.
        """

        class SimpleToolset(AbstractToolset):
            """A minimal toolset without needs_approval."""

            @property
            def id(self):
                return "simple_toolset"

            async def get_tools(self, ctx):
                return {"do_something": MagicMock()}

            async def call_tool(self, name, tool_args, ctx, tool):
                if name == "do_something":
                    return f"Did: {tool_args.get('action', 'nothing')}"
                raise ValueError(f"Unknown tool: {name}")

        approval_requests: list[ApprovalRequest] = []

        def capture_callback(request: ApprovalRequest) -> ApprovalDecision:
            approval_requests.append(request)
            return ApprovalDecision(approved=True)

        toolset = SimpleToolset()

        approved_toolset = ApprovalToolset(
            inner=toolset,
            approval_callback=capture_callback,
        )

        ctx = MagicMock(spec=RunContext)
        tool = MagicMock()
        tool.function._requires_approval = False

        result = asyncio.run(
            approved_toolset.call_tool(
                "do_something",
                {"action": "test"},
                ctx,
                tool,
            )
        )

        # Should have prompted for approval with default presentation
        assert len(approval_requests) == 1
        assert approval_requests[0].tool_name == "do_something"
        assert "do_something" in approval_requests[0].description
        assert result == "Did: test"
