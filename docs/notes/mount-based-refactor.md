# Mount-Based Architecture Refactor

## Goal

Replace dual-mode architecture (root mode vs multi-path mode) with Docker-style mounts.

## Current Problems

1. Two path formats: `sandbox_name/path` vs `/path`
2. `_is_root_mode` branches throughout codebase (8 occurrences)
3. `_paths` dict handling complexity (14 occurrences)
4. User confusion about which mode they're in
5. Schema docs must explain both formats

## New Design

### Configuration

```python
class Mount(BaseModel):
    """Mount a host directory into the virtual filesystem."""
    host_path: Path
    mount_point: str  # e.g., "/docs", "/data" - must start with /
    mode: Literal["ro", "rw"] = "ro"
    suffixes: list[str] | None = None
    max_file_bytes: int | None = None
    write_approval: bool = True
    read_approval: bool = False

class SandboxConfig(BaseModel):
    mounts: list[Mount]  # At least one required
```

### Path Resolution

All paths use `/mount_point/relative` format:
- `/docs/readme.md` → host `/home/user/docs/readme.md`
- `/data/output.json` → host `/var/data/output.json`

Resolution algorithm:
1. Normalize path (strip whitespace, resolve `//` to `/`)
2. Find longest matching mount point prefix
3. Resolve relative part within that mount's host_path
4. Validate result stays within host_path (prevent traversal)

### Simplified Sandbox Class

```python
class Sandbox:
    def __init__(self, config: SandboxConfig, base_path: Path | None = None):
        self.config = config
        self._base_path = base_path or Path.cwd()
        self._mounts: list[tuple[str, Path, Mount]] = []  # (mount_point, resolved_host_path, config)
        self._setup_mounts()

    def _setup_mounts(self) -> None:
        for mount in self.config.mounts:
            host = Path(mount.host_path)
            if not host.is_absolute():
                host = (self._base_path / host).resolve()
            host.mkdir(parents=True, exist_ok=True)
            self._mounts.append((mount.mount_point, host, mount))
        # Sort by mount_point length descending (longest prefix first)
        self._mounts.sort(key=lambda x: len(x[0]), reverse=True)

    def _find_mount(self, path: str) -> tuple[str, Path, Mount]:
        """Find the mount that contains this path."""
        normalized = self._normalize_path(path)
        for mount_point, host_path, config in self._mounts:
            if normalized == mount_point or normalized.startswith(mount_point + "/"):
                return mount_point, host_path, config
        raise PathNotInSandboxError(path, self.readable_roots)

    def resolve(self, path: str) -> Path:
        mount_point, host_path, _ = self._find_mount(path)
        relative = path[len(mount_point):].lstrip("/")
        resolved = (host_path / relative).resolve()
        # Validate stays within mount
        resolved.relative_to(host_path)  # Raises ValueError if escape
        return resolved
```

### Derive Simplification

```python
def derive(self, *, allow_read: str | list[str] | None = None, ...):
    # Allowlists are just paths like "/docs", "/data/subdir"
    # No more sandbox_name vs root-mode handling
    ...
```

## Migration

### Old API (deprecated but supported for one version)

```python
# Multi-path mode
SandboxConfig(paths={
    "docs": PathConfig(root="./docs", mode="ro"),
    "data": PathConfig(root="./data", mode="rw"),
})

# Root mode
SandboxConfig(root=RootSandboxConfig(root="./project", readonly=False))
```

### New API

```python
# Equivalent to multi-path
SandboxConfig(mounts=[
    Mount(host_path="./docs", mount_point="/docs", mode="ro"),
    Mount(host_path="./data", mount_point="/data", mode="rw"),
])

# Equivalent to root mode
SandboxConfig(mounts=[
    Mount(host_path="./project", mount_point="/", mode="rw"),
])
```

### Convenience Factory

```python
@classmethod
def single_root(cls, root: Path, mode: str = "rw") -> "SandboxConfig":
    """Create a sandbox with a single root mount."""
    return cls(mounts=[Mount(host_path=root, mount_point="/", mode=mode)])
```

## Files to Change

1. `sandbox.py` - Core refactor
   - New `Mount` and `SandboxConfig` models
   - Remove `PathConfig`, `RootSandboxConfig` (or deprecate)
   - Remove `_is_root_mode`, `_root_path`, `_root_path_config`
   - Replace `_paths` dict with `_mounts` list
   - Simplify `_find_mount()` (was `_find_path_for_boundary` + `_find_root_mode_boundary` + `_find_multi_path_boundary`)
   - Simplify `_resolve_allow_prefix()`

2. `toolset.py` - Simplify path handling
   - Update `_format_result_path()` - always use `/mount/path` format
   - Update schema descriptions - one format only
   - Remove any mode-specific logic

3. `approval_toolset.py` - Minor updates
   - `_make_display_path()` already uses `_format_result_path()`

4. `__init__.py` - Update exports
   - Export `Mount`
   - Deprecate `PathConfig`, `RootSandboxConfig`

5. Tests - Update all
   - Convert to mount-based configs
   - Verify same behavior with new API

## Open Questions

1. Should we support migration shim (auto-convert old configs)?
2. Mount point validation - allow `/` only once? Nested mounts?
3. What if two mounts overlap? First match wins?

## Implementation Order

1. Add new `Mount` model alongside existing
2. Add `_mounts` list, populate from both old and new config
3. Refactor internal methods to use `_mounts`
4. Remove old config support
5. Update tests
6. Update docs
