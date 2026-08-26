"""File writing tool for agents."""

from pathlib import Path
from core.sandbox import resolve_path, SandboxViolation

SCHEMA = {
    "name": "write_file",
    "description": "Write content to a file. Creates parent directories if needed. All paths are relative to the workspace.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write. Use relative paths.",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
}


def execute(path: str, content: str, cwd: str | None = None) -> str:
    if not cwd:
        return "[error: no workspace set — write_file requires a workspace context]"
    try:
        p = resolve_path(path, cwd)
    except SandboxViolation as e:
        return f"[error: {e}]"

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"[wrote {len(content)} chars to {p}]"
    except Exception as e:
        return f"[error: {e}]"
