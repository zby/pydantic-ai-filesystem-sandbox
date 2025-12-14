# API Reference

- [Configuration](#configuration): [SandboxConfig](#sandboxconfig), [RootSandboxConfig](#rootsandboxconfig), [PathConfig](#pathconfig)
- [Sandbox](#sandbox): [Methods](#methods), [Properties](#properties)
- [FileSystemToolset](#filesystemtoolset): [Methods](#methods-1)
- [ReadResult](#readresult)
- [Errors](#errors)

---

## Configuration

### SandboxConfig

Top-level configuration for a sandbox. Requires exactly one of `root` or `paths`.

```python
class SandboxConfig(BaseModel):
    root: RootSandboxConfig | None = None  # Single-root mode
    paths: dict[str, PathConfig] | None = None  # Multi-path mode
```

### RootSandboxConfig

Configuration for a single-root sandbox where a host directory becomes virtual `/`.

```python
class RootSandboxConfig(BaseModel):
    root: Path  # Host directory that becomes virtual '/'
    readonly: bool = False  # If true, no writes anywhere
    suffixes: list[str] | None = None  # Allowed file extensions (None = all)
    max_file_bytes: int | None = None  # Max file size (None = no limit)
```

### PathConfig

Configuration for a named path in multi-path mode.

```python
class PathConfig(BaseModel):
    root: str  # Root directory path
    mode: Literal["ro", "rw"] = "ro"  # Access mode
    suffixes: list[str] | None = None  # Allowed file extensions
    max_file_bytes: int | None = None  # Max file size
    write_approval: bool = True  # Require approval for writes
    read_approval: bool = False  # Require approval for reads
```

---

## Sandbox

Security boundary for permission checking and path resolution. Does not perform file I/O.

### Constructor

```python
Sandbox(config: SandboxConfig, base_path: Path | None = None)
```

- `config`: Sandbox configuration
- `base_path`: Base path for resolving relative roots (defaults to `cwd()`)

### Methods

#### resolve

```python
def resolve(self, path: str) -> Path
```

Resolve a sandbox path to an absolute filesystem path.

- **Raises**: `PathNotInSandboxError` if path is outside sandbox

#### can_read / can_write

```python
def can_read(self, path: str) -> bool
def can_write(self, path: str) -> bool
```

Check if a path is readable/writable within sandbox boundaries.

#### needs_read_approval / needs_write_approval

```python
def needs_read_approval(self, path: str) -> bool
def needs_write_approval(self, path: str) -> bool
```

Check if reading/writing this path requires approval.

#### derive

```python
def derive(
    self,
    *,
    allow_read: str | list[str] | None = None,
    allow_write: str | list[str] | None = None,
    readonly: bool | None = None,
    inherit: bool = False,
) -> Sandbox
```

Create a restricted child sandbox using allowlists.

- `allow_read`: Paths/prefixes the child can read
- `allow_write`: Paths/prefixes the child can write
- `readonly`: Force read-only (raises error if parent is readonly and this is False)
- `inherit`: If False (default), child starts with no access; if True, inherits parent access

**Raises**: `SandboxPermissionEscalationError` if attempting to expand permissions.

#### get_path_config

```python
def get_path_config(self, path: str) -> tuple[str, Path, PathConfig]
```

Get sandbox name, resolved path, and config for a path.

#### check_suffix / check_size

```python
def check_suffix(self, path: Path, config: PathConfig) -> None
def check_size(self, path: Path, config: PathConfig) -> None
```

Validate file suffix and size against config limits.

- **Raises**: `SuffixNotAllowedError`, `FileTooLargeError`

### Properties

```python
readable_roots: list[str]  # List of readable path roots
writable_roots: list[str]  # List of writable path roots
```

---

## FileSystemToolset

PydanticAI AbstractToolset providing file I/O tools.

### Constructor

```python
FileSystemToolset(
    sandbox: Sandbox,
    id: str | None = None,
    max_retries: int = 1,
)
```

### Factory Method

```python
@classmethod
def create_default(
    cls,
    root: str | Path,
    mode: str = "rw",
    id: str | None = None,
) -> FileSystemToolset
```

Create a toolset with a single "data" sandbox path.

### Methods

#### read

```python
def read(
    self,
    path: str,
    max_chars: int = 20_000,
    offset: int = 0,
) -> ReadResult
```

Read a text file from the sandbox.

#### write

```python
def write(self, path: str, content: str) -> str
```

Write a text file. Parent directories are created automatically.

#### edit

```python
def edit(self, path: str, old_text: str, new_text: str) -> str
```

Edit a file by replacing exact text. `old_text` must match exactly and appear only once.

#### delete

```python
def delete(self, path: str) -> str
```

Delete a file from the sandbox.

#### move

```python
def move(self, source: str, destination: str) -> str
```

Move or rename a file. Parent directories of destination are created automatically.

#### copy

```python
def copy(self, source: str, destination: str) -> str
```

Copy a file. Source can be read-only, destination must be writable.

#### list_files

```python
def list_files(self, path: str = ".", pattern: str = "**/*") -> list[str]
```

List files matching a glob pattern.

### Properties

```python
sandbox: Sandbox  # Access the underlying sandbox
```

---

## ReadResult

Result of reading a file.

```python
class ReadResult(BaseModel):
    content: str  # The file content read
    truncated: bool  # True if more content exists
    total_chars: int  # Total file size in characters
    offset: int  # Starting position used
    chars_read: int  # Characters actually returned
```

---

## Errors

All errors inherit from `SandboxError` and include LLM-friendly messages with guidance on what IS allowed.

| Error | When Raised |
|-------|-------------|
| `SandboxError` | Base class for all sandbox errors |
| `PathNotInSandboxError` | Path is outside sandbox boundaries |
| `PathNotWritableError` | Attempting to write to a read-only path |
| `SandboxPermissionEscalationError` | Child sandbox derivation would expand permissions |
| `SuffixNotAllowedError` | File extension not in allowed list |
| `FileTooLargeError` | File exceeds size limit |
| `EditError` | Edit failed (text not found or not unique) |
