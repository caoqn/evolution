import copy
import json
import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIX_DIR = ROOT / "benchmarks" / "MIX-COOP"
MANIFEST_PATH = MIX_DIR / "manifests" / "mix_coop_smoke_v1.json"
PILOT_MANIFEST_PATH = MIX_DIR / "manifests" / "mix_coop_gaia_locobench_v1.json"

if str(MIX_DIR) not in sys.path:
    sys.path.insert(0, str(MIX_DIR))

from mix_coop_manifest import (  # noqa: E402
    ManifestValidationError,
    MixCoopManifest,
)
from task_wrapper import MixedTaskWrapper  # noqa: E402
from benchmarks.adapter import _apply_native_tool_scope  # noqa: E402


def test_smoke_manifest_is_balanced_and_round_robin():
    manifest = MixCoopManifest.load(MANIFEST_PATH)

    for phase in ("evolve", "test"):
        tasks = manifest.tasks_for_phase(phase)
        assert [task.order for task in tasks] == list(range(4))
        assert {task.source.benchmark for task in tasks} == {
            "locobench", "locabench", "gaia", "beyondswe",
        }


def test_smoke_manifest_native_references_resolve():
    manifest = MixCoopManifest.load(MANIFEST_PATH)
    wrapper = MixedTaskWrapper(manifest)

    for task in manifest.tasks:
        resolved = wrapper.resolve(task)
        assert resolved.adapter.get_item_id(resolved.item) == task.source.task_id


def test_each_native_adapter_keeps_its_output_contract():
    manifest = MixCoopManifest.load(MANIFEST_PATH)
    wrapper = MixedTaskWrapper(manifest)
    expected = {
        "gaia": "gaia",
        "locobench": "locobench_solution_summary",
        "locabench": "locabench_completion_summary",
        "beyondswe": "beyondswe_patch_summary",
    }

    for task in manifest.tasks_for_phase("evolve"):
        resolved = wrapper.resolve(task)
        assert resolved.adapter.output_contract().name == expected[task.source.benchmark]


def test_selector_visibility_rejects_benchmark_leakage():
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["isolation_policy"]["selector_visible_fields"].append("source_benchmark")

    manifest = MixCoopManifest.from_dict(payload)
    with pytest.raises(ManifestValidationError, match="leaks routing/analysis metadata"):
        manifest.validate()


def test_native_task_id_detects_dataset_drift():
    manifest = MixCoopManifest.load(MANIFEST_PATH)
    changed = copy.deepcopy(manifest)
    task = changed.tasks[0]
    object.__setattr__(task.source, "task_id", "changed-native-id")
    wrapper = MixedTaskWrapper(changed)

    with pytest.raises(ValueError, match="does not match adapter item"):
        wrapper.resolve(task)


def test_native_tool_scope_is_applied_only_to_disposable_copy(tmp_path):
    source = ROOT / "agents" / "pool_MIX_COOP"
    case_copy = tmp_path / "case-team"
    shutil.copytree(source, case_copy)

    _apply_native_tool_scope(case_copy, {"read_file", "bash"})

    import yaml

    source_config = yaml.safe_load(
        (source / "planner" / "config.yaml").read_text(encoding="utf-8")
    )
    case_config = yaml.safe_load(
        (case_copy / "planner" / "config.yaml").read_text(encoding="utf-8")
    )
    assert set(source_config["tools"]) > set(case_config["tools"])
    assert set(case_config["tools"]) <= {"read_file", "bash"}


def test_gaia_locobench_pilot_counts_and_order():
    manifest = MixCoopManifest.load(PILOT_MANIFEST_PATH)
    assert manifest.active_benchmarks == {"gaia", "locobench"}

    expected_counts = {"evolve": 10, "test": 40}
    for phase, per_benchmark in expected_counts.items():
        tasks = manifest.tasks_for_phase(phase)
        assert len(tasks) == per_benchmark * 2
        assert [task.order for task in tasks] == list(range(len(tasks)))
        assert [task.source.benchmark for task in tasks] == [
            benchmark
            for _ in range(per_benchmark)
            for benchmark in ("gaia", "locobench")
        ]

    loco_evolve = [
        task for task in manifest.tasks_for_phase("evolve")
        if task.source.benchmark == "locobench"
    ]
    assert sum(task.source.adapter_args["category"] == "feature_implementation" for task in loco_evolve) == 5
    assert sum(task.source.adapter_args["category"] == "cross_file_refactoring" for task in loco_evolve) == 5
