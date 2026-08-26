from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.evolution_models import TeamReflectionDecision, TemplateMember
from core.team_selector import (
    LLMTeamSelector,
    PublicAgentProfile,
    TeamSelectionContext,
    TeamSelectionError,
)
from core.team_reflector import (
    LLMTeamReflector,
    TeamReflectionContext,
)
from core.template_registry import TemplateRegistry


def _response(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        {
                            "id": "call_1",
                            "function": {
                                "name": name,
                                "arguments": arguments,
                            },
                        }
                    ]
                )
            )
        ]
    )


def _registry() -> TemplateRegistry:
    registry = TemplateRegistry()
    registry.apply_reflection(
        TeamReflectionDecision(
            action="create",
            family_id="web_research",
            family_description="External research",
            reason="initial",
            members=[TemplateMember("web", "Collect sources")],
        ),
        task_id="seed",
        known_agent_ids={"chairman", "web"},
    )
    return registry


@pytest.mark.asyncio
async def test_reuse_only_sends_task_and_catalog_and_does_not_load_agents():
    calls = []

    async def completion(**kwargs):
        calls.append(kwargs["messages"])
        return _response(
            "select_template_family",
            {"family_action": "reuse", "family_id": "web_research"},
        )

    def forbidden_provider():
        raise AssertionError("global roster must not be loaded for reuse")

    selector = LLMTeamSelector(
        model="test",
        registry=_registry(),
        completion=completion,
        agent_catalog_provider=forbidden_provider,
    )
    result = await selector.select(
        TeamSelectionContext(
            raw_task="Find sources",
            chairman_id="chairman",
            template_catalog=selector.registry.catalog(),
        )
    )
    assert result.allowed_agent_ids == ["web"]
    assert len(calls) == 1
    assert "public_agent_registry" not in calls[0][1]["content"]


@pytest.mark.asyncio
async def test_cold_start_loads_global_roster_only_after_family_miss():
    calls = []
    loaded = 0

    async def completion(**kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            return _response(
                "select_template_family",
                {
                    "family_action": "cold_start",
                    "family_id": "new_family",
                    "family_description": "A new task family",
                },
            )
        return _response(
            "compose_cold_start_team", {"provisional_agent_ids": ["web"]}
        )

    def provider():
        nonlocal loaded
        loaded += 1
        return [PublicAgentProfile("web", "researcher", "Find evidence")]

    selector = LLMTeamSelector(
        model="test",
        registry=_registry(),
        completion=completion,
        agent_catalog_provider=provider,
    )
    result = await selector.select(
        TeamSelectionContext(
            raw_task="Investigate a new topic",
            chairman_id="chairman",
            template_catalog=selector.registry.catalog(),
        )
    )
    assert result.family_action == "create"
    assert result.allowed_agent_ids == ["web"]
    assert result.family_id.startswith("family_")
    assert result.family_id != "new_family"
    assert "selector_reason" in calls[1][1]["content"]
    assert "soft" in calls[1][1]["content"]
    assert loaded == 1
    assert "public_agent_registry" not in calls[0][1]["content"]
    assert "public_agent_registry" in calls[1][1]["content"]


@pytest.mark.asyncio
async def test_cold_start_requires_a_lazy_catalog_provider():
    selector = LLMTeamSelector(
        model="test",
        registry=_registry(),
        completion=lambda **kwargs: None,
    )
    with pytest.raises(TeamSelectionError, match="agent_catalog_provider"):
        await selector._load_agent_catalog()


@pytest.mark.asyncio
async def test_invalid_template_selection_falls_back_to_cold_start():
    calls = []

    async def completion(**kwargs):
        calls.append(kwargs["tools"][0]["function"]["name"])
        if calls[-1] == "select_template_family":
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(tool_calls=[])
            )])
        return _response("compose_cold_start_team", {"provisional_agent_ids": ["web"]})

    selector = LLMTeamSelector(
        model="test",
        registry=_registry(),
        completion=completion,
        agent_catalog_provider=lambda: [
            PublicAgentProfile("web", "researcher", "Find evidence")
        ],
    )
    result = await selector.select(TeamSelectionContext(
        raw_task="A task whose family call is malformed",
        chairman_id="chairman",
        template_catalog=selector.registry.catalog(),
    ))
    assert result.family_action == "create"
    assert result.allowed_agent_ids == ["web"]
    assert result.metadata["source"] == "template_selection_fallback"
    assert calls == [
        "select_template_family", "select_template_family", "select_template_family",
        "compose_cold_start_team",
    ]


@pytest.mark.asyncio
async def test_invalid_selection_and_composition_uses_public_roster():
    async def completion(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(tool_calls=[])
        )])

    selector = LLMTeamSelector(
        model="test",
        registry=_registry(),
        completion=completion,
        agent_catalog_provider=lambda: [
            PublicAgentProfile("web", "researcher", "Find evidence"),
            PublicAgentProfile("data", "analyst", "Analyze data"),
        ],
    )
    result = await selector.select(TeamSelectionContext(
        raw_task="A task whose selector and composer are malformed",
        chairman_id="chairman",
        template_catalog=selector.registry.catalog(),
    ))
    assert result.allowed_agent_ids == ["web", "data"]
    assert result.metadata["fallback_roster_source"] == "deterministic_all_public_agents"


@pytest.mark.asyncio
async def test_deterministic_fallback_excludes_global_answer_agent():
    async def completion(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(tool_calls=[])
        )])

    selector = LLMTeamSelector(
        model="test",
        registry=_registry(),
        completion=completion,
        agent_catalog_provider=lambda: [
            PublicAgentProfile("web", "researcher", "Find evidence"),
            PublicAgentProfile("answer_agent", "service", "Synthesize output"),
        ],
    )
    result = await selector.select(TeamSelectionContext(
        raw_task="A task requiring fallback",
        chairman_id="chairman",
        template_catalog=selector.registry.catalog(),
    ))
    assert result.allowed_agent_ids == ["web"]


@pytest.mark.asyncio
async def test_team_reflection_uses_co_evolution_style_agent_ids():
    async def completion(**kwargs):
        return _response(
            "record_team_template_decision",
            {
                "action": "create",
                "reason": "The team worked repeatedly across the completed task",
                "family_description": "A new reusable research family",
                "target_template_id": "stale_template_id",
                "agent_ids": ["web"],
                "evidence": {"score": 1.0},
            },
        )

    reflector = LLMTeamReflector(model="test", completion=completion)
    decision = await reflector.reflect(
        TeamReflectionContext(
            task_id="task_1",
            raw_task="Investigate a new topic",
            chairman_id="chairman",
            family_id="new_family",
            current_template=None,
            allowed_agent_ids=["web"],
            actual_agent_ids=["chairman", "web"],
            execution_summary={"output": "done"},
            evaluation={"score": 1.0},
            failures=[],
            global_agents=[PublicAgentProfile("web", "researcher", "Find evidence")],
        )
    )
    assert decision.action == "create"
    assert decision.target_template_id is None
    assert decision.members[0].agent_id == "web"


@pytest.mark.asyncio
async def test_reflection_no_update_does_not_require_a_replacement_roster():
    async def completion(**kwargs):
        return _response(
            "record_team_template_decision",
            {
                "action": "no_update",
                "reason": "The task did not provide reusable structural evidence",
            },
        )

    registry = _registry()
    current = registry.get_current("web_research")
    assert current is not None
    reflector = LLMTeamReflector(model="test", completion=completion)
    decision = await reflector.reflect(
        TeamReflectionContext(
            task_id="task_2",
            raw_task="Find sources",
            chairman_id="chairman",
            family_id="web_research",
            current_template=current,
            allowed_agent_ids=current.allowed_agent_ids,
            actual_agent_ids=["chairman", "web"],
            execution_summary={},
            evaluation={"score": 0.0},
            failures=["insufficient evidence"],
            global_agents=[PublicAgentProfile("web", "researcher", "Find evidence")],
        )
    )
    assert decision.action == "no_update"
