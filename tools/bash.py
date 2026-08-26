"""Bash command execution tool."""

import asyncio
import os
import signal
import sys
from pathlib import Path

_MAX_TIMEOUT = 120


_BLOCKED_PREFIXES = [
    "vim ", "vi ", "emacs ", "nano ", "nohup ",
    "gdb ", "less ", "tail -f ",
]

_BLOCKED_EXACT = {
    "python", "python3", "ipython", "bash", "sh",
    "/bin/bash", "/bin/sh", "su",
}


def _is_command_blocked(command: str) -> bool:
    for line in command.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in _BLOCKED_EXACT:
            return True
        for prefix in _BLOCKED_PREFIXES:
            if stripped.startswith(prefix):
                return True
    return False

SCHEMA = {
    "name": "bash",
    "description": "Execute a bash command in the workspace directory and return stdout/stderr. Max timeout is 120 seconds.",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute. Runs in the workspace directory.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default 30, max 120.",
                "default": 30,
            },
        },
        "required": ["command"],
    },
}


async def execute(command: str, timeout: int = 30, cwd: str | None = None) -> str:
    if not cwd:
        return "[error: no workspace set, cannot execute bash commands]"

    if _is_command_blocked(command):
        return "Error: command blocked by security policy."

    timeout = min(max(1, timeout), _MAX_TIMEOUT)

    env = os.environ.copy()
    env["HOME"] = cwd
    env["PWD"] = cwd
    # Keep agent-issued Python commands in the same virtual environment as
    # the runner, so GAIA attachment parsers resolve consistently.  Some venv
    # layouts expose only `python3`; provide a shell-local `python` alias for
    # agents that use the common `python -c ...` form.
    interpreter_dir = str(Path(sys.executable).resolve().parent)
    env["PATH"] = interpreter_dir + os.pathsep + env.get("PATH", "")
    command = 'python() { command python3 "$@"; }\n' + command

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            await proc.wait()
            return f"[timeout after {timeout}s]"

        output = ""
        if stdout:
            output += stdout.decode(errors="replace")
        if stderr:
            output += f"\n[stderr]\n{stderr.decode(errors='replace')}"
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"

        max_len = 30000
        if len(output) > max_len:
            output = output[:max_len] + f"\n[output truncated at {max_len} chars]"

        return output.strip() or "[no output]"
    except Exception as e:
        return f"[error: {e}]"
