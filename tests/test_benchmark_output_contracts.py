from pathlib import Path

from core.output_contract import output_contract_from_settings
from main import load_pool


ROOT = Path(__file__).resolve().parents[1]
POOL_NAMES = (
    "pool_GAIA_MT",
    "pool_DeepResearch",
    "pool_SWE_Pro",
    "pool_BeyondSWE",
    "pool_LoCoBench",
    "pool_LOCAbench",
)


def test_each_benchmark_declares_an_answer_agent_and_protocol():
    for pool_name in POOL_NAMES:
        pool_dir = ROOT / "agents" / pool_name
        pool = load_pool(pool_dir)
        answer_name = pool.settings["answer_agent"]
        contract = output_contract_from_settings(
            pool.settings, pool.settings.get("output_contract"),
        )

        answer_dir = pool_dir / answer_name
        assert (answer_dir / "config.yaml").exists()
        assert (answer_dir / "prompt.md").exists()
        assert pool.settings["answer_agent_required"] is True
        assert pool.settings["answer_protocol"]
        assert contract.answer_agent_enabled is True
        assert contract.enforce_answer_agent is True
        assert contract.has_emergency_finalize is True


def test_answer_agent_prompts_use_their_declared_protocol():
    for pool_name in POOL_NAMES:
        pool_dir = ROOT / "agents" / pool_name
        pool = load_pool(pool_dir)
        prompt = (pool_dir / pool.settings["answer_agent"] / "prompt.md").read_text()
        if pool.settings["answer_protocol"] == "gaia_short_answer":
            assert "FINAL ANSWER:" in prompt
        else:
            assert "FINAL OUTPUT:" in prompt
