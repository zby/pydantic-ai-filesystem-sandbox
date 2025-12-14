# Changelog

All notable changes to this project will be documented in this file.

## [0.9.1] - Unreleased

### Fixed
- Binary file handling: `read_file` now raises a clear error for non-UTF-8 files
- Validation: reject negative `offset` and `max_chars` parameters in `read_file`
- Security: error messages no longer leak host filesystem paths (use virtual paths)
- Security: derived sandbox permission checks now properly traverse parent chain
- Allowlist entries pointing to files now raise an error with suggested parent directory

### Changed
- **Nested mounts now allowed**: mounts like `/data` and `/data/special` can coexist; the most specific mount wins
- Simplified internal derive system (consolidated state variables)
- `check_suffix()` and `check_size()` now accept optional `display_path` parameter
- Terminology: error messages now use "mount" instead of "sandbox"

## [0.9.0] - 2025-01-14

### Added
- New `Mount` model: Docker-style mounts for mapping host directories to virtual paths
- Unified path format: all paths now use `/mount/path` format (e.g., `/docs/file.txt`)
- Path normalization: paths without leading `/` are automatically normalized
- Mount validation: rejects duplicate mount points

### Changed
- **Breaking**: Path format now always uses `/mount/path` style
- `SandboxConfig` now accepts `mounts=[Mount(...)]` as the primary configuration method
- `readable_roots` and `writable_roots` now return mount points (e.g., `["/docs", "/output"]`)
- Simplified internal architecture: removed dual-mode handling (root mode vs multi-path mode)

### Deprecated
- `PathConfig` - use `Mount` instead
- `RootSandboxConfig` - use `Mount(mount_point="/")` instead
- `SandboxConfig(paths=...)` - use `SandboxConfig(mounts=[...])` instead
- `SandboxConfig(root=...)` - use `SandboxConfig(mounts=[Mount(mount_point="/")])` instead

### Migration
```python
# Old (0.8.0) - multi-path mode
config = SandboxConfig(paths={
    "docs": PathConfig(root="./docs", mode="ro"),
})

# New (0.9.0)
config = SandboxConfig(mounts=[
    Mount(host_path="./docs", mount_point="/docs", mode="ro"),
])

# Old (0.8.0) - root mode
config = SandboxConfig(root=RootSandboxConfig(root=".", readonly=False))

# New (0.9.0)
config = SandboxConfig(mounts=[
    Mount(host_path=".", mount_point="/", mode="rw"),
])
```

Note: The deprecated APIs still work via an internal conversion layer that emits `DeprecationWarning`.

## [0.8.0] - 2025-01-14

### Added
- New `ApprovableFileSystemToolset` class: extends `FileSystemToolset` with approval protocol
- New `delete_file` tool: delete files from sandbox
- New `move_file` tool: move/rename files (auto-creates parent directories)
- New `copy_file` tool: copy files (source can be read-only)
- New `RootSandboxConfig`: single-root sandbox mode where a host directory becomes virtual `/`
- New `Sandbox.derive()` method: create restricted child sandboxes with allowlists
- New `SandboxPermissionEscalationError`: raised when child derivation would expand permissions

### Changed
- **Breaking**: `pydantic-ai-blocking-approval` is now a required dependency (was optional)
- **Breaking**: `needs_approval()` and `get_approval_description()` moved from `FileSystemToolset` to `ApprovableFileSystemToolset`
- **Breaking**: `SandboxConfig` now requires exactly one of `root` or `paths` (cannot be empty or have both)
- `write_file` now documents that parent directories are created automatically
- Requires `pydantic-ai-blocking-approval>=0.7.0`

### Migration
```python
# Old (0.6.0) - if using approval
from pydantic_ai_filesystem_sandbox import FileSystemToolset
toolset = FileSystemToolset(sandbox)
approved = ApprovalToolset(inner=toolset, ...)

# New (0.7.0) - use ApprovableFileSystemToolset for approval
from pydantic_ai_filesystem_sandbox import ApprovableFileSystemToolset
toolset = ApprovableFileSystemToolset(sandbox)
approved = ApprovalToolset(inner=toolset, ...)

# If NOT using approval, FileSystemToolset still works unchanged
from pydantic_ai_filesystem_sandbox import FileSystemToolset
toolset = FileSystemToolset(sandbox)  # No changes needed

# Old (0.6.0) - SandboxConfig with empty paths
config = SandboxConfig()  # No longer valid

# New (0.7.0) - must specify root or paths
config = SandboxConfig(paths={"data": PathConfig(root="./data")})
# Or use root mode:
config = SandboxConfig(root=RootSandboxConfig(root="."))
```

## [0.6.0] - 2025-12-02

### Added
- New `Sandbox` class: pure security boundary for permission checking and path resolution
- New `FileSystemToolset` class: file I/O tools that use Sandbox
- `FileSystemToolset.create_default()` factory for simple setups

### Changed
- **Breaking**: Architecture refactored to separate concerns:
  - `Sandbox` handles policy (permissions, boundaries, approval requirements)
  - `FileSystemToolset` handles file I/O and implements `needs_approval()`
- **Breaking**: Requires `pydantic-ai-blocking-approval>=0.5.0`
- **Breaking**: Removed `FileSandboxApprovalToolset` - use `ApprovalToolset` directly
- **Breaking**: Removed `FileSandboxImpl`, `FileSandboxConfig`, `FileSandboxError` aliases

### Migration
```python
# Old (0.5.0)
from pydantic_ai_filesystem_sandbox import FileSandboxImpl, FileSandboxConfig
from pydantic_ai_filesystem_sandbox.approval import FileSandboxApprovalToolset

sandbox = FileSandboxImpl(config)
approved = FileSandboxApprovalToolset(inner=sandbox, approval_callback=callback)

# New (0.6.0)
from pydantic_ai_filesystem_sandbox import Sandbox, SandboxConfig, FileSystemToolset
from pydantic_ai_blocking_approval import ApprovalToolset

sandbox = Sandbox(config)
toolset = FileSystemToolset(sandbox)
approved = ApprovalToolset(inner=toolset, approval_callback=callback)
```

## [0.5.0] - 2025-11-30

### Added
- New `FileSandboxApprovalToolset` class in `pydantic_ai_filesystem_sandbox.approval`
- Subclasses `ApprovalToolset` with filesystem-aware approval logic

### Changed
- **Breaking**: Requires `pydantic-ai-blocking-approval>=0.4.0`
- Updated to new approval API: `config` dict instead of `pre_approved` list
- `needs_approval()` on `FileSandboxImpl` is now a helper method used by `FileSandboxApprovalToolset`

### Migration
```python
# Old (0.4.0)
from pydantic_ai_blocking_approval import ApprovalToolset
approved_sandbox = ApprovalToolset(inner=sandbox, approval_callback=callback)

# New (0.5.0)
from pydantic_ai_filesystem_sandbox.approval import FileSandboxApprovalToolset
approved_sandbox = FileSandboxApprovalToolset(inner=sandbox, approval_callback=callback)
```

## [0.4.0] - 2025-11-30

### Added
- New `edit_file` tool for search/replace editing (like Claude Code's Edit tool)
- `EditError` exception for edit operation failures
- LLM provides `old_text` and `new_text`; CLI can render as colored diff for approval

## [0.3.0] - 2025-11-30

### Changed
- **Breaking**: Requires `pydantic-ai-blocking-approval>=0.3.0` for approval functionality
- Simplified `needs_approval()` return - now only returns `description` key (no `payload`)
- ApprovalRequest now uses `tool_args` instead of `payload` for session cache matching

## [0.2.0] - 2025-11-30

### Changed
- **Breaking**: Requires `pydantic-ai-blocking-approval>=0.2.0` for approval functionality
- Updated examples and tests to use `approval_callback` parameter (renamed from `prompt_fn`)

### Added
- GitHub Actions CI workflow (tests on Python 3.12, 3.13, 3.14)
- LICENSE file (MIT)
- This changelog

## [0.1.0] - 2025-11-29

### Added
- Initial release
- `FileSandboxImpl` toolset for PydanticAI agents
- `PathConfig` for per-path configuration (root, mode, suffixes, max_file_bytes)
- LLM-friendly error messages that guide correction
- `read_file`, `write_file`, `list_files` tools
- `needs_approval()` protocol support for fine-grained approval control
- Integration with `pydantic-ai-blocking-approval` for human-in-the-loop approval
