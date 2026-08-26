from __future__ import annotations

import asyncio
from types import SimpleNamespace
import yaml
import pytest

from core.runner import Runner
from core.output_contract import GAIA_OUTPUT_CONTRACT, output_contract_from_name


def _pool(tmp_path, *, with_answer_agent=False):
    names = ["chairman", "worker_a", "worker_b"]
    if with_answer_agent:
        names.append("answer_agent")
    for name in names:
        agent_dir = tmp_path / name
        agent_dir.mkdir()
        (agent_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "role": "worker",
                    "description": name,
                    "model": "test",
                    "tools": [],
                }
            ),
            encoding="utf-8",
        )
        (agent_dir / "prompt.md").write_text("test", encoding="utf-8")
    return tmp_path


def test_selected_team_is_the_only_recruitable_pool_view(tmp_path):
    pool = _pool(tmp_path)
    runner = Runner(
        pool_dir=pool,
        chairman_name="chairman",
        allowed_agent_ids={"worker_a"},
    )
    assert [row["name"] for row in runner.list_pool_agents()] == ["worker_a"]
    with pytest.raises(PermissionError):
        runner.load_agent_from_pool("worker_b")


def test_unrestricted_runner_preserves_legacy_pool_visibility(tmp_path):
    runner = Runner(pool_dir=_pool(tmp_path), chairman_name="chairman")
    assert [row["name"] for row in runner.list_pool_agents()] == [
        "worker_a",
        "worker_b",
    ]


def test_final_output_is_locked_before_reflection(tmp_path):
    runner = Runner(pool_dir=_pool(tmp_path), chairman_name="chairman")

    async def exercise():
        assert await runner.set_final_output("FINAL ANSWER: 17") == "Output recorded successfully."
        runner._phase = "l1_reflection"
        message = await runner.set_final_output("reflection prose")
        assert "locked" in message

    asyncio.run(exercise())
    assert runner._result == "FINAL ANSWER: 17"


def test_chairman_context_has_one_mode_specific_submission_protocol(tmp_path):
    pool = _pool(tmp_path)
    eval_context = Runner(
        pool_dir=pool, chairman_name="chairman", enable_reflection=False,
        output_contract=GAIA_OUTPUT_CONTRACT,
    )._build_chairman_context()
    evolve_context = Runner(
        pool_dir=pool, chairman_name="chairman", enable_reflection=True,
        output_contract=GAIA_OUTPUT_CONTRACT,
    )._build_chairman_context()

    assert "Call exactly `set_final_output(output=<final output>)`" in eval_context
    assert "Do not call `finalize_task` in this run." in eval_context
    assert "Call exactly `finalize_task(output=<final output>)`" in evolve_context
    assert "Do not call `set_final_output` or `terminate` during task execution." in evolve_context


def test_worker_context_does_not_duplicate_l2_profiles(tmp_path):
    pool = _pool(tmp_path)
    profile_dir = pool / "worker_a" / "evolution"
    profile_dir.mkdir()
    (profile_dir / "teammate_profiles.yaml").write_text(
        "chairman:\n  reliability: high\n",
        encoding="utf-8",
    )
    runner = Runner(pool_dir=pool, chairman_name="chairman")
    worker = runner.load_agent_from_pool("worker_a")

    assert worker.build_system_prompt().count("## Teammate Profiles") == 1
    assert "reliability: high" not in runner._build_agent_context(worker)


def test_emergency_finalization_uses_plain_completion_and_locks_output(tmp_path, monkeypatch):
    runner = Runner(
        pool_dir=_pool(tmp_path), chairman_name="chairman",
        output_contract=GAIA_OUTPUT_CONTRACT,
    )
    runner._forced_termination = "timeout"
    chairman = SimpleNamespace(
        config=SimpleNamespace(name="chairman", model="test", temperature=0, max_tokens=32),
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "task"}],
    )
    runner.agents["chairman"] = chairman
    answer_agent = SimpleNamespace(
        config=SimpleNamespace(name="answer_agent", model="test", temperature=0, max_tokens=32),
        messages=[],
        build_system_prompt=lambda: "AnswerAgent system",
    )
    runner.agents["answer_agent"] = answer_agent
    captured = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="FINAL ANSWER: 17"))])

    monkeypatch.setattr("core.llm.complete", fake_complete)
    asyncio.run(runner._emergency_finalize_after_forced_termination())

    assert "tools" not in captured
    assert captured["messages"][0]["content"] == "AnswerAgent system"
    assert runner._result == "17"
    assert runner._final_output_locked is True
    assert runner._finalization_mode == "emergency"


def test_emergency_finalization_strips_tool_protocol_from_history(tmp_path, monkeypatch):
    runner = Runner(
        pool_dir=_pool(tmp_path), chairman_name="chairman",
        output_contract=GAIA_OUTPUT_CONTRACT,
    )
    runner._forced_termination = "timeout"
    chairman = SimpleNamespace(
        config=SimpleNamespace(name="chairman", model="test", temperature=0, max_tokens=32),
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "content": "I will inspect the source.",
                "tool_calls": [{"id": "call_1", "function": {"name": "web", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "The answer is 17."},
        ],
    )
    runner.agents["chairman"] = chairman
    answer_agent = SimpleNamespace(
        config=SimpleNamespace(name="answer_agent", model="test", temperature=0, max_tokens=32),
        messages=[],
        build_system_prompt=lambda: "AnswerAgent system",
    )
    runner.agents["answer_agent"] = answer_agent
    captured = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="FINAL ANSWER: 17"))])

    monkeypatch.setattr("core.llm.complete", fake_complete)
    asyncio.run(runner._emergency_finalize_after_forced_termination())

    assert "tools" not in captured
    assert all(message["role"] != "tool" for message in captured["messages"])
    assert all("tool_calls" not in message for message in captured["messages"])
    assert any("Previously gathered tool result" in message["content"] for message in captured["messages"])
    assert runner._result == "17"


def test_emergency_finalization_rejects_explanatory_text(tmp_path, monkeypatch):
    runner = Runner(
        pool_dir=_pool(tmp_path), chairman_name="chairman",
        output_contract=GAIA_OUTPUT_CONTRACT,
    )
    runner._forced_termination = "timeout"
    chairman = SimpleNamespace(
        config=SimpleNamespace(name="chairman", model="test", temperature=0, max_tokens=32),
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "task"}],
    )
    runner.agents["chairman"] = chairman
    answer_agent = SimpleNamespace(
        config=SimpleNamespace(name="answer_agent", model="test", temperature=0, max_tokens=32),
        messages=[],
        build_system_prompt=lambda: "AnswerAgent system",
    )
    runner.agents["answer_agent"] = answer_agent

    async def fake_complete(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="Based on the evidence, the answer is 17.",
        ))])

    monkeypatch.setattr("core.llm.complete", fake_complete)
    asyncio.run(runner._emergency_finalize_after_forced_termination())

    assert runner._result is None
    assert runner._final_output_locked is False
    assert runner._finalization_mode == "none"


def test_non_gaia_emergency_finalization_accepts_tagged_multiline_output(tmp_path, monkeypatch):
    runner = Runner(
        pool_dir=_pool(tmp_path), chairman_name="chairman",
        output_contract=output_contract_from_name("deep_research_report"),
    )
    runner._forced_termination = "timeout"
    runner._task = "Write a report"
    runner.agents["chairman"] = SimpleNamespace(
        config=SimpleNamespace(name="chairman", model="test", temperature=0, max_tokens=32),
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "task"}],
    )
    runner.agents["answer_agent"] = SimpleNamespace(
        config=SimpleNamespace(name="answer_agent", model="test", temperature=0, max_tokens=32),
        messages=[],
        build_system_prompt=lambda: "Research AnswerAgent system",
    )

    async def fake_complete(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="FINAL OUTPUT:\n# Report\n\nConclusion.\nConfidence: 90%",
        ))])

    monkeypatch.setattr("core.llm.complete", fake_complete)
    asyncio.run(runner._emergency_finalize_after_forced_termination())

    assert runner._result == "# Report\n\nConclusion.\nConfidence: 90%"
    assert runner._finalization_mode == "emergency"


def test_global_answer_agent_is_not_recruitable_but_gates_normal_submission(tmp_path):
    runner = Runner(
        pool_dir=_pool(tmp_path, with_answer_agent=True),
        chairman_name="chairman",
        allowed_agent_ids={"worker_a"},
        output_contract=GAIA_OUTPUT_CONTRACT,
    )
    assert [row["name"] for row in runner.list_pool_agents()] == ["worker_a"]
    assert "AnswerAgent has not returned" in runner.validate_answer_agent_submission("17")
    runner.message_store.register("chairman")
    runner.message_store.send(
        sender="answer_agent", receiver="chairman", content="FINAL ANSWER: 17",
    )
    assert runner.validate_answer_agent_submission("17") is None
    assert "must equal AnswerAgent" in runner.validate_answer_agent_submission("FINAL ANSWER: 17")


def test_global_answer_agent_receives_a_task_length_idle_budget(tmp_path, monkeypatch):
    runner = Runner(
        pool_dir=_pool(tmp_path, with_answer_agent=True),
        chairman_name="chairman",
        max_seconds=900,
        idle_timeout=120,
        allowed_agent_ids={"worker_a"},
        output_contract=GAIA_OUTPUT_CONTRACT,
    )
    answer_agent = runner.load_agent_from_pool("answer_agent")
    captured = {}

    async def fake_run_loop(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(answer_agent, "run_loop", fake_run_loop)
    async def exercise():
        runner.start_agent(answer_agent, global_service=True)
        await asyncio.sleep(0)

    asyncio.run(exercise())
    assert captured["max_idle_rounds"] == 9


def test_generic_contract_neither_requires_answer_agent_nor_emergency_text(tmp_path, monkeypatch):
    runner = Runner(pool_dir=_pool(tmp_path), chairman_name="chairman")
    runner._forced_termination = "timeout"
    chairman = SimpleNamespace(
        config=SimpleNamespace(name="chairman", model="test", temperature=0, max_tokens=32),
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "task"}],
    )
    runner.agents["chairman"] = chairman

    async def fail_if_called(**kwargs):
        raise AssertionError("generic benchmarks must not emergency-finalize text")

    monkeypatch.setattr("core.llm.complete", fail_if_called)
    asyncio.run(runner._emergency_finalize_after_forced_termination())

    assert runner.validate_answer_agent_submission("a complete report") is None
    assert runner._result is None
