from argparse import Namespace

import pytest

from benchmarks.adapter_gaia import GAIAAdapter
from core.api_circuit_breaker import APICircuitOpenError, SharedAPICircuitBreaker
from core.api_resilience import is_infrastructure_error
from core.result_manifest import ResultManifest


def test_prepare_items_preserves_original_indices():
    items = [{"task_id": "a"}, {"task_id": "b"}, {"task_id": "c"}]
    selected = GAIAAdapter().prepare_items(items, Namespace(cases="2,0"))
    assert [item["_source_index"] for item in selected] == [2, 0]


def test_manifest_replaces_pending_but_not_usable_result(tmp_path):
    manifest = ResultManifest(tmp_path / "gaia.json", "gaia", "test_100")
    manifest.record_attempt(
        {"task_id": "task", "extracted_answer": "[no output — Chairman did not call set_final_output]"},
        run_id="first", original_index=65,
    )
    manifest.record_attempt(
        {"task_id": "task", "is_correct": True, "score": 1.0, "extracted_answer": "2"},
        run_id="retry", original_index=65,
    )
    manifest.record_attempt(
        {"task_id": "task", "infrastructure_failure": True, "run_error": "API_FAILURE: 502"},
        run_id="later_outage", original_index=65,
    )
    effective = manifest.effective_records()
    assert len(effective) == 1
    assert effective[0]["run_id"] == "retry"
    assert manifest.summary()["correct"] == 1


def test_shared_circuit_opens_after_terminal_failures(tmp_path):
    breaker = SharedAPICircuitBreaker(
        tmp_path / "circuit.json", threshold=2, cooldown_seconds=60,
    )
    breaker.record_infrastructure_failure(ConnectionError("gateway unavailable"))
    breaker.record_infrastructure_failure(ConnectionError("gateway unavailable"))
    with pytest.raises(APICircuitOpenError):
        breaker.check()
    breaker.record_success()
    breaker.check()


class _RunWithResults:
    def __init__(self, records):
        self.records = records

    def load_case_results(self):
        return self.records


def test_evolve_resume_uses_only_continuous_completed_prefix():
    adapter = GAIAAdapter()
    items = [
        {"task_id": "a", "_source_index": 5},
        {"task_id": "b", "_source_index": 9},
        {"task_id": "c", "_source_index": 12},
    ]
    run = _RunWithResults([
        {"idx": 5, "task_id": "a"},
        {"idx": 9, "task_id": "b", "infrastructure_failure": True},
    ])

    completed, next_position = adapter._resume_evolution_prefix(items, run)

    assert [record["idx"] for record in completed] == [5]
    assert next_position == 1


def test_evolve_resume_rejects_records_after_an_incomplete_case():
    adapter = GAIAAdapter()
    items = [
        {"task_id": "a", "_source_index": 0},
        {"task_id": "b", "_source_index": 1},
        {"task_id": "c", "_source_index": 2},
    ]
    run = _RunWithResults([
        {"idx": 0, "task_id": "a"},
        {"idx": 1, "task_id": "b", "infrastructure_failure": True},
        {"idx": 2, "task_id": "c"},
    ])

    with pytest.raises(RuntimeError, match="Cannot safely resume"):
        adapter._resume_evolution_prefix(items, run)


def test_plain_gateway_error_is_an_infrastructure_failure():
    assert is_infrastructure_error(RuntimeError("request failed: upstream timeout"))
    assert not is_infrastructure_error(RuntimeError("invalid API key"))
