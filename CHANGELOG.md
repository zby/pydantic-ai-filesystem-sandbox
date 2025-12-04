# Changelog

All notable changes to this project will be documented in this file.

## [0.7.0] - 2025-01-04

### Added
- New `ApprovableFileSystemToolset` class: extends `FileSystemToolset` with approval protocol
- New `delete_file` tool: delete files from sandbox
- New `move_file` tool: move/rename files (auto-creates parent directories)
- New `copy_file` tool: copy files (source can be read-only)

### Changed
- **Breaking**: `pydantic-ai-blocking-approval` is now a required dependency (was optional)
- **Breaking**: `needs_approval()` and `get_approval_description()` moved from `FileSystemToolset` to `ApprovableFileSystemToolset`
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
