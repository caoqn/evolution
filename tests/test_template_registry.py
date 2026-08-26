from __future__ import annotations

import json

import pytest

from core.evolution_models import TeamReflectionDecision, TemplateMember
from core.template_registry import TemplateRegistry


KNOWN_AGENTS = {"plan_agent", "web_agent", "answer_agent", "file_agent"}


def create_decision() -> TeamReflectionDecision:
    return TeamReflectionDecision(
        action="create",
        family_id="web_research",
        family_description="External source research tasks",
        reason="The cold-start rollout established a reusable team",
        members=[
            TemplateMember("web_agent", "Collect external evidence"),
            TemplateMember("answer_agent", "Synthesize final answers"),
        ],
    )


def test_create_refine_and_retain_one_current_lineage() -> None:
    registry = TemplateRegistry()
    created = registry.apply_reflection(
        create_decision(), task_id="task_1", known_agent_ids=KNOWN_AGENTS
    )
    assert created is not None
    assert created.version == 1
    assert created.successful_task_ids == ["task_1"]
    assert created.failed_task_ids == []

    refined = registry.apply_reflection(
        TeamReflectionDecision(
            action="refine",
            family_id="web_research",
            family_description=(
                "External research requiring attachment inspection and source verification"
            ),
            target_template_id=created.template_id,
            reason="File evidence is now required",
            members=[
                TemplateMember("web_agent", "Collect external evidence"),
                TemplateMember("file_agent", "Inspect task attachments"),
                TemplateMember("answer_agent", "Synthesize final answers"),
            ],
        ),
        task_id="task_2",
        known_agent_ids=KNOWN_AGENTS,
    )
    assert refined is created
    assert refined.version == 2
    assert len(refined.version_history) == 1
    assert refined.version_history[0].version == 1
    assert refined.version_history[0].family_description == (
        "External source research tasks"
    )
    assert registry.get_family("web_research").description == (
        "External research requiring attachment inspection and source verification"
    )

    retained = registry.apply_reflection(
        TeamReflectionDecision(
            action="retain",
            family_id="web_research",
            target_template_id=created.template_id,
            reason="The current structure remained sufficient",
        ),
        task_id="task_3",
        known_agent_ids=KNOWN_AGENTS,
    )
    assert retained is created
    assert retained.version == 2
    assert retained.evidence_task_ids == ["task_1", "task_2", "task_3"]
    assert retained.successful_task_ids == ["task_1", "task_2", "task_3"]


def test_task_member_evidence_tracks_actual_members_not_template_roster() -> None:
    registry = TemplateRegistry()
    created = registry.apply_reflection(
        create_decision(),
        task_id="task_1",
        known_agent_ids=KNOWN_AGENTS,
        actual_agent_ids=["web_agent"],
    )
    assert created is not None
    assert created.allowed_agent_ids == ["web_agent", "answer_agent"]
    assert created.task_member_evidence == {"task_1": ["web_agent"]}

    retained = registry.apply_reflection(
        TeamReflectionDecision(
            action="retain",
            family_id="web_research",
            target_template_id=created.template_id,
            reason="The candidate roster remains reusable",
        ),
        task_id="task_2",
        known_agent_ids=KNOWN_AGENTS,
        actual_agent_ids=[],
    )
    assert retained is created
    assert retained.task_member_evidence["task_2"] == []


def test_legacy_template_without_member_evidence_loads_as_unknown(tmp_path) -> None:
    registry = TemplateRegistry()
    registry.apply_reflection(
        create_decision(), task_id="legacy", known_agent_ids=KNOWN_AGENTS
    )
    payload = registry.to_dict()
    payload["templates"][0].pop("task_member_evidence")
    path = tmp_path / "legacy_templates.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    restored = TemplateRegistry.load(path).get_current("web_research")
    assert restored is not None
    assert restored.task_member_evidence == {}


def test_failed_task_is_retained_as_negative_template_evidence() -> None:
    registry = TemplateRegistry()
    created = registry.apply_reflection(
        create_decision(), task_id="success", known_agent_ids=KNOWN_AGENTS
    )
    assert created is not None

    refined = registry.apply_reflection(
        TeamReflectionDecision(
            action="refine",
            family_id="web_research",
            family_description=(
                "External research with independent evidence verification"
            ),
            target_template_id=created.template_id,
            reason="The web handoff timed out; add a verification role",
            members=[
                TemplateMember("web_agent", "Collect external evidence"),
                TemplateMember("file_agent", "Verify retrieved evidence"),
                TemplateMember("answer_agent", "Synthesize final answers"),
            ],
        ),
        task_id="failed",
        known_agent_ids=KNOWN_AGENTS,
        task_success=False,
    )
    assert refined.version == 2
    assert refined.evidence_task_ids == ["success", "failed"]
    assert refined.successful_task_ids == ["success"]
    assert refined.failed_task_ids == ["failed"]


def test_registry_rejects_parallel_template_create() -> None:
    registry = TemplateRegistry()
    registry.apply_reflection(
        create_decision(), task_id="task_1", known_agent_ids=KNOWN_AGENTS
    )
    with pytest.raises(ValueError, match="competing"):
        registry.apply_reflection(
            create_decision(), task_id="task_2", known_agent_ids=KNOWN_AGENTS
        )


def test_registry_round_trip_uses_atomic_json_file(tmp_path) -> None:
    path = tmp_path / "templates.json"
    registry = TemplateRegistry()
    created = registry.apply_reflection(
        create_decision(), task_id="task_1", known_agent_ids=KNOWN_AGENTS
    )
    registry.save(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert "chairman_id" not in payload["templates"][0]
    loaded = TemplateRegistry.load(path)
    restored = loaded.get_current("web_research")
    assert restored is not None
    assert restored.template_id == created.template_id
    assert restored.allowed_agent_ids == ["web_agent"]


def test_legacy_snapshot_without_description_still_loads(tmp_path) -> None:
    registry = TemplateRegistry()
    created = registry.apply_reflection(
        create_decision(), task_id="task_1", known_agent_ids=KNOWN_AGENTS
    )
    registry.apply_reflection(
        TeamReflectionDecision(
            action="refine",
            family_id="web_research",
            family_description="Research with attachment inspection",
            target_template_id=created.template_id,
            reason="Attachment inspection became part of the collaboration pattern",
            members=[TemplateMember("file_agent", "Inspect task attachments")],
        ),
        task_id="task_2",
        known_agent_ids=KNOWN_AGENTS,
    )
    payload = registry.to_dict()
    payload["templates"][0]["version_history"][0].pop("family_description")
    path = tmp_path / "legacy_templates.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = TemplateRegistry.load(path).get_current("web_research")
    assert restored is not None
    assert restored.version_history[0].family_description == ""


def test_registry_rejects_duplicate_family_rows() -> None:
    registry = TemplateRegistry()
    created = registry.apply_reflection(
        create_decision(), task_id="task_1", known_agent_ids=KNOWN_AGENTS
    )
    family = registry.get_family("web_research")
    assert family is not None and created is not None
    with pytest.raises(ValueError, match="duplicate template family"):
        TemplateRegistry(families=[family, family], templates=[created])


def test_registry_rejects_malformed_serialized_rows() -> None:
    with pytest.raises(ValueError, match="list of objects"):
        TemplateRegistry.from_dict(
            {"schema_version": 1, "families": ["invalid"], "templates": []}
        )


def test_template_payload_rejects_old_workflow_fields() -> None:
    registry = TemplateRegistry()
    registry.apply_reflection(
        create_decision(), task_id="task_1", known_agent_ids=KNOWN_AGENTS
    )
    payload = registry.to_dict()
    payload["templates"][0]["workflow"] = ["s1", "s2"]
    with pytest.raises(ValueError, match="forbidden execution fields"):
        TemplateRegistry.from_dict(payload)


def test_registry_migrates_legacy_template_chairman_field() -> None:
    registry = TemplateRegistry()
    registry.apply_reflection(
        create_decision(), task_id="legacy_seed", known_agent_ids=KNOWN_AGENTS
    )
    payload = registry.to_dict()
    payload["schema_version"] = 1
    payload["templates"][0]["chairman_id"] = "plan_agent"
    restored = TemplateRegistry.from_dict(payload)
    template = restored.get_current("web_research")
    assert template is not None
    assert not hasattr(template, "chairman_id")
    assert "chairman_id" not in restored.catalog()[0]
