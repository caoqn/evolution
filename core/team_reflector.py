"""Post-task LLM reflection for creating or refining team templates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from core.evolution_models import (
    TeamReflectionDecision,
    TeamTemplate,
    TemplateMember,
)
from core.family_policy import FAMILY_POLICY
from core.team_selector import PublicAgentProfile


@dataclass
class TeamReflectionContext:
    task_id: str
    raw_task: str
    chairman_id: str
    family_id: str
    current_template: TeamTemplate | None
    allowed_agent_ids: list[str]
    actual_agent_ids: list[str]
    execution_summary: dict[str, Any]
    evaluation: dict[str, Any]
    failures: list[str]
    global_agents: list[PublicAgentProfile] = field(default_factory=list)
    historical_task_summaries: list[dict[str, Any]] = field(default_factory=list)


class TeamReflector(Protocol):
    async def reflect(self, context: TeamReflectionContext) -> TeamReflectionDecision:
        """Author one structured team decision after a completed task."""
        ...


class TeamReflectionError(ValueError):
    """The LLM did not author a valid team-template reflection."""


CompletionCallable = Callable[..., Awaitable[Any]]


class LLMTeamReflector:
    MAX_ATTEMPTS = 3
    GLOBAL_SERVICE_AGENT_IDS = frozenset({"answer_agent"})

    def __init__(
        self,
        *,
        model: str,
        completion: CompletionCallable | None = None,
        api_tracker: Any | None = None,
    ) -> None:
        self.model = model
        self._completion = completion
        self._api_tracker = api_tracker
        self.last_diagnostics: dict[str, Any] = {"status": "not_started"}

    async def reflect(
        self, context: TeamReflectionContext
    ) -> TeamReflectionDecision:
        known_agent_ids = {
            context.chairman_id,
            *(agent.agent_id for agent in context.global_agents
              if agent.agent_id not in self.GLOBAL_SERVICE_AGENT_IDS),
        }
        current = context.current_template
        allowed_actions = (
            ["create", "no_update"]
            if current is None
            else ["refine", "retain", "no_update"]
        )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You perform an independent post-task team reflection. "
                    "Call record_team_template_decision exactly once. Evaluate "
                    "reusable team membership and the family abstraction it supports; "
                    "never emit assignments, a "
                    "workflow, subtasks, skills, handoff rules, slots, or capabilities. "
                    "The reflector is authoritative for the family id/description. "
                    "Family names and descriptions must be reusable collaboration "
                    "patterns, not labels for this individual task. Never base them "
                    "on benchmark names, repository names, domains, business topics, "
                    "entities, titles, filenames, or one-off requested objects. "
                    "Describe the family as a compact, reusable collaboration pattern "
                    "that can cover multiple tasks. The description should communicate "
                    "four things in natural prose: the core collaboration mode; how the "
                    "members complement one another; the evidence and verification "
                    "principles; and a brief abstract applicability boundary. Keep it "
                    "informative but compact (normally a short paragraph, not a full "
                    "workflow). Do not list detailed steps, concrete tools, file names, "
                    "repositories, benchmarks, domains, entities, or one-off task topics. "
                    "When the evaluation failed, you MUST explicitly diagnose "
                    "whether the failure is a team-structure failure.\n\n"
                    "A single failed task is not sufficient evidence to remove a member "
                    "that has successful historical support. Remove an existing member "
                    "only when historical evidence or the current trace clearly shows "
                    "that it is: (1) persistently mismatched with the family collaboration "
                    "pattern; (2) redundant with another member; (3) misleading or harmful "
                    "in execution; (4) repeatedly misrecruited or causing ineffective work; "
                    "or (5) replaced by a more stable and accurate member. Otherwise preserve "
                    "it and add the missing capability when justified."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task_id": context.task_id,
                        "task": context.raw_task,
                        "family_id": context.family_id,
                        "family_policy": FAMILY_POLICY,
                        "allowed_actions": allowed_actions,
                        "fixed_chairman_id": context.chairman_id,
                        "current_template": (
                            current.to_dict() if current is not None else None
                        ),
                        "provisional_agent_ids": context.allowed_agent_ids,
                        "actual_agent_ids": context.actual_agent_ids,
                        "execution_summary": context.execution_summary,
                        "evaluation": context.evaluation,
                        "failures": context.failures,
                        "historical_task_summaries": context.historical_task_summaries,
                        "agents": [
                            {
                                "agent_id": agent.agent_id,
                                "role": agent.role,
                                "description": agent.description,
                            }
                            for agent in context.global_agents
                            if agent.agent_id not in self.GLOBAL_SERVICE_AGENT_IDS
                        ],
                        "instruction": (
                            "For a cold start, create a v1 template only when the "
                            "completed run provides reusable evidence; otherwise use "
                            "no_update. For an existing template, refine only when "
                            "membership should change, retain when the current team is "
                            "supported, and no_update when evidence is insufficient. "
                            "Use normalized collaboration evidence first and consult "
                            "full_task when needed; ignore benchmark boilerplate, titles, "
                            "repository names, domains, entities, and filenames as family identity. "
                            "For create/refine, return a complete non-empty agent_ids list "
                            "using exact available IDs and a complete collaboration-pattern "
                            "family_description as a concise natural-language paragraph. It "
                            "must cover the core collaboration mode, complementary member "
                            "roles, evidence/verification principles, and a short abstract "
                            "applicability boundary, without becoming a workflow or tool "
                            "catalog. Do not encode the current task's topic or "
                            "title in family_id or family_description. On refine, update the "
                            "description only when the reusable collaboration pattern changes. "
                            "Retain/no_update must omit both agent_ids "
                            "and family_description so the stored description stays unchanged. "
                            "For a failed evaluation, structural_diagnosis is required "
                            "and must contain boolean is_team_structure_failure, a "
                            "failure_type, and a concise basis. Distinguish missing or "
                            "misassigned team roles from skill, handoff, model/API, "
                            "network, and tool failures. Apply the member-removal evidence "
                            "standard above; do not treat one task's unused member as proof "
                            "that the member should be deleted."
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            response = await self._call(messages)
            calls = self._extract_calls(response)
            invalid_reason = (
                "record_team_template_decision must be called exactly once"
            )
            decision = None
            if len(calls) == 1:
                try:
                    decision = self._decision_from_payload(
                        calls[0], context, known_agent_ids
                    )
                    validate_team_reflection(
                        decision,
                        context=context,
                        known_agent_ids=known_agent_ids,
                    )
                    invalid_reason = ""
                except (TypeError, ValueError) as exc:
                    invalid_reason = str(exc)
            attempts.append(
                {
                    "attempt": attempt,
                    "call_count": len(calls),
                    "valid": not invalid_reason,
                    "invalid_reason": invalid_reason,
                }
            )
            if decision is not None and not invalid_reason:
                self.last_diagnostics = {
                    "status": "reflected",
                    "attempts": attempts,
                }
                return decision
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The previous decision was invalid: {invalid_reason}. "
                        "Call record_team_template_decision exactly once."
                    ),
                }
            )
        self.last_diagnostics = {"status": "invalid", "attempts": attempts}
        raise TeamReflectionError(
            "team reflection remained invalid after bounded retries: "
            f"{attempts[-1]['invalid_reason']}"
        )

    async def _call(self, messages: list[dict[str, str]]) -> Any:
        completion = self._completion
        if completion is None:
            from core.llm import complete

            completion = complete
        return await completion(
            model=self.model,
            messages=messages,
            tools=[self._tool_schema()],
            temperature=0.1,
            max_tokens=2400,
            api_tracker=self._api_tracker,
        )

    @staticmethod
    def _extract_calls(response: Any) -> list[dict[str, Any]]:
        from core.llm import extract_tool_calls

        return [
            dict(call.get("arguments") or {})
            for call in extract_tool_calls(response)
            if call.get("name") == "record_team_template_decision"
            and isinstance(call.get("arguments"), dict)
        ]

    @staticmethod
    def _decision_from_payload(
        payload: dict[str, Any],
        context: TeamReflectionContext,
        known_agent_ids: set[str],
    ) -> TeamReflectionDecision:
        raw_agent_ids = payload.get("agent_ids", [])
        if not isinstance(raw_agent_ids, list):
            raise ValueError("agent_ids must be a list")
        agent_ids = [str(item).strip() for item in raw_agent_ids]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("agent_ids must be unique")
        if context.chairman_id in agent_ids:
            raise ValueError("agent_ids must not include the chairman")
        if any(item in LLMTeamReflector.GLOBAL_SERVICE_AGENT_IDS for item in agent_ids):
            raise ValueError("agent_ids must not include global service agents")
        if any(item not in known_agent_ids for item in agent_ids):
            raise ValueError("agent_ids contains an unknown agent")
        profiles = {item.agent_id: item for item in context.global_agents}
        members = [
            TemplateMember(
                agent_id=agent_id,
                role=profiles.get(agent_id).role
                if profiles.get(agent_id) is not None
                else "team member",
            )
            for agent_id in agent_ids
        ]
        evidence = payload.get("evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("evidence must be an object")
        action = str(payload.get("action") or "").strip().lower()
        return TeamReflectionDecision(
            action=action,
            family_id=context.family_id,
            reason=str(payload.get("reason") or "").strip(),
            members=members,
            family_description=str(
                payload.get("family_description") or ""
            ).strip(),
            target_template_id=(
                None
                if action == "create"
                else str(payload.get("target_template_id") or "").strip() or None
            ),
            evidence={
                **evidence,
                "structural_diagnosis": payload.get("structural_diagnosis", {}),
            },
        )

    @staticmethod
    def _tool_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "record_team_template_decision",
                "description": "Record one post-task team-template decision.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "refine", "retain", "no_update"],
                        },
                        "reason": {"type": "string"},
                        "family_description": {"type": "string"},
                        "target_template_id": {"type": "string"},
                        "agent_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "structural_diagnosis": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                        "evidence": {"type": "object"},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        }


def validate_team_reflection(
    decision: TeamReflectionDecision,
    *,
    context: TeamReflectionContext,
    known_agent_ids: set[str],
) -> TeamReflectionDecision:
    decision.validate(known_agent_ids)
    evaluation_failed = context.evaluation.get("success") is False
    if evaluation_failed:
        diagnosis = decision.evidence.get("structural_diagnosis")
        if not isinstance(diagnosis, dict):
            raise ValueError(
                "failed evaluation requires structural_diagnosis object"
            )
        if not isinstance(diagnosis.get("is_team_structure_failure"), bool):
            raise ValueError(
                "structural_diagnosis.is_team_structure_failure must be boolean"
            )
        if not str(diagnosis.get("failure_type") or "").strip():
            raise ValueError(
                "structural_diagnosis.failure_type is required for failed evaluation"
            )
        if not str(diagnosis.get("basis") or "").strip():
            raise ValueError(
                "structural_diagnosis.basis is required for failed evaluation"
            )
    current = context.current_template
    if decision.family_id != context.family_id:
        raise ValueError("team reflection cannot switch the selected task family")
    if current is None:
        if decision.action not in {"create", "no_update"}:
            raise ValueError("cold-start reflection may only create or skip")
    else:
        if decision.action not in {"refine", "retain", "no_update"}:
            raise ValueError("an existing family cannot create a competing template")
        if (
            decision.action in {"refine", "retain"}
            and decision.target_template_id != current.template_id
        ):
            raise ValueError(
                f"{decision.action} must target the selected current template"
            )
    return decision
