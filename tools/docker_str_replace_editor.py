"""String-replace file editor for Docker containers."""

import asyncio
import io
import tarfile

from tools.docker_bash import _ctx_container, _ctx_work_dir

_MAX_VIEW_LINES = 500

_SNIPPET_CONTEXT_LINES = 4


SCHEMA = {
    "name": "docker_str_replace_editor",
    "description": (
        "Custom editing tool for viewing, creating and editing files inside the Docker container.\n"
        "* State is persistent across command calls.\n"
        "* If `path` is a file, `view` displays the content with line numbers. "
        "If `path` is a directory, `view` lists non-hidden files up to 2 levels deep.\n"
        "* The `create` command cannot be used if the file already exists.\n"
        "* For `str_replace`: `old_str` must match EXACTLY one occurrence in the file "
        "(be mindful of whitespace). If not unique, include more context.\n"
        "* For `insert`: `new_str` is inserted AFTER `insert_line`.\n"
        "* Always use absolute paths (starting with /)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["view", "create", "str_replace", "insert"],
                "description": "The operation to perform.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Absolute path to file or directory inside the container, "
                    "e.g. `/workspace/src/main.py` or `/workspace/src`."
                ),
            },
            "file_text": {
                "type": "string",
                "description": (
                    "Required for `create`: the full content of the new file."
                ),
            },
            "old_str": {
                "type": "string",
                "description": (
                    "Required for `str_replace`: the exact string in the file to replace. "
                    "Must match exactly one occurrence."
                ),
            },
            "new_str": {
                "type": "string",
                "description": (
                    "For `str_replace`: the replacement string (empty = delete). "
                    "For `insert`: the text to insert."
                ),
            },
            "insert_line": {
                "type": "integer",
                "description": (
                    "Required for `insert`: line number after which to insert `new_str`. "
                    "Use 0 to insert at the beginning."
                ),
            },
            "view_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Optional for `view` on a file: [start_line, end_line]. "
                    "1-indexed. Use -1 as end_line to view until end of file."
                ),
            },
        },
        "required": ["command", "path"],
    },
}


def _read_file_from_container(container, path: str) -> tuple[bool, str]:
    try:
        exit_code, output = container.exec_run(
            ["cat", path], demux=True,
        )
        if exit_code != 0:
            stderr = output[1].decode(errors="replace") if output[1] else ""
            return False, f"Error reading {path}: {stderr.strip() or f'exit code {exit_code}'}"
        content = output[0].decode(errors="replace") if output[0] else ""
        return True, content
    except Exception as e:
        return False, f"Error reading {path}: {e}"


def _write_file_to_container(container, path: str, content: str) -> tuple[bool, str]:
    try:
        parent = path.rsplit("/", 1)[0] if "/" in path else "/"
        container.exec_run(["mkdir", "-p", parent])

        file_bytes = content.encode("utf-8")
        dir_part = path.rsplit("/", 1)[0] if "/" in path else "/"
        file_name = path.rsplit("/", 1)[1] if "/" in path else path

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=file_name)
            info.size = len(file_bytes)
            tar.addfile(info, io.BytesIO(file_bytes))
        buf.seek(0)
        container.put_archive(dir_part, buf)
        return True, ""
    except AttributeError:
        try:
            import base64
            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            safe_path = "'" + path.replace("'", "'\\''") + "'"
            cmd = f"mkdir -p $(dirname {safe_path}) && echo '{b64}' | base64 -d > {safe_path}"
            exit_code, output = container.exec_run(
                ["bash", "-c", cmd], demux=True)
            if exit_code != 0:
                stderr = output[1].decode(errors="replace") if output[1] else ""
                return False, f"Error writing {path}: {stderr}"
            return True, ""
        except Exception as e2:
            return False, f"Error writing {path} (fallback): {e2}"
    except Exception as e:
        return False, f"Error writing {path}: {e}"


def _format_snippet(content: str, center_line: int, new_str_lines: int) -> str:
    lines = content.split("\n")
    start = max(0, center_line - 1 - _SNIPPET_CONTEXT_LINES)
    end = min(len(lines), center_line - 1 + new_str_lines + _SNIPPET_CONTEXT_LINES)

    snippet_lines = []
    for i in range(start, end):
        snippet_lines.append(f"{i + 1:6}\t{lines[i]}")
    return "\n".join(snippet_lines)


async def execute(
    command: str,
    path: str,
    file_text: str = "",
    old_str: str = "",
    new_str: str = "",
    insert_line: int = 0,
    view_range: list | None = None,
    **kwargs,
) -> str:
    container = _ctx_container.get()
    if container is None:
        return "[error: no Docker container set — this tool requires a running container]"

    if not path:
        return "Error: 'path' parameter is required."

    if command == "view":
        return await asyncio.to_thread(_do_view, container, path, view_range)
    elif command == "create":
        return await asyncio.to_thread(_do_create, container, path, file_text)
    elif command == "str_replace":
        return await asyncio.to_thread(_do_str_replace, container, path, old_str, new_str)
    elif command == "insert":
        return await asyncio.to_thread(_do_insert, container, path, insert_line, new_str)
    else:
        return f"Error: unknown command '{command}'. Valid: view, create, str_replace, insert."


# ── view ──────────────────────────────────────────────────────────

def _do_view(container, path: str, view_range: list | None) -> str:
    exit_code, output = container.exec_run(
        ["test", "-d", path], demux=True,
    )
    is_dir = (exit_code == 0)

    if is_dir:
        return _view_directory(container, path)
    return _view_file(container, path, view_range)


def _view_directory(container, path: str) -> str:
    safe_path = "'" + path.replace("'", "'\\''") + "'"
    exit_code, output = container.exec_run(
        ["bash", "-c",
         f"find {safe_path} -maxdepth 2 -not -path '*/\\.*' 2>/dev/null | head -200 | sort"],
        demux=True,
    )
    stdout = output[0].decode(errors="replace") if output[0] else ""
    if not stdout.strip():
        return f"Directory '{path}' is empty or does not exist."
    return stdout.strip()


def _view_file(container, path: str, view_range: list | None) -> str:
    exit_code, output = container.exec_run(
        ["cat", "-n", path], demux=True,
    )
    if exit_code != 0:
        stderr = output[1].decode(errors="replace") if output[1] else ""
        return f"Error viewing {path}: {stderr.strip() or f'exit code {exit_code}'}"

    content = output[0].decode(errors="replace") if output[0] else ""
    if not content:
        return f"File '{path}' is empty."

    if view_range and len(view_range) == 2:
        lines = content.split("\n")
        start = max(0, view_range[0] - 1)
        end = len(lines) if view_range[1] == -1 else view_range[1]
        content = "\n".join(lines[start:end])
    else:
        lines = content.split("\n")
        if len(lines) > _MAX_VIEW_LINES:
            content = "\n".join(lines[:_MAX_VIEW_LINES]) + "\n<response clipped>"

    return content


# ── create ────────────────────────────────────────────────────────

def _do_create(container, path: str, file_text: str) -> str:
    if not file_text:
        return "Error: 'file_text' parameter is required for 'create' command."

    exit_code, _ = container.exec_run(["test", "-f", path], demux=True)
    if exit_code == 0:
        return (
            f"Error: file '{path}' already exists. "
            "Use 'str_replace' to edit existing files, or choose a different path."
        )

    ok, err = _write_file_to_container(container, path, file_text)
    if not ok:
        return err
    return f"File created successfully at: {path}"


# ── str_replace ───────────────────────────────────────────────────

def _do_str_replace(container, path: str, old_str: str, new_str: str) -> str:
    if not old_str:
        return "Error: 'old_str' parameter is required for 'str_replace' command."

    ok, content = _read_file_from_container(container, path)
    if not ok:
        return content  # error message

    if old_str not in content:
        return (
            f"Error: no match found for `old_str` in {path}. "
            "Check that the string matches EXACTLY, including whitespace and indentation."
        )

    count = content.count(old_str)
    if count > 1:
        return (
            f"Error: `old_str` found {count} times in {path}. "
            "Include more context in `old_str` to make it unique."
        )

    replacement_line = content[:content.index(old_str)].count("\n") + 1
    new_content = content.replace(old_str, new_str, 1)

    ok, err = _write_file_to_container(container, path, new_content)
    if not ok:
        return err

    new_str_lines = new_str.count("\n") + 1 if new_str else 0
    snippet = _format_snippet(new_content, replacement_line, new_str_lines)

    return (
        f"The file {path} has been edited. Here's the result of running "
        f"`cat -n` on a snippet of {path}:\n{snippet}\n"
        "Review the changes and make sure they are as expected. "
        "Edit the file again if necessary."
    )


# ── insert ────────────────────────────────────────────────────────

def _do_insert(container, path: str, line_num: int, text: str) -> str:
    if not text:
        return "Error: 'new_str' parameter is required for 'insert' command."

    ok, content = _read_file_from_container(container, path)
    if not ok:
        return content

    lines = content.split("\n")
    if line_num < 0 or line_num > len(lines):
        return (
            f"Error: insert_line {line_num} is out of range "
            f"(valid range: 0 to {len(lines)})."
        )

    new_lines = text.split("\n")
    lines[line_num:line_num] = new_lines
    new_content = "\n".join(lines)

    ok, err = _write_file_to_container(container, path, new_content)
    if not ok:
        return err

    start = max(0, line_num - _SNIPPET_CONTEXT_LINES)
    end = min(len(lines), line_num + len(new_lines) + _SNIPPET_CONTEXT_LINES)
    snippet_lines = []
    for i in range(start, end):
        snippet_lines.append(f"{i + 1:6}\t{lines[i]}")
    snippet = "\n".join(snippet_lines)

    return (
        f"The file {path} has been edited. Here's the result of running "
        f"`cat -n` on a snippet of the edited file:\n{snippet}\n"
        "Review the changes and make sure they are as expected (correct indentation, "
        "no duplicate lines, etc). Edit the file again if necessary."
    )
