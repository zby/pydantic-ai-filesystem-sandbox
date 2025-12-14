# AGENTS.md — Field Guide for AI Agents

Filesystem sandbox for PydanticAI agents. Separates security policy (Sandbox) from file I/O (FileSystemToolset).

See `README.md` for full documentation.

---

## Quick Reference

| What | Where |
|------|-------|
| Usage & API | `README.md` |
| Security boundary | `sandbox.py` → `Sandbox` |
| File I/O tools | `toolset.py` → `FileSystemToolset` |
| Test examples | `tests/` |

---

## Development

- **Always use `uv run`** — never use global `pytest` or `python`
  - `uv run pytest` — run tests
  - `uv run python script.py` — run scripts
- Style: PEP 8, type hints required
- No backwards compatibility — prioritize clean design
- Delete dead code

---

## Common Pitfalls

- **Path format**: Always use `/mount/relative/path` (e.g., `/docs/readme.md`)
- **Config choice**: `SandboxConfig` requires `mounts=[Mount(...)]` (at least one mount)
- **Base path**: `_base_path` defaults to `cwd()` — set explicitly for reproducibility
- **Auto-creation**: Directories are created on init — be aware of side effects
- **Separation**: `Sandbox` = policy, `FileSystemToolset` = I/O — keep them separate
- **Approval return**: `needs_approval()` returns `ApprovalResult` (blocked/pre_approved/needs_approval)
- **Error context**: All errors include guidance — don't catch and re-raise without it
- **Derive safety**: `Sandbox.derive()` creates restricted children — default is empty (no access)

---

## Git Discipline

- **Never** `git add -A` — review `git status` and stage specific files
- Check `git diff` before committing
- Write clear commit messages (why, not just what)

---

## Notes

- `docs/notes/` — working design documents, explorations, bug investigations
- Create notes to offload complex thinking that doesn't fit in a commit or TODO
- Include "Open Questions" section for unresolved decisions
- Move to `docs/notes/archive/` when resolved or implemented
