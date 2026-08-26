"""Sandboxed bash execution in Docker containers."""

import asyncio
import contextvars
import re

_MAX_TIMEOUT = 600

_ENV_PREFIX = (
    'PAGER=cat GIT_PAGER=cat MANPAGER=cat LESS=-R '
    'PIP_PROGRESS_BAR=off TQDM_DISABLE=1 '
)


_BLOCKED_PREFIXES = [
    "vim ", "vi ", "emacs ", "nano ", "nohup ",
    "gdb ", "less ", "tail -f ",
]

_BLOCKED_EXACT = {
    "python", "python3", "ipython", "bash", "sh",
    "/bin/bash", "/bin/sh", "su",
}

_BLOCKED_PATTERNS = [
    re.compile(r"git\s+log\s+.*--all"),
    re.compile(r"git\s+verify-pack"),
    re.compile(r"git\s+fsck"),
    re.compile(r"git\s+cat-file"),
    re.compile(r"git\s+fetch"),
    re.compile(r"git\s+pull\b"),
    re.compile(r"git\s+show\s"),
    re.compile(r"git\s+commit"),
    re.compile(r"git\s+clone"),
    re.compile(r"api\.github\.com"),
    re.compile(r"github\.io"),
    re.compile(r"githubusercontent"),
]


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
        for pattern in _BLOCKED_PATTERNS:
            if pattern.search(stripped):
                return True
    return False

_ctx_container: contextvars.ContextVar = contextvars.ContextVar(
    'docker_container', default=None)
_ctx_work_dir: contextvars.ContextVar = contextvars.ContextVar(
    'docker_work_dir', default="/workspace")


def set_container(container, work_dir: str = "/workspace"):
    _ctx_container.set(container)
    _ctx_work_dir.set(work_dir)


def clear_container():
    _ctx_container.set(None)


SCHEMA = {
    "name": "docker_bash",
    "description": (
        "Execute a bash command inside the Docker sandbox and return stdout/stderr. "
        "Max timeout is 600 seconds. Use this for running Python scripts, "
        "shell commands, pip install, and running tests."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute inside the Docker container.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default 300, max 600.",
                "default": 300,
            },
        },
        "required": ["command"],
    },
}


async def execute(command: str, timeout: int = 300, **kwargs) -> str:
    container = _ctx_container.get()
    work_dir = _ctx_work_dir.get()

    if container is None:
        return "[error: no Docker container set — docker_bash requires a running container]"

    if not command or not command.strip():
        return "[error: empty command]"

    if _is_command_blocked(command):
        return "Error: command blocked by security policy."

    timeout = min(max(1, timeout), _MAX_TIMEOUT)

    try:
        wrapped_command = f"{_ENV_PREFIX}bash -c {_shell_quote(command)}"

        def _exec():
            exit_code, output_bytes = container.exec_run(
                ["bash", "-c", wrapped_command],
                workdir=work_dir,
                demux=True,
            )
            stdout = output_bytes[0].decode(errors="replace") if output_bytes[0] else ""
            stderr = output_bytes[1].decode(errors="replace") if output_bytes[1] else ""
            return exit_code, stdout, stderr

        exit_code, stdout, stderr = await asyncio.wait_for(
            asyncio.to_thread(_exec), timeout=timeout
        )

        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += f"\n[stderr]\n{stderr}"
        if exit_code != 0:
            output += f"\n[exit code: {exit_code}]"

        max_len = 32000
        if len(output) > max_len:
            half = max_len // 2
            truncated = len(output) - max_len
            output = (
                output[:half]
                + f"\n\n<response clipped>\n"
                f"[{truncated} characters truncated. "
                f"Use head, tail, sed -n, or a more specific command "
                f"to get the output you need]\n\n"
                + output[-half:]
            )

        return output.strip() or "[no output]"

    except asyncio.TimeoutError:
        return f"[timeout after {timeout}s — command may still be running in the container]"
    except Exception as e:
        return f"[error: {e}]"


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
