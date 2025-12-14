# pydantic-ai-filesystem-sandbox

Filesystem sandbox toolset for PydanticAI agents with LLM-friendly errors.

## Why This Package?

When building LLM agents that interact with the filesystem, you need:

1. **Sandboxing** - Restrict which directories the agent can access
2. **Read/Write Control** - Fine-grained permissions per path
3. **LLM-Friendly Errors** - Error messages that help the LLM correct its behavior
4. **Approval Integration** - Works with human-in-the-loop approval flows

## Architecture

- [**Sandbox**](docs/api.md#sandbox) - Security boundary for permission checking and path resolution
- [**FileSystemToolset**](docs/api.md#filesystemtoolset) - File I/O tools that use Sandbox
- **ApprovalToolset** - Optional wrapper for human-in-the-loop approval

## Installation

```bash
pip install pydantic-ai-filesystem-sandbox
```

## Quick Start

```python
from pydantic_ai import Agent
from pydantic_ai_filesystem_sandbox import (
    FileSystemToolset,
    Sandbox,
    SandboxConfig,
    PathConfig,
)

# Configure sandbox paths
config = SandboxConfig(paths={
    "input": PathConfig(root="./data/input", mode="ro"),   # Read-only
    "output": PathConfig(root="./data/output", mode="rw"), # Read-write
})

# Create the sandbox (security boundary)
sandbox = Sandbox(config)

# Create the toolset (file I/O tools)
toolset = FileSystemToolset(sandbox)

# Use with PydanticAI agent
agent = Agent("openai:gpt-4", toolsets=[toolset])
```

### Simple Usage

For simple cases, use the factory method:

```python
from pydantic_ai_filesystem_sandbox import FileSystemToolset

# Single directory with read-write access
toolset = FileSystemToolset.create_default("./data", mode="rw")

agent = Agent("openai:gpt-4", toolsets=[toolset])
```

## Configuration

See [API Reference](docs/api.md#configuration) for complete details.

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
config = SandboxConfig(paths={
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

### [RootSandboxConfig](docs/api.md#rootsandboxconfig) (single root)

You can configure a sandbox as a single virtual `/` rooted at a host directory:

```python
from pydantic_ai_filesystem_sandbox import Sandbox, SandboxConfig, RootSandboxConfig

config = SandboxConfig(root=RootSandboxConfig(root=".", readonly=False))
sandbox = Sandbox(config)

sandbox.resolve("/src/main.py")  # -> <cwd>/src/main.py
```

### Deriving child sandboxes

Use [`Sandbox.derive()`](docs/api.md#derive) to create a restricted child sandbox. By default the child has **no access** unless you explicitly allow paths.

```python
parent = Sandbox(config)

# Empty child (secure by default)
child = parent.derive()

# Allow read-only access to a subtree
reader = parent.derive(allow_read="output/reports")

# Allow read/write access to a subtree
writer = parent.derive(allow_write="output/reports")
```

## Available Tools

The [toolset](docs/api.md#filesystemtoolset) provides seven tools to the agent:

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

Write a text file to the sandbox (requires `mode="rw"`). Parent directories are created automatically.

```
Path format: 'sandbox_name/relative/path'
Parameters:
  - path: str (required)
  - content: str (required)
```

### edit_file

Edit a file by replacing exact text (requires `mode="rw"`).

```
Path format: 'sandbox_name/relative/path'
Parameters:
  - path: str (required)
  - old_text: str (required) - must match exactly and be unique
  - new_text: str (required)
```

### delete_file

Delete a file from the sandbox (requires `mode="rw"`).

```
Path format: 'sandbox_name/relative/path'
Parameters:
  - path: str (required)
```

### move_file

Move or rename a file within the sandbox (requires `mode="rw"` for both source and destination). Parent directories are created automatically.

```
Path format: 'sandbox_name/relative/path'
Parameters:
  - source: str (required)
  - destination: str (required)
```

### copy_file

Copy a file within the sandbox. Source can be read-only, destination requires `mode="rw"`. Parent directories are created automatically.

```
Path format: 'sandbox_name/relative/path'
Parameters:
  - source: str (required)
  - destination: str (required)
```

### list_files

List files matching a glob pattern.

```
Parameters:
  - path: str (default: "." for all sandboxes)
  - pattern: str (default: "**/*")
```

## LLM-Friendly Errors

All [errors](docs/api.md#errors) include guidance on what IS allowed:

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

# EditError
"Cannot edit 'output/file.txt': text not found in file.
Searched for: 'old text...'"
```

## Approval Integration

Works with [pydantic-ai-blocking-approval](https://github.com/zby/pydantic-ai-blocking-approval) for human-in-the-loop:

```python
from pydantic_ai_filesystem_sandbox import (
    ApprovableFileSystemToolset, Sandbox, SandboxConfig, PathConfig
)
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
```

`ApprovableFileSystemToolset` extends `FileSystemToolset` with `needs_approval()` (returns `ApprovalResult`) and `get_approval_description()` for the approval UI.

## Using the Sandbox Directly

The [`Sandbox`](docs/api.md#sandbox) class can be used independently for permission checking:

```python
sandbox = Sandbox(config)

# Check permissions
if sandbox.can_write("output/file.txt"):
    resolved = sandbox.resolve("output/file.txt")
    # ... perform operation

# Query boundaries
print(sandbox.readable_roots)  # ["input", "output"]
print(sandbox.writable_roots)  # ["output"]

# Check approval requirements
sandbox.needs_write_approval("output/file.txt")  # True/False
```

## [ReadResult](docs/api.md#readresult)

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
result = toolset.read("input/large.txt", max_chars=10000)
if result.truncated:
    # Continue reading
    result2 = toolset.read("input/large.txt", max_chars=10000, offset=10000)
```

## API Reference

See [docs/api.md](docs/api.md) for full API documentation.

## License

MIT
