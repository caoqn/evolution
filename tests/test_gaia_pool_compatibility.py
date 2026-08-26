from __future__ import annotations

from pathlib import Path

from core.runner import Runner
from main import load_pool


POOL_DIR = Path(__file__).resolve().parents[1] / "agents" / "pool_GAIA_MT"

COMPACT_ROLES = {
    "reading_agent": "Evidence and Archive Agent",
    "coding_agent": "Coding and Data Agent",
    "science_agent": "Quantitative and Science Agent",
    "verification_agent": "Verification Agent",
}


def test_compact_roles_load_in_meta_team_gaia_pool() -> None:
    pool = load_pool(POOL_DIR)
    assert pool.chairman_name == "plan_agent"
    assert len(pool.agents) == 8
    assert "answer_agent" in pool.agents
    for agent_id, role in COMPACT_ROLES.items():
        agent = pool.agents[agent_id]
        assert agent.config.role == role
        assert agent.config.description
        assert (POOL_DIR / agent_id / "prompt.md").exists()
        assert all(pool.registry.get(tool) is not None for tool in agent.config.tools)


def test_runner_exposes_only_selected_expanded_members() -> None:
    pool = load_pool(POOL_DIR)
    runner = Runner(
        pool_dir=POOL_DIR,
        chairman_name=pool.chairman_name,
        tool_registry=pool.registry,
        allowed_agent_ids={"reading_agent", "verification_agent"},
    )
    assert [item["name"] for item in runner.list_pool_agents()] == [
        "reading_agent",
        "verification_agent",
    ]
