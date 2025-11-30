# Specification: Structured Approval Payloads for File Operations

**Status:** Draft - Pending CLI Review
**Version:** 0.1
**Date:** 2025-01-30

## Overview

This specification proposes a structured approach for file operation approvals between the filesystem sandbox and CLI. The goal is to enable rich display of file changes (diffs, paging) while keeping the sandbox decoupled from display concerns.

### Design Principles

1. **Sandbox provides data, CLI decides display** - The sandbox returns structured payloads with all information needed for display; the CLI interprets and renders them.
2. **Typed payloads** - Each operation type has a well-defined payload schema, enabling CLI to handle each appropriately.
3. **Progressive enhancement** - CLI can fall back to simple text display if it doesn't recognize a payload type.
4. **Paging is a CLI concern** - The sandbox provides metadata (line counts, truncation info) but doesn't control terminal interaction.

---

## New Tool: `edit_file`

### Motivation

LLMs are trained to edit files using patch-style operations rather than full rewrites. Benefits:

- **Token efficient** - Only output the changed portion, not entire file
- **Reviewable** - Users see exactly what changes, not a wall of text
- **Safer** - Uniqueness check prevents edits in wrong location
- **Composable** - Multiple small edits are clearer than one large rewrite

### Tool Signature

```python
def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False
) -> EditResult
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | `str` | Yes | Path in format `sandbox_name/relative/path` |
| `old_string` | `str` | Yes | Exact text to find and replace. Must be unique in file (unless `replace_all=True`) |
| `new_string` | `str` | Yes | Replacement text. Can be empty string to delete. |
| `replace_all` | `bool` | No | If `True`, replace all occurrences. Default: `False` |

### Behavior

1. Locate `old_string` in the file
2. If `replace_all=False` and multiple matches found, return error with match count and locations
3. If no matches found, return error with helpful context (similar strings, line numbers)
4. Replace the text and return result

### Return Value

```python
class EditResult(BaseModel):
    """Result of an edit_file operation."""
    path: str                    # Normalized path
    replacements_made: int       # Number of replacements
    lines_changed: int           # Lines affected
    message: str                 # Human-readable summary
```

### Error Cases

| Error | When | Message Format |
|-------|------|----------------|
| `EditNotUniqueError` | Multiple matches, `replace_all=False` | "Found {n} matches for old_string. Use replace_all=True or provide more context. Matches at lines: {lines}" |
| `EditNotFoundError` | No matches | "old_string not found in {path}. File contains {n} lines. Did you mean: {suggestions}?" |
| `PathNotInSandboxError` | Path outside sandbox | (existing error) |
| `PathNotWritableError` | Path is read-only | (existing error) |

---

## Structured Approval Payloads

### Payload Schema

All approval payloads share a common base structure:

```python
class ApprovalPayload(TypedDict):
    """Base structure for all approval payloads."""
    type: str                    # Discriminator: "write", "edit", "read", etc.
    description: str             # Human-readable one-line description
    path: str                    # File path being operated on
    sandbox: str                 # Sandbox name
```

### Write Approval Payload

Returned by `needs_approval()` for `write_file` operations:

```python
class WriteApprovalPayload(TypedDict):
    type: Literal["write"]
    description: str             # e.g., "Write 150 lines to workspace/src/main.py"
    path: str                    # Full sandbox path
    sandbox: str                 # Sandbox name

    # Content info
    content: str                 # Full content to write
    content_lines: int           # Total line count
    content_bytes: int           # Size in bytes

    # For preview/paging
    preview: str                 # First N lines (e.g., 50 lines)
    preview_truncated: bool      # True if preview != full content

    # Context (for existing files)
    file_exists: bool            # Whether file already exists
    existing_lines: int | None   # Current file line count (if exists)
    existing_bytes: int | None   # Current file size (if exists)
```

### Edit Approval Payload

Returned by `needs_approval()` for `edit_file` operations:

```python
class EditApprovalPayload(TypedDict):
    type: Literal["edit"]
    description: str             # e.g., "Edit workspace/src/main.py: replace 5 lines"
    path: str
    sandbox: str

    # The edit itself
    old_string: str              # Text being replaced
    new_string: str              # Replacement text
    replace_all: bool            # Whether replacing all occurrences

    # Diff representation
    unified_diff: str            # Standard unified diff format
    diff_lines: int              # Number of lines in diff

    # Context
    match_line: int              # Line number of first match (1-indexed)
    match_count: int             # Total matches (relevant if replace_all)
    context_before: str          # ~3 lines before the match
    context_after: str           # ~3 lines after the match

    # File info
    file_lines: int              # Total lines in file
    file_bytes: int              # File size
```

### Read Approval Payload

Returned by `needs_approval()` for `read_file` when `read_approval=True`:

```python
class ReadApprovalPayload(TypedDict):
    type: Literal["read"]
    description: str             # e.g., "Read workspace/secrets/config.yaml"
    path: str
    sandbox: str

    # File info (no content - that's what we're approving access to)
    file_lines: int
    file_bytes: int
    file_exists: bool
```

---

## CLI Handling

### Dispatch by Type

The CLI should dispatch on the `type` field:

```python
async def handle_approval(payload: dict) -> bool:
    match payload.get("type"):
        case "edit":
            return await handle_edit_approval(payload)
        case "write":
            return await handle_write_approval(payload)
        case "read":
            return await handle_read_approval(payload)
        case _:
            # Fallback: show description, prompt yes/no
            return await handle_generic_approval(payload)
```

### Edit Approval Display

Recommended CLI behavior for edit approvals:

```
┌─ Edit: workspace/src/main.py (line 42) ─────────────────────┐
│                                                              │
│  @@ -40,7 +40,7 @@                                          │
│   def calculate_total(items):                                │
│       """Calculate the total price."""                       │
│  -    total = 0                                              │
│  +    total = Decimal('0')                                   │
│       for item in items:                                     │
│           total += item.price                                │
│       return total                                           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
Apply this edit? [y/n/v(iew full file)]
```

**Paging:** If `diff_lines > terminal_height`, use a pager (less-style scrolling).

### Write Approval Display

Recommended CLI behavior for write approvals:

```
┌─ Write: workspace/output/report.md (150 lines, new file) ───┐
│                                                              │
│  # Analysis Report                                           │
│                                                              │
│  ## Summary                                                  │
│  This report contains...                                     │
│  ... (showing 50 of 150 lines)                               │
│                                                              │
└──────────────────────────────────────────────────────────────┘
Write this file? [y/n/v(iew all)]
```

**Paging:** If user selects "view all", page through full content.

**Existing file warning:** If `file_exists=True`, show warning:
```
⚠ This will overwrite existing file (was 42 lines, now 150 lines)
```

---

## Paging Protocol

### Approach: Full Content in Payload

The payload contains the full content (`content` for writes, `unified_diff` for edits). The CLI decides whether to:

1. Show it all (small content)
2. Show preview + offer to view full
3. Immediately invoke pager (very large)

### Thresholds (Suggested)

| Content Size | CLI Behavior |
|--------------|--------------|
| < 30 lines | Show in-line |
| 30-100 lines | Show preview, offer [v]iew |
| > 100 lines | Auto-invoke pager |

### Future: Chunked Fetching

For extremely large files (>1MB), we may need chunked fetching:

```python
class LargeContentPayload(TypedDict):
    type: Literal["write_large"]
    # ... base fields ...

    content_truncated: bool      # Always True for this type
    content_preview: str         # First chunk
    fetch_url: str               # URL/method to fetch more chunks
    chunk_size: int              # Bytes per chunk
    total_chunks: int            # Total number of chunks
```

This is deferred - the current design handles files up to sandbox limits.

---

## Backwards Compatibility

### For Existing CLI Implementations

If the CLI doesn't recognize the structured payload:

1. The `description` field provides a human-readable fallback
2. CLI can show: `"{description}" - Approve? [y/n]`
3. All essential info is in `description`

### Migration Path

1. **Phase 1:** Sandbox returns new structured payloads
2. **Phase 2:** CLI adds handlers for each type
3. **Phase 3:** CLI adds paging support
4. **Phase 4:** Consider chunked fetching for large files

---

## Implementation Checklist

### Sandbox (`pydantic-ai-filesystem-sandbox`)

- [ ] Add `edit_file` tool with patch semantics
- [ ] Add `EditResult` model
- [ ] Add `EditNotUniqueError`, `EditNotFoundError`
- [ ] Update `needs_approval()` to return typed payloads
- [ ] Add diff computation (use `difflib.unified_diff`)
- [ ] Add `edit_approval` config flag to `PathConfig`
- [ ] Add payload type definitions (as TypedDict or Pydantic models)
- [ ] Tests for edit operations
- [ ] Tests for approval payload structure

### CLI (`pydantic-ai-blocking-approval` or CLI package)

- [ ] Add payload type dispatch in approval handler
- [ ] Implement edit approval display with diff coloring
- [ ] Implement write approval display with preview
- [ ] Add paging support (integrate with terminal pager or custom)
- [ ] Add "view full" option for truncated previews
- [ ] Add overwrite warning for existing files
- [ ] Fallback for unknown payload types

---

## Open Questions for CLI Team

1. **Pager integration:** Does the CLI have access to a pager (like `less`)? Or should we implement custom scrolling?

2. **Diff coloring:** Should the sandbox pre-compute ANSI-colored diff, or should CLI handle coloring?

3. **Interactive options:** Beyond y/n, what actions should be available?
   - `[v]iew full` - show all content
   - `[e]dit` - allow user to modify before applying?
   - `[d]iff` - show as diff vs current file?
   - `[c]ancel all` - abort entire agent run?

4. **Approval memory:** Should "approve similar" be path-based, content-based, or tool-based?
   - "Approve all edits to this file"
   - "Approve all edits in this sandbox"
   - "Approve all edits (this session)"

5. **Content size limits:** What's the maximum content size the CLI can reasonably display? Should sandbox enforce a limit?

6. **Async considerations:** Does the CLI need the sandbox to yield control during long operations, or is blocking acceptable?

---

## Example: Full Flow

```
Agent calls: edit_file(
    path="workspace/src/utils.py",
    old_string="import os\nimport sys",
    new_string="import os\nimport sys\nimport json"
)

Sandbox.needs_approval() returns:
{
    "type": "edit",
    "description": "Edit workspace/src/utils.py: add 1 line at line 1",
    "path": "workspace/src/utils.py",
    "sandbox": "workspace",
    "old_string": "import os\nimport sys",
    "new_string": "import os\nimport sys\nimport json",
    "replace_all": false,
    "unified_diff": "--- a/src/utils.py\n+++ b/src/utils.py\n@@ -1,2 +1,3 @@\n import os\n import sys\n+import json\n",
    "diff_lines": 7,
    "match_line": 1,
    "match_count": 1,
    "context_before": "",
    "context_after": "\ndef helper():\n    pass",
    "file_lines": 50,
    "file_bytes": 1234
}

CLI displays:
┌─ Edit: workspace/src/utils.py (line 1) ─────────────────────┐
│  @@ -1,2 +1,3 @@                                            │
│   import os                                                  │
│   import sys                                                 │
│  +import json                                                │
└──────────────────────────────────────────────────────────────┘
Apply this edit? [y/n]: y

Sandbox executes edit, returns:
EditResult(
    path="workspace/src/utils.py",
    replacements_made=1,
    lines_changed=1,
    message="Added 1 line at line 1 in workspace/src/utils.py"
)
```

---

## Appendix: Diff Format

The `unified_diff` field uses standard unified diff format:

```diff
--- a/path/to/file
+++ b/path/to/file
@@ -start,count +start,count @@
 context line
-removed line
+added line
 context line
```

This format is:
- Well-known and parseable
- Supported by syntax highlighters
- Compatible with `patch` command
- Human-readable

The sandbox computes this using Python's `difflib.unified_diff()`.
