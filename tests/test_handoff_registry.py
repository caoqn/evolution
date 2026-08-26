from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.handoff_registry import HandoffRegistry
from core.evolution_models import HandoffDecision
from core.handoff_reflector import HandoffReflectionContext, LLMHandoffReflector
from core.handoff_registry import HandoffRegistry


KNOWN = {"reader", "developer", "answer_agent"}


def _decision(action: str, handoff_family_id: str, instruction: str) -> HandoffDecision:
    return HandoffDecision(
        action=action,
        handoff_family_id=handoff_family_id,
        from_agent="reader",
        to_agent="developer",
        reason="Evidence supports a reusable handoff contract",
        instruction=instruction,
        payload_schema={"summary": "string"},
        required_evidence=["source_context"],
        verification="Developer confirms the summary is actionable.",
        fallback="Request the missing source context once.",
        rule_id=(
            "rule_research" if handoff_family_id == "hf_research"
            else "rule_code"
        ),
    )


def test_same_agent_pair_can_have_rules_in_different_handoff_families() -> None:
    registry = HandoffRegistry()
    registry.apply_decision(
        _decision("create", "hf_research", "Send cited research evidence."),
        task_id="task_research",
        known_agent_ids=KNOWN,
        team_family_id="family_research",
    )
    registry.apply_decision(
        _decision("create", "hf_code_review", "Send a reproducible code diagnosis."),
        task_id="task_code",
        known_agent_ids=KNOWN,
        team_family_id="family_code",
    )

    assert registry.rules_for_team_family("family_research")[0].instruction == (
        "Send cited research evidence."
    )
    assert registry.rules_for_team_family("family_code")[0].instruction == (
        "Send a reproducible code diagnosis."
    )


def test_refine_preserves_handoff_version_history_and_round_trips(tmp_path) -> None:
    registry = HandoffRegistry()
    created = registry.apply_decision(
        _decision("create", "hf_research", "Send cited research evidence."),
        task_id="task_1",
        known_agent_ids=KNOWN,
        team_family_id="family_research",
    )
    assert created is not None

    refined = registry.apply_decision(
        _decision("refine", "hf_research", "Send cited evidence and unresolved ambiguity."),
        task_id="task_2",
        known_agent_ids=KNOWN,
        team_family_id="family_research",
    )
    assert refined is created
    assert refined.version == 2
    assert refined.version_history[0]["version"] == 1
    assert refined.evidence_task_ids == ["task_1", "task_2"]

    path = tmp_path / "handoff_rules.json"
    registry.save(path)
    restored = HandoffRegistry.load(path)
    assert restored.handoff_family_for("family_research") == "hf_research"
    assert restored.list_rules()[0].version == 2


@pytest.mark.asyncio
async def test_handoff_reflector_receives_trace_and_allows_free_context_notes() -> None:
    seen = []

    async def completion(**kwargs):
        seen.append(kwargs["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            tool_calls=[{
                "id": "call_1",
                "function": {
                    "name": "record_handoff_decision",
                    "arguments": {
                        "action": "create",
                        "rule_id": "rule_trace_notes",
                        "from_agent": "reader",
                        "to_agent": "developer",
                        "reason": "Keep ambiguity labels in the evidence packet",
                        "instruction": "Send confirmed evidence before open hypotheses.",
                        "payload_schema": {"confirmed": "array"},
                        "required_evidence": ["source_context"],
                        "verification": "Developer confirms the packet is actionable.",
                        "fallback": "Request the missing context.",
                        "context_notes": "Preserve alternative interpretations when wording is ambiguous.",
                    },
                },
            }]
        ))])

    reflector = LLMHandoffReflector(model="test", completion=completion)
    decision = await reflector.reflect(HandoffReflectionContext(
        task_id="task_1",
        team_family_id="family_research",
        handoff_family_id="hf_research",
        task="Research task",
        actual_agent_ids=["reader", "developer"],
        handoff_trace=[{
            "from_agent": "reader",
            "to_agent": "developer",
            "content": "Confirmed source and unresolved ambiguity",
        }],
    ))

    payload = json.loads(seen[0][1]["content"])
    assert payload["handoff_trace"][0]["from_agent"] == "reader"
    assert decision.context_notes.startswith("Preserve alternative")


@pytest.mark.asyncio
async def test_handoff_reflector_allows_no_update_without_invented_agent_pair() -> None:
    async def completion(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            tool_calls=[{
                "id": "call_1",
                "function": {
                    "name": "record_handoff_decision",
                    "arguments": {
                        "action": "no_update",
                        "from_agent": "",
                        "to_agent": "",
                        "reason": "No meaningful inter-agent handoff occurred.",
                    },
                },
            }]
        ))])

    reflector = LLMHandoffReflector(model="test", completion=completion)
    decision = await reflector.reflect(HandoffReflectionContext(
        task_id="task_without_handoff",
        team_family_id="family_research",
        handoff_family_id="hf_research",
        task="Independent task",
        actual_agent_ids=["reader"],
    ))

    assert decision.action == "no_update"
    assert decision.from_agent == ""
    assert decision.to_agent == ""


def test_same_agent_pair_can_have_multiple_rules_and_retire_one() -> None:
    registry = HandoffRegistry()
    first = _decision("create", "hf_research", "Send source evidence.")
    second = HandoffDecision(
        action="create",
        handoff_family_id="hf_research",
        from_agent="reader",
        to_agent="developer",
        rule_id="rule_research_alt",
        reason="A separate evidence contract is useful for a different exchange.",
        instruction="Send competing interpretations and verification status.",
    )
    registry.apply_decision(first, task_id="task_1", known_agent_ids=KNOWN,
                            team_family_id="family_research")
    registry.apply_decision(second, task_id="task_2", known_agent_ids=KNOWN,
                            team_family_id="family_research")

    assert len(registry.rules_for_team_family("family_research")) == 2
    retired = registry.apply_decision(
        HandoffDecision(
            action="retire",
            handoff_family_id="hf_research",
            from_agent="reader",
            to_agent="developer",
            rule_id="rule_research_alt",
            reason="The alternative contract is obsolete after a stable replacement.",
        ),
        task_id="task_3",
        known_agent_ids=KNOWN,
        team_family_id="family_research",
    )
    assert retired is not None and retired.status == "retired"
    assert [r.rule_id for r in registry.rules_for_team_family("family_research")] == [
        "rule_research"
    ]
