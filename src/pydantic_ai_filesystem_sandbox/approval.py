"""Approval integration for filesystem sandbox.

FileSystemToolset implements needs_approval() directly, so you can use
ApprovalToolset from pydantic-ai-blocking-approval without any special wrapper.

Usage:
    from pydantic_ai_filesystem_sandbox import FileSystemToolset, Sandbox, SandboxConfig, PathConfig
    from pydantic_ai_blocking_approval import ApprovalToolset

    sandbox = Sandbox(config)
    toolset = FileSystemToolset(sandbox)
    approved = ApprovalToolset(inner=toolset, approval_callback=my_callback)

Requires: pydantic-ai-blocking-approval>=0.5.0
Install with: pip install pydantic-ai-filesystem-sandbox[approval]
"""
