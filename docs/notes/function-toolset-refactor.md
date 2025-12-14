# FunctionToolset Refactor

## Context

The current `FileSystemToolset` uses `AbstractToolset` which requires:
- Manual JSON schema definitions for each tool
- Manual `ToolsetTool` instantiation
- Manual `call_tool` dispatch

This is verbose (~250 lines of boilerplate for 7 tools).

## Alternative: FunctionToolset

PydanticAI's `FunctionToolset` auto-generates schemas from type-annotated methods:

```python
from pydantic_ai.toolsets import FunctionToolset

class FileSystemToolset(FunctionToolset):
    def __init__(self, sandbox: Sandbox):
        self._sandbox = sandbox
        super().__init__([
            self.read_file,
            self.write_file,
            self.edit_file,
            self.delete_file,
            self.move_file,
            self.copy_file,
            self.list_files,
        ])

    def read_file(
        self,
        path: str,
        max_chars: int = 20_000,
        offset: int = 0,
    ) -> ReadResult:
        """Read a text file from the sandbox.

        Args:
            path: Path format '/mount/relative/path' (e.g., '/docs/readme.md')
            max_chars: Maximum characters to read
            offset: Character position to start from
        """
        return self.read(path, max_chars, offset)
```

Benefits:
- Schemas generated from type hints + docstrings
- No manual JSON schema maintenance
- Less code (~100 lines saved)

## Why We Use AbstractToolset

1. **Custom error handling** - We wrap operations with sandbox permission checks
2. **Dynamic tool names** - Tool names don't match method names (e.g., `read_file` vs `read`)
3. **Approval protocol** - `ApprovableFileSystemToolset` needs to intercept calls

## Open Questions

1. Can `FunctionToolset` support the approval protocol? Need to check if `needs_approval()` is called.

2. Would a hybrid approach work? Use `FunctionToolset` for schema generation but override `call_tool`?

3. Is the boilerplate actually a problem? The explicit schemas are clear and self-documenting.

## Decision

**Keep AbstractToolset for now.** The explicitness is valuable, and refactoring would need to verify approval integration still works. Revisit if we add more tools and the boilerplate becomes burdensome.

---

*Created: 2025-01-04*
