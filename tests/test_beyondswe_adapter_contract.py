import pytest

from benchmarks.adapter_beyondswe import (
    BeyondSWEAdapter,
    BeyondSWEInfrastructureError,
    _completion_evidence,
    _make_error_result,
    _normalize_pre_commands,
    _patch_touches_tests,
    _reset_repo_for_eval,
    _run_pre_commands,
)


def _diff(path: str) -> str:
    return f"diff --git a/{path} b/{path}\n"


def test_test_patch_detection_uses_path_boundaries():
    assert _patch_touches_tests(_diff("tests/test_widget.py"))
    assert _patch_touches_tests(_diff("pkg/test_widget.py"))
    assert _patch_touches_tests(_diff("pkg/widget_test.py"))
    assert _patch_touches_tests(_diff("pkg/conftest.py"))
    assert not _patch_touches_tests(_diff("pkg/testing_utils.py"))
    assert not _patch_touches_tests(_diff("pkg/testimonials.py"))


def test_completion_evidence_preserves_native_evaluator_counts():
    evidence = _completion_evidence({
        "patch": "diff --git a/a.py b/a.py\n",
        "patch_reapplied": True,
        "tests_run": 5,
        "f2p_pass": 2,
        "f2p_total": 2,
        "p2p_pass": 2,
        "p2p_total": 3,
        "p2p_regressed_names": ["tests/test_a.py::test_old"],
        "parse_source": "junit_xml",
        "regressions": 1,
    })

    assert evidence["patch_present"] is True
    assert evidence["patch_reapplied"] is True
    assert evidence["tests_run"] == 5
    assert evidence["fail_to_pass"] == {"passed": 2, "total": 2}
    assert evidence["pass_to_pass"] == {"passed": 2, "total": 3}
    assert evidence["regressions"] == 1


def test_error_evidence_does_not_claim_tests_or_regressions_ran():
    result = _make_error_result("", ["new"], ["old"], "patch replay failed")
    evidence = _completion_evidence(result)

    assert evidence["patch_present"] is False
    assert evidence["patch_reapplied"] is False
    assert evidence["tests_run"] == 0
    assert evidence["regressions"] is None


class _FailedPreCommandContainer:
    def exec_run(self, *args, **kwargs):
        return 17, (b"", b"native preparation failed")


def test_pre_command_failure_is_infrastructure_failure():
    with pytest.raises(BeyondSWEInfrastructureError, match="pre_commands.*17"):
        _run_pre_commands(
            _FailedPreCommandContainer(),
            {"pre_commands": "prepare-native-repository"},
            "/repo",
        )


def test_missing_evaluation_container_is_infrastructure_failure():
    adapter = BeyondSWEAdapter()
    with pytest.raises(BeyondSWEInfrastructureError, match="No Docker container"):
        adapter.evaluate({}, "", None)


def test_infrastructure_error_is_a_runtime_error_for_runner_propagation():
    assert issubclass(BeyondSWEInfrastructureError, RuntimeError)


def test_execution_policy_matches_actual_beyondswe_agents_and_tools():
    policy = BeyondSWEAdapter().execution_policy({"_task_type": "CrossRepo"})

    assert "bash" not in policy.allowed_tools
    assert policy.allowed_tools == (
        "read_file", "docker_bash", "docker_str_replace_editor",
    )
    assert set(policy.role_instructions) == {
        "repo_analyst",
        "developer",
        "reviewer",
        "test_engineer",
        "integration_verifier",
        "dependency_specialist",
        "domain_debugger",
    }


def test_dataset_pre_commands_are_idempotent_for_repeated_evaluation_setup():
    assert _normalize_pre_commands("git gc --aggressive\\n") == "git gc --aggressive"
    assert _normalize_pre_commands("printf '\\\\n'\\n") == "printf '\\\\n'"
    assert _normalize_pre_commands(
        "git checkout deadbeef && git checkout -b realswe\\n"
    ) == (
        "git checkout deadbeef && "
        "(git branch -D realswe 2>/dev/null || true) && "
        "git checkout -b realswe"
    )
    assert _normalize_pre_commands(
        "git checkout -b experiment"
    ) == "git checkout -b experiment"


class _EvalResetContainer:
    def __init__(self):
        self.commands = []

    def exec_run(self, command, **kwargs):
        self.commands.append(command)
        return 0, (b"", b"")


def test_evaluation_reset_detaches_before_recreating_realswe_branch():
    container = _EvalResetContainer()

    assert _reset_repo_for_eval(
        container,
        "/repo",
        {"pre_commands": "git checkout -b realswe"},
    )

    reset_command = container.commands[0][2]
    assert "git checkout --detach HEAD" in reset_command
    assert container.commands[1] == [
        "bash", "-c",
        "(git branch -D realswe 2>/dev/null || true) && git checkout -b realswe",
    ]
