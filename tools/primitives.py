"""Primitive team tools: recruit, send_message, terminate, set_final_output."""


def build_primitive_tools(runner) -> list[dict]:
    """Build the core team tool schemas (recruit, send_message, terminate, etc.)."""
    tools = _build_schemas(runner)

    _register_handlers(runner)

    return tools


def rebuild_primitive_schemas(runner, include_chairman_tools: bool = True) -> list[dict]:
    return _build_schemas(runner, include_chairman_tools=include_chairman_tools)


def _build_schemas(runner, include_chairman_tools: bool = True) -> list[dict]:
    pool_agents = runner.list_pool_agents()

    tools = []

    tools.append({
        "type": "function",
        "function": {
            "name": "list_pool",
            "description": (
                "List all available agents in the Pool with their capabilities. "
                "Use this to understand who you can recruit for the current task."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    })

    if include_chairman_tools:
        start_agent_schema = {
            "type": "function",
            "function": {
                "name": "start_agent",
                "description": (
                    "Recruit an agent from the Pool to join the current task. "
                    "The agent will start in idle mode, waiting for your message. "
                    "After starting, use send_message to assign them work."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the agent to start.",
                        },
                    },
                    "required": ["name"],
                },
            },
        }
        if pool_agents:
            enum_values = [a["name"] for a in pool_agents]
            running = [a["name"] for a in pool_agents if a["started"]]
            start_agent_schema["function"]["parameters"]["properties"]["name"]["enum"] = enum_values
            if running:
                start_agent_schema["function"]["description"] += (
                    f"\n\nCurrently running: {', '.join(running)}. "
                    f"Starting an already-running agent is a no-op."
                )
        tools.append(start_agent_schema)

        # stop_agent
        tools.append({
            "type": "function",
            "function": {
                "name": "stop_agent",
                "description": (
                    "Stop a running agent. Use this if an agent is stuck or no longer needed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the agent to stop.",
                        },
                    },
                    "required": ["name"],
                },
            },
        })

        if runner.enable_reflection:
            tools.append({
                "type": "function",
                "function": {
                    "name": "finalize_task",
                    "description": (
                        "Submit the final output/answer and enter the "
                        "reflection phase. You MUST pass the complete "
                        "final answer string as the `output` argument — "
                        "not an empty call. For writing / research / QA "
                        "tasks, `output` must contain the complete final "
                        "text (report, answer, analysis). "
                        "After calling this, you and your team will reflect "
                        "on performance to improve for future tasks. "
                        "Do NOT call terminate before this."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "output": {
                                "type": "string",
                                "minLength": 1,
                                "description": (
                                    "REQUIRED non-empty string. The complete "
                                    "final answer. For writing tasks include "
                                    "the entire report here."
                                ),
                            },
                        },
                        "required": ["output"],
                    },
                },
            })
        else:
            tools.append({
                "type": "function",
                "function": {
                    "name": "set_final_output",
                    "description": (
                        "Submit the final output/answer for the task. "
                        "You MUST pass the complete final answer string as "
                        "the `output` argument — not an empty call. "
                        "For code tasks, this is a summary (actual code is "
                        "extracted from git diff). For writing / research / "
                        "QA tasks, `output` must contain the complete final "
                        "text (report, answer, analysis). "
                        "After submitting, call terminate to end the task."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "output": {
                                "type": "string",
                                "minLength": 1,
                                "description": (
                                    "REQUIRED non-empty string. The complete "
                                    "final answer. For writing tasks include "
                                    "the entire report here; for code tasks "
                                    "include a brief summary."
                                ),
                            },
                        },
                        "required": ["output"],
                    },
                },
            })

        # terminate
        tools.append({
            "type": "function",
            "function": {
                "name": "terminate",
                "description": (
                    "End the entire task. Call this after set_final_output/finalize_task "
                    "and any reflection is complete. All agents will be stopped."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for terminating (e.g., 'task completed', 'reflection done').",
                        },
                    },
                    "required": [],
                },
            },
        })

    if runner.enable_reflection and runner._phase != "task_execution":
        tools.extend(_build_reflection_schemas(runner, phase=runner._phase))

    return tools


def _build_reflection_schemas(runner, phase: str = "") -> list[dict]:
    from tools.reflection import (
        build_update_prompt_patch_schema,
        build_update_skill_schema,
        build_skip_l1_reflection_schema,
        build_update_teammate_profile_schema,
        build_skip_l2_reflection_schema,
        build_view_reflection_schema,
        build_suggest_agent_change_schema,
        build_skip_l3_reflection_schema,
    )

    tools: list[dict] = []

    if phase == "l1_reflection":
        tools.append({"type": "function", "function": build_update_prompt_patch_schema()})
        tools.append({"type": "function", "function": build_update_skill_schema()})
        tools.append({"type": "function", "function": build_skip_l1_reflection_schema()})

    elif phase == "l2_reflection":
        all_agent_names = list(runner.agents.keys())
        pool_agents = runner.list_pool_agents()
        for a in pool_agents:
            if a["name"] not in all_agent_names:
                all_agent_names.append(a["name"])

        tools.append({"type": "function", "function": build_update_teammate_profile_schema(all_agent_names)})
        tools.append({"type": "function", "function": build_skip_l2_reflection_schema()})

    elif phase == "l3_reflection":
        all_agent_names = list(runner.agents.keys())
        pool_agents = runner.list_pool_agents()
        for a in pool_agents:
            if a["name"] not in all_agent_names:
                all_agent_names.append(a["name"])

        tools.append({
            "type": "function",
            "function": {
                "name": "propose_reflection",
                "description": (
                    "Propose a change to team configuration based on task reflection. "
                    "You can call this multiple times to build up a set of proposals.\n\n"
                    "Available files:\n"
                    "  - pool.yaml = pool settings (chairman, max_seconds, etc.)\n"
                    "  - constitution.md = team collaboration rules\n"
                    "  - <agent_name>/config.yaml = agent configuration\n"
                    "  - <agent_name>/prompt.md = agent prompt/instructions\n\n"
                    "To CREATE a new agent, propose config.yaml AND prompt.md.\n"
                    "To REMOVE an agent, propose config.yaml with content '__REMOVE__'.\n\n"
                    "NOTE: This tool is only available during L3 reflection phase."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_file": {
                            "type": "string",
                            "description": (
                                "Which file to modify or create. "
                                "Settings: 'pool.yaml', 'constitution.md'. "
                                "Agent: '<agent_name>/config.yaml' or "
                                "'<agent_name>/prompt.md'."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "The proposed NEW content for the entire file. "
                                "Must be the complete file content, not a diff or patch. "
                                "Use '__REMOVE__' for config.yaml to mark an agent for removal."
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": (
                                "Why this change is needed. Reference specific observations "
                                "from the task execution."
                            ),
                        },
                    },
                    "required": ["target_file", "content", "reason"],
                },
            },
        })
        tools.append({"type": "function", "function": build_view_reflection_schema()})
        tools.append({
            "type": "function",
            "function": {
                "name": "view_current_config",
                "description": (
                    "View the current content of a team configuration file. "
                    "Use this BEFORE proposing changes with propose_reflection "
                    "to see the existing content you need to preserve or modify.\n\n"
                    "Available files:\n"
                    "  - pool.yaml = pool settings (chairman, max_seconds, etc.)\n"
                    "  - constitution.md = team collaboration rules\n"
                    "  - <agent_name>/config.yaml = agent configuration\n"
                    "  - <agent_name>/prompt.md = agent prompt/instructions\n\n"
                    "NOTE: Only available during L3 reflection phase."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_file": {
                            "type": "string",
                            "description": (
                                "Which file to view. "
                                "Settings: 'pool.yaml', 'constitution.md'. "
                                "Agent: '<agent_name>/config.yaml' or "
                                "'<agent_name>/prompt.md'."
                            ),
                        },
                    },
                    "required": ["target_file"],
                },
            },
        })
        tools.append({"type": "function", "function": build_suggest_agent_change_schema(
            team_names=[],
            agent_names=all_agent_names,
        )})
        tools.append({
            "type": "function",
            "function": {
                "name": "apply_reflection",
                "description": (
                    "Apply all reflection proposals to team configuration files. "
                    "This writes changes and ends the reflection phase. "
                    "NOTE: Only available during L3 reflection phase. Only Chairman can call this."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "confirmation": {
                            "type": "string",
                            "description": "Type 'apply' to confirm.",
                        },
                    },
                    "required": ["confirmation"],
                },
            },
        })
        tools.append({
            "type": "function",
            "function": {
                "name": "skip_reflection",
                "description": (
                    "Skip L3 structural reflection — no configuration changes needed. "
                    "Use this when the team structure worked well. "
                    "NOTE: Only Chairman can call this."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why L3 reflection is being skipped.",
                        },
                    },
                    "required": ["reason"],
                },
            },
        })
        tools.append({"type": "function", "function": build_skip_l3_reflection_schema()})

    return tools



_runner_handlers: dict[str, dict] = {}


def _register_handlers(runner) -> None:
    runner_id = runner.runner_id

    # ------------------------------------------------------------------

    async def _list_pool(**kwargs) -> str:
        pool_agents = runner.list_pool_agents()
        if not pool_agents:
            return "No agents available in the Pool."

        lines = [f"## Pool Members ({len(pool_agents)} available)\n"]
        for a in pool_agents:
            status = "🟢 ACTIVE" if a["started"] else "⚪ available"
            desc = a["description"] or "(no description)"
            tools_str = ", ".join(a["tools"]) if a["tools"] else "none"
            skills_str = ", ".join(a["skills"]) if a["skills"] else "none"
            lines.append(
                f"- **{a['name']}** [{status}]\n"
                f"  Description: {desc}\n"
                f"  Model: {a['model']}\n"
                f"  Tools: {tools_str}\n"
                f"  Skills: {skills_str}"
            )
        return "\n".join(lines)

    def _check_chairman_only(tool_name: str, caller: str) -> str | None:
        if caller == runner.chairman_name:
            return None
        return (
            f"Error: '{tool_name}' is only available to the Chairman. "
            f"You ({caller}) cannot use this tool."
        )

    def _capture_assistant_content(runner) -> str:
        chairman = runner.agents.get(runner.chairman_name)
        if chairman is None:
            return ""
        msgs = getattr(chairman, "messages", None) or []
        for msg in reversed(msgs):
            if msg.get("role") != "assistant":
                continue
            if msg.get("tool_calls"):
                continue
            content = msg.get("content") or ""
            if isinstance(content, str) and len(content.strip()) >= 200:
                return content.strip()
        for msg in reversed(msgs):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content") or ""
            if isinstance(content, str) and len(content.strip()) >= 500:
                return content.strip()
        reasoning = getattr(chairman, "_recent_reasoning_texts", None) or []
        if reasoning:
            joined = "\n\n".join(t for t in reasoning if t and t.strip())
            if len(joined.strip()) >= 200:
                return joined.strip()
        return ""

    async def _start_agent(name: str, _caller_name: str = "", **kwargs) -> str:
        err = _check_chairman_only("start_agent", _caller_name)
        if err:
            return err

        clean_name = name.strip()

        if runner._is_global_service(clean_name):
            return (
                f"'{clean_name}' is the Runner-managed global final-answer service. "
                "It is already available; send it the final evidence packet instead of recruiting it."
            )

        if clean_name in runner.agents:
            return f"Agent '{clean_name}' is already running."

        if clean_name == runner.chairman_name:
            return "Error: Cannot start yourself."

        try:
            agent = runner.load_agent_from_pool(clean_name)
            event_log = runner._event_log
            cwd_path = runner._cwd

            runner.start_agent(agent, event_log=event_log, cwd=cwd_path)

            desc = agent.config.description if hasattr(agent.config, 'description') and agent.config.description else ""
            desc_hint = f" ({desc})" if desc else ""
            return (
                f"Agent '{clean_name}'{desc_hint} started successfully.\n"
                f"They are now waiting for your instructions.\n"
                f"Use send_message(to=[\"{clean_name}\"], content=\"...\") to assign work."
            )
        except FileNotFoundError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error starting agent '{clean_name}': {e}"

    async def _stop_agent(name: str, _caller_name: str = "", **kwargs) -> str:
        err = _check_chairman_only("stop_agent", _caller_name)
        if err:
            return err

        name = name.strip()

        if name == runner.chairman_name:
            return "Error: Cannot stop yourself. Use terminate to end the task."

        if name not in runner.agents:
            return f"Error: Agent '{name}' is not running."

        success = runner.stop_agent(name)
        if success:
            return f"Agent '{name}' has been stopped."
        else:
            return f"Agent '{name}' has already finished."

    if not hasattr(runner, "_empty_submit_attempts"):
        runner._empty_submit_attempts = {"set_final_output": 0, "finalize_task": 0}

    async def _set_final_output(output: str = "", _caller_name: str = "", **kwargs) -> str:
        err = _check_chairman_only("set_final_output", _caller_name)
        if err:
            return err
        answer_error = runner.validate_answer_agent_submission(output)
        if answer_error:
            return f"Error: {answer_error}"
        if not output or not output.strip():
            runner._empty_submit_attempts["set_final_output"] += 1
            attempt = runner._empty_submit_attempts["set_final_output"]
            if attempt <= 2:
                return (
                    f"❌ ERROR (attempt {attempt}/3): set_final_output() was "
                    f"called with NO `output` argument or empty string.\n\n"
                    f"This tool DOES NOT auto-capture your reasoning or recent "
                    f"messages. The grader sees ONLY the literal string you "
                    f"pass to `output=`.\n\n"
                    f"You MUST first WRITE the full report as text in your "
                    f"next assistant message OR directly inside the `output=` "
                    f"argument, then call:\n\n"
                    f"  set_final_output(output=\"# Title\\n\\n## Section 1\\n\\n"
                    f"...full markdown report 8K-20K chars...\\n\\nConfidence: X%\")\n\n"
                    f"After {3 - attempt} more empty call(s), the system will "
                    f"fall back to capturing whatever fragments it can find — "
                    f"this WILL produce a near-zero score. Write the report NOW."
                )
            # Attempt 3+: fallback capture
            captured = _capture_assistant_content(runner)
            if captured:
                result = await runner.set_final_output(captured)
                runner.terminate(reason="auto_terminate_after_fallback_capture")
                return (
                    f"⚠️ Auto-captured {len(captured)} chars after {attempt} "
                    f"empty calls and terminated. Original tool result: {result}"
                )
            return (
                "❌ ERROR: set_final_output() was called without output, "
                "AND no fallback content could be captured. "
                "You MUST pass output=\"YOUR FULL REPORT TEXT\"."
            )
        runner._empty_submit_attempts["set_final_output"] = 0
        result = await runner.set_final_output(output)
        return result

    async def _terminate(reason: str = "chairman_terminate", _caller_name: str = "", **kwargs) -> str:
        err = _check_chairman_only("terminate", _caller_name)
        if err:
            return err
        bridge_result = await runner.submission_bridge.finalize_available_answer(
            reason="chairman_terminate_without_commit",
            event_log=runner._event_log,
        )
        if bridge_result.used:
            if runner.enable_reflection:
                return (
                    "AnswerAgent response committed through the native submission "
                    "bridge. Continue the current reflection phase."
                )
            return "AnswerAgent response committed and task terminated."
        runner.terminate(reason=reason)
        return "Task terminated. All agents will be stopped."

    async def _finalize_task(output: str = "", _caller_name: str = "", **kwargs) -> str:
        err = _check_chairman_only("finalize_task", _caller_name)
        if err:
            return err
        answer_error = runner.validate_answer_agent_submission(output)
        if answer_error:
            return f"Error: {answer_error}"
        if not output or not output.strip():
            runner._empty_submit_attempts["finalize_task"] += 1
            attempt = runner._empty_submit_attempts["finalize_task"]
            if attempt <= 2:
                return (
                    f"❌ ERROR (attempt {attempt}/3): finalize_task() was "
                    f"called with NO `output` argument or empty string.\n\n"
                    f"This tool DOES NOT auto-capture. The grader sees only "
                    f"the literal `output=...` string.\n\n"
                    f"WRITE the report as text first, then call:\n\n"
                    f"  finalize_task(output=\"# Report\\n\\n...full markdown "
                    f"8K-20K chars...\\n\\nConfidence: X%\")\n\n"
                    f"After {3 - attempt} more empty calls, the system will "
                    f"fall back to fragment capture (near-zero score)."
                )
            captured = _capture_assistant_content(runner)
            if captured:
                result = await runner.finalize_task(captured)
                return (
                    f"⚠️ Auto-captured {len(captured)} chars after {attempt} "
                    f"empty calls. Now in reflection. Result: {result}"
                )
            return (
                "❌ ERROR: finalize_task() called without output, no fallback. "
                "You MUST pass output=\"YOUR FULL REPORT TEXT\"."
            )
        runner._empty_submit_attempts["finalize_task"] = 0
        result = await runner.finalize_task(output)
        return result

    handlers = {
        "list_pool": _list_pool,
        "start_agent": _start_agent,
        "stop_agent": _stop_agent,
        "set_final_output": _set_final_output,
        "terminate": _terminate,
        "finalize_task": _finalize_task,
    }


    if runner.enable_reflection:
        handlers.update(_build_reflection_handlers(runner))

    _runner_handlers[runner_id] = handlers


def _build_reflection_handlers(runner) -> dict:
    from tools.reflection import (
        execute_update_prompt_patch,
        execute_update_skill,
        execute_skip_l1_reflection,
        execute_update_teammate_profile,
        execute_skip_l2_reflection,
        execute_skip_l3_reflection,
        execute_propose_reflection,
        execute_view_reflection,
        execute_view_current_config,
        execute_suggest_agent_change,
    )


    _L1_TOOLS = {"update_prompt_patch", "update_skill", "skip_l1_reflection"}
    _L2_TOOLS = {"update_teammate_profile", "skip_l2_reflection"}
    _L3_CHAIR_TOOLS = {
        "propose_reflection", "view_reflection", "view_current_config",
        "apply_reflection", "skip_reflection",
    }
    _L3_MEMBER_TOOLS = {"suggest_team_improvement", "skip_l3_reflection"}

    def _check_phase(tool_name: str, caller_name: str) -> str | None:
        phase = runner._phase

        if phase == "task_execution":
            return "Error: Reflection tools are not available during task execution."

        if phase == "l1_reflection":
            if tool_name in _L1_TOOLS:
                return None
            return f"Error: '{tool_name}' is not available during L1 reflection. Use L1 tools: update_prompt_patch, update_skill, skip_l1_reflection."

        if phase == "l2_reflection":
            if tool_name in _L2_TOOLS:
                return None
            return f"Error: '{tool_name}' is not available during L2 reflection. Use L2 tools: update_teammate_profile, skip_l2_reflection."

        if phase == "l3_reflection":
            if tool_name in _L3_CHAIR_TOOLS:
                if caller_name != runner.chairman_name:
                    return f"Error: '{tool_name}' is only available to the Chairman."
                return None
            if tool_name in _L3_MEMBER_TOOLS:
                return None
            return f"Error: '{tool_name}' is not available during L3 reflection."

        return f"Error: reflection not active (phase={phase})."

    def _check_already_completed(tool_name: str, caller_name: str) -> str | None:
        phase = runner._phase
        if phase == "l1_reflection" and caller_name in runner._l1_completed:
            if tool_name in _L1_TOOLS:
                return f"You have already completed L1 reflection. Wait for the system to advance to L2."
        if phase == "l2_reflection" and caller_name in runner._l2_completed:
            if tool_name in _L2_TOOLS:
                return f"You have already completed L2 reflection. Wait for the system to advance to L3."
        return None

    def _get_agent_dir(caller_name: str) -> str:
        return runner._agent_dirs.get(caller_name, "")

    # L1 handlers
    # ------------------------------------------------------------------

    async def _update_prompt_patch(
        action: str = "add", patch: str = "", patches=None,
        _caller_name: str = "", **kwargs,
    ) -> str:
        err = _check_phase("update_prompt_patch", _caller_name)
        if err:
            return err
        err = _check_already_completed("update_prompt_patch", _caller_name)
        if err:
            return err
        agent_dir = _get_agent_dir(_caller_name)
        if not agent_dir:
            return f"Error: no agent directory found for {_caller_name}."
        result = execute_update_prompt_patch(
            action=action, patch=patch, patches=patches,
            _agent_name=_caller_name, _agent_dir=agent_dir,
        )
        return result + (
            "\n\nYou can add more patches with update_prompt_patch, create skills with "
            "update_skill, or call skip_l1_reflection when you are done with L1."
        )

    async def _update_skill(
        skill_name: str = "", content: str = "",
        _caller_name: str = "", **kwargs,
    ) -> str:
        err = _check_phase("update_skill", _caller_name)
        if err:
            return err
        err = _check_already_completed("update_skill", _caller_name)
        if err:
            return err
        agent_dir = _get_agent_dir(_caller_name)
        if not agent_dir:
            return f"Error: no agent directory found for {_caller_name}."
        result = execute_update_skill(
            skill_name=skill_name, content=content,
            _agent_name=_caller_name, _agent_dir=agent_dir,
        )
        return result + (
            "\n\nCall skip_l1_reflection when you are done with L1 reflection."
        )

    async def _skip_l1(reason: str = "", _caller_name: str = "", **kwargs) -> str:
        err = _check_phase("skip_l1_reflection", _caller_name)
        if err:
            return err
        err = _check_already_completed("skip_l1_reflection", _caller_name)
        if err:
            return err
        result = execute_skip_l1_reflection(reason=reason, _agent_name=_caller_name)
        runner._l1_completed.add(_caller_name)
        progress = runner._check_and_advance_phase("L1", _caller_name)
        return result + progress + (
            "\n\nYour L1 updates are recorded. "
            "Wait in idle — the system will advance to L2 once all agents complete L1."
        )

    # L2 handlers
    # ------------------------------------------------------------------

    async def _update_teammate_profile(
        teammate_name: str = "", profile: str = "",
        _caller_name: str = "", **kwargs,
    ) -> str:
        err = _check_phase("update_teammate_profile", _caller_name)
        if err:
            return err
        err = _check_already_completed("update_teammate_profile", _caller_name)
        if err:
            return err
        agent_dir = _get_agent_dir(_caller_name)
        if not agent_dir:
            return f"Error: no agent directory found for {_caller_name}."
        result = execute_update_teammate_profile(
            teammate_name=teammate_name, profile=profile,
            _agent_name=_caller_name, _agent_dir=agent_dir,
        )
        return result + (
            "\n\nCall skip_l2_reflection when you are done with L2 reflection."
        )

    async def _skip_l2(reason: str = "", _caller_name: str = "", **kwargs) -> str:
        err = _check_phase("skip_l2_reflection", _caller_name)
        if err:
            return err
        err = _check_already_completed("skip_l2_reflection", _caller_name)
        if err:
            return err
        result = execute_skip_l2_reflection(reason=reason, _agent_name=_caller_name)
        runner._l2_completed.add(_caller_name)

        for name in runner._all_reflection_agents:
            if name == _caller_name or name in runner._l2_completed:
                continue
            partners = runner.message_store.get_communication_partners(name)
            if _caller_name in partners:
                runner.message_store.send(
                    sender="[SYSTEM]",
                    receiver=name,
                    content=(
                        f"[L2 UPDATE] {_caller_name} has completed L2 reflection "
                        f"and will not send further messages. "
                        f"If you were waiting for them, proceed with your own L2 reflection."
                    ),
                )

        progress = runner._check_and_advance_phase("L2", _caller_name)
        return result + progress + (
            "\n\nYour L2 updates are recorded. You will now wait in idle. "
            "The system will advance to L3 once ALL agents have completed L2."
        )

    # L3 handlers
    # ------------------------------------------------------------------

    async def _suggest_team_improvement(
        _caller_name: str = "", **kwargs,
    ) -> str:
        err = _check_phase("suggest_team_improvement", _caller_name)
        if err:
            return err
        args = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        result = execute_suggest_agent_change(
            _agent_name=_caller_name,
            _suggestions_pool=runner._agent_change_suggestions,
            **args,
        )
        category = kwargs.get("category", "improvement")
        description = kwargs.get("description", "")[:500]
        runner.message_store.send(
            sender=_caller_name,
            receiver=runner.chairman_name,
            content=(
                f"[TEAM IMPROVEMENT SUGGESTION] {_caller_name} suggests a "
                f"**{category}** improvement:\n\n"
                f"{description}\n\n"
                f"Total suggestions so far: {len(runner._agent_change_suggestions)}"
            ),
        )
        return result

    async def _skip_l3(reason: str = "", _caller_name: str = "", **kwargs) -> str:
        err = _check_phase("skip_l3_reflection", _caller_name)
        if err:
            return err
        result = execute_skip_l3_reflection(reason=reason, _agent_name=_caller_name)
        runner._l3_completed.add(_caller_name)

        if _caller_name != runner.chairman_name:
            runner.message_store.send(
                sender=_caller_name,
                receiver=runner.chairman_name,
                content=(
                    f"[L3 COMPLETE] {_caller_name} has completed L3 reflection "
                    f"with no further suggestions."
                    + (f" Reason: {reason}" if reason else "")
                ),
            )

        return result

    async def _propose_reflection(
        target_file: str = "", content: str = "", reason: str = "",
        _caller_name: str = "", **kwargs,
    ) -> str:
        err = _check_phase("propose_reflection", _caller_name)
        if err:
            return err
        result = execute_propose_reflection(
            target_file=target_file, content=content, reason=reason,
            _agent_name=_caller_name,
            _reflection_plan=runner._reflection_plan,
            _reflection_review_state=runner._reflection_review_state,
        )
        return result + (
            "\n\n**NEXT STEP**: Call `apply_reflection(confirmation='apply')` "
            "to apply the proposed changes, or `skip_reflection(reason)` "
            "to end without applying."
        )

    async def _view_reflection(_caller_name: str = "", **kwargs) -> str:
        err = _check_phase("view_reflection", _caller_name)
        if err:
            return err
        result = execute_view_reflection(
            _reflection_plan=runner._reflection_plan,
            _reflection_review_state=runner._reflection_review_state,
        )
        if runner._agent_change_suggestions:
            result += "\n\n## Team Improvement Suggestions\n\n"
            result += _format_suggestions_summary(runner._agent_change_suggestions)
        return result

    async def _view_current_config(
        target_file: str = "", _caller_name: str = "", **kwargs,
    ) -> str:
        err = _check_phase("view_current_config", _caller_name)
        if err:
            return err
        pool_dir = str(runner.pool_dir)
        return execute_view_current_config(target_file=target_file, _team_dir=pool_dir)

    async def _apply_reflection(
        confirmation: str = "", _caller_name: str = "", **kwargs,
    ) -> str:
        err = _check_phase("apply_reflection", _caller_name)
        if err:
            return err
        result = runner._apply_reflection(confirmation)
        return result

    async def _skip_reflection_l3(
        reason: str = "", _caller_name: str = "", **kwargs,
    ) -> str:
        err = _check_phase("skip_reflection", _caller_name)
        if err:
            return err
        result = runner._skip_reflection(reason)
        return result

    return {
        # L1
        "update_prompt_patch": _update_prompt_patch,
        "update_skill": _update_skill,
        "skip_l1_reflection": _skip_l1,
        # L2
        "update_teammate_profile": _update_teammate_profile,
        "skip_l2_reflection": _skip_l2,
        # L3
        "suggest_team_improvement": _suggest_team_improvement,
        "skip_l3_reflection": _skip_l3,
        "propose_reflection": _propose_reflection,
        "view_reflection": _view_reflection,
        "view_current_config": _view_current_config,
        "apply_reflection": _apply_reflection,
        "skip_reflection": _skip_reflection_l3,
    }


def _format_suggestions_summary(suggestions: list[dict]) -> str:
    if not suggestions:
        return "No team improvement suggestions received."
    parts = [f"**{len(suggestions)} Team Improvement Suggestion(s):**\n"]
    for i, s in enumerate(suggestions, 1):
        category = s.get("category", "agent_change")
        suggested_by = s.get("suggested_by", "unknown")
        desc = s.get("description", s.get("reason", ""))[:300]
        evidence = s.get("evidence", "")[:150]
        parts.append(
            f"{i}. [{category}] — suggested by {suggested_by}\n"
            f"   {desc}"
            + (f"\n   Evidence: {evidence}" if evidence else "")
        )
    return "\n".join(parts)


def get_handler(runner, tool_name: str):
    """Get the handler function for a given tool name."""
    runner_id = runner.runner_id
    handlers = _runner_handlers.get(runner_id, {})
    return handlers.get(tool_name)


def cleanup_handlers(runner) -> None:
    _runner_handlers.pop(runner.runner_id, None)



PRIMITIVE_TOOL_NAMES = frozenset({
    "list_pool", "start_agent", "stop_agent",
    "set_final_output", "terminate", "finalize_task",
})

REFLECTION_TOOL_NAMES = frozenset({
    "update_prompt_patch", "update_skill", "skip_l1_reflection",
    "update_teammate_profile", "skip_l2_reflection",
    "propose_reflection", "view_reflection", "view_current_config",
    "apply_reflection", "skip_reflection",
    "suggest_team_improvement", "skip_l3_reflection",
})

ALL_TOOL_NAMES = PRIMITIVE_TOOL_NAMES | REFLECTION_TOOL_NAMES
