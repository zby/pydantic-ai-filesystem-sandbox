# pydantic-ai-filesystem-sandbox

Filesystem sandbox toolset for PydanticAI agents with LLM-friendly errors.

## Why This Package?

When building LLM agents that interact with the filesystem, you need:

1. **Sandboxing** - Restrict which directories the agent can access
2. **Read/Write Control** - Fine-grained permissions per path
3. **LLM-Friendly Errors** - Error messages that help the LLM correct its behavior
4. **Approval Integration** - Works with human-in-the-loop approval flows

This package provides a `FileSandboxImpl` toolset that implements all of these as a PydanticAI `AbstractToolset`.

## Installation

```bash
pip install pydantic-ai-filesystem-sandbox
```

## Quick Start

```python
from pydantic_ai import Agent
from pydantic_ai_filesystem_sandbox import (
    FileSandboxImpl,
    FileSandboxConfig,
    PathConfig,
)

# Configure sandbox paths
config = FileSandboxConfig(paths={
    "input": PathConfig(root="./data/input", mode="ro"),   # Read-only
    "output": PathConfig(root="./data/output", mode="rw"), # Read-write
})

# Create the sandbox toolset
sandbox = FileSandboxImpl(config)

# Use with PydanticAI agent
agent = Agent("openai:gpt-4", toolsets=[sandbox])
```

## Configuration

### PathConfig Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `root` | str | required | Root directory path |
| `mode` | "ro" \| "rw" | "ro" | Access mode |
| `suffixes` | list[str] \| None | None | Allowed file extensions (None = all) |
| `max_file_bytes` | int \| None | None | Maximum file size limit |
| `write_approval` | bool | True | Require approval for writes |
| `read_approval` | bool | False | Require approval for reads |

### Example Configuration

```python
config = FileSandboxConfig(paths={
    # Read-only input - any file type
    "input": PathConfig(
        root="./data/input",
        mode="ro",
    ),
    # Read-write output - only markdown and text
    "output": PathConfig(
        root="./data/output",
        mode="rw",
        suffixes=[".md", ".txt"],
        max_file_bytes=1_000_000,  # 1MB limit
    ),
    # Config files - read-only, requires approval
    "config": PathConfig(
        root="./config",
        mode="ro",
        read_approval=True,
    ),
})
```

## Available Tools

The sandbox provides three tools to the agent:

### read_file

Read a text file from the sandbox.

```
Path format: 'sandbox_name/relative/path'
Parameters:
  - path: str (required)
  - max_chars: int (default: 20,000)
  - offset: int (default: 0)
```

### write_file

Write a text file to the sandbox (requires `mode="rw"`).

```
Path format: 'sandbox_name/relative/path'
Parameters:
  - path: str (required)
  - content: str (required)
```

### list_files

List files matching a glob pattern.

```
Parameters:
  - path: str (default: "." for all sandboxes)
  - pattern: str (default: "**/*")
```

## LLM-Friendly Errors

All errors include guidance on what IS allowed:

```python
# PathNotInSandboxError
"Cannot access 'secret/file.txt': path is outside sandbox.
Readable paths: input, output"

# PathNotWritableError
"Cannot write to 'input/file.txt': path is read-only.
Writable paths: output"

# SuffixNotAllowedError
"Cannot access 'output/data.json': suffix '.json' not allowed.
Allowed suffixes: .md, .txt"

# FileTooLargeError
"Cannot read 'output/huge.txt': file too large (5,000,000 bytes).
Maximum allowed: 1,000,000 bytes"
```

## Approval Integration

Works with [pydantic-ai-blocking-approval](https://github.com/zby/pydantic-ai-blocking-approval) for human-in-the-loop:

```python
from pydantic_ai_filesystem_sandbox import FileSandboxImpl, FileSandboxConfig, PathConfig
from pydantic_ai_blocking_approval import ApprovalToolset, ApprovalController

# Create sandbox
config = FileSandboxConfig(paths={
    "output": PathConfig(root="./output", mode="rw", write_approval=True),
})
sandbox = FileSandboxImpl(config)

# Wrap with approval
controller = ApprovalController(mode="interactive", approval_callback=my_prompt_fn)
approved_sandbox = ApprovalToolset(
    inner=sandbox,
    prompt_fn=controller.approval_callback,
    memory=controller.memory,
    require_approval=["write_file", "read_file"],
)

agent = Agent(..., toolsets=[approved_sandbox])
```

The sandbox implements `needs_approval()` and `present_for_approval()` for fine-grained approval control.

## ReadResult

The `read_file` tool returns a `ReadResult` object:

```python
class ReadResult(BaseModel):
    content: str        # The file content read
    truncated: bool     # True if more content exists
    total_chars: int    # Total file size in characters
    offset: int         # Starting position used
    chars_read: int     # Characters actually returned
```

This allows agents to handle large files by reading in chunks:

```python
# First read
result = sandbox.read("input/large.txt", max_chars=10000)
if result.truncated:
    # Continue reading
    result2 = sandbox.read("input/large.txt", max_chars=10000, offset=10000)
```

## API Reference

### Configuration

- `FileSandboxConfig` - Top-level configuration with named paths
- `PathConfig` - Configuration for a single sandbox path

### Toolset

- `FileSandboxImpl` - PydanticAI AbstractToolset implementation

### Errors

- `FileSandboxError` - Base class for all sandbox errors
- `PathNotInSandboxError` - Path outside sandbox boundaries
- `PathNotWritableError` - Write to read-only path
- `SuffixNotAllowedError` - File extension not allowed
- `FileTooLargeError` - File exceeds size limit

### Types

- `ReadResult` - Result of read operations with metadata

## License

MIT
