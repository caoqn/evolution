import sys
from pathlib import Path

from core.execution_policy import ExecutionPolicy, FallbackPolicy
from core.output_contract import output_contract_from_name
from core.runner import Runner


ROOT = Path(__file__).resolve().parents[1]
MIX_DIR = ROOT / "benchmarks" / "MIX-COOP"
if str(MIX_DIR) not in sys.path:
    sys.path.insert(0, str(MIX_DIR))

from mix_coop_manifest import MixCoopManifest  # noqa: E402
from task_wrapper import MixedTaskWrapper  # noqa: E402


MANIFEST = MIX_DIR / "manifests" / "mix_coop_smoke_v1.json"


def test_manifest_tools_match_native_execution_policies():
    manifest = MixCoopManifest.load(MANIFEST)
    wrapper = MixedTaskWrapper(manifest)

    for benchmark, binding in manifest.benchmark_bindings.items():
        adapter = wrapper.resolve(next(
            task for task in manifest.tasks
            if task.source.benchmark == benchmark
        )).adapter
        assert set(binding["native_allowed_tools"]) == set(
            adapter.execution_policy({}).allowed_tools
        )


def test_selector_evidence_excludes_explicit_routing_metadata():
    manifest = MixCoopManifest.load(MANIFEST)
    wrapper = MixedTaskWrapper(manifest)

    for task in manifest.tasks_for_phase("evolve"):
        resolved = wrapper.resolve(task)
        evidence = resolved.adapter.build_team_selection_task(resolved.item, "PRIVATE")
        assert "PRIVATE" not in evidence
        assert task.mix_task_id not in evidence
        assert task.source.task_id not in evidence


def test_runner_injects_policy_without_explicit_benchmark_contract_name():
    policy = ExecutionPolicy(
        name="private_native_name",
        version="1",
        environment_mode="workspace",
        allowed_tools=("read_file",),
        chairman_instructions=("Inspect the supplied evidence.",),
    ).bind()
    contract = output_contract_from_name("locobench_solution_summary")
    runner = Runner(
        ROOT / "agents" / "pool_MIX_COOP",
        "planner",
        output_contract=contract,
        execution_policy=policy,
    )

    context = runner._build_chairman_context()
    assert "Inspect the supplied evidence." in context
    assert "private_native_name" not in context
    assert "locobench_solution_summary" not in context
    assert "solution_files_plus_completion_summary" in context


def test_fallback_is_not_score_or_evolution_eligible():
    policy = ExecutionPolicy(
        name="stateful_service_operations",
        version="1",
        environment_mode="native_service",
        allowed_tools=("service_tool", "bash"),
        fallback_policy=FallbackPolicy(
            trigger="fatal_service_failure",
            mode="local_fallback",
        ),
    )
    bound = policy.bind(
        execution_mode="local_fallback",
        available_tools=("bash",),
        fallback_used=True,
    )

    assert bound.official_score_eligible is False
    assert bound.evolution_eligible is False
