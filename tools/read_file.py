"""File reading tool for agents."""

from pathlib import Path
from core.sandbox import resolve_path, SandboxViolation

SCHEMA = {
    "name": "read_file",
    "description": "Read the contents of a file. All paths are relative to the workspace.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read. Use relative paths.",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (0-based). Optional.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read. Optional.",
            },
        },
        "required": ["path"],
    },
}


def execute(path: str, offset: int | None = None, limit: int | None = None, cwd: str | None = None) -> str:
    if not cwd:
        return "[error: no workspace set — read_file requires a workspace context]"
    try:
        p = resolve_path(path, cwd)
    except SandboxViolation as e:
        return f"[error: {e}]"

    if not p.exists():
        return f"[error: file not found: {p}]"
    if not p.is_file():
        return f"[error: not a file: {p}]"
    try:
        text = p.read_text(encoding="utf-8")

        max_len = 50000
        if offset is not None or limit is not None:
            lines = text.split("\n")
            start = offset or 0
            end = (start + limit) if limit else len(lines)
            lines = lines[start:end]
            result = "\n".join(f"{start + i + 1}|{line}" for i, line in enumerate(lines))
            if len(result) > max_len:
                result = result[:max_len] + f"\n[output truncated at {max_len} chars]"
            return result
        if len(text) > max_len:
            text = text[:max_len] + f"\n[output truncated at {max_len} chars]"
        return text
    except Exception as e:
        return f"[error: {e}]"
