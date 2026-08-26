"""ResearchRubrics benchmark adapter."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any

_BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE_DIR))

from benchmarks.adapter import BenchmarkAdapter, EvalResult, EnvContext
from evaluation.researchrubrics import ResearchRubricsEvaluator



RR_DATA_PATH = _BASE_DIR / "data" / "researchrubrics" / "researchrubrics.jsonl"
SPLITS_DIR = _BASE_DIR / "data" / "splits"


# ---------------------------------------------------------------------------

def _make_metateam_completer(model: str = "claude-sonnet-4.6"):
    async def _completer(messages: list[dict]) -> dict:
        from core import llm
        resp = await llm.complete(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=4096,
        )
        text = llm.extract_text(resp) or ""
        return {"content": text}
    return _completer


# ---------------------------------------------------------------------------

def _build_validation_summary(judge: dict) -> str:
    metrics = judge.get("metrics", {})
    overall = metrics.get("overall_score", 0.0)
    axis_parts = []
    for axis in ["Explicit Criteria", "Implicit Criteria",
                 "Synthesis of Information", "Communication Quality",
                 "Instruction Following", "References & Citation Quality"]:
        if axis in metrics:
            axis_parts.append(f"{axis[:4]}={metrics[axis]:.2f}")
    return f"overall={overall:.3f}  " + "  ".join(axis_parts)


def _build_validation_details(judge: dict, prediction: str) -> str:
    lines = []

    # --- 1. Per-axis scores ---
    metrics = judge.get("metrics", {})
    axis_sorted = [(k, v) for k, v in metrics.items() if k != "overall_score"]
    axis_sorted.sort(key=lambda x: x[1])
    lines.append("PER-AXIS SCORES (worst first):")
    for axis, score in axis_sorted:
        lines.append(f"  {axis}: {score:.3f}")

    # --- 2. Top failed rubrics (by abs weight) ---
    items = judge.get("rubric_items_with_grades", [])
    failed = [r for r in items if r.get("verdict", "").lower() != "satisfied"]
    failed.sort(key=lambda x: -abs(x.get("weight", 0)))
    if failed:
        lines.append("\nTOP UNSATISFIED RUBRICS (highest weight first):")
        for r in failed[:8]:
            w = r.get("weight", 0)
            sign = "(-)" if w < 0 else "(+)"
            lines.append(
                f"  {sign} w={w:+.1f} [{r.get('axis','?')}] "
                f"{r.get('criterion','')[:150]}"
            )
            reason = r.get("reasoning", "")[:200].replace("\n", " ")
            if reason:
                lines.append(f"       why failed: {reason}")

    neg_triggered = [
        r for r in items
        if r.get("weight", 0) < 0 and r.get("verdict", "").lower() == "satisfied"
    ]
    if neg_triggered:
        lines.append(f"\nNEGATIVE BEHAVIORS TRIGGERED ({len(neg_triggered)}):")
        for r in neg_triggered[:3]:
            lines.append(
                f"  w={r.get('weight'):+.1f} [{r.get('axis','?')}] "
                f"{r.get('criterion','')[:150]}"
            )

    pred = prediction or ""
    last_tail = pred[-300:].strip() if len(pred) > 300 else pred
    has_conf = ("confidence:" in last_tail.lower())
    truncated_signal = (
        not has_conf or
        not last_tail.rstrip().endswith((".", "!", "?", "%", '"', ")", "]", "*"))
    )
    if truncated_signal:
        lines.append(
            f"\n⚠ TRUNCATION SIGNAL: final report does NOT end with a "
            f"'Confidence: X%' line. Last 100 chars: {last_tail[-100:]!r}"
        )

    out = "\n".join(lines)
    return out[:2500]


# ---------------------------------------------------------------------------

def _load_split(split_path: Path) -> dict:
    if not split_path.exists():
        raise FileNotFoundError(
            f"Split file not found: {split_path}\n"
            f"Available splits in {SPLITS_DIR}:"
            + "\n  ".join([""] + [str(p.name) for p in SPLITS_DIR.glob("*.json")])
        )
    return json.loads(split_path.read_text(encoding="utf-8"))


def _load_rr_data() -> list[dict]:
    if not RR_DATA_PATH.exists():
        raise FileNotFoundError(
            f"RR data not found: {RR_DATA_PATH}\n"
            f"Download from: https://huggingface.co/datasets/AggAgent/ResearchRubrics"
        )
    return [json.loads(l) for l in RR_DATA_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


# Adapter
# ---------------------------------------------------------------------------

class ResearchRubricsAdapter(BenchmarkAdapter):
    benchmark_name = "researchrubrics"
    default_team = "pool_DeepResearch"
    default_timeout = 1800.0
    default_evolve_timeout = 2400.0
    default_max_cost = 5.0
    default_split = ""
    results_subdir = "researchrubrics-results"
    split_choices = None

    def __init__(self):
        super().__init__()
        self._rr_data: list[dict] | None = None
        self._split_cache: dict | None = None

    # ------------------------------------------------------------------

    def add_extra_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--split-file", type=str, default=None,
            help="Split JSON file path (data/splits/*.json). "
                 "Only run these sample_ids when specified."
        )
        parser.add_argument(
            "--max-items", type=int, default=None,
            help="Max items to load (truncated after split filter)",
        )
        parser.add_argument(
            "--domain", type=str, default=None,
            help="Run only specified domain (e.g. 'AI & ML', 'STEM')",
        )
        parser.add_argument(
            "--sample-ids", type=str, default=None,
            help="Comma-separated sample_ids (overrides --split-file)",
        )
        parser.add_argument(
            "--judge-model", type=str, default="claude-sonnet-4.6",
            help="LLM for rubric judge (default: claude-sonnet-4.6)",
        )

    # ------------------------------------------------------------------

    def load_dataset(self, args: argparse.Namespace) -> list[dict]:
        items = _load_rr_data()

        # Filter by domain
        if args.domain:
            items = [x for x in items if x["domain"] == args.domain]

        if args.sample_ids:
            target = {s.strip() for s in args.sample_ids.split(",")}
            items = [x for x in items if x["sample_id"] in target]
        elif args.split_file:
            split_path = Path(args.split_file)
            if not split_path.is_absolute():
                cand = _BASE_DIR / split_path
                if cand.exists():
                    split_path = cand
            split = _load_split(split_path)
            self._split_cache = split
            split_ids = split.get("sample_ids", [])
            id_to_item = {x["sample_id"]: x for x in items}
            items = [id_to_item[sid] for sid in split_ids if sid in id_to_item]
            print(f"  [split] loaded {len(items)} items from "
                  f"split='{split.get('name','?')}'")

        # Max items
        if args.max_items:
            items = items[: args.max_items]

        return items

    def get_item_id(self, item: dict) -> str:
        return item["sample_id"]

    # ------------------------------------------------------------------

    def build_task(self, item: dict, session: Any,
                   workspace_files: list[str]) -> str:
        return item["prompt"]

    def build_team_selection_task(self, item: dict, full_task: str) -> str:
        return json.dumps({
            "work_request": item.get("prompt", ""),
            "verification_rubric": item.get("rubric", item.get("rubrics", [])),
        }, ensure_ascii=False)

    # ------------------------------------------------------------------

    def evaluate(self, item: dict, predicted: str, session: Any,
                 **ctx) -> EvalResult:
        import asyncio
        import concurrent.futures

        evaluator = ResearchRubricsEvaluator(max_concurrent=10)
        evaluator.set_completer(_make_metateam_completer(
            model=getattr(self, "_judge_model", "claude-sonnet-4.6")
        ))

        if not predicted or not predicted.strip():
            return EvalResult(
                success=False,
                score=0.0,
                summary="Empty prediction",
                details="Agent returned no content.",
                extra={"metrics": {}, "rubric_items_with_grades": []},
            )

        async def _do_eval():
            return await evaluator.compute_score(
                prediction=predicted,
                item=item,
                llm="claude-sonnet-4.6",
            )

        def _run_in_new_loop():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_do_eval())
            finally:
                loop.close()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_in_new_loop)
                judge = future.result(timeout=600)  # 10 min judge timeout
        except Exception as e:
            tb = traceback.format_exc()
            return EvalResult(
                success=False,
                score=0.0,
                summary=f"Evaluation error: {e}",
                details=tb[:1500],
                extra={"error": str(e)},
            )

        metrics = judge.get("metrics", {})
        overall = metrics.get("overall_score", 0.0)
        summary = _build_validation_summary(judge)
        details = _build_validation_details(judge, predicted)

        return EvalResult(
            success=overall >= 0.68,
            score=float(overall),
            summary=summary,
            details=details,
            extra={
                "metrics": {k: round(v, 4) for k, v in metrics.items()},
                "rubric_items_with_grades": judge.get("rubric_items_with_grades", []),
                "n_rubrics": len(judge.get("rubric_items_with_grades", [])),
                "n_not_satisfied": sum(
                    1 for r in judge.get("rubric_items_with_grades", [])
                    if r.get("verdict", "").lower() != "satisfied"
                ),
            },
        )

    # ------------------------------------------------------------------

    def build_task_validator(self, item: dict, session: Any, **ctx):
        from core.types import TaskValidation

        async def _validator(output: str) -> TaskValidation:
            evaluator = ResearchRubricsEvaluator(max_concurrent=10)
            evaluator.set_completer(_make_metateam_completer(
                model=getattr(self, "_judge_model", "claude-sonnet-4.6")
            ))

            if not output or not output.strip():
                return TaskValidation(
                    success=False,
                    summary="Empty output",
                    details="Agent did not produce any output.",
                )

            try:
                judge = await evaluator.compute_score(
                    prediction=output,
                    item=item,
                    llm="claude-sonnet-4.6",
                )
            except Exception as e:
                return TaskValidation(
                    success=False,
                    summary=f"Validation error: {e}",
                    details=traceback.format_exc()[:1500],
                )

            metrics = judge.get("metrics", {})
            overall = metrics.get("overall_score", 0.0)
            return TaskValidation(
                success=overall >= 0.68,
                summary=_build_validation_summary(judge),
                details=_build_validation_details(judge, output),
            )

        return _validator

    # ------------------------------------------------------------------

    def build_record(self, item: dict, idx: int, predicted: str,
                     eval_result: EvalResult, session: Any,
                     error: str) -> dict:
        extra = eval_result.extra or {}
        metrics = extra.get("metrics", {})

        return {
            "idx": idx,
            "sample_id": item["sample_id"],
            "domain": item["domain"],
            "conceptual_breadth": item.get("conceptual_breadth"),
            "logical_nesting": item.get("logical_nesting"),
            "exploration": item.get("exploration"),
            "n_rubrics": extra.get("n_rubrics", 0),
            "overall_score": round(eval_result.score, 4),
            "success": eval_result.success,
            "axis_scores": {
                k: round(v, 4) for k, v in metrics.items()
                if k != "overall_score"
            },
            "n_not_satisfied": extra.get("n_not_satisfied", 0),
            "eval_summary": eval_result.summary[:200],
            "prediction_len": len(predicted or ""),
            "model_output": (predicted or "")[:3000],
            "run_error": error,
            "session_id": getattr(session, "id", ""),
        }

    # ------------------------------------------------------------------

    def print_summary(self, records: list[dict]) -> None:
        if not records:
            print("No records.")
            return

        print("\n" + "=" * 72)
        print(f"ResearchRubrics Results ({len(records)} cases)")
        print("=" * 72)

        valid = [r for r in records if r.get("overall_score") is not None]
        if not valid:
            print("No valid scores.")
            return

        avg_overall = sum(r["overall_score"] for r in valid) / len(valid)
        n_success = sum(1 for r in valid if r.get("success"))
        print(f"\n  avg overall_score: {avg_overall:.3f}")
        print(f"  success (>=0.68): {n_success}/{len(valid)} "
              f"({100*n_success/len(valid):.0f}%)")

        # Per-axis average
        axis_totals: dict[str, list[float]] = defaultdict(list)
        for r in valid:
            for axis, v in r.get("axis_scores", {}).items():
                axis_totals[axis].append(v)
        if axis_totals:
            print("\n  avg axis scores:")
            for axis in [
                "Explicit Criteria", "Implicit Criteria",
                "Synthesis of Information", "Communication Quality",
                "Instruction Following", "References & Citation Quality",
            ]:
                if axis in axis_totals:
                    scores = axis_totals[axis]
                    print(f"    {axis:<35s} {sum(scores)/len(scores):.3f}  "
                          f"(n={len(scores)})")

        # Per-domain
        by_domain: dict[str, list[float]] = defaultdict(list)
        for r in valid:
            by_domain[r["domain"]].append(r["overall_score"])
        print("\n  avg by domain:")
        for domain in sorted(by_domain):
            scores = by_domain[domain]
            print(f"    {domain:<35s} {sum(scores)/len(scores):.3f}  "
                  f"(n={len(scores)})")

        print("\n  individual:")
        for r in valid:
            icon = "✅" if r.get("success") else (
                "🔶" if r["overall_score"] >= 0.4 else "❌")
            err = f"  ERR: {r['run_error'][:40]}" if r.get("run_error") else ""
            print(f"    {icon} [{r['idx']:3d}] {r['sample_id']:<26s} "
                  f"{r['domain']:<30s} score={r['overall_score']:.3f}"
                  f"{err}")

    # ------------------------------------------------------------------

    def compute_summary(self, records: list[dict],
                        args: argparse.Namespace) -> dict:
        total = len(records)
        valid = [r for r in records if r.get("overall_score") is not None]
        if not valid:
            return {"total": total, "avg_score": 0.0, "correct": 0}

        avg = sum(r["overall_score"] for r in valid) / len(valid)
        correct = sum(1 for r in valid if r.get("success"))

        # Per-axis summary
        axis_totals: dict[str, list[float]] = defaultdict(list)
        for r in valid:
            for axis, v in r.get("axis_scores", {}).items():
                axis_totals[axis].append(v)
        axis_avgs = {
            axis: round(sum(scores) / len(scores), 4)
            for axis, scores in axis_totals.items()
        }

        base = super().compute_summary(records, args)
        base.update({
            "avg_score": round(avg, 4),
            "correct": correct,
            "axis_avgs": axis_avgs,
            "success_rate": round(correct / len(valid), 4),
        })
        return base


# Entry point

if __name__ == "__main__":
    adapter = ResearchRubricsAdapter()

    # Patch cli() to stash judge_model on adapter
    original_cli = adapter.cli

    def _cli_wrapper():
        parser = adapter.build_parser()
        args = parser.parse_args()
        adapter._judge_model = getattr(args, "judge_model", "claude-sonnet-4.6")

        if args.timeout is None:
            args.timeout = (adapter.default_evolve_timeout if args.evolve
                            else adapter.default_timeout)
        if args.max_cost is None:
            args.max_cost = adapter.default_max_cost

        if args.evolve and args.workers > 1:
            print("[error] --evolve and --workers > 1 are mutually exclusive.")
            sys.exit(1)

        import asyncio
        asyncio.run(adapter.run(args))

    _cli_wrapper()
