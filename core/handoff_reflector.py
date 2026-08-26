"""Post-task reflection for family-scoped agent handoff contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.evolution_models import HandoffDecision
from core.handoff_registry import HandoffRegistry


@dataclass
class HandoffReflectionContext:
    task_id: str
    team_family_id: str
    handoff_family_id: str
    task: str
    actual_agent_ids: list[str]
    current_rules: list[dict[str, Any]] = field(default_factory=list)
    execution_summary: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    handoff_trace: list[dict[str, Any]] = field(default_factory=list)
    historical_rule_summaries: list[dict[str, Any]] = field(default_factory=list)


class HandoffReflectionError(ValueError):
    pass


CompletionCallable = Callable[..., Awaitable[Any]]


class LLMHandoffReflector:
    MAX_ATTEMPTS = 3

    def __init__(self, *, model: str, completion: CompletionCallable | None = None,
                 api_tracker: Any | None = None) -> None:
        self.model = model
        self._completion = completion
        self._api_tracker = api_tracker

    async def reflect(self, context: HandoffReflectionContext) -> HandoffDecision:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the post-task reflector for reusable information handoffs "
                    "between agents. Call record_handoff_decision exactly once. Do not "
                    "create a workflow, DAG, task assignment, skill, capability list, "
                    "or team template. A handoff rule is a directed information contract "
                    "for one sender and one receiver within a handoff family.\n\n"
                    "A handoff family may contain multiple rules for the same agent pair. "
                    "Use the stable handoff_rule_id to identify the specific contract being "
                    "created, refined, retained, or retired; do not overwrite a different "
                    "rule merely because its sender and receiver match.\n\n"
                    "Ground every claim in the actual handoff trace, execution outcome, "
                    "evaluation, failures, or existing rule history. Distinguish a bad "
                    "handoff from an API/provider failure, missing capability, or task "
                    "assignment error; do not encode those unrelated failures as protocol "
                    "guidance. Do not infer a stable preference from a single unsupported "
                    "observation.\n\n"
                    "Record generalizable collaboration patterns, not one-task details. "
                    "Do not mention concrete filenames, class names, module names, PR names, "
                    "repository names, benchmark names, issue numbers, or other "
                    "benchmark-specific objects. Prefer reusable guidance about what the "
                    "receiver needs, evidence/provenance and uncertainty, acceptance or "
                    "verification information, communication style, agreed conventions, "
                    "and a sensible fallback when information is incomplete.\n\n"
                    "Treat the current rule as a living record: preserve guidance that is "
                    "still supported, remove or correct claims contradicted by newer "
                    "evidence, and add only information that improves future handoffs. "
                    "The named fields are a stable minimum schema, not a fixed prose "
                    "template. Use context_notes for important nuances that do not fit "
                    "the other fields; do not force information into a field or discard it "
                    "just because it is unusual."
                    "\n\n"
                    "A single failed task is not sufficient evidence to remove a member or "
                    "retire a rule that has successful historical support. Removal/retirement "
                    "is justified only when historical evidence or the current trace clearly "
                    "shows that the member or rule is: (1) persistently mismatched with the "
                    "family collaboration pattern; (2) redundant with another member or rule; "
                    "(3) misleading or harmful in execution; (4) repeatedly misrecruited or "
                    "causing ineffective handoffs; or (5) replaced by a more stable and accurate "
                    "rule. Otherwise preserve it or add a separate rule."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "task_id": context.task_id,
                    "team_family_id": context.team_family_id,
                    "handoff_family_id": context.handoff_family_id,
                    "task": context.task,
                    "actual_agent_ids": context.actual_agent_ids,
                    "current_rules": context.current_rules,
                    "execution_summary": context.execution_summary,
                    "evaluation": context.evaluation,
                    "failures": context.failures,
                    "handoff_trace": context.handoff_trace,
                    "historical_rule_summaries": context.historical_rule_summaries,
                    "instruction": (
                        "Use create when this task provides a useful new handoff contract, "
                        "refine when one identified existing contract needs correction, retain when it "
                        "remains useful, retire only with the explicit historical evidence standard "
                        "above, and no_update when evidence is insufficient. For create/refine "
                        "provide a new or existing handoff_rule_id, instruction, payload_schema, required_evidence, "
                        "verification, fallback, and optional context_notes. These fields may "
                        "be concise and need not all be populated when not applicable. Do not "
                        "add trigger conditions or task-specific routing rules. For refine, "
                        "return a complete merged replacement, keep valid prior guidance, "
                        "remove obsolete one-off observations, and explain what was retained, "
                        "removed, and added in reason. For no_update, use empty from_agent and "
                        "to_agent rather than inventing a pair."
                    ),
                }, ensure_ascii=False),
            },
        ]
        for _ in range(self.MAX_ATTEMPTS):
            response = await self._call(messages)
            calls = self._extract_calls(response)
            if len(calls) == 1:
                try:
                    decision = HandoffDecision(
                        action=str(calls[0].get("action") or "").strip().lower(),
                        handoff_family_id=context.handoff_family_id,
                        rule_id=str(calls[0].get("rule_id") or "").strip(),
                        from_agent=str(calls[0].get("from_agent") or "").strip(),
                        to_agent=str(calls[0].get("to_agent") or "").strip(),
                        reason=str(calls[0].get("reason") or "").strip(),
                        instruction=str(calls[0].get("instruction") or "").strip(),
                        payload_schema=calls[0].get("payload_schema") or {},
                        required_evidence=[str(x) for x in calls[0].get("required_evidence", [])],
                        verification=str(calls[0].get("verification") or "").strip(),
                        fallback=str(calls[0].get("fallback") or "").strip(),
                        context_notes=str(calls[0].get("context_notes") or "").strip(),
                        evidence={"team_family_id": context.team_family_id},
                    )
                    decision.validate()
                    if decision.action != "no_update" and any(
                        agent not in context.actual_agent_ids
                        for agent in (decision.from_agent, decision.to_agent)
                    ):
                        raise ValueError("handoff agents must participate in this task")
                    return decision
                except (TypeError, ValueError):
                    pass
            messages.append({"role": "user", "content": "Return exactly one valid handoff decision tool call."})
        raise HandoffReflectionError("handoff reflection remained invalid after bounded retries")

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
            max_tokens=1800,
            api_tracker=self._api_tracker,
        )

    @staticmethod
    def _extract_calls(response: Any) -> list[dict[str, Any]]:
        from core.llm import extract_tool_calls
        return [
            dict(call.get("arguments") or {})
            for call in extract_tool_calls(response)
            if call.get("name") == "record_handoff_decision"
            and isinstance(call.get("arguments"), dict)
        ]

    @staticmethod
    def _tool_schema() -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": "record_handoff_decision",
            "description": "Record one family-scoped agent handoff contract decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "refine", "retain", "retire", "no_update"]},
                    "rule_id": {
                        "type": "string",
                        "description": "Stable id of this specific handoff rule. Required for create/refine/retain/retire; empty only for no_update.",
                    },
                    "from_agent": {"type": "string"},
                    "to_agent": {"type": "string"},
                    "reason": {"type": "string"},
                    "instruction": {"type": "string"},
                    "payload_schema": {"type": "object"},
                    "required_evidence": {"type": "array", "items": {"type": "string"}},
                    "verification": {"type": "string"},
                    "fallback": {"type": "string"},
                    "context_notes": {"type": "string"},
                },
                "required": ["action", "reason", "rule_id"],
                "additionalProperties": False,
            },
        }}
