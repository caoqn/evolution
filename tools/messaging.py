"""Inter-agent messaging tool."""

# ---------------------------------------------------------------------------

def build_send_message_schema(agent_names: list[str]) -> dict:
    return {
        "name": "send_message",
        "description": (
            "Send a message to one or more agents. "
            f"Available agents: {', '.join(agent_names)}. "
            "To send to everyone, list all names in the 'to' array."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": agent_names,
                    },
                    "description": (
                        "One or more agent names to send the message to. "
                        "Examples: [\"strategist\"] for one agent, "
                        "[\"strategist\", \"critic\"] for multiple agents."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "The message content.",
                },
            },
            "required": ["to", "content"],
        },
    }


def execute_send_message(
    to,
    content: str,
    *,
    _agent_name: str,
    _message_store,
) -> str:
    if not to or not content:
        return "Error: 'to' and 'content' are required."

    if isinstance(to, str):
        recipients = [to]
    else:
        recipients = list(to)

    recipients = [r for r in recipients if r != _agent_name]
    if not recipients:
        return "Error: cannot send a message to yourself."

    seen = set()
    unique = []
    for r in recipients:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    recipients = unique

    registered = getattr(_message_store, "registered_agents", None)
    if registered is not None:
        invalid = [r for r in recipients if r not in registered]
        if invalid:
            return f"Error: unknown recipient(s): {', '.join(invalid)}."

    msgs = []
    for receiver in recipients:
        msgs.append(_message_store.send(sender=_agent_name, receiver=receiver, content=content))

    names = ", ".join(m.receiver for m in msgs)
    if len(msgs) == 1:
        return (
            f"Message sent to {names}. (id={msgs[0].id})\n\n"
            f"Tip: Use check_agent_status() to see if {names} has read your message "
            f"and is working on it."
        )
    else:
        return (
            f"Message sent to {len(msgs)} agent(s): {names}.\n\n"
            f"Tip: Use check_agent_status() to monitor whether they have read your "
            f"message and are working on it."
        )


# read_messages

READ_MESSAGES_SCHEMA = {
    "name": "read_messages",
    "description": (
        "Read messages sent to you. Returns unread messages by default. "
        "Messages are automatically marked as read after retrieval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sender": {
                "type": "string",
                "description": "Only read messages from this specific agent. Optional.",
            },
            "unread_only": {
                "type": "boolean",
                "description": "If true (default), only return unread messages.",
                "default": True,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of messages to return. Default 20.",
                "default": 20,
            },
        },
        "required": [],
    },
}


def execute_read_messages(
    sender: str | None = None,
    unread_only: bool = True,
    limit: int = 20,
    *,
    _agent_name: str,
    _message_store,
) -> str:
    messages = _message_store.read(
        reader=_agent_name,
        sender=sender,
        unread_only=unread_only,
        limit=limit,
    )

    if not messages:
        return "No messages."

    parts = []
    for msg in messages:
        ts = _format_time(msg.timestamp)
        parts.append(f"[{ts}] {msg.sender}: {msg.content}")

    header = f"{len(messages)} message(s)"
    unread_count = _message_store.count_unread(_agent_name)
    if unread_count > 0:
        header += f" ({unread_count} more unread)"

    return f"{header}:\n" + "\n".join(parts)


def _format_time(ts: float) -> str:
    import time
    return time.strftime("%H:%M:%S", time.localtime(ts))


# ---------------------------------------------------------------------------

def build_wait_for_replies_schema(agent_names: list[str]) -> dict:
    return {
        "name": "wait_for_replies",
        "description": (
            "Explicitly pause and wait for messages from other agents. "
            "Use this after you have finished your current work (e.g., sent messages, "
            "submitted a review) and have NOTHING ELSE to do until they respond. "
            "The tool will block until ANY message arrives or timeout is reached, "
            "then return ALL unread messages. "
            "Use from_agents to indicate whose reply you are primarily waiting for — "
            "the result will note whether those agents have replied, but messages "
            "from other agents and the system will also be delivered."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_agents": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": agent_names,
                    },
                    "description": (
                        "Agent(s) whose reply you are primarily waiting for. "
                        "All messages will still be delivered, but the result "
                        "will indicate whether these specific agents have replied. "
                        f"Available agents: {', '.join(agent_names)}."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Brief reason for waiting (e.g., 'waiting for tester results', "
                        "'waiting for chairman instructions'). Logged for trace visibility."
                    ),
                },
            },
            "required": ["reason"],
        },
    }


async def execute_wait_for_replies(
    reason: str = "",
    from_agents: list[str] | None = None,
    *,
    _agent_name: str,
    _message_store,
    _timeout: float = 120.0,
) -> str:
    has_msg = await _message_store.wait_for_message(
        _agent_name, timeout=_timeout, from_agents=from_agents,
    )

    if _message_store.is_terminated():
        return "System terminated while waiting."

    if not has_msg:
        all_messages = _message_store.read(
            reader=_agent_name, unread_only=True, limit=20,
        )
        if not all_messages:
            if from_agents:
                agents_str = ", ".join(from_agents)
                return (
                    f"No messages received within {int(_timeout)}s "
                    f"(was waiting for reply from {agents_str}). "
                    "Use check_agent_status() to see if they are still working, "
                    "call wait_for_replies() again to keep waiting, "
                    "or proceed with your own judgment."
                )
            return (
                f"No messages received within {int(_timeout)}s. "
                "Use check_agent_status() to check on agents, "
                "call wait_for_replies() again to keep waiting, "
                "or proceed with your own judgment."
            )

    else:
        all_messages = _message_store.read(
            reader=_agent_name, unread_only=True, limit=20,
        )

    if not all_messages:
        return "Wake signal received but no unread messages found."

    parts = []
    senders = []
    for msg in all_messages:
        ts = _format_time(msg.timestamp)
        parts.append(f"[{ts}] {msg.sender}: {msg.content}")
        if msg.sender not in senders:
            senders.append(msg.sender)

    header = f"Received {len(all_messages)} message(s) from {', '.join(senders)}"
    unread_count = _message_store.count_unread(_agent_name)
    if unread_count > 0:
        header += f" ({unread_count} more unread)"

    result = f"{header}:\n" + "\n".join(parts)

    if from_agents:
        replied = [a for a in from_agents if a in senders]
        pending = [a for a in from_agents if a not in senders]
        unexpected = [s for s in senders if s not in from_agents and s != "[SYSTEM]"]
        if unexpected:
            unexpected_str = ", ".join(unexpected)
            action_msg = (
                f"\n\n📩 ACTION REQUIRED: You received message(s) from {unexpected_str}"
                f" (not who you were waiting for). "
                f"You MUST reply to them using send_message() — they are waiting for your response."
            )
            if pending:
                action_msg += f" Then continue waiting for {', '.join(pending)}."
            result += action_msg
        if pending:
            result += (
                f"\n\n⚠ PENDING: Still waiting for reply from: "
                + ", ".join(pending)
                + ". They have not responded yet."
            )

    return result


# check_agent_status
# ---------------------------------------------------------------------------

def build_check_agent_status_schema(agent_names: list[str]) -> dict:
    return {
        "name": "check_agent_status",
        "description": (
            "Check the working status of other agents and whether they have read "
            "your messages. Use this after sending messages to monitor progress "
            "instead of calling read_messages repeatedly. "
            f"Available agents: {', '.join(agent_names)}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "enum": agent_names,
                    "description": (
                        "Check a specific agent's status. "
                        "If omitted, returns status of all agents."
                    ),
                },
            },
            "required": [],
        },
    }


def execute_check_agent_status(
    agent_name: str | None = None,
    *,
    _agent_name: str,
    _message_store,
    _agent_states: dict[str, dict],
) -> str:
    import time as _time

    now = _time.time()
    targets = [agent_name] if agent_name else [
        n for n in _agent_states if n != _agent_name
    ]

    if not targets:
        return "No other agents to check."

    lines = []
    all_working_on_mine = True
    any_working = False

    for name in targets:
        state_info = _agent_states.get(name)
        if not state_info:
            lines.append(f"- {name}: unknown (not registered)")
            all_working_on_mine = False
            continue

        state = state_info["state"]
        since = state_info["state_since"]
        duration = int(now - since)

        msg_stats = _message_store.count_from(sender=_agent_name, receiver=name)
        reply_stats = _message_store.count_from(sender=name, receiver=_agent_name)

        status_str = f"{'WORKING' if state == 'working' else 'IDLE'} ({duration}s)"

        if msg_stats["total"] == 0:
            msg_detail = "No messages sent to this agent"
            all_working_on_mine = False
        elif msg_stats["unread"] > 0:
            msg_detail = (
                f"Your messages: {msg_stats['total']} sent, "
                f"{msg_stats['unread']} unread — not yet seen"
            )
            all_working_on_mine = False
        else:
            msg_detail = (
                f"Your messages: {msg_stats['total']} sent, "
                f"all read — processing"
            )

        reply_detail = ""
        if reply_stats["unread"] > 0:
            reply_detail = f" | {reply_stats['unread']} unread reply(s) waiting for you"

        if state == "working":
            any_working = True

        lines.append(f"- {name}: {status_str}\n  {msg_detail}{reply_detail}")

    result = "Agent Status:\n" + "\n".join(lines)

    if any_working and all_working_on_mine:
        result += (
            "\n\n⚠️ IMPORTANT: All agents have read your messages and are ACTIVELY WORKING "
            "on their response. You MUST wait for their replies before proceeding. "
            "Do NOT finalize or make decisions until they have responded. "
            "Use read_messages() later to check for their responses."
        )
    elif any_working:
        result += (
            "\n\n⚠️ IMPORTANT: Some agents are still WORKING. "
            "You MUST wait for them to finish before finalizing. "
            "Check again shortly, or use read_messages() to see if any replies have arrived."
        )
    else:
        result += (
            "\n\nAll agents are idle. "
            "If you're waiting for responses, they may have already replied — "
            "use read_messages() to check."
        )

    return result
