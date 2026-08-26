"""MCP tool bridge for LOCA-bench environments."""

import json
import asyncio
import threading
import contextvars

_active_mcp_tool: contextvars.ContextVar = contextvars.ContextVar(
    "loca_mcp_tool", default=None
)
_active_tool_catalog: contextvars.ContextVar = contextvars.ContextVar(
    "loca_tool_catalog", default=None
)


def set_mcp_tool(mcp_tool, catalog: list[dict] | None = None) -> None:
    _active_mcp_tool.set(mcp_tool)
    _active_tool_catalog.set(catalog)


def clear_mcp_tool() -> None:
    _active_mcp_tool.set(None)
    _active_tool_catalog.set(None)


def _build_catalog_description() -> str:
    catalog = _active_tool_catalog.get()
    if not catalog:
        return "No LOCA-bench MCP tools available. The environment may not be initialized."

    lines = [f"Available LOCA-bench MCP tools ({len(catalog)} total):"]
    for t in catalog:
        desc = t.get("description", "")[:80]
        lines.append(f"  - {t['name']}: {desc}")
    return "\n".join(lines)


SCHEMA = {
    "name": "loca_mcp",
    "description": (
        "Call a LOCA-bench MCP tool. Use 'list_tools' action to see all available tools, "
        "or 'call' action to invoke a specific tool.\n\n"
        "Example — list tools:\n"
        '  loca_mcp(action="list_tools")\n\n'
        "Example — call a tool:\n"
        '  loca_mcp(action="call", tool_name="woocommerce_list_products", '
        'arguments=\'{"per_page": 100, "page": 1}\')'
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_tools", "call"],
                "description": "Action to perform: 'list_tools' to see available MCP tools, 'call' to invoke one.",
            },
            "tool_name": {
                "type": "string",
                "description": "Name of the MCP tool to call (required when action='call').",
            },
            "arguments": {
                "type": "string",
                "description": "JSON string of arguments to pass to the MCP tool (required when action='call').",
            },
        },
        "required": ["action"],
    },
}


async def execute(
    action: str = "list_tools",
    tool_name: str = "",
    arguments: str = "{}",
    **_kwargs,
) -> str:
    mcp_tool = _active_mcp_tool.get()
    if mcp_tool is None:
        return (
            "Error: No LOCA-bench MCP environment is active. "
            "This tool is only available during LOCA-bench task execution."
        )

    if action == "list_tools":
        return _build_catalog_description()

    if action == "call":
        if not tool_name:
            return "Error: tool_name is required when action='call'."

        try:
            params = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in arguments: {e}"

        try:
            #
            result_holder = {}

            def _call_in_thread():
                try:
                    result_holder["value"] = mcp_tool.execute_tool(
                        tool_name, params, "meta_team_call"
                    )
                except Exception as exc:
                    result_holder["error"] = exc

            t = threading.Thread(target=_call_in_thread, daemon=True)
            t.start()
            _MCP_CALL_TIMEOUT = 150.0
            _elapsed = 0.0
            while t.is_alive():
                await asyncio.sleep(0.05)
                _elapsed += 0.05
                if _elapsed >= _MCP_CALL_TIMEOUT:
                    return (
                        f"Error: MCP tool '{tool_name}' timed out after "
                        f"{_MCP_CALL_TIMEOUT:.0f}s. The tool call may still "
                        f"be running in a background thread."
                    )

            if "error" in result_holder:
                err_str = str(result_holder["error"])
                if "session was closed" in err_str or "Server session was closed" in err_str:
                    setattr(mcp_tool, "_meta_team_fallback_triggered", True)
                    return (
                        f"[FATAL MCP ERROR] The MCP server for '{tool_name}' has crashed "
                        f"(session closed unexpectedly). This server CANNOT be restarted. "
                        f"Do NOT retry this tool or sleep+retry — it will never recover. "
                        f"Instead: (1) Try using bash to read local files in workspace/local_db/. "
                        f"(2) If you cannot complete the task, call set_final_output with what "
                        f"you have, then terminate."
                    )
                return f"Error calling MCP tool '{tool_name}': {err_str}"

            is_valid, has_error, observation, _name, _id = result_holder["value"]
            if not is_valid:
                return f"Error: Tool '{tool_name}' not found. Use action='list_tools' to see available tools."

            obs_str = str(observation) if observation else ""
            if "session was closed" in obs_str or "Server session was closed" in obs_str:
                setattr(mcp_tool, "_meta_team_fallback_triggered", True)
                return (
                    f"[FATAL MCP ERROR] The MCP server for '{tool_name}' has crashed "
                    f"(session closed unexpectedly). This server CANNOT be restarted. "
                    f"Do NOT retry this tool or sleep+retry — it will never recover. "
                    f"Instead: (1) Try using bash to read local files in workspace/local_db/. "
                    f"(2) If you cannot complete the task, call set_final_output with what "
                    f"you have, then terminate."
                )

            return observation
        except Exception as e:
            return f"Error calling MCP tool '{tool_name}': {e}"

    return f"Error: Unknown action '{action}'. Use 'list_tools' or 'call'."
