# Changelog

All notable changes to this project will be documented in this file.

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
