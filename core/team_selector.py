"""Interface and deterministic validation boundary for future team selection."""

from __future__ import annotations

import json
import inspect
import hashlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from core.evolution_models import TeamSelection
from core.template_registry import TemplateRegistry


@dataclass(frozen=True)
class PublicAgentProfile:
    agent_id: str
    role: str
    description: str


@dataclass
class TeamSelectionContext:
    raw_task: str
    chairman_id: str
    template_catalog: list[dict[str, Any]] = field(default_factory=list)


class TeamSelector(Protocol):
    async def select(self, context: TeamSelectionContext) -> TeamSelection:
        """Author one family/team selection without executing the task."""
        ...


class TeamSelectionError(ValueError):
    """The LLM did not author a structurally valid team selection."""


CompletionCallable = Callable[..., Awaitable[Any]]
AgentCatalogProvider = Callable[
    [], list[PublicAgentProfile] | Awaitable[list[PublicAgentProfile]]
]


class LLMTeamSelector:
    """Select a family while keeping reusable-template membership deterministic."""

    MAX_ATTEMPTS = 3

    def __init__(
        self,
        *,
        model: str,
        registry: TemplateRegistry,
        completion: CompletionCallable | None = None,
        agent_catalog_provider: AgentCatalogProvider | None = None,
        api_tracker: Any | None = None,
    ) -> None:
        self.model = model
        self.registry = registry
        self._completion = completion
        self._agent_catalog_provider = agent_catalog_provider
        self._api_tracker = api_tracker
        self.last_diagnostics: dict[str, Any] = {"status": "not_started"}

    async def select(self, context: TeamSelectionContext) -> TeamSelection:
        if not context.raw_task.strip():
            raise TeamSelectionError("team selection requires a non-empty task")
        try:
            family_payload, family_attempts = await self._select_family(context)
        except TeamSelectionError as exc:
            # Template selection is an optimization.  A malformed selector
            # response must not prevent execution: fall back to a provisional
            # roster selected from the public global-agent catalog.
            return await self._fallback_cold_start(context, str(exc))
        action = str(family_payload.get("family_action") or "").strip().lower()
        family_id = str(family_payload.get("family_id") or "").strip()
        if action == "reuse":
            current = self.registry.get_current(family_id)
            if current is None:
                raise TeamSelectionError(
                    "family selector returned reuse for a missing template family"
                )
            selection = TeamSelection(
                family_action="reuse",
                family_id=family_id,
                chairman_id=context.chairman_id,
                allowed_agent_ids=list(current.allowed_agent_ids),
                template_id=current.template_id,
                template_version=current.version,
                reason=str(family_payload.get("reason") or "").strip(),
                metadata={
                    "source": "llm_team_selector",
                    "family_attempt_count": len(family_attempts),
                    "cold_start_composer_used": False,
                },
            )
            known_agent_ids = None
        else:
            # A cold-start selector only reserves a registry key. Keep topic
            # semantics out of the key; the reflector owns the reusable family
            # abstraction and its human-readable description.
            family_payload = {
                **family_payload,
                "family_id": self._provisional_family_id(context.raw_task),
            }
            agents = await self._load_agent_catalog()
            known_agent_ids = {
                context.chairman_id,
                *(agent.agent_id for agent in agents),
            }
            selection, composition_attempts = await self._compose_cold_start(
                context, family_payload, agents
            )
            selection.metadata.update(
                {
                    "source": "llm_team_selector",
                    "family_attempt_count": len(family_attempts),
                    "cold_start_composer_used": True,
                    "composition_attempt_count": len(composition_attempts),
                }
            )
        try:
            validate_team_selection(
                selection,
                registry=self.registry,
                known_agent_ids=known_agent_ids,
                expected_chairman_id=context.chairman_id,
            )
        except ValueError as exc:
            self.last_diagnostics = {
                "status": "invalid_selection",
                "family_attempts": family_attempts,
                "error": str(exc),
            }
            raise TeamSelectionError(str(exc)) from exc
        self.last_diagnostics = {
            "status": "selected",
            "family_attempts": family_attempts,
            "cold_start_composer_used": action == "cold_start",
        }
        return selection

    @staticmethod
    def _provisional_family_id(raw_task: str) -> str:
        digest = hashlib.sha1(raw_task.encode("utf-8")).hexdigest()[:12]
        return f"family_{digest}"

    async def _fallback_cold_start(
        self, context: TeamSelectionContext, failure_reason: str,
    ) -> TeamSelection:
        agents = await self._load_agent_catalog()
        digest = hashlib.sha1(context.raw_task.encode("utf-8")).hexdigest()[:12]
        family_payload = {
            "family_id": f"fallback_{digest}",
            "family_description": "Automatic cold-start fallback after template selection failure",
            "reason": f"Template selection failed: {failure_reason}",
        }
        try:
            selection, attempts = await self._compose_cold_start(
                context, family_payload, agents,
            )
            roster_source = "llm_cold_start_composer"
        except TeamSelectionError as exc:
            # The fallback itself must be executable even if the composer also
            # misses its tool protocol.  The Chairman still sees only this
            # selected provisional roster, not a persistent global view.
            selection = TeamSelection(
                family_action="create",
                family_id=family_payload["family_id"],
                family_description=family_payload["family_description"],
                chairman_id=context.chairman_id,
                # AnswerAgent is a global post-task service. It lives in the
                # pool for loading, but must never become a template member or
                # a Chairman-recruitable team member.
                allowed_agent_ids=[
                    agent.agent_id for agent in agents
                    if agent.agent_id != "answer_agent"
                ],
                reason=(
                    f"Template selection and cold-start composition failed; "
                    f"using the public roster. {exc}"
                ),
            )
            attempts = []
            roster_source = "deterministic_all_public_agents"
        selection.metadata.update({
            "source": "template_selection_fallback",
            "fallback_reason": failure_reason,
            "cold_start_composer_used": True,
            "composition_attempt_count": len(attempts),
            "fallback_roster_source": roster_source,
        })
        validate_team_selection(
            selection,
            registry=self.registry,
            known_agent_ids={context.chairman_id, *(a.agent_id for a in agents)},
            expected_chairman_id=context.chairman_id,
        )
        self.last_diagnostics = {
            "status": "fallback_cold_start",
            "fallback_reason": failure_reason,
            "composition_attempts": attempts,
        }
        return selection

    async def _select_family(
        self, context: TeamSelectionContext
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You select a task-template family. Use the "
                    "select_template_family tool exactly once. You cannot see the "
                    "global agent registry and must not compose a team. "
                    "Do not reuse a family merely because the task has a similar "
                    "topic, domain, entity, title, repository, or benchmark. "
                    "Reuse only when the actual task requires substantially the same "
                    "collaboration pattern, including work type, scope, specialist "
                    "roles, and verification strategy."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": context.raw_task,
                        "template_catalog": context.template_catalog,
                        "instruction": (
                            "When the catalog contains a reasonable match based on the "
                            "complete task's actual collaboration needs, choose reuse "
                            "with the exact existing family_id. Do not choose reuse "
                            "solely from topical or lexical similarity; focus on whether "
                            "the actual task's collaboration needs match the template. "
                            "Otherwise choose "
                            "cold_start and provide a provisional lowercase snake_case "
                            "family_id and short provisional family_description for "
                            "the post-task reflector to finalize. Use the complete task "
                            "and catalog as evidence, but do not compose a team or output agents, assignments, "
                            "workflows, skills, handoff rules, slots, or capabilities."
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            response = await self._call(messages, self._family_tool_schema())
            calls = self._extract_calls(response, "select_template_family")
            invalid_reason = "select_template_family must be called exactly once"
            if len(calls) == 1:
                invalid_reason = self._family_decision_error(calls[0])
            attempts.append(
                {
                    "attempt": attempt,
                    "family_call_count": len(calls),
                    "valid": not invalid_reason,
                    "invalid_reason": invalid_reason,
                }
            )
            if len(calls) == 1 and not invalid_reason:
                return calls[0], attempts
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The previous family decision was invalid: {invalid_reason}. "
                        "Call select_template_family exactly once."
                    ),
                }
            )
        raise TeamSelectionError(
            "LLM family selection remained invalid after bounded retries: "
            f"{attempts[-1]['invalid_reason']}"
        )

    async def _compose_cold_start(
        self,
        context: TeamSelectionContext,
        family_payload: dict[str, Any],
        agents: list[PublicAgentProfile],
    ) -> tuple[TeamSelection, list[dict[str, Any]]]:
        family_id = str(family_payload.get("family_id") or "").strip()
        description = str(family_payload.get("family_description") or "").strip()
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You compose a provisional cold-start team for a new task "
                    "family. Use compose_cold_start_team exactly once."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": context.raw_task,
                        "new_family_id": family_id,
                        "family_description": description,
                        "selector_reason": str(family_payload.get("reason") or "").strip(),
                        "fixed_chairman_id": context.chairman_id,
                        "public_agent_registry": [
                            {
                                "agent_id": item.agent_id,
                                "role": item.role,
                                "description": item.description,
                            }
                            for item in agents
                            if item.agent_id not in {context.chairman_id, "answer_agent"}
                        ],
                        "instruction": (
                            "Select a non-empty provisional_agent_ids list using "
                            "exact public agent IDs. Treat selector_reason as a soft "
                            "prior, and choose a candidate set justified by the complete "
                            "task rather than by role availability alone. Do not assign "
                            "subtasks or emit workflow, skills, handoff rules, slots, "
                            "or capabilities."
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        attempts: list[dict[str, Any]] = []
        known_agent_ids = {
            context.chairman_id,
            *(item.agent_id for item in agents),
        }
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            response = await self._call(messages, self._cold_start_tool_schema())
            calls = self._extract_calls(response, "compose_cold_start_team")
            invalid_reason = "compose_cold_start_team must be called exactly once"
            selection: TeamSelection | None = None
            if len(calls) == 1:
                provisional = calls[0].get("provisional_agent_ids", [])
                if not isinstance(provisional, list):
                    invalid_reason = "provisional_agent_ids must be a list"
                else:
                    selection = TeamSelection(
                        family_action="create",
                        family_id=family_id,
                        family_description=description,
                        chairman_id=context.chairman_id,
                        allowed_agent_ids=[str(item).strip() for item in provisional],
                        reason=str(family_payload.get("reason") or "").strip(),
                    )
                    try:
                        selection.validate(known_agent_ids)
                        invalid_reason = ""
                    except ValueError as exc:
                        invalid_reason = str(exc)
            attempts.append(
                {
                    "attempt": attempt,
                    "composition_call_count": len(calls),
                    "valid": not invalid_reason,
                    "invalid_reason": invalid_reason,
                }
            )
            if selection is not None and not invalid_reason:
                return selection, attempts
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The previous cold-start composition was invalid: "
                        f"{invalid_reason}. Call compose_cold_start_team exactly once."
                    ),
                }
            )
        raise TeamSelectionError(
            "LLM cold-start composition remained invalid after bounded retries: "
            f"{attempts[-1]['invalid_reason']}"
        )

    async def _load_agent_catalog(self) -> list[PublicAgentProfile]:
        if self._agent_catalog_provider is None:
            raise TeamSelectionError(
                "cold start requires an agent_catalog_provider"
            )
        result = self._agent_catalog_provider()
        if inspect.isawaitable(result):
            result = await result
        agents = list(result)
        if not agents:
            raise TeamSelectionError("cold start agent catalog is empty")
        if any(not isinstance(agent, PublicAgentProfile) for agent in agents):
            raise TeamSelectionError(
                "agent_catalog_provider must return PublicAgentProfile values"
            )
        agent_ids = [agent.agent_id for agent in agents]
        if any(not agent_id.strip() for agent_id in agent_ids):
            raise TeamSelectionError("agent catalog contains an empty agent_id")
        if len(agent_ids) != len(set(agent_ids)):
            raise TeamSelectionError("agent catalog contains duplicate agent IDs")
        return agents

    async def _call(
        self, messages: list[dict[str, str]], tool: dict[str, Any]
    ) -> Any:
        completion = self._completion
        if completion is None:
            from core.llm import complete

            completion = complete
        return await completion(
            model=self.model,
            messages=messages,
            tools=[tool],
            temperature=0.1,
            max_tokens=2048,
            api_tracker=self._api_tracker,
        )

    def _family_decision_error(self, payload: dict[str, Any]) -> str:
        action = str(payload.get("family_action") or "").strip().lower()
        family_id = str(payload.get("family_id") or "").strip()
        if action == "reuse":
            return "" if self.registry.get_current(family_id) else (
                "reuse must name an existing template family"
            )
        if action != "cold_start":
            return "family_action must be reuse or cold_start"
        if self.registry.get_family(family_id) is not None:
            return "cold_start requires a new family_id"
        from core.evolution_models import validate_family_id

        try:
            validate_family_id(family_id)
        except ValueError as exc:
            return str(exc)
        if not str(payload.get("family_description") or "").strip():
            return "cold_start requires family_description"
        return ""

    @staticmethod
    def _extract_calls(response: Any, tool_name: str) -> list[dict[str, Any]]:
        from core.llm import extract_tool_calls

        return [
            dict(call.get("arguments") or {})
            for call in extract_tool_calls(response)
            if call.get("name") == tool_name
            and isinstance(call.get("arguments"), dict)
        ]

    @staticmethod
    def _family_tool_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "select_template_family",
                "description": (
                    "Choose an existing template family or request cold start."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "family_action": {
                            "type": "string",
                            "enum": ["reuse", "cold_start"],
                        },
                        "family_id": {"type": "string"},
                        "family_description": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["family_action", "family_id"],
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _cold_start_tool_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "compose_cold_start_team",
                "description": "Select the provisional members for a new family.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "provisional_agent_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["provisional_agent_ids"],
                    "additionalProperties": False,
                },
            },
        }


def validate_team_selection(
    selection: TeamSelection,
    *,
    registry: TemplateRegistry,
    known_agent_ids: set[str] | None,
    expected_chairman_id: str,
) -> TeamSelection:
    selection.validate(known_agent_ids)
    if selection.chairman_id != expected_chairman_id:
        raise ValueError("team selection cannot replace the configured chairman")
    current = registry.get_current(selection.family_id)
    if selection.family_action == "reuse":
        if current is None:
            raise ValueError("reuse requires an existing template family")
        if selection.template_id != current.template_id:
            raise ValueError("reuse must bind the family's current template")
        if selection.template_version != current.version:
            raise ValueError("reuse must bind the current template version")
        if selection.allowed_agent_ids != current.allowed_agent_ids:
            raise ValueError("reuse cannot change or reorder template members")
    elif selection.family_action == "create":
        if current is not None:
            raise ValueError("cold start must use a new family_id")
    elif current is not None:
        raise ValueError("no_match cannot name an existing template family")
    return selection
