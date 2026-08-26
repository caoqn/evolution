"""Runner — minimal task execution engine that manages Agent lifecycles and reflection."""

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from core.agent import Agent, AgentResult
from core.api_resilience import APIReliabilityTracker
from core.message_store import MessageStore
from core.output_contract import OutputContract
from core.reflection_runner import ReflectionMixin
from core.submission_bridge import SubmissionBridge
from core.tool_registry import ToolRegistry
from core.utils import load_prompt_template as _load_prompt_template

logger = logging.getLogger(__name__)


@dataclass
class RunnerResult:
    """Result of a Runner execution."""
    output: str
    agents: dict[str, Agent] = field(default_factory=dict)
    cost_seconds: float = 0.0
    reflection_applied: bool = False
    metadata: dict = field(default_factory=dict)


class Runner(ReflectionMixin):
    """Minimal task execution engine. Starts Chairman, waits for terminate or timeout, collects results."""

    REFLECTION_MAX_STEPS = 50
    REFLECTION_PHASE_TIMEOUT = 300.0
    _CHAIRMAN_EXIT_GRACE_SECONDS = 2.0
    GLOBAL_ANSWER_AGENT_NAME = "answer_agent"

    def __init__(
        self,
        pool_dir: str | Path,
        chairman_name: str,
        constitution: str = "",
        tool_registry: ToolRegistry | None = None,
        max_seconds: float = 600.0,
        max_messages: int = 200,
        idle_timeout: float = 120.0,
        enable_reflection: bool = False,
        max_cost_usd: float = 0.0,
        reflection_phase_timeout: float = 0.0,
        wall_clock_seconds: float | None = None,
        api_tracker: APIReliabilityTracker | None = None,
        budget_started_at: float | None = None,
        allowed_agent_ids: set[str] | None = None,
        team_selection_metadata: dict | None = None,
        output_contract: OutputContract | None = None,
        execution_policy: Any | None = None,
    ):
        self.pool_dir = Path(pool_dir)
        self.chairman_name = chairman_name
        self.constitution = constitution
        self.tool_registry = tool_registry or ToolRegistry(
            str(Path(__file__).parent.parent / "tools")
        )
        # ``max_seconds`` is the effective execution budget. Failed,
        # retryable provider requests are discounted from it; the independent
        # wall-clock cap stops an unhealthy upstream from extending a case
        # forever.
        self.max_seconds = max_seconds
        self.wall_clock_seconds = (
            max_seconds if wall_clock_seconds is None else wall_clock_seconds
        )
        self.max_messages = max_messages
        self.idle_timeout = idle_timeout
        self.enable_reflection = enable_reflection
        self.max_cost_usd = max_cost_usd
        self.reflection_phase_timeout = (
            reflection_phase_timeout if reflection_phase_timeout > 0
            else self.REFLECTION_PHASE_TIMEOUT
        )
        self.allowed_agent_ids = (
            frozenset(str(agent_id) for agent_id in allowed_agent_ids)
            if allowed_agent_ids is not None
            else None
        )
        if (
            self.allowed_agent_ids is not None
            and self.chairman_name in self.allowed_agent_ids
        ):
            raise ValueError(
                "allowed_agent_ids contains recruitable members only; "
                "the chairman must not be duplicated"
            )
        self.team_selection_metadata = dict(team_selection_metadata or {})
        self.output_contract = output_contract or OutputContract()
        self.execution_policy = execution_policy

        self.runner_id: str = uuid.uuid4().hex

        self._pool_agents_cache: list[dict] | None = None
        self.api_tracker = api_tracker or APIReliabilityTracker()
        self._budget_started_at = budget_started_at

        self.message_store = MessageStore()
        self.submission_bridge = SubmissionBridge(self)
        self.agents: dict[str, Agent] = {}
        self._agent_tasks: dict[str, asyncio.Task] = {}  # name → asyncio.Task
        self._result: str | None = None
        self._final_output_locked: bool = False
        self._finalization_mode: str = "none"
        self._submission_bridge_used: bool = False
        self._global_service_agent_ids: frozenset[str] = (
            frozenset({self.output_contract.answer_agent_name})
            if self.output_contract.answer_agent_enabled
            else frozenset()
        )
        self._done = asyncio.Event()
        self._forced_termination: str | None = None

        self._stopped_agents_cost: float = 0.0

        self._phase: str = "task_execution"
        self._all_reflection_agents: list[str] = []
        self._l1_completed: set[str] = set()
        self._l2_completed: set[str] = set()
        self._l3_completed: set[str] = set()
        self._reflection_phase_start: float = 0.0
        self._reflection_phase_guard: asyncio.Task | None = None
        self._agent_dirs: dict[str, str] = {}
        self._agent_change_suggestions: list[dict] = []
        self._reflection_applied: bool = False
        self._task_validation = None
        self._task_validator = None
        self._pre_reflection_hook = None
        self._evolution_description_key: str = ""
        self._run_start_time: float = 0.0
        self._event_log = None
        self._cwd: str | None = None
        self._task: str = ""

        self._reflection_plan = None
        self._reflection_review_state = None

    # ------------------------------------------------------------------

    def set_task_validator(self, validator) -> None:
        """Set task validation callback (injected by adapter)."""
        self._task_validator = validator

    def set_pre_reflection_hook(self, hook) -> None:
        """Set pre-reflection callback (injected by adapter for eval snapshot)."""
        self._pre_reflection_hook = hook

    # ------------------------------------------------------------------

    def load_agent_from_pool(self, agent_name: str) -> Agent:
        """Load an Agent from the Pool directory (does not start run_loop)."""
        if not self._is_agent_allowed(agent_name) and not self._is_global_service(agent_name):
            raise PermissionError(
                f"Agent '{agent_name}' is outside the selected team boundary."
            )
        agent_dir = self.pool_dir / agent_name
        if not agent_dir.exists():
            raise FileNotFoundError(f"Agent '{agent_name}' not found in pool: {agent_dir}")
        config_path = agent_dir / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Agent '{agent_name}' has no config.yaml: {config_path}")
        return Agent(str(agent_dir), self.tool_registry)

    def start_agent(
        self,
        agent: Agent,
        event_log=None,
        cwd: str | None = None,
        global_service: bool = False,
    ) -> None:
        """Start an agent from the pool and begin its run loop."""
        name = agent.config.name
        if name == self.chairman_name or (
            not self._is_agent_allowed(name)
            and not (global_service and self._is_global_service(name))
        ):
            raise PermissionError(
                f"Agent '{name}' is outside the selected recruitable team."
            )
        if name in self.agents:
            return

        agent.bind_message_store(self.message_store)
        agent._runner_ref = self
        agent._reflection_enabled = self.enable_reflection
        self.agents[name] = agent

        if self.enable_reflection:
            from tools.primitives import rebuild_primitive_schemas
            agent._primitive_tools_refresher = lambda: rebuild_primitive_schemas(
                self, include_chairman_tools=False,
            )

        if hasattr(agent, 'agent_dir'):
            self._agent_dirs[name] = str(agent.agent_dir)

        system_context = self._build_agent_context(agent)

        task = asyncio.create_task(
            agent.run_loop(
                event_log=event_log,
                cwd=cwd,
                initial_task=None,
                system_context=system_context,
                idle_timeout=self.idle_timeout,
                # The global AnswerAgent must remain available until the
                # Chairman has finished evidence gathering and asks for the
                # required final synthesis.
                max_idle_rounds=(
                    max(5, int(self.max_seconds / max(self.idle_timeout, 1)) + 2)
                    if global_service and self.max_seconds > 0
                    else 5
                ),
            ),
            name=f"agent-{name}",
        )
        self._agent_tasks[name] = task

    def stop_agent(self, agent_name: str) -> bool:
        """Stop a running agent gracefully."""
        task = self._agent_tasks.get(agent_name)
        if task and not task.done():
            task.cancel()
            agent = self.agents.get(agent_name)
            if agent:
                self._stopped_agents_cost += getattr(agent, "_total_cost_usd", 0.0)
            self.message_store.unregister(agent_name)
            self.agents.pop(agent_name, None)
            self._agent_tasks.pop(agent_name, None)
            return True
        return False

    def get_agent_states(self) -> dict[str, dict]:
        """Return current state of all agents in the pool."""
        states = {}
        for name, agent in self.agents.items():
            states[name] = {
                "state": agent._state,
                "state_since": agent._state_since,
            }
        return states

    def list_pool_agents(self) -> list[dict]:
        """List available agents in the pool directory."""
        if self._pool_agents_cache is None:
            self._pool_agents_cache = self._scan_pool_agents()

        for entry in self._pool_agents_cache:
            entry["started"] = entry["name"] in self.agents
        return self._pool_agents_cache

    def _is_agent_allowed(self, agent_name: str) -> bool:
        if agent_name == self.chairman_name or self._is_global_service(agent_name):
            return True
        return (
            self.allowed_agent_ids is None
            or agent_name in self.allowed_agent_ids
        )

    def _is_global_service(self, agent_name: str) -> bool:
        return (
            self.output_contract.answer_agent_enabled
            and agent_name == self.output_contract.answer_agent_name
        )

    def _answer_agent_final_answer(self) -> str:
        """Return AnswerAgent's most recent exact answer, if it supplied one."""
        return self.submission_bridge.extract_answer()

    def validate_answer_agent_submission(self, output: str) -> str | None:
        return self.submission_bridge.validate(output)

    def _scan_pool_agents(self) -> list[dict]:
        import os
        import yaml
        model_override = os.environ.get("META_TEAM_MODEL")

        result = []
        for agent_dir in sorted(self.pool_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            config_path = agent_dir / "config.yaml"
            if not config_path.exists():
                continue
            try:
                raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = raw.get("name", agent_dir.name)
            if name == self.chairman_name or self._is_global_service(name):
                continue
            if not self._is_agent_allowed(name):
                continue
            result.append({
                "name": name,
                "description": raw.get("description", ""),
                "model": model_override or raw.get("model", ""),
                "tools": raw.get("tools", []),
                "skills": raw.get("skills", []),
                "started": False,
            })
        return result

    # ------------------------------------------------------------------

    async def run(self, task: str, session=None) -> RunnerResult:
        """Execute the full task lifecycle: start chairman, wait for completion, run reflection."""
        start = self._budget_started_at or time.time()
        self._run_start_time = start
        event_log = session.event_log if session else None
        cwd = session.workspace if session else None
        self._event_log = event_log
        self._cwd = cwd
        self._task = task

        logger.info(
            "Runner[%s] starting: chairman=%s, pool=%s, effective_budget=%.0fs, "
            "wall_clock_cap=%.0fs, max_messages=%d, reflection=%s",
            self.runner_id[:8], self.chairman_name, self.pool_dir.name,
            self.max_seconds, self.wall_clock_seconds, self.max_messages,
            self.enable_reflection,
        )

        if event_log:
            event_log.log("runner.start", data={
                "task": task[:500],
                "chairman": self.chairman_name,
                "pool_dir": str(self.pool_dir),
                "effective_task_budget_seconds": self.max_seconds,
                "wall_clock_seconds": self.wall_clock_seconds,
                "enable_reflection": self.enable_reflection,
                "output_contract": self.output_contract.name,
                "execution_policy": (
                    self.execution_policy.to_dict()
                    if self.execution_policy is not None else None
                ),
            })

        if self.enable_reflection:
            from core.types import ReflectionPlan, ReviewState
            self._reflection_plan = ReflectionPlan()
            self._reflection_review_state = ReviewState()

        chairman = self.load_agent_from_pool(self.chairman_name)
        chairman.bind_message_store(self.message_store)
        chairman._runner_ref = self
        chairman._reflection_enabled = self.enable_reflection
        self.agents[self.chairman_name] = chairman

        if hasattr(chairman, 'agent_dir'):
            self._agent_dirs[self.chairman_name] = str(chairman.agent_dir)

        # An AnswerAgent is benchmark-specific. Only contracts that request
        # one (currently GAIA) start it as a global final-answer service.
        if self.output_contract.answer_agent_enabled:
            answer_agent = self.load_agent_from_pool(
                self.output_contract.answer_agent_name,
            )
            self.start_agent(
                answer_agent, event_log=event_log, cwd=cwd, global_service=True,
            )

        chairman_context = self._build_chairman_context()

        from tools.primitives import build_primitive_tools, rebuild_primitive_schemas, cleanup_handlers
        primitive_tools = build_primitive_tools(self)

        chairman._primitive_tools_refresher = lambda: rebuild_primitive_schemas(
            self, include_chairman_tools=True,
        )

        chairman_task = asyncio.create_task(
            chairman.run_loop(
                event_log=event_log,
                cwd=cwd,
                initial_task=f"## Task\n\n{task}",
                system_context=chairman_context,
                extra_tools=primitive_tools,
                idle_timeout=self.idle_timeout,
                max_idle_rounds=3,
            ),
            name=f"agent-{self.chairman_name}",
        )
        self._agent_tasks[self.chairman_name] = chairman_task

        guard_tasks = []
        if self.max_messages > 0:
            guard_tasks.append(
                asyncio.create_task(self._guard_max_messages(event_log))
            )
        if self.max_cost_usd > 0:
            guard_tasks.append(
                asyncio.create_task(self._guard_cost_limit(event_log))
            )

        #
        #

        async def _guard_chairman_exit():
            try:
                await chairman_task
            except (asyncio.CancelledError, Exception):
                pass
            await asyncio.sleep(self._CHAIRMAN_EXIT_GRACE_SECONDS)
            if not self._done.is_set():
                if event_log:
                    event_log.log("runner.chairman_exited_without_terminate", data={
                        "elapsed": round(time.time() - start, 2),
                        "phase": self._phase,
                    })
                bridge_result = await self.submission_bridge.finalize_available_answer(
                    reason="chairman_exited_without_commit",
                    event_log=event_log,
                )
                if not bridge_result.used:
                    self.message_store.terminate(reason="chairman_exited")
                    self._done.set()

        chairman_guard = asyncio.create_task(_guard_chairman_exit())
        guard_tasks.append(chairman_guard)

        timeout_kind = await self._wait_for_completion_with_budgets(start)
        if timeout_kind:
            wall_elapsed = round(time.time() - start, 2)
            effective_elapsed = round(self._effective_elapsed(start), 2)
            recovery_seconds = round(self.api_tracker.recovery_seconds(), 2)
            total_cost = round(self._get_total_cost_usd(), 4)
            limit = (
                self.max_seconds
                if timeout_kind == "effective_task_timeout"
                else self.wall_clock_seconds
            )
            if event_log:
                event_log.log("runner.timeout", data={
                    "kind": timeout_kind,
                    "effective_task_budget_seconds": self.max_seconds,
                    "wall_clock_seconds": self.wall_clock_seconds,
                    "wall_elapsed_seconds": wall_elapsed,
                    "effective_elapsed_seconds": effective_elapsed,
                    "api_recovery_seconds": recovery_seconds,
                    "total_cost_usd": total_cost,
                    "api_reliability": self.api_tracker.summary(),
                })
            self._forced_termination = (
                f"{timeout_kind} (wall {wall_elapsed}s, effective {effective_elapsed}s, "
                f"limit {limit}s, API recovery {recovery_seconds}s, cost ${total_cost})"
            )

        try:
            if (
                self._forced_termination
                and self._phase == "task_execution"
            ):
                for t in guard_tasks:
                    t.cancel()
                if guard_tasks:
                    await asyncio.gather(*guard_tasks, return_exceptions=True)
                guard_tasks.clear()

                # Stop normal agent loops before making the no-tool AnswerAgent
                # emergency call; otherwise an agent could issue a concurrent
                # normal request while its final output is being synthesized.
                execution_tasks = list(self._agent_tasks.values())
                for t in execution_tasks:
                    if not t.done():
                        t.cancel()
                if execution_tasks:
                    await asyncio.gather(*execution_tasks, return_exceptions=True)

                await self._emergency_finalize_after_forced_termination(event_log)

                if self.enable_reflection:
                    await self._enter_forced_reflection(event_log)

                if self.enable_reflection and not self._done.is_set():
                    reflection_total_timeout = self.reflection_phase_timeout * 3 + 60
                    try:
                        await asyncio.wait_for(
                            self._done.wait(), timeout=reflection_total_timeout,
                        )
                    except asyncio.TimeoutError:
                        if event_log:
                            event_log.log("runner.reflection_total_timeout", data={
                                "timeout": reflection_total_timeout,
                                "elapsed": round(time.time() - start, 2),
                                "phase": self._phase,
                            })
                        self.message_store.terminate(reason="forced_reflection_total_timeout")
        finally:
            cleanup_handlers(self)

        if self._agent_tasks:
            done, pending = await asyncio.wait(
                self._agent_tasks.values(),
                timeout=10.0,
                return_when=asyncio.ALL_COMPLETED,
            )
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        for t in guard_tasks:
            t.cancel()
        if self._reflection_phase_guard and not self._reflection_phase_guard.done():
            self._reflection_phase_guard.cancel()
            guard_tasks.append(self._reflection_phase_guard)
        if guard_tasks:
            await asyncio.gather(*guard_tasks, return_exceptions=True)

        cost = time.time() - start
        total_cost_usd = self._get_total_cost_usd()

        # Reflection messages are never a task answer.  If the Chairman did
        # not submit an answer before the task deadline, preserve an explicit
        # no-output marker instead of leaking L1/L2 prose into evaluation.
        if not self._result and not self.enable_reflection:
            chairman = self.agents.get(self.chairman_name)
            fallback_output = ""
            fallback_reason = ""
            if chairman is not None and hasattr(chairman, "messages"):
                for msg in reversed(chairman.messages or []):
                    if msg.get("role") != "assistant":
                        continue
                    if msg.get("tool_calls"):
                        continue
                    content = msg.get("content") or ""
                    if isinstance(content, str) and len(content.strip()) >= 200:
                        fallback_output = content.strip()
                        fallback_reason = "last_pure_assistant_message"
                        break

                if not fallback_output:
                    for msg in reversed(chairman.messages or []):
                        if msg.get("role") != "assistant":
                            continue
                        content = msg.get("content") or ""
                        if isinstance(content, str) and len(content.strip()) >= 500:
                            fallback_output = content.strip()
                            fallback_reason = "last_assistant_content_with_tool_calls"
                            break

            if fallback_output:
                if event_log:
                    event_log.log("runner.fallback_output_from_assistant", data={
                        "reason": fallback_reason,
                        "output_len": len(fallback_output),
                    })
                logger.warning(
                    "Runner[%s]: Chairman did not call set_final_output/finalize_task; "
                    "falling back to last assistant content (%d chars, reason=%s)",
                    self.runner_id[:8], len(fallback_output), fallback_reason,
                )
                self._result = fallback_output

        output = self._result or "[no output — Chairman did not call set_final_output/finalize_task]"

        logger.info(
            "Runner[%s] finished: %.1fs elapsed, $%.4f cost, agents=%s, "
            "phase=%s, reflection_applied=%s%s",
            self.runner_id[:8], cost, total_cost_usd,
            list(self.agents.keys()), self._phase,
            self._reflection_applied,
            f", forced_termination={self._forced_termination}" if self._forced_termination else "",
        )

        if event_log:
            event_log.log("runner.end", data={
                "output": output[:1000],
                "cost_seconds": round(cost, 2),
                "cost_usd": round(total_cost_usd, 4),
                "effective_task_seconds": round(self._effective_elapsed(start), 2),
                "api_recovery_seconds": round(self.api_tracker.recovery_seconds(), 2),
                "agents_used": list(self.agents.keys()),
                "reflection_applied": self._reflection_applied,
            })

        return RunnerResult(
            output=output,
            agents=self.agents,
            cost_seconds=cost,
            reflection_applied=self._reflection_applied,
            metadata={
                "agents_used": list(self.agents.keys()),
                "enable_reflection": self.enable_reflection,
                "output_contract": self.output_contract.name,
                "execution_policy": (
                    self.execution_policy.to_dict()
                    if self.execution_policy is not None else None
                ),
                "global_service_agent_ids": sorted(self._global_service_agent_ids),
                "team_selection": self.team_selection_metadata,
                "allowed_agent_ids": (
                    sorted(self.allowed_agent_ids)
                    if self.allowed_agent_ids is not None
                    else None
                ),
                "phase": self._phase,
                "forced_termination": self._forced_termination,
                "finalization_mode": self._finalization_mode,
                "submission_bridge_used": self._submission_bridge_used,
                "effective_task_seconds": round(self._effective_elapsed(start), 3),
                "effective_task_budget_seconds": self.max_seconds,
                "wall_clock_seconds": self.wall_clock_seconds,
                "api_reliability": self.api_tracker.summary(),
                "global_service_agent_ids": sorted(self._global_service_agent_ids),
                "handoff_trace": list(getattr(self, "_handoff_trace", [])),
            },
        )

    # ------------------------------------------------------------------

    def _effective_elapsed(self, started_at: float) -> float:
        """Elapsed task time after discounting failed retryable API requests."""
        return max(
            0.0,
            time.time() - started_at - self.api_tracker.recovery_seconds(),
        )

    async def _wait_for_completion_with_budgets(
        self, started_at: float,
    ) -> str | None:
        """Enforce the effective budget and independent recovery wall-clock cap.

        The dual budget applies to task execution only. Once a submitted task
        enters reflection, the existing reflection phase guards own its
        lifecycle and the task clock no longer cuts that phase short.
        """
        while not self._done.is_set():
            if self._phase != "task_execution":
                await self._done.wait()
                return None

            wall_elapsed = time.time() - started_at
            effective_elapsed = self._effective_elapsed(started_at)
            if (
                self.wall_clock_seconds > 0
                and wall_elapsed >= self.wall_clock_seconds
            ):
                return "wall_clock_recovery_timeout"
            if self.max_seconds > 0 and effective_elapsed >= self.max_seconds:
                return "effective_task_timeout"

            waits = [1.0]
            if self.wall_clock_seconds > 0:
                waits.append(max(0.01, self.wall_clock_seconds - wall_elapsed))
            if self.max_seconds > 0:
                waits.append(max(0.01, self.max_seconds - effective_elapsed))
            try:
                await asyncio.wait_for(self._done.wait(), timeout=min(waits))
            except asyncio.TimeoutError:
                continue
        return None

    # ------------------------------------------------------------------

    def _build_chairman_context(self) -> str:
        parts = []

        if self.constitution:
            parts.append(f"## Constitution\n\n{self.constitution}")

        if self.execution_policy is not None:
            parts.append(self.execution_policy.chairman_context())

        if self.output_contract.answer_agent_enabled:
            parts.append(
                "## Current Native Output Contract\n\n"
                f"Submission type: `{self.output_contract.submission_type}`\n"
                "Required AnswerAgent prefix: "
                f"`{self.output_contract.answer_agent_prefix}`\n\n"
                f"{self.output_contract.chairman_instruction}\n"
                "Wait for a message from the AnswerAgent beginning exactly with "
                f"`{self.output_contract.answer_agent_prefix}` before submitting. "
                "Submit only the content following that prefix."
            )

        handoff_rules = self.team_selection_metadata.get("handoff_rules", [])
        if handoff_rules:
            parts.append(
                "## Family-Scoped Handoff Rules\n\n"
                "Apply only rules relevant to agents actually recruited for this task. "
                "These rules do not require recruiting every template member.\n\n"
                f"{json.dumps(handoff_rules, ensure_ascii=False, indent=2)}"
            )

        if self.enable_reflection:
            parts.append(
                "## Runtime Submission Protocol (Authoritative)\n\n"
                "This section overrides any submission-tool names or sequences in "
                "pool prompts, constitutions, skills, or teammate messages.\n\n"
                f"{self.output_contract.chairman_submission_hint} Call exactly "
                "`finalize_task(output=<final output>)`. This records and locks "
                "the benchmark answer, then starts reflection. Do not call "
                "`set_final_output` or `terminate` during task execution. After "
                "reflection completes, call `terminate`.\n\n"
                "## Your Role\n\n"
                "You are the Chairman of this Meta-Team. Your responsibilities:\n"
                "1. **Analyze the task** and decide which agents to recruit from the Pool\n"
                "2. **Use `list_pool`** to see available agents and their capabilities\n"
                "3. **Use `start_agent`** to recruit agents into the team\n"
                "4. **Use `send_message`** to assign tasks and coordinate work\n"
                "5. **Use `wait_for_replies`** to wait for agents to complete their work\n"
                "6. **Use the Runtime Submission Protocol above** to submit the final result\n"
                "7. After reflection is complete, **use `terminate`** to end the task\n\n"
                "All agents can communicate freely with each other via send_message.\n\n"
                "The Runtime Submission Protocol is the only source of truth for "
                "how this run ends."
            )
        else:
            parts.append(
                "## Runtime Submission Protocol (Authoritative)\n\n"
                "This section overrides any submission-tool names or sequences in "
                "pool prompts, constitutions, skills, or teammate messages.\n\n"
                f"{self.output_contract.chairman_submission_hint} Call exactly "
                "`set_final_output(output=<final output>)`, confirm success, and "
                "then call `terminate`. Do not call `finalize_task` in this run.\n\n"
                "## Your Role\n\n"
                "You are the Chairman of this Meta-Team. Your responsibilities:\n"
                "1. **Analyze the task** and decide which agents to recruit from the Pool\n"
                "2. **Use `list_pool`** to see available agents and their capabilities\n"
                "3. **Use `start_agent`** to recruit agents into the team\n"
                "4. **Use `send_message`** to assign tasks and coordinate work\n"
                "5. **Use `wait_for_replies`** to wait for agents to complete their work\n"
                "6. **Use the Runtime Submission Protocol above** to submit the final result\n"
                "7. **Use `terminate`** to end the task\n\n"
                "All agents can communicate freely with each other via send_message.\n\n"
                "The Runtime Submission Protocol is the only source of truth for "
                "how this run ends."
            )

        return "\n\n---\n\n".join(parts)

    def _build_agent_context(self, agent: Agent) -> str:
        parts = []

        if self.constitution:
            parts.append(f"## Constitution\n\n{self.constitution}")

        handoff_rules = self.team_selection_metadata.get("handoff_rules", [])
        relevant_rules = [
            rule for rule in handoff_rules
            if rule.get("from_agent") == agent.config.name
            or rule.get("to_agent") == agent.config.name
        ]
        if relevant_rules:
            parts.append(
                "## Relevant Handoff Rules\n\n"
                f"{json.dumps(relevant_rules, ensure_ascii=False, indent=2)}"
            )

        if self.execution_policy is not None:
            native_role_context = self.execution_policy.agent_context(
                agent.config.name, agent.config.role,
            )
            if native_role_context:
                parts.append(native_role_context)

        if (
            self.output_contract.answer_agent_enabled
            and agent.config.name == self.output_contract.answer_agent_name
        ):
            parts.append(
                "## Current Native Output Contract\n\n"
                f"Submission type: `{self.output_contract.submission_type}`\n"
                f"Required response prefix: `{self.output_contract.answer_agent_prefix}`\n\n"
                f"{self.output_contract.answer_agent_instruction}\n"
                "Send the formatted response to the Chairman. The prefix must be "
                "the first non-whitespace text and must match exactly."
            )

        parts.append(
            "## Your Role\n\n"
            "You are a member of a Meta-Team, recruited by the Chairman for this task.\n"
            "- Wait for the Chairman to assign you work via message\n"
            "- Complete your assigned work using your tools\n"
            "- Report results back to the Chairman via send_message\n"
            "- You can also communicate with other team members directly"
        )

        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------------------

    @staticmethod
    def _plain_text_emergency_history(messages: list[dict]) -> list[dict]:
        """Convert an agent/tool transcript into a valid no-tool chat history.

        Anthropic rejects a request without ``tools`` when its history still
        contains OpenAI tool-call fields or ``role="tool"`` messages.  The
        emergency path intentionally makes a no-tool completion, so retain
        the useful text while removing that protocol-specific structure.
        """
        clean_messages: list[dict] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if content is None:
                continue
            if not isinstance(content, str):
                content = str(content)
            if not content.strip():
                continue

            if role == "system":
                clean_role = "system"
            elif role == "assistant":
                clean_role = "assistant"
            else:
                # Tool results have no valid representation in a no-tool
                # request.  Present their text as already-observed evidence.
                clean_role = "user"
                if role == "tool":
                    content = f"[Previously gathered tool result]\n{content}"

            clean_messages.append({"role": clean_role, "content": content})

        return clean_messages

    async def _force_finalize_answer_agent(
        self, answer_agent, chairman, event_log=None,
    ) -> str:
        from core import llm

        if self.output_contract.emergency_mode == "strict_tagged_answer":
            format_instruction = (
                "Your entire response must be exactly one line in this form: "
                f"{self.output_contract.emergency_prefix} <answer>. "
                "Do not add any other text."
            )
        else:
            format_instruction = (
                "Start your response with the exact prefix "
                f"`{self.output_contract.emergency_prefix}` and then provide only "
                "the required final output. Do not add meta-commentary."
            )
        FORCE_FINISH_MSG = (
            "You have reached the resource budget limit "
            f"({self._forced_termination}). "
            "You can no longer call any tools. "
            "Based only on the emergency evidence packet below, "
            f"{self.output_contract.emergency_instruction} "
            f"{format_instruction}"
        )
        history = self._plain_text_emergency_history(chairman.messages)
        evidence = "\n\n".join(
            f"[{message['role'].upper()}]\n{message['content']}"
            for message in history
        )
        system_prompt = ""
        builder = getattr(answer_agent, "build_system_prompt", None)
        if callable(builder):
            system_prompt = builder()
        force_msgs = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "## Emergency Evidence Packet\n\n"
                    f"Original task:\n{self._task}\n\n"
                    f"Chairman transcript and gathered evidence:\n{evidence}\n\n"
                    f"## Required action\n{FORCE_FINISH_MSG}"
                ),
            },
        ]

        if event_log:
            event_log.log("runner.force_finalize_start",
                agent=answer_agent.config.name, data={
                    "reason": self._forced_termination,
                    "history_len": len(chairman.messages),
                    "answer_agent": answer_agent.config.name,
                    "plain_history_len": len(history),
                })

        response = await asyncio.wait_for(
            llm.complete(
                model=answer_agent.config.model,
                messages=force_msgs,
                temperature=answer_agent.config.temperature,
                max_tokens=max(4096, answer_agent.config.max_tokens),
                api_tracker=self.api_tracker,
            ),
            timeout=240.0,
        )

        if not response.choices:
            return ""
        msg = response.choices[0].message
        content = (msg.content or "").strip()

        try:
            if hasattr(answer_agent, "_track_cost_usd"):
                answer_agent._track_cost_usd(response)
        except Exception as e:
            logger.debug("Cost tracking failed for force-finalize: %s", e)

        return content

    async def _emergency_finalize_after_forced_termination(self, event_log=None) -> None:
        """Lock a best-effort answer before reflection after a forced stop."""
        if (
            self._result
            or not self._forced_termination
            or not self.output_contract.has_emergency_finalize
        ):
            return

        chairman = self.agents.get(self.chairman_name)
        answer_agent = self.agents.get(self.output_contract.answer_agent_name)
        if (
            chairman is None
            or answer_agent is None
            or len(getattr(chairman, "messages", []) or []) < 2
        ):
            return

        try:
            forced_output = await self._force_finalize_answer_agent(
                answer_agent, chairman, event_log,
            )
            if not forced_output.strip():
                if event_log:
                    event_log.log("runner.force_finalize_empty", data={
                        "reason": self._forced_termination,
                    })
                return
            lines = [line.strip() for line in forced_output.splitlines() if line.strip()]
            prefix_pattern = re.escape(self.output_contract.emergency_prefix)
            strict_one_line = self.output_contract.emergency_mode == "strict_tagged_answer"
            if strict_one_line:
                valid = len(lines) == 1 and bool(re.fullmatch(
                    rf"{prefix_pattern}\s*.+", lines[0], flags=re.IGNORECASE,
                ))
            else:
                valid = bool(re.match(
                    rf"^\s*{prefix_pattern}\s*.+", forced_output,
                    flags=re.IGNORECASE | re.DOTALL,
                ))
            if not valid:
                if event_log:
                    event_log.log("runner.force_finalize_rejected", data={
                        "reason": self._forced_termination,
                        "output_len": len(forced_output),
                        "rejection": (
                            "expected_single_final_answer_line"
                            if strict_one_line else "expected_tagged_final_output"
                        ),
                    })
                logger.info(
                    "Runner[%s]: rejected non-strict emergency finalization output "
                    "(trigger=%s)", self.runner_id[:8], self._forced_termination,
                )
                return
            answer = re.sub(
                rf"^\s*{prefix_pattern}\s*", "", forced_output,
                flags=re.IGNORECASE,
            ).strip()
            if not answer:
                if event_log:
                    event_log.log("runner.force_finalize_rejected", data={
                        "reason": self._forced_termination,
                        "output_len": len(forced_output),
                        "rejection": "empty_final_answer",
                    })
                return
            # This exceptional timeout path is synthesized by the configured
            # AnswerAgent, preserving the benchmark's normal format ownership.
            self._result = answer
            self._final_output_locked = True
            self._finalization_mode = "emergency"
            if event_log:
                event_log.log("runner.force_finalize_success", data={
                    "reason": self._forced_termination,
                    "output_len": len(self._result or ""),
                    "finalization_mode": self._finalization_mode,
                })
            logger.info(
                "Runner[%s]: emergency finalization recorded %d-char output "
                "(trigger=%s)",
                self.runner_id[:8], len(self._result or ""),
                self._forced_termination,
            )
        except Exception as e:
            if event_log:
                event_log.log("runner.force_finalize_error", data={
                    "reason": self._forced_termination,
                    "error": str(e)[:500],
                })
            logger.warning("Runner[%s]: emergency finalization failed: %s", self.runner_id[:8], e)

    # ------------------------------------------------------------------

    async def _guard_max_messages(self, event_log=None) -> None:
        try:
            while not self._done.is_set():
                await asyncio.sleep(5.0)
                if self._phase != "task_execution":
                    continue
                if self.message_store.total_count >= self.max_messages:
                    if event_log:
                        event_log.log("runner.guard", data={
                            "reason": "max_messages",
                            "count": self.message_store.total_count,
                            "limit": self.max_messages,
                        })
                    self._forced_termination = (
                        f"max_messages ({self.message_store.total_count} >= {self.max_messages})"
                    )
                    self._done.set()
                    break
        except asyncio.CancelledError:
            pass

    def _get_total_cost_usd(self) -> float:
        total = self._stopped_agents_cost
        for agent in self.agents.values():
            total += getattr(agent, "_total_cost_usd", 0.0)
        return total

    REFLECTION_MAX_COST_USD = 30.0

    async def _guard_cost_limit(self, event_log=None) -> None:
        try:
            reflection_cost_baseline: float | None = None

            while not self._done.is_set():
                await asyncio.sleep(10.0)
                total_cost = self._get_total_cost_usd()

                if self._phase == "task_execution":
                    if total_cost >= self.max_cost_usd:
                        if event_log:
                            event_log.log("runner.guard", data={
                                "reason": "cost_limit",
                                "total_cost_usd": round(total_cost, 4),
                                "limit_usd": self.max_cost_usd,
                            })
                        self._forced_termination = (
                            f"cost_limit (${total_cost:.2f} >= ${self.max_cost_usd:.2f})"
                        )
                        self._done.set()
                        break
                else:
                    if self.REFLECTION_MAX_COST_USD <= 0:
                        continue
                    if reflection_cost_baseline is None:
                        reflection_cost_baseline = total_cost
                    reflection_cost = total_cost - reflection_cost_baseline
                    if reflection_cost >= self.REFLECTION_MAX_COST_USD:
                        if event_log:
                            event_log.log("runner.guard", data={
                                "reason": "reflection_cost_limit",
                                "reflection_cost_usd": round(reflection_cost, 4),
                                "limit_usd": self.REFLECTION_MAX_COST_USD,
                                "total_cost_usd": round(total_cost, 4),
                            })
                        self.terminate(
                            reason=f"reflection_cost_limit_${reflection_cost:.2f}"
                                   f">=${self.REFLECTION_MAX_COST_USD:.2f}"
                        )
                        break
        except asyncio.CancelledError:
            pass
