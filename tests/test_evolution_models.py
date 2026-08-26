from __future__ import annotations

import pytest

from core.evolution_models import (
    HandoffRule,
    SkillDecision,
    TeamReflectionDecision,
    TeamSelection,
    TemplateMember,
)


KNOWN_AGENTS = {"plan_agent", "web_agent", "answer_agent", "file_agent"}


def test_team_selection_validates_private_allowed_roster() -> None:
    selection = TeamSelection(
        family_action="create",
        family_id="web_research",
        family_description="Tasks requiring external source research",
        chairman_id="plan_agent",
        allowed_agent_ids=["web_agent", "answer_agent"],
    )
    selection.validate(KNOWN_AGENTS)


def test_team_selection_rejects_unknown_or_duplicate_agents() -> None:
    with pytest.raises(ValueError, match="unique"):
        TeamSelection(
            family_action="create",
            family_id="web_research",
            family_description="External research",
            chairman_id="plan_agent",
            allowed_agent_ids=["web_agent", "web_agent"],
        ).validate(KNOWN_AGENTS)


def test_skill_create_requires_complete_markdown_metadata() -> None:
    with pytest.raises(ValueError, match="requires name"):
        SkillDecision(
            action="create",
            agent_id="web_agent",
            skill_id="source_check",
            reason="The procedure should be reused",
        ).validate()


def test_handoff_rule_is_directed_and_cannot_self_loop() -> None:
    with pytest.raises(ValueError, match="different agents"):
        HandoffRule(
            handoff_family_id="hf_web_research",
            from_agent="web_agent",
            to_agent="web_agent",
            version=1,
            instruction="Send cited evidence",
        ).validate(KNOWN_AGENTS)


def test_team_reflection_create_requires_complete_team() -> None:
    decision = TeamReflectionDecision(
        action="create",
        family_id="web_research",
        family_description="External source research",
        reason="The observed team covered research and synthesis",
        members=[
            TemplateMember("web_agent", "Collect primary-source evidence"),
            TemplateMember("answer_agent", "Synthesize the final answer"),
        ],
    )
    decision.validate(KNOWN_AGENTS)
    assert decision.to_dict()["action"] == "create"


def test_refine_requires_description_and_retain_cannot_replace_it() -> None:
    with pytest.raises(ValueError, match="refine requires family_description"):
        TeamReflectionDecision(
            action="refine",
            family_id="web_research",
            target_template_id="tpl_web_research_v1",
            reason="The reusable roster changed",
            members=[TemplateMember("web_agent", "Collect sources")],
        ).validate(KNOWN_AGENTS)

    with pytest.raises(ValueError, match="must not emit replacement"):
        TeamReflectionDecision(
            action="retain",
            family_id="web_research",
            family_description="Do not overwrite this",
            target_template_id="tpl_web_research_v1",
            reason="The current structure remains sufficient",
        ).validate(KNOWN_AGENTS)
