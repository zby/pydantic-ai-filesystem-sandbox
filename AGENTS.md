# AGENTS.md — Field Guide for AI Agents

Key expectations that frequently trip up automation agents. See `README.md` for setup and usage.

---

## Key References

- `README.md` — package overview, installation, usage patterns
- `src/pydantic_ai_filesystem_sandbox/` — source modules
- `tests/` — test suite with usage examples

---

## Architecture Overview

This package provides a **filesystem sandbox toolset** for PydanticAI agents:

```
FileSandboxImpl (AbstractToolset)
    ├── read_file — read text files with truncation/offset
    ├── write_file — write text files (rw paths only)
    ├── list_files — glob-based file listing
    └── needs_approval() — per-call approval decision (False | dict)

PathConfig (per-path settings)
    ├── root — base directory
    ├── mode — "ro" or "rw"
    ├── suffixes — allowed file extensions
    ├── max_file_bytes — size limit
    └── write_approval / read_approval — require approval flags

LLM-Friendly Errors
    ├── PathNotInSandboxError — includes list of valid paths
    ├── PathNotWritableError — includes writable paths
    ├── SuffixNotAllowedError — includes allowed suffixes
    └── FileTooLargeError — includes size limit
```

---

## Development

- Run `uv run pytest` before committing (tests use mocks, no live API calls)
- For executing python scripts use `uv run python`
- Style: PEP 8, type hints required, Pydantic models for data classes
- Do not preserve backwards compatibility; prioritize cleaner design
- Favor clear architecture over hacks; delete dead code when possible

---

## Module Responsibilities

| Module | Purpose |
|--------|---------|
| `sandbox.py` | Core implementation: config, errors, `FileSandboxImpl` toolset |
| `__init__.py` | Public API exports |

---

## Integration Patterns

1. **Standalone**: Create `FileSandboxImpl` and pass to agent as toolset (no approval)
2. **With Approval**: Wrap with `ApprovalToolset` from `pydantic-ai-blocking-approval`
3. **Custom**: Extend `FileSandboxImpl` and override `needs_approval()` to return custom dict

---

## Git Discipline

- **Never** `git add -A` — review `git status` and stage specific files
- Check `git diff` before committing
- Write clear commit messages (why, not just what)

---

## Common Pitfalls

- Path format is `sandbox_name/relative/path` — don't use absolute paths
- The `_base_path` defaults to `cwd()` — set it explicitly for reproducibility
- Directories are auto-created on `_setup_paths()` — be aware of side effects
- `needs_approval()` returns `False` or `dict` with description/payload — ApprovalToolset handles the rest
- All errors include guidance for the LLM — don't catch and re-raise without context

---

## Error Message Philosophy

All errors are designed to help the LLM self-correct:

```python
# BAD: "Permission denied"
# GOOD: "Cannot write to 'input/file.txt': path is read-only.\nWritable paths: output"
```

The LLM can parse these messages and adjust its behavior without additional prompting.

---

Stay focused, stay sandboxed, trust the boundaries.
