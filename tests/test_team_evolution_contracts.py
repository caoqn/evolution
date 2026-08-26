from __future__ import annotations

import pytest

from core.evolution_models import (
    TeamReflectionDecision,
    TeamSelection,
    TemplateMember,
)
from core.team_reflector import TeamReflectionContext, validate_team_reflection
from core.team_selector import validate_team_selection
from core.template_registry import TemplateRegistry


KNOWN_AGENTS = {"plan_agent", "web_agent", "answer_agent", "file_agent"}


def _registry() -> TemplateRegistry:
    registry = TemplateRegistry()
    registry.apply_reflection(
        TeamReflectionDecision(
            action="create",
            family_id="web_research",
            family_description="External source research tasks",
            reason="Reusable cold-start team",
            members=[
                TemplateMember("web_agent", "Collect evidence"),
                TemplateMember("answer_agent", "Synthesize the answer"),
            ],
        ),
        task_id="task_1",
        known_agent_ids=KNOWN_AGENTS,
    )
    return registry


def test_reuse_selection_must_equal_current_template_roster() -> None:
    registry = _registry()
    current = registry.get_current("web_research")
    assert current is not None
    valid = TeamSelection(
        family_action="reuse",
        family_id="web_research",
        chairman_id="plan_agent",
        allowed_agent_ids=["web_agent", "answer_agent"],
        template_id=current.template_id,
        template_version=current.version,
    )
    validate_team_selection(
        valid,
        registry=registry,
        known_agent_ids=KNOWN_AGENTS,
        expected_chairman_id="plan_agent",
    )

    valid.allowed_agent_ids = ["web_agent", "file_agent", "answer_agent"]
    with pytest.raises(ValueError, match="cannot change"):
        validate_team_selection(
            valid,
            registry=registry,
            known_agent_ids=KNOWN_AGENTS,
            expected_chairman_id="plan_agent",
        )


def test_existing_family_reflection_cannot_create_parallel_template() -> None:
    registry = _registry()
    current = registry.get_current("web_research")
    assert current is not None
    context = TeamReflectionContext(
        task_id="task_2",
        raw_task="Find and verify an external fact",
        chairman_id="plan_agent",
        family_id="web_research",
        current_template=current,
        allowed_agent_ids=current.allowed_agent_ids,
        actual_agent_ids=["plan_agent", "web_agent", "answer_agent"],
        execution_summary={},
        evaluation={"score": 1.0},
        failures=[],
    )
    with pytest.raises(ValueError, match="cannot create"):
        validate_team_reflection(
            TeamReflectionDecision(
                action="create",
                family_id="web_research",
                family_description="Duplicate branch",
                reason="Invalid parallel proposal",
                members=[TemplateMember("file_agent", "Inspect files")],
            ),
            context=context,
            known_agent_ids=KNOWN_AGENTS,
        )


def test_failed_reflection_requires_structural_failure_diagnosis() -> None:
    registry = _registry()
    current = registry.get_current("web_research")
    assert current is not None
    context = TeamReflectionContext(
        task_id="failed_task",
        raw_task="Find an external fact",
        chairman_id="plan_agent",
        family_id="web_research",
        current_template=current,
        allowed_agent_ids=current.allowed_agent_ids,
        actual_agent_ids=["plan_agent", "web_agent", "answer_agent"],
        execution_summary={},
        evaluation={"success": False, "score": 0.0},
        failures=["web agent timed out"],
    )
    decision = TeamReflectionDecision(
        action="refine",
        family_id="web_research",
        family_description="External evidence collection with source verification",
        target_template_id=current.template_id,
        reason="The failure needs diagnosis before changing membership",
        members=[TemplateMember("web_agent", "Collect evidence")],
    )
    with pytest.raises(ValueError, match="structural_diagnosis"):
        validate_team_reflection(
            decision, context=context, known_agent_ids=KNOWN_AGENTS
        )

    decision.evidence = {
        "structural_diagnosis": {
            "is_team_structure_failure": False,
            "failure_type": "network",
            "basis": "The roster was sufficient; the provider timed out.",
        }
    }
    validate_team_reflection(
        decision, context=context, known_agent_ids=KNOWN_AGENTS
    )
