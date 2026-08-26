import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIX_DIR = ROOT / "benchmarks" / "MIX-COOP"
if str(MIX_DIR) not in sys.path:
    sys.path.insert(0, str(MIX_DIR))

from mix_coop_manifest import MixCoopManifest  # noqa: E402
from randomize_manifest import materialize_payload  # noqa: E402


MANIFEST_PATH = MIX_DIR / "manifests" / "mix_coop_smoke_v1.json"


def _sequence(payload, phase):
    return [
        row["source"]["benchmark"]
        for row in payload["tasks"]
        if row["phase"] == phase and row.get("enabled", True)
    ]


def test_materialized_order_is_balanced_and_reproducible():
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    first = materialize_payload(payload, seed=7)
    second = materialize_payload(payload, seed=7)

    assert first == second
    assert first["ordering"]["materialized_order"] is True
    assert first["ordering"]["shuffle_scope"] == "evolve"
    assert len(set(_sequence(first, "evolve"))) == 4
    assert _sequence(first, "test") == _sequence(payload, "test")
    assert first["ordering"]["order_hash"]


def test_different_seeds_change_evolve_order_without_changing_tasks():
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    first = materialize_payload(payload, seed=7)
    second = materialize_payload(payload, seed=8)

    assert _sequence(first, "evolve") != _sequence(second, "evolve")
    assert {
        row["mix_task_id"] for row in first["tasks"]
    } == {
        row["mix_task_id"] for row in second["tasks"]
    }
    assert first["ordering"]["order_hash"] != second["ordering"]["order_hash"]


def test_materialized_manifest_loads_with_native_validation(tmp_path):
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    output = tmp_path / "seed.json"
    output.write_text(
        json.dumps(materialize_payload(payload, seed=19)), encoding="utf-8"
    )
    manifest = MixCoopManifest.load(output)
    assert [task.order for task in manifest.tasks_for_phase("evolve")] == [0, 1, 2, 3]


def test_materialized_order_hash_rejects_manual_reordering(tmp_path):
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    materialized = materialize_payload(payload, seed=19)
    materialized["tasks"][0]["order"], materialized["tasks"][1]["order"] = (1, 0)
    output = tmp_path / "tampered.json"
    output.write_text(json.dumps(materialized), encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="order_hash"):
        MixCoopManifest.load(output)
