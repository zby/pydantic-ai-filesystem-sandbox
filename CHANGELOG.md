# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2025-11-30

### Changed
- **Breaking**: Requires `pydantic-ai-blocking-approval>=0.2.0` for approval functionality
- Updated examples and tests to use `approval_callback` parameter (renamed from `prompt_fn`)
- Removed `require_approval` parameter from `ApprovalToolset` usage examples

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
