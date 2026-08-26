"""Base adapter class for benchmark integration."""

import os
import sys
import json
import atexit
import asyncio
import inspect
import threading
import argparse
import subprocess
import concurrent.futures
import time
import yaml
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from core.execution_policy import BoundExecutionPolicy, ExecutionPolicy
from core.output_contract import output_contract_from_settings

_print_lock = threading.Lock()


def _handoff_rule_context(rule: Any) -> dict[str, Any]:
    """Expose the active contract without replaying its full audit history."""
    payload = rule.to_dict()
    payload.pop("version_history", None)
    return payload


async def _load_team_selection(
    runtime_team_dir: Path,
    pool: Any,
    task: str,
    api_tracker: Any | None = None,
):
    """Select a template family before constructing the execution Runner."""
    from core.template_registry import TemplateRegistry
    from core.team_selector import (
        LLMTeamSelector,
        PublicAgentProfile,
        TeamSelectionContext,
    )

    registry_path = runtime_team_dir / "templates.json"
    if registry_path.exists():
        registry = TemplateRegistry.load(registry_path)
    else:
        registry = TemplateRegistry()

    def agent_catalog_provider():
        return [
            PublicAgentProfile(
                agent_id=agent.config.name,
                role=agent.config.role,
                description=agent.config.description,
            )
            for agent in pool.agents.values()
            if agent.config.name not in {pool.chairman_name, "answer_agent"}
        ]

    chairman = pool.agents.get(pool.chairman_name)
    chairman_model = chairman.config.model if chairman is not None else ""
    model = (
        pool.settings.get("team_selection_model")
        or os.environ.get("TEAM_SELECTOR_MODEL")
        or chairman_model
        or "claude-sonnet-4-6"
    )
    selector = LLMTeamSelector(
        model=model,
        registry=registry,
        agent_catalog_provider=agent_catalog_provider,
        api_tracker=api_tracker,
    )
    selection = await selector.select(
        TeamSelectionContext(
            raw_task=task,
            chairman_id=pool.chairman_name,
            template_catalog=registry.catalog(),
        )
    )
    return selection, registry


async def _reflect_team_template(
    *,
    runtime_team_dir: Path,
    pool: Any,
    registry: Any,
    selection: Any,
    task_id: str,
    task: str,
    result: Any,
    eval_result: Any,
    error_msg: str,
    api_tracker: Any | None = None,
):
    """Reflect once after evaluation and persist the current template lineage."""
    from core.team_reflector import LLMTeamReflector, TeamReflectionContext
    from core.team_selector import PublicAgentProfile

    global_agents = [
        PublicAgentProfile(
            agent_id=agent.config.name,
            role=agent.config.role,
            description=agent.config.description,
        )
        for agent in pool.agents.values()
        if agent.config.name not in {pool.chairman_name, "answer_agent"}
    ]
    chairman = pool.agents.get(pool.chairman_name)
    model = (
        pool.settings.get("team_reflection_model")
        or os.environ.get("TEAM_REFLECTION_MODEL")
        or (chairman.config.model if chairman is not None else "")
        or "claude-sonnet-4-6"
    )
    reflector = LLMTeamReflector(model=model, api_tracker=api_tracker)
    current = registry.get_current(selection.family_id)
    failures = [error_msg] if error_msg else []
    if not eval_result.success:
        failures.append(eval_result.summary or "evaluation unsuccessful")
    historical_task_summaries = []
    if current is not None:
        successful = set(current.successful_task_ids)
        failed = set(current.failed_task_ids)
        for historical_task_id in current.evidence_task_ids:
            historical_task_summaries.append({
                "task_id": historical_task_id,
                "outcome": (
                    "success" if historical_task_id in successful
                    else "failure" if historical_task_id in failed
                    else "observed"
                ),
                "actual_agent_ids": list(
                    current.task_member_evidence[historical_task_id]
                )
                if historical_task_id in current.task_member_evidence
                else [],
                "member_evidence_available": (
                    historical_task_id in current.task_member_evidence
                ),
            })
        for snapshot in current.version_history:
            historical_task_summaries.append({
                "task_id": snapshot.task_id,
                "outcome": "historical_template_version",
                "template_version": snapshot.version,
                "members": [member.agent_id for member in snapshot.members],
                "reason": snapshot.reason,
            })
    context = TeamReflectionContext(
        task_id=task_id,
        raw_task=task,
        chairman_id=pool.chairman_name,
        family_id=selection.family_id,
        current_template=current,
        allowed_agent_ids=list(selection.allowed_agent_ids),
        actual_agent_ids=[
            agent_id for agent_id in result.metadata.get("agents_used", [])
            if agent_id not in {
                pool.chairman_name,
                *result.metadata.get("global_service_agent_ids", []),
            }
        ],
        execution_summary={
            "output": (result.output or "")[:4000],
            "cost_seconds": result.cost_seconds,
            "forced_termination": result.metadata.get("forced_termination"),
        },
        evaluation={
            "success": eval_result.success,
            "score": eval_result.score,
            "summary": eval_result.summary,
            "details": eval_result.details,
        },
        failures=failures,
        global_agents=global_agents,
        historical_task_summaries=historical_task_summaries[-40:],
    )
    decision = await reflector.reflect(context)
    known_agent_ids = {pool.chairman_name, *(pool.agents.keys())}
    registry.apply_reflection(
        decision,
        task_id=task_id,
        known_agent_ids=known_agent_ids,
        task_success=eval_result.success,
        actual_agent_ids=[
            agent_id for agent_id in result.metadata.get("agents_used", [])
            if agent_id not in {
                pool.chairman_name,
                *result.metadata.get("global_service_agent_ids", []),
            }
        ],
    )
    if decision.action != "no_update":
        registry.save(runtime_team_dir / "templates.json")
    return decision


async def _reflect_handoff_rules(
    *,
    runtime_team_dir: Path,
    pool: Any,
    handoff_registry: Any,
    selection: Any,
    task_id: str,
    task: str,
    result: Any,
    eval_result: Any,
    error_msg: str,
    api_tracker: Any | None = None,
):
    """Reflect one family-scoped handoff contract after task execution."""
    from core.handoff_reflector import (
        HandoffReflectionContext,
        LLMHandoffReflector,
    )

    family_id = selection.family_id
    handoff_family_id = handoff_registry.handoff_family_for(family_id)
    if not handoff_family_id:
        handoff_family_id = f"hf_{family_id}"
        handoff_registry.bind_team_family(family_id, handoff_family_id)
    current_rules = handoff_registry.rules_for_team_family(family_id)
    historical_rule_summaries = []
    for rule in current_rules:
        historical_rule_summaries.append({
            "handoff_rule_id": rule.rule_id,
            "from_agent": rule.from_agent,
            "to_agent": rule.to_agent,
            "version": rule.version,
            "evidence_task_ids": list(rule.evidence_task_ids),
            "status": rule.status,
        })
    chairman = pool.agents.get(pool.chairman_name)
    model = (
        pool.settings.get("team_reflection_model")
        or os.environ.get("TEAM_REFLECTION_MODEL")
        or (chairman.config.model if chairman is not None else "")
        or "claude-sonnet-4-6"
    )
    reflector = LLMHandoffReflector(model=model, api_tracker=api_tracker)
    decision = await reflector.reflect(HandoffReflectionContext(
        task_id=task_id,
        team_family_id=family_id,
        handoff_family_id=handoff_family_id,
        task=task,
        actual_agent_ids=[
            agent_id for agent_id in result.metadata.get("agents_used", [])
            if agent_id not in result.metadata.get("global_service_agent_ids", [])
        ],
        current_rules=[_handoff_rule_context(rule) for rule in current_rules],
        execution_summary={
            "output": (result.output or "")[:3000],
            "forced_termination": result.metadata.get("forced_termination"),
        },
        evaluation={
            "success": eval_result.success,
            "score": eval_result.score,
            "summary": eval_result.summary,
        },
        failures=[item for item in [error_msg, eval_result.summary] if item],
        handoff_trace=list(result.metadata.get("handoff_trace", [])),
        historical_rule_summaries=historical_rule_summaries,
    ))
    handoff_registry.apply_decision(
        decision,
        task_id=task_id,
        known_agent_ids=set(pool.agents.keys()),
        team_family_id=family_id,
    )
    if decision.action != "no_update":
        handoff_registry.save(runtime_team_dir / "handoff_rules.json")
    return decision


_CONTAINER_PREFIXES = (
    "meta-team-swe-",       # SWE-bench
    "mt-pro-",              # SWE-bench Pro
    "mt-nl2rb-",            # NL2RepoBench (agent)
    "nl2rb-eval-",          # NL2RepoBench (eval)
    "mt-bswe-",             # BeyondSWE
    "sa-baseline-",         # Single-Agent Baseline
)

_owned_containers: dict[str, object] = {}   # container_name → container obj
_owned_lock = threading.Lock()


def register_container(name: str, container) -> None:
    with _owned_lock:
        _owned_containers[name] = container


def unregister_container(name: str) -> None:
    with _owned_lock:
        _owned_containers.pop(name, None)


def _cleanup_owned_containers() -> None:
    with _owned_lock:
        to_clean = list(_owned_containers.items())
    if not to_clean:
        return
    for name, ctr in to_clean:
        try:
            ctr.stop(timeout=5)
        except Exception:
            pass
        try:
            ctr.remove(force=True)
        except Exception:
            pass
    for name, _ in to_clean:
        try:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass


def _apply_native_tool_scope(team_dir: Path, allowed_tools: set[str]) -> None:
    """Filter tools in a disposable per-case team copy.

    The shared pool contains the union of cross-benchmark capabilities. Native
    isolation is enforced after copying that pool into the case workspace, so
    a GAIA case cannot accidentally expose LOCA MCP or Docker tools. The
    persisted source team and its evolution lineage are not modified.
    """
    for config_path in sorted(team_dir.rglob("config.yaml")):
        if config_path.parent == team_dir:
            continue
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            declared = payload.get("tools", [])
            if not isinstance(declared, list):
                declared = []
            payload["tools"] = [tool for tool in declared if tool in allowed_tools]
            config_path.write_text(
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(
                f"failed to apply native tool scope to {config_path}: {exc}"
            ) from exc


def cleanup_stale_containers(quiet: bool = False) -> int:
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return 0

        names = [n.strip() for n in result.stdout.strip().split("\n") if n.strip()]
        to_remove = [n for n in names
                     if any(n.startswith(p) for p in _CONTAINER_PREFIXES)]

        if not to_remove:
            return 0

        if not quiet:
            print(f"\n[cleanup] Found {len(to_remove)} stale meta-team container(s), "
                  f"removing...")

        removed = 0
        for name in to_remove:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", name],
                    capture_output=True, timeout=30,
                )
                removed += 1
                if not quiet:
                    print(f"  removed: {name}")
            except Exception:
                if not quiet:
                    print(f"  [warn] failed to remove: {name}")

        if not quiet and removed:
            print(f"[cleanup] Removed {removed} container(s).")
        return removed

    except FileNotFoundError:
        return 0
    except Exception:
        return 0


atexit.register(_cleanup_owned_containers)

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    success: bool
    score: float
    summary: str = ""
    details: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class EnvContext:
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------

class BenchmarkAdapter(ABC):

    benchmark_name: str = ""            # "swe_bench" / "swe_bench_pro" / "nl2repobench" / "beyondswe"
    default_team: str = ""              # "pool_SWE_Pro"
    default_timeout: float = 300.0
    default_evolve_timeout: float = 600.0
    # When unset, the effective task budget equals the wall-clock cap for
    # backward compatibility. Benchmarks with unreliable upstream providers
    # can set a shorter useful-work budget plus a recovery allowance.
    default_effective_timeout: float | None = None
    default_max_cost: float = 0.0
    default_split: str = ""
    results_subdir: str = ""
    split_choices: list[str] | None = None

    def output_contract(self):
        """Return this benchmark's submission semantics for the Runner."""
        return output_contract_from_settings({}, self.benchmark_name)

    def execution_policy(self, item: dict) -> ExecutionPolicy:
        """Return the adapter-owned execution rules for one native task."""
        return ExecutionPolicy(
            name=self.benchmark_name or "generic",
            version="1",
            environment_mode="workspace",
            allowed_tools=(),
        )

    def bind_execution_policy(
        self,
        policy: ExecutionPolicy,
        item: dict,
        env_ctx: EnvContext,
        session: Any,
    ) -> BoundExecutionPolicy:
        """Bind runtime paths and environment state after setup completes."""
        return policy.bind(workspace_paths={"workspace": str(session.workspace)})

    # ---------------------------------------------------------------------------

    @abstractmethod
    def load_dataset(self, args: argparse.Namespace) -> list[dict]:
        ...

    @abstractmethod
    def get_item_id(self, item: dict) -> str:
        ...

    @abstractmethod
    def build_task(self, item: dict, session: Any, workspace_files: list[str]) -> str:
        ...

    def build_team_selection_task(self, item: dict, full_task: str) -> str:
        """Return task evidence used only for family selection and reflection."""
        return full_task

    @abstractmethod
    def evaluate(self, item: dict, predicted: str, session: Any, **ctx) -> EvalResult:
        ...

    @abstractmethod
    def build_record(self, item: dict, idx: int, predicted: str,
                     eval_result: EvalResult, session: Any, error: str) -> dict:
        ...

    @abstractmethod
    def print_summary(self, records: list[dict]) -> None:
        ...

    # ---------------------------------------------------------------------------

    def setup_environment(self, item: dict, session: Any) -> EnvContext:
        return EnvContext()

    def teardown_environment(self, env_ctx: EnvContext) -> None:
        pass

    def post_process(self, item: dict, session: Any, env_ctx: EnvContext) -> None:
        pass

    def get_patch_for_snapshot(self, env_ctx: EnvContext) -> str:
        return ""

    def build_task_validator(self, item: dict, session: Any, **ctx):
        return None

    def add_extra_args(self, parser: argparse.ArgumentParser) -> None:
        pass

    def compute_summary(self, records: list[dict], args: argparse.Namespace) -> dict:
        total = len(records)
        scores = [r.get("score", 0.0) for r in records]
        avg = sum(scores) / total if total else 0.0
        correct = sum(1 for s in scores if s >= 1.0)

        timeout_count = 0
        error_count = 0
        for r in records:
            err = r.get("run_error") or r.get("error") or ""
            if err:
                error_count += 1
                if "TIMEOUT" in err.upper():
                    timeout_count += 1

        return {
            "total": total,
            "avg_score": round(avg, 4),
            "correct": correct,
            "timeout_count": timeout_count,
            "error_count": error_count,
        }

    def dry_run_print(self, items: list[dict], args: argparse.Namespace) -> None:
        print(f"{self.benchmark_name} {getattr(args, 'split', '')}: {len(items)} items\n")
        for i, item in enumerate(items):
            item_id = self.get_item_id(item)
            print(f"  [{i}] {item_id}")
            if i >= 19:
                remaining = len(items) - 20
                if remaining > 0:
                    print(f"  ... and {remaining} more")
                break

    def prepare_items(self, items: list[dict],
                      args: argparse.Namespace) -> list[dict]:
        # Preserve source indices after --cases filtering. Result manifests
        # must use benchmark indices, never this run's local case position.
        items = [
            {**item, "_source_index": item.get("_source_index", original_index)}
            for original_index, item in enumerate(items)
        ]
        if args.cases is not None:
            indices = [int(x.strip()) for x in args.cases.split(",")]
            items = [items[i] for i in indices if 0 <= i < len(items)]
        return items

    # ---------------------------------------------------------------------------

    @property
    def results_dir(self) -> Path:
        return BASE_DIR / "benchmarks" / self.results_subdir

    async def run_single_case(
        self,
        item: dict,
        idx: int,
        args: argparse.Namespace,
        run_mgr: Any,
        team_name: str,
        evolve: bool,
        timeout_secs: float,
        effective_timeout_secs: float,
        result_manifest: Any | None = None,
        run_id: str = "",
        task_id_override: str | None = None,
        native_allowed_tools: set[str] | None = None,
        allow_pool_answer_protocol_override: bool = True,
    ) -> dict:
        from main import (
            _save_trace, copy_team_to_session,
        )
        from core.session import Session

        # MIX-COOP supplies a globally unique task id so shared template and
        # handoff evidence cannot collide when two native benchmarks use the
        # same source index or a recycled native identifier.
        task_id = task_id_override or self.get_item_id(item)
        with _print_lock:
            print(f"\n{'=' * 60}")
            print(f"[{idx}] {task_id}")
            print(f"{'=' * 60}")

        team_version, actual_source = run_mgr.get_latest_team_version()
        with _print_lock:
            print(f"  [RunManager] using team {team_version}")

        # Pool + Runner info + Session
        from main import load_pool
        pre_pool = load_pool(actual_source)
        team_info = {
            "team_name": team_name,
            "structure": "pool",
            "chairman": pre_pool.chairman_name,
            "total_agents": len(pre_pool.agents),
            "agents": list(pre_pool.agents.keys()),
        }

        case_dir = run_mgr.create_case_dir(idx, task_id)
        session = Session.create(
            task=task_id,
            team_info=team_info,
            session_dir=case_dir,
        )

        # Copy team/pool to session
        runtime_team_dir = copy_team_to_session(Path(session.dir), actual_source)
        execution_policy = self.execution_policy(item)
        if native_allowed_tools is not None:
            declared_tools = set(native_allowed_tools)
            policy_tools = set(execution_policy.allowed_tools)
            if declared_tools != policy_tools:
                raise ValueError(
                    f"native tool contract mismatch for {self.benchmark_name}: "
                    f"manifest={sorted(declared_tools)}, "
                    f"adapter_policy={sorted(policy_tools)}"
                )
            _apply_native_tool_scope(runtime_team_dir, policy_tools)

        predicted = ""
        error_msg = ""
        result = None
        env_ctx = EnvContext()
        eval_result = None
        task = ""
        team_selection_task = ""
        pool = None
        team_selection = None
        template_registry = None
        handoff_registry = None
        team_reflection_decision = None
        handoff_reflection_decision = None
        team_reflection_updated = False
        bound_execution_policy: BoundExecutionPolicy | None = None
        infrastructure_failure = False
        failure_stage = "setup"
        from core.api_resilience import APIReliabilityTracker
        api_tracker = APIReliabilityTracker()
        case_budget_started_at = 0.0

        try:
            env_ctx = self.setup_environment(item, session)
            if inspect.isawaitable(env_ctx):
                env_ctx = await env_ctx
            bound_execution_policy = self.bind_execution_policy(
                execution_policy, item, env_ctx, session,
            )
            if native_allowed_tools is not None:
                _apply_native_tool_scope(
                    runtime_team_dir, set(bound_execution_policy.available_tools),
                )

            failure_stage = "task_construction"
            workspace_files = [
                f.name for f in Path(session.workspace).iterdir()
                if not f.name.startswith(".")
            ]
            task = self.build_task(item, session, workspace_files)
            team_selection_task = self.build_team_selection_task(item, task)
            # Family selection receives only adapter-normalized collaboration
            # evidence. Native task text and benchmark routing remain private.
            case_budget_started_at = time.monotonic()
            failure_stage = "team_selection"

            from main import load_pool
            from core.runner import Runner
            pool = load_pool(runtime_team_dir)
            if pool.settings.get("team_selection_enabled", False):
                if timeout_secs > 0:
                    selection_remaining = timeout_secs - (
                        time.monotonic() - case_budget_started_at
                    )
                    if selection_remaining <= 0:
                        raise asyncio.TimeoutError(
                            "wall-clock recovery cap exhausted before team selection"
                        )
                    team_selection, template_registry = await asyncio.wait_for(
                        _load_team_selection(
                            runtime_team_dir, pool, team_selection_task, api_tracker=api_tracker,
                        ),
                        timeout=selection_remaining,
                    )
                else:
                    team_selection, template_registry = await _load_team_selection(
                        runtime_team_dir, pool, team_selection_task, api_tracker=api_tracker,
                    )
                from core.handoff_registry import HandoffRegistry
                handoff_registry = HandoffRegistry.load(
                    runtime_team_dir / "handoff_rules.json"
                )
                selected_family = template_registry.get_family(team_selection.family_id)
                if selected_family is not None:
                    handoff_registry.bind_team_family(
                        selected_family.family_id,
                        selected_family.handoff_family_id,
                    )
                    handoff_registry.save(runtime_team_dir / "handoff_rules.json")
                handoff_rules = handoff_registry.rules_for_team_family(
                    team_selection.family_id
                )
                team_selection.metadata.update({
                    "handoff_family_id": handoff_registry.handoff_family_for(
                        team_selection.family_id
                    ),
                    "handoff_rules": [
                        _handoff_rule_context(rule) for rule in handoff_rules
                    ],
                })
                session.team_info["team_selection"] = team_selection.to_dict()
                session._write_meta("running")
            runner = Runner(
                pool_dir=runtime_team_dir,
                chairman_name=pool.chairman_name,
                constitution=pool.constitution,
                tool_registry=pool.registry,
                max_seconds=float(effective_timeout_secs),
                wall_clock_seconds=float(timeout_secs),
                api_tracker=api_tracker,
                budget_started_at=time.time() - (
                    time.monotonic() - case_budget_started_at
                ),
                max_messages=int(pool.settings.get("max_messages", 200)),
                enable_reflection=evolve,
                max_cost_usd=float(getattr(args, "max_cost", 0) or 0),
                reflection_phase_timeout=float(pool.settings.get("reflection_phase_timeout", 0)),
                allowed_agent_ids=(
                    set(team_selection.allowed_agent_ids)
                    if team_selection is not None
                    else None
                ),
                team_selection_metadata=(
                    team_selection.to_dict() if team_selection is not None else {}
                ),
                output_contract=output_contract_from_settings(
                    pool.settings,
                    self.benchmark_name,
                    native_contract=self.output_contract(),
                    allow_pool_override=allow_pool_answer_protocol_override,
                ),
                execution_policy=bound_execution_policy,
            )
            failure_stage = "execution"

            if evolve:
                validator = self.build_task_validator(
                    item, session, **env_ctx.data)
                if validator:
                    runner.set_task_validator(validator)

                split = getattr(args, "split", "") or ""
                runner._evolution_description_key = f"{self.benchmark_name}_{split}" if split else self.benchmark_name

                def _freeze_eval_snapshot():
                    snapshot_path = Path(session.workspace) / "patch_pre_reflection.diff"
                    if snapshot_path.exists():
                        return
                    try:
                        patch = self.get_patch_for_snapshot(env_ctx)
                        if patch:
                            snapshot_path.write_text(patch, encoding="utf-8")
                    except Exception:
                        pass

                runner.set_pre_reflection_hook(_freeze_eval_snapshot)

            if timeout_secs <= 0:
                # ``--timeout 0`` disables only the wall-clock recovery cap.
                # The effective task budget, cost, and message limits remain
                # active unless their own values are explicitly set to zero.
                result = await runner.run(task, session=session)
            else:
                remaining_task_wall = timeout_secs - (
                    time.monotonic() - case_budget_started_at
                )
                if remaining_task_wall <= 0:
                    raise asyncio.TimeoutError(
                        "wall-clock recovery cap exhausted before runner start"
                    )
                if evolve:
                    reflection_phase_timeout = float(pool.settings.get("reflection_phase_timeout", 0)) or 300.0
                    outer_timeout = (
                        remaining_task_wall + reflection_phase_timeout * 3 + 120
                    )
                else:
                    outer_timeout = remaining_task_wall + 300
                result = await asyncio.wait_for(
                    runner.run(task, session=session),
                    timeout=outer_timeout,
                )

            predicted = result.output.strip() if result.output else ""

            # A terminal provider failure is infrastructure metadata, not a
            # model answer error. Keep a valid submitted answer if the rest of
            # the team recovered; otherwise mark this case for same-ID retry.
            api_summary = result.metadata.get("api_reliability", {})
            no_normal_finalization = (
                result.metadata.get("finalization_mode") == "none"
            )
            if (
                no_normal_finalization
                and api_summary.get("terminal_infrastructure_failure_count", 0) > 0
            ):
                error_msg = (
                    "API_FAILURE: no final answer after terminal provider "
                    f"failure(s): {api_summary.get('terminal_failures', [])}"
                )
                infrastructure_failure = True
            elif (
                no_normal_finalization
                and "wall_clock_recovery_timeout" in str(
                    result.metadata.get("forced_termination", "")
                )
                and api_summary.get("retryable_incident_count", 0) > 0
            ):
                error_msg = (
                    "API_FAILURE: wall-clock recovery cap reached after "
                    f"{api_summary.get('retryable_incident_count')} retryable "
                    "provider failure(s)"
                )
                infrastructure_failure = True

            failure_stage = "evaluation"
            self.post_process(item, session, env_ctx)
            bound_execution_policy = self.bind_execution_policy(
                execution_policy, item, env_ctx, session,
            )

            try:
                eval_result = self.evaluate(
                    item, predicted, session, **env_ctx.data)
            except Exception as e:
                infrastructure_failure = True
                error_msg = f"INFRASTRUCTURE_FAILURE: native evaluator failed: {e}"
                eval_result = EvalResult(
                    success=False, score=0.0,
                    summary=f"Evaluation error: {e}",
                    details=str(e)[:500],
                )

        except asyncio.TimeoutError:
            predicted = ""
            if api_tracker.summary().get("retryable_incident_count", 0) > 0:
                infrastructure_failure = True
                error_msg = (
                    "API_FAILURE: wall-clock recovery cap exhausted during "
                    "provider retries"
                )
            else:
                error_msg = f"TIMEOUT after {timeout_secs}s"
        except Exception as e:
            predicted = ""
            from core.api_resilience import is_infrastructure_error
            infrastructure_failure = (
                failure_stage in {"setup", "task_construction", "evaluation"}
                or is_infrastructure_error(e)
            )
            prefix = "INFRASTRUCTURE_FAILURE: " if infrastructure_failure else ""
            error_msg = prefix + str(e)
            import traceback
            with _print_lock:
                traceback.print_exc()
        finally:
            if eval_result is None:
                try:
                    eval_result = self.evaluate(
                        item, predicted, session, **env_ctx.data)
                except Exception as e:
                    infrastructure_failure = True
                    if not error_msg:
                        error_msg = (
                            "INFRASTRUCTURE_FAILURE: native evaluator failed: "
                            f"{e}"
                        )
                    eval_result = EvalResult(
                        success=False, score=0.0,
                        summary=f"Evaluation error: {e}",
                        details=str(e)[:500],
                    )

            try:
                self.teardown_environment(env_ctx)
            except Exception as e:
                with _print_lock:
                    print(f"[warn] failed to teardown environment: {e}")

        if (
            evolve
            and result is not None
            and eval_result is not None
            and not infrastructure_failure
            and pool is not None
            and team_selection is not None
            and template_registry is not None
            and (
                bound_execution_policy is None
                or bound_execution_policy.evolution_eligible
            )
        ):
            try:
                team_reflection_decision = await _reflect_team_template(
                    runtime_team_dir=runtime_team_dir,
                    pool=pool,
                    registry=template_registry,
                    selection=team_selection,
                    task_id=task_id,
                    task=team_selection_task,
                    result=result,
                    eval_result=eval_result,
                    error_msg=error_msg,
                    api_tracker=api_tracker,
                )
                team_reflection_updated = (
                    team_reflection_decision.action != "no_update"
                )
                session.team_info["team_reflection"] = (
                    team_reflection_decision.to_dict()
                )
                session._write_meta("running")
            except Exception as e:
                with _print_lock:
                    print(f"[warn] team template reflection failed: {e}")
                from core.api_resilience import is_infrastructure_error
                if is_infrastructure_error(e):
                    # Team-template reflection belongs to the evolution
                    # transaction. A terminal provider failure here must not
                    # let L1/L2 changes from this case advance the version
                    # chain as if the complete evolve step had succeeded.
                    error_msg = (
                        "API_FAILURE: team-template reflection failed after "
                        f"provider recovery attempts: {e}"
                    )
                    infrastructure_failure = True

            if handoff_registry is not None:
                try:
                    handoff_reflection_decision = await _reflect_handoff_rules(
                        runtime_team_dir=runtime_team_dir,
                        pool=pool,
                        handoff_registry=handoff_registry,
                        selection=team_selection,
                        task_id=task_id,
                        task=team_selection_task,
                        result=result,
                        eval_result=eval_result,
                        error_msg=error_msg,
                        api_tracker=api_tracker,
                    )
                    session.team_info["handoff_reflection"] = (
                        handoff_reflection_decision.to_dict()
                    )
                    session._write_meta("running")
                except Exception as e:
                    with _print_lock:
                        print(f"[warn] handoff reflection failed: {e}")

        # Close session
        try:
            status = "completed" if not error_msg else "failed"
            session.close(
                status,
                summary={"error": error_msg} if error_msg else None,
            )
            _save_trace(session.id, str(session.dir))
        except Exception as e:
            with _print_lock:
                print(f"[warn] failed to close session: {e}")

        has_reflection = (
            result is not None
            and getattr(result, "reflection_applied", False)
        )
        new_version = team_version
        if (
            evolve
            and not infrastructure_failure
            and (
                bound_execution_policy is None
                or bound_execution_policy.evolution_eligible
            )
        ):
            try:
                include_l3 = has_reflection or team_reflection_updated
                before_version = team_version
                new_version, modified = run_mgr.persist_team_version(
                    session_team_dir=runtime_team_dir,
                    include_l3=include_l3,
                )
                if modified:
                    run_mgr.write_changelog(
                        case_index=idx,
                        task_id=task_id,
                        from_version=before_version,
                        to_version=new_version,
                        modified_files=modified,
                        reflection_applied=include_l3,
                    )
            except Exception as e:
                with _print_lock:
                    print(f"[warn] failed to persist evolution: {e}")

        icon = "✅" if eval_result.success else (
            "🔶" if eval_result.score > 0 else "❌")
        with _print_lock:
            print(f"\n{icon} [{idx}] {task_id}: score={eval_result.score:.3f}")
            if error_msg:
                print(f"   Run Error: {error_msg[:200]}")
            if eval_result.details:
                print(f"   Eval: {eval_result.summary[:200]}")


        record = self.build_record(
            item, idx, predicted, eval_result, session, error_msg)
        record.setdefault("is_correct", bool(eval_result.success))
        record.setdefault("success", bool(eval_result.success))
        if result is not None:
            record["runtime_budget"] = {
                "effective_task_seconds": result.metadata.get(
                    "effective_task_seconds"
                ),
                "effective_task_budget_seconds": result.metadata.get(
                    "effective_task_budget_seconds"
                ),
                "wall_clock_seconds": result.metadata.get("wall_clock_seconds"),
            }
            record["api_reliability"] = result.metadata.get("api_reliability", {})
        else:
            record["api_reliability"] = api_tracker.summary()
        record["infrastructure_failure"] = infrastructure_failure
        if bound_execution_policy is not None:
            record["execution_policy"] = bound_execution_policy.to_dict()
            record["official_score_eligible"] = (
                bound_execution_policy.official_score_eligible
            )
            record["evolution_eligible"] = bound_execution_policy.evolution_eligible
        else:
            record["execution_policy"] = {
                "policy_id": execution_policy.policy_id,
                "execution_mode": "setup_failed",
            }
            record["official_score_eligible"] = False
            record["evolution_eligible"] = False
        if team_selection is not None:
            record["team_selection"] = team_selection.to_dict()
        if team_reflection_decision is not None:
            record["team_reflection"] = team_reflection_decision.to_dict()
        if handoff_reflection_decision is not None:
            record["handoff_reflection"] = handoff_reflection_decision.to_dict()
        run_mgr.save_case_result(Path(session.dir), record)
        if result_manifest is not None:
            record["_result_path"] = str(Path(session.dir) / "result.json")
            result_manifest.record_attempt(
                record,
                run_id=run_id,
                original_index=int(item.get("_source_index", idx)),
                force_replace_valid=bool(getattr(args, "manifest_replace_valid", False)),
            )

        return record

    def _make_error_record(self, idx: int, item: dict, error: Exception) -> dict:
        return {
            "idx": idx,
            "index": idx,
            "instance_id": item.get("instance_id", ""),
            "repo": item.get("repo", ""),
            "task_type": item.get("_task_type", ""),
            "status": "error",
            "error": str(error),
            "run_error": str(error),
            "resolved": False,
            "score": 0.0,
            "eval_summary": f"Error: {str(error)[:150]}",
            "model_output": "",
            "patch": "",
        }

    @staticmethod
    def _is_completed_evolution_record(record: dict | None) -> bool:
        """Return whether a saved case may advance an evolution chain.

        A provider outage has no scientific outcome and, crucially, no team
        version.  It must therefore be retried at the same position rather
        than treated as an ordinary failed benchmark answer.
        """
        return bool(record) and not bool(record.get("infrastructure_failure"))

    def _resume_evolution_prefix(
        self,
        items: list[dict],
        run_mgr: Any,
    ) -> tuple[list[dict], int]:
        """Load only the uninterrupted completed prefix of an evolve run.

        If a historic run contains later records after an incomplete case, its
        team-version lineage is ambiguous (the old runner may have skipped an
        outage).  Refuse to continue that run rather than silently training on
        a different sequence from the declared one.
        """
        by_index: dict[int, dict] = {}
        for record in run_mgr.load_case_results():
            raw_index = record.get("idx", record.get("index"))
            try:
                if raw_index is not None:
                    by_index[int(raw_index)] = record
            except (TypeError, ValueError):
                continue

        completed: list[dict] = []
        first_incomplete = len(items)
        for position, item in enumerate(items):
            source_index = int(item.get("_source_index", position))
            record = by_index.get(source_index)
            if self._is_completed_evolution_record(record):
                completed.append(record)
                continue
            first_incomplete = position
            break

        expected_later = {
            int(item.get("_source_index", position))
            for position, item in enumerate(items[first_incomplete + 1:], first_incomplete + 1)
        }
        later_records = sorted(expected_later.intersection(by_index))
        if later_records:
            raise RuntimeError(
                "Cannot safely resume this evolve run: it contains completed "
                f"cases after the first incomplete case ({later_records}). "
                "Start a new run from a known valid team version instead."
            )
        return completed, first_incomplete

    async def run(self, args: argparse.Namespace) -> int:
        items = self.load_dataset(args)
        items = self.prepare_items(items, args)

        if args.dry_run:
            self.dry_run_print(items, args)
            return 0

        if args.results_only:
            self._show_results_only()
            return 0

        team_name = args.team
        evolve = args.evolve
        timeout_secs = args.timeout
        effective_timeout_secs = args.effective_timeout

        run_id = (args.run_id
                  or datetime.now().strftime("%Y%m%d_%H%M%S")
                  + f"_{self.benchmark_name}")
        # The on-disk evolve run always ends in ``_evolve``.  Accept that
        # already-resolved directory name on --resume so a user can copy the
        # printed run id verbatim without accidentally creating *_evolve_evolve.
        if evolve and not run_id.endswith("_evolve"):
            run_id += "_evolve"

        source_team_dir = BASE_DIR / "agents" / team_name
        if not source_team_dir.exists():
            print(f"[error] Team not found: {source_team_dir}")
            sys.exit(1)

        from core.run_manager import RunManager
        from core.result_manifest import ResultManifest
        # core.llm constructs the shared breaker lazily on its first call, so
        # these CLI values apply before team selection or Agent execution.
        if getattr(args, "api_circuit_file", None):
            os.environ["META_TEAM_API_CIRCUIT_FILE"] = args.api_circuit_file
        os.environ["META_TEAM_API_CIRCUIT_THRESHOLD"] = str(args.api_circuit_threshold)
        os.environ["META_TEAM_API_CIRCUIT_COOLDOWN"] = str(args.api_circuit_cooldown)
        manifest_override = args.result_manifest or os.environ.get(
            "META_TEAM_RESULT_MANIFEST"
        )
        manifest_path = Path(manifest_override) if manifest_override else (
            BASE_DIR / "runs" / "manifests" / f"{self.benchmark_name}_{args.split}.json"
        )
        result_manifest = ResultManifest(
            manifest_path, self.benchmark_name, getattr(args, "split", "")
        )
        resume = getattr(args, "resume", False)
        run_mgr = RunManager.create_run(
            run_id=run_id,
            source_team_dir=source_team_dir,
            team_name=team_name,
            config={
                "benchmark": self.benchmark_name,
                "split": getattr(args, "split", ""),
                "cases": len(items),
                "timeout": timeout_secs,
                "effective_timeout": effective_timeout_secs,
                "evolve": evolve,
                "layers": getattr(args, "layers", None),
            },
            resume=resume,
        )

        print(f"Run ID: {run_id}")
        print(f"Benchmark: {self.benchmark_name}")
        print(f"Cases: {len(items)}")
        print(f"Team: {team_name}")
        print(f"Effective task budget: {effective_timeout_secs}s per case")
        print(f"Wall-clock recovery cap: {timeout_secs}s per case")
        print(f"Evolve: {evolve}")
        print(f"Workers: {getattr(args, 'workers', 1)}")
        print(f"Run Dir: {run_mgr.run_dir}")


        records = []
        workers = getattr(args, "workers", 1)
        training_status = "completed"
        training_blocked_case: dict[str, Any] | None = None

        try:
            if evolve or workers <= 1:
                start_position = 0
                if evolve and resume:
                    records, start_position = self._resume_evolution_prefix(items, run_mgr)
                    if start_position:
                        print(
                            f"[resume] retained {start_position} completed evolution "
                            "case(s); continuing from the first incomplete case."
                        )

                for i, item in enumerate(items[start_position:], start_position):
                    source_index = int(item.get("_source_index", i))
                    case_retry = 0
                    while True:
                        record = await self.run_single_case(
                            item, source_index, args, run_mgr,
                            team_name=team_name,
                            evolve=evolve,
                            timeout_secs=timeout_secs,
                            effective_timeout_secs=effective_timeout_secs,
                            result_manifest=result_manifest,
                            run_id=run_id,
                        )
                        if not evolve or not record.get("infrastructure_failure"):
                            records.append(record)
                            break

                        if case_retry >= args.api_case_retries:
                            records.append(record)
                            training_status = "blocked_by_api"
                            training_blocked_case = {
                                "position": i,
                                "source_index": source_index,
                                "task_id": self.get_item_id(item),
                                "case_retries_exhausted": case_retry,
                                "run_error": record.get("run_error", ""),
                            }
                            print(
                                "[evolve] API recovery attempts exhausted; stopping "
                                "without advancing the team version. Resume this same "
                                f"run later with --run-id {run_id} --resume."
                            )
                            break

                        case_retry += 1
                        wait_seconds = max(0.0, float(args.api_case_retry_wait))
                        # If the shared provider circuit is open, waiting only
                        # the nominal retry interval would turn the next
                        # attempt into a guaranteed APICircuitOpenError.  Use
                        # its remaining cooldown while still allowing a
                        # caller to request a longer fixed pause.
                        try:
                            from core.api_circuit_breaker import get_shared_circuit_breaker
                            breaker = get_shared_circuit_breaker()
                            if breaker is not None:
                                circuit = breaker.snapshot()
                                wait_seconds = max(
                                    wait_seconds,
                                    max(0.0, circuit.open_until - time.time()),
                                )
                        except Exception:
                            pass
                        print(
                            f"[evolve] infrastructure failure on case {source_index}; "
                            f"retrying the same case ({case_retry}/{args.api_case_retries}) "
                            f"after {wait_seconds:.0f}s."
                        )
                        if wait_seconds:
                            await asyncio.sleep(wait_seconds)

                    if training_status != "completed":
                        break
            else:
                print(f"Parallel mode: {workers} concurrent asyncio tasks")

                # LiteLLM owns process-global async logging workers.  Running
                # each case in a separate thread-local event loop races those
                # workers during startup and can tear them down while another
                # case is using them.  Keep every case on this adapter's loop
                # and use a semaphore for the requested concurrency instead.
                semaphore = asyncio.Semaphore(workers)

                async def _process_case(i: int, item: dict) -> tuple[int, dict]:
                    async with semaphore:
                        source_index = int(item.get("_source_index", i))
                        try:
                            record = await self.run_single_case(
                                item, source_index, args, run_mgr,
                                team_name=team_name,
                                evolve=evolve,
                                timeout_secs=timeout_secs,
                                effective_timeout_secs=effective_timeout_secs,
                                result_manifest=result_manifest,
                                run_id=run_id,
                            )
                        except Exception as e:
                            import traceback
                            with _print_lock:
                                print(f"[Case {i}] EXCEPTION: {e}")
                                traceback.print_exc()
                            record = self._make_error_record(source_index, item, e)
                        return i, record

                completed = await asyncio.gather(
                    *(_process_case(i, item) for i, item in enumerate(items))
                )
                records = [record for _, record in sorted(completed)]
        finally:
            with _owned_lock:
                leaked = len(_owned_containers)
            if leaked:
                print(f"\n[cleanup] Found {leaked} leaked container(s) "
                      f"from this process, cleaning up...")
                _cleanup_owned_containers()
                print(f"[cleanup] Done.")

        # Summary
        summary_data = self.compute_summary(records, args)
        summary = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "benchmark": self.benchmark_name,
            "split": getattr(args, "split", ""),
            "team": team_name,
            "evolve": evolve,
            "training_status": training_status,
            "training_blocked_case": training_blocked_case,
            "records": records,
            "team_versions": run_mgr.list_team_versions(),
            "result_manifest": str(manifest_path),
            "result_manifest_summary": result_manifest.summary(),
            **summary_data,
        }
        run_mgr.save_summary(summary)

        # Legacy results dir
        legacy_dir = self.results_dir / run_id
        legacy_dir.mkdir(parents=True, exist_ok=True)
        with open(legacy_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        self.print_summary(records)
        # A nonzero temporary-failure status makes shell launchers and job
        # schedulers notice that this is an intentionally incomplete training
        # chain, not a finished experiment.  The summary/case record has
        # already been written and can be resumed with the same --run-id.
        return 75 if training_status == "blocked_by_api" else 0

    def _show_results_only(self) -> None:
        if not self.results_dir.exists():
            print("No results yet.")
            return
        for run_dir in sorted(self.results_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            summary_file = run_dir / "summary.json"
            if summary_file.exists():
                with open(summary_file, encoding="utf-8") as sf:
                    s = json.load(sf)
                score = s.get("avg_score", s.get("accuracy", 0))
                total = s.get("total", 0)
                split = s.get("split", "?")
                team = s.get("team", "?")
                evo = " [evolve]" if s.get("evolve") else ""
                print(f"  {run_dir.name}: score={score:.3f} "
                      f"({total} cases) split={split} team={team}{evo}")

    # CLI
    # ---------------------------------------------------------------------------

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=f"{self.benchmark_name} benchmark")
        if self.split_choices:
            parser.add_argument(
                "--split", type=str,
                default=self.default_split,
                choices=self.split_choices,
                help=f"Data subset (default: {self.default_split})",
            )
        parser.add_argument(
            "--cases", type=str, default=None,
            help="Comma-separated case indices")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print items only, do not run")
        parser.add_argument(
            "--results-only", action="store_true",
            help="Show existing results only")
        parser.add_argument(
            "--run-id", type=str, default=None,
            help="Specify run ID")
        parser.add_argument(
            "--team", type=str, default=self.default_team,
            help=f"Team to use (default: {self.default_team})")
        parser.add_argument(
            "--evolve", action="store_true",
            help="Enter evolution phase after each case")
        parser.add_argument(
            "--timeout", type=float, default=None,
            help="Wall-clock recovery cap per case (0=unlimited)")
        parser.add_argument(
            "--effective-timeout", type=float, default=None,
            help="Useful task-time budget; retryable provider outages do not consume it (0=unlimited)")
        parser.add_argument(
            "--max-cost", type=float, default=None,
            help="Max cost USD per case (0=unlimited)")
        parser.add_argument(
            "--layers", type=str, default=None,
            help="Ablation: specify enabled evolution layers")
        parser.add_argument(
            "--workers", type=int, default=1,
            help="Parallel workers (non-evolve only, default 1=serial)")
        parser.add_argument(
            "--resume", action="store_true",
            help="Reuse existing run dir; evolve resumes from the first incomplete case")
        parser.add_argument(
            "--api-case-retries", type=int, default=3,
            help="Extra same-case recovery attempts in evolve mode before safely stopping (default: 3)")
        parser.add_argument(
            "--api-case-retry-wait", type=float, default=30.0,
            help="Seconds between evolve same-case API recovery attempts (default: 30)")
        parser.add_argument(
            "--rollout", type=int, default=1,
            help="Number of independent runs for avg@K (default: 1)")
        parser.add_argument(
            "--result-manifest", type=str, default=None,
            help="Canonical de-duplicated ledger (default: runs/manifests/<benchmark>_<split>.json)")
        parser.add_argument(
            "--manifest-replace-valid", action="store_true",
            help="Replace an existing non-pending manifest result (use only for an intentional corrected rerun)")
        parser.add_argument(
            "--api-circuit-file", type=str, default=None,
            help="Shared API circuit state file (default: runs/.api_circuit.json)")
        parser.add_argument(
            "--api-circuit-threshold", type=int, default=3,
            help="Consecutive terminal infrastructure failures before pausing calls")
        parser.add_argument(
            "--api-circuit-cooldown", type=float, default=120.0,
            help="Seconds to pause provider calls after the circuit opens")
        self.add_extra_args(parser)
        return parser

    def cli(self) -> None:
        parser = self.build_parser()
        args = parser.parse_args()

        if args.timeout is None:
            args.timeout = (self.default_evolve_timeout if args.evolve
                            else self.default_timeout)

        if args.effective_timeout is None:
            args.effective_timeout = (
                self.default_effective_timeout
                if self.default_effective_timeout is not None
                else args.timeout
            )

        if args.max_cost is None:
            args.max_cost = self.default_max_cost

        if args.evolve and args.workers > 1:
            print("[error] --evolve and --workers > 1 are mutually exclusive. "
                  "Evolution mode requires sequential execution (--workers 1).")
            sys.exit(1)

        if args.api_case_retries < 0 or args.api_case_retry_wait < 0:
            parser.error("--api-case-retries and --api-case-retry-wait must be non-negative")

        if args.rollout <= 1:
            exit_code = asyncio.run(self.run(args))
            if exit_code:
                sys.exit(exit_code)
        else:
            base_run_id = args.run_id or f"{self.benchmark_name}"
            for r in range(1, args.rollout + 1):
                args.run_id = f"{base_run_id}_r{r}"
                print(f"\n=== Rollout {r}/{args.rollout} (run_id: {args.run_id}) ===")
                exit_code = asyncio.run(self.run(args))
                if exit_code:
                    sys.exit(exit_code)
