# Hierarchical Root Sandboxes for `pydantic_ai_filesystem_sandbox`

## Purpose

Add first‑class support for:

1. A *single-root* sandbox that treats a host directory as virtual `/`.
2. Deriving *child sandboxes* from a parent via allowlists of readable/writable paths, with secure‑by‑default empty access unless explicitly allowed.
3. Clear invariants + LLM‑friendly errors when derivation arguments would expand permissions.

These capabilities allow llm-do to implement program‑level sandbox configuration and worker inheritance described in `docs/tasks/active/30-program-sandbox-config.md`, without the base package knowing about programs/workers.

## Current assumptions (from llm-do usage)

The library currently exposes:

- `SandboxConfig` with `paths: dict[str, PathConfig]`.
- `PathConfig { root, mode, suffixes, max_file_bytes, read_approval, write_approval }`.
- `Sandbox(config)` providing:
  - `resolve(path: str) -> Path`
  - `can_read(path)`, `can_write(path)`
  - `readable_roots`, `writable_roots`
  - LLM‑friendly errors: `PathNotInSandboxError`, `PathNotWritableError`, etc.

llm-do aliases and extends `Sandbox` in `llm_do/worker_sandbox.py` but does not override core access semantics.

## Goals / Non‑goals

**Goals**

- Provide an API to represent a sandbox rooted at a single directory (program sandbox).
- Provide a robust method to create child sandboxes that only restrict.
- Ensure restriction/readonly rules are enforced by the base package, so llm-do doesn’t re‑implement security logic.
- Keep errors LLM‑friendly: show what is allowed.

**Non‑goals**

- No new toolset behavior or approval logic in this library.
- No program/worker config schemas in this package.
- No OS/kernel isolation features.
- No dynamic runtime permission escalation.

## Public API additions

### 1. New config model: `RootSandboxConfig`

Pydantic model representing a single-root sandbox.

```python
class RootSandboxConfig(BaseModel):
    root: Path  # required, absolute or relative to caller-supplied base
    readonly: bool = False  # if true, no writes anywhere
    suffixes: list[str] | None = None  # allowed read suffixes; None => allow all
    max_file_bytes: int | None = None  # read size cap
    # approvals are intentionally omitted (library-agnostic); root-mode uses PathConfig defaults
```

Notes:

- `root` defines the host directory that becomes virtual `/`.
- This config does not use named paths; it is the “program sandbox” primitive.
- Root-mode uses a synthetic `PathConfig` for `/` with default approvals (`read_approval=False`, `write_approval=True`).

### 2. Extend `SandboxConfig` to accept root form

Allow `SandboxConfig` to be *either*:

- existing multi-path form, or
- the new root form.

```python
class SandboxConfig(BaseModel):
    # Exactly one of these must be provided:
    root: RootSandboxConfig | None = None
    paths: dict[str, PathConfig] | None = None

    @model_validator(mode="after")
    def _xor_root_paths(self): ...
```

Semantics:

- If `root` is set, virtual `/` maps to `root.root`.
- If `paths` is set, existing named-sandbox behavior applies.

### 3. New method: `Sandbox.derive(...)`

Derive a child sandbox from a parent sandbox using allowlists. Derivation is monotonic: the child can only see a subset of the parent.

```python
class Sandbox:
    def derive(
        self,
        *,
        allow_read: str | list[str] | None = None,
        allow_write: str | list[str] | None = None,
        readonly: bool | None = None,
        inherit: bool = False,  # if False, start empty (secure-by-default)
    ) -> "Sandbox": ...
```

Rules:

- If `inherit=False` (default), the child starts with no readable/writable paths.
- If `inherit=True`, the child starts with full parent access, then allowlists further restrict.
- `allow_read` and `allow_write` are sandbox paths/prefixes:
  - root-mode: must be sandbox-absolute (start with `/`).
  - multi-path: any parent-resolvable sandbox path, e.g. `"output/reports"`, `"output:reports"`, or a bare mount name `"output"`.
- Each allowlist entry is resolved inside the parent; if it resolves to a file, its parent directory is used as the allowed prefix.
- If `allow_read` is provided and `allow_write` is not, the child has no write access (least-privilege default).
- If `allow_write` is provided and `allow_read` is not, `allow_read` defaults to `allow_write` (write implies read).
- `readonly=True` forces no writes anywhere. `readonly=False` is only allowed if the parent has write access; otherwise it raises `SandboxPermissionEscalationError`.

The returned sandbox shares the parent’s suffix policy, size caps, and approval flags for any allowed paths.

### 4. New error type: `SandboxPermissionEscalationError`

Raised when `derive()` arguments attempt to expand access.

```python
class SandboxPermissionEscalationError(SandboxError):
    """Child sandbox attempted to expand permissions."""
```

Error message must:

- state what was requested,
- state parent’s effective boundaries,
- suggest valid alternatives.

Example:

> Cannot create child sandbox with readonly=False: parent sandbox is readonly. Child sandboxes may only restrict access.

## Core semantics

### Root sandbox resolution

For root-mode sandboxes:

- `resolve("foo/bar.txt")` resolves to `<root>/foo/bar.txt` after:
  - normalizing separators to POSIX,
  - rejecting `~`, drive letters, or absolute host paths,
  - resolving symlinks,
  - ensuring the final path is within `<root>`.

If not within root, raise `PathNotInSandboxError` listing `/` as readable root (and `/` as writable root only if not readonly).

### Multi-path sandboxes unchanged

If using `paths`, behavior stays as-is:

- paths are addressed as `"name/relative/path"`,
- readable/writable roots list those names.

### Child sandbox derivation (allowlists)

Given parent sandbox `P`, `P.derive(...)` produces a child `C` with the same path namespace as `P` (no rebasing). All reads/writes in `C` must satisfy both the parent’s rules and the child’s allowlists.

1. **Normalize allowlists and defaults**
   - `allow_read`/`allow_write` may be `None` (meaning “no further restriction when inheriting”) or a list (possibly empty) meaning an explicit allowlist.
   - If `allow_write` is provided and `allow_read` is `None`, set `allow_read = allow_write`.
   - If `allow_read` is provided and `allow_write` is `None`, treat `allow_write` as an explicit empty allowlist (no writes).
   - If both allowlists are `None` and `inherit=True`, there is no additional restriction beyond the parent (child == parent unless `readonly=True`).

2. **Resolve allowlist entries inside the parent**
   - For each entry in `allow_read` and `allow_write`:
     - Root-mode: entry must start with `/` and contain no `..`; resolve via `P.resolve(entry)`.
     - Multi-path: resolve via `P.get_path_config(entry)` / `P.resolve(entry)` to find the containing mount.
     - If the resolved path is a file, replace it with `resolved.parent`.
     - Record the allowed prefix in sandbox terms (e.g., `/src` or `output/reports`).

3. **Compute effective readable/writable areas**
   - If `inherit=False` (default): base readable/writable areas are empty.
   - If `inherit=True`: base readable/writable areas equal the parent’s effective areas.
   - Allowed read area:
     - `allow_read is None` ⇒ allowed reads = base readable area (no extra restriction).
     - `allow_read` list ⇒ allowed reads = union of resolved read prefixes.
   - Allowed write area (before `readonly`):
     - `allow_write is None` and `allow_read is None` ⇒ allowed writes = base writable area (no extra restriction).
     - `allow_write` list ⇒ allowed writes = union of resolved write prefixes.
     - `allow_write` explicit empty list ⇒ no writes.
   - Child readable area = base readable area ∩ allowed reads.
   - Child writable area = base writable area ∩ allowed writes.
   - If `readonly=True`, child writable area is empty.

4. **Invariants**
   - Child readable area ⊆ parent readable area.
   - Child writable area ⊆ parent writable area.
   - All path strings accepted by `C` are the same format as in `P`; only the allowed set changes.

### Read/write checks

- `can_read(path)` is true only if the parent allows the path and it is within an allowed read prefix (or `inherit=True` with no read allowlist).
- `can_write(path)` is true only if the parent allows writes to the path, it is within an allowed write prefix, and the child is not readonly.
- LLM-friendly errors should reference the child’s allowed prefixes.

## Inheritance needed (llm-do semantics)

We need monotonic restriction at two levels:

1. **Program → worker (config inheritance)**  
   Workers start with an empty sandbox derived from the program sandbox (secure-by-default) and may only reduce access:
   - inherit program effective root(s) and readonly state,
   - inherit read constraints (`suffixes`, `max_file_bytes`, etc.) without loosening,
   - if program has no sandbox, worker gets no FS access by default (llm-do policy).

   Worker config applies via allowlists:
   - `inherit: true` ⇒ start with full program access,
   - `allow_read: ["/subtree", "output/reports"]` ⇒ allow reads only in those prefixes,
   - `allow_write: [...]` ⇒ allow writes only in those prefixes (and implies read if no read allowlist is set),
   - `readonly: true` ⇒ force read-only regardless of allow_write.

2. **Caller worker → callee worker (runtime inheritance for delegation)**  
   When a worker calls another worker, callee sandbox is derived from caller’s effective sandbox and then further restricted by callee config, so no callee can see more than caller.

## Examples

### Program sandbox (single root)

```python
cfg = SandboxConfig(root=RootSandboxConfig(root=".", readonly=False))
sandbox = Sandbox(cfg, base_path=program_dir)
sandbox.resolve("/src/main.py")   # -> <program_dir>/src/main.py
sandbox.can_write("/tmp/x")       # False (outside)
```

### Worker allowlists + readonly

```python
program_sb = Sandbox(SandboxConfig(root=RootSandboxConfig(root=".", readonly=False)),
                     base_path=program_dir)

# secure default: empty sandbox
empty_sb = program_sb.derive()
empty_sb.can_read("/src/a.py")        # False

analyzer_sb = program_sb.derive(allow_read="/src", readonly=True)
analyzer_sb.can_read("/src/a.py")     # True
analyzer_sb.can_write("/src/a.py")    # False
analyzer_sb.resolve("/docs/x.md")     # PathNotInSandboxError
```

### Illegal escalation

```python
ro_parent = Sandbox(SandboxConfig(root=RootSandboxConfig(root=".", readonly=True)),
                    base_path=program_dir)

ro_parent.derive(inherit=True, readonly=False)
# -> SandboxPermissionEscalationError
```

## Tests to add (library side)

1. Root sandbox resolves relative and `/`‑prefixed paths correctly.
2. Root sandbox rejects escapes (`..`, symlink out, absolute host paths).
3. `derive()` with no arguments returns an empty sandbox that blocks all access.
4. `derive(allow_read=...)` allows reads only in those prefixes (root-mode and multi-path).
5. `derive(allow_read=..., allow_write=None)` yields no writes; `derive(allow_write=..., allow_read=None)` implies reads for those prefixes.
6. `derive(inherit=True)` yields full parent access, and allowlists further restrict.
7. Readonly downgrade and escalation rules enforced; multi-path sandboxes unaffected when not deriving.

## Open questions / future extensions (do not implement now)

- Should `derive()` allow *narrowing* suffixes or max_file_bytes? (Probably yes later; out of scope now.)
- Do we need a `Sandbox.clone()` helper? (Not required if `derive()` exists.)
- Should root-mode allow named aliases? (Leave to llm-do.)
