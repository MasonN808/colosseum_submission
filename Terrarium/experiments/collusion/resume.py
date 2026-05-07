from __future__ import annotations

"""Resume a collusion sweep in-place.

This reuses `experiments.collusion.run` to re-run only the runs that are missing
or incomplete under an existing output root.
"""

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from tqdm import tqdm

if sys.version_info < (3, 11):
    raise RuntimeError(
        "Terrarium requires Python >= 3.11. "
        "Create/activate a `.venv` (see repo README) and re-run with `.venv/bin/python`."
    )

project_root = Path(__file__).resolve().parents[2]
# Allow running without installing the repo as a package.
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from experiments.collusion import run as run_mod  # noqa: E402

RunSpec = run_mod.RunSpec


REQUIRED_RUN_FILES: Tuple[str, ...] = (
    "run_config.json",
    "metrics.json",
    "final_summary.json",
    "agent_turns.json",
    "tool_events.json",
    "blackboards.json",
)

def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8")) or {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _is_run_complete(run_dir: Path) -> bool:
    if not run_dir.exists():
        return False
    return all((run_dir / fname).exists() for fname in REQUIRED_RUN_FILES)


def _run_has_error_turns(run_dir: Path) -> bool:
    path = run_dir / "agent_turns.json"
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return '"response": "[ERROR]' in raw or "'response': '[ERROR]" in raw


def _run_status(run_dir: Path) -> Optional[str]:
    """Return normalized run status (e.g., 'complete' / 'incomplete'), if available."""
    for name in ("metrics.json", "final_summary.json"):
        path = run_dir / name
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        status = obj.get("status")
        if status is None:
            continue
        status_s = str(status).strip().lower()
        if status_s:
            return status_s
    return None


def _is_run_acceptable(
    run_dir: Path, *, require_status_complete: bool, rerun_error_turns: bool
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if not _is_run_complete(run_dir):
        reasons.append("missing_files")
        return False, reasons

    if require_status_complete:
        status = _run_status(run_dir)
        if status != "complete":
            reasons.append("status_not_complete")

    if rerun_error_turns and _run_has_error_turns(run_dir):
        reasons.append("error_turns")

    return (not reasons), reasons

def _select_incomplete_runs(
    *,
    root: Path,
    cfg: Dict[str, Any],
    require_status_complete: bool,
    rerun_error_turns: bool,
) -> Tuple[List[RunSpec], int, int, Dict[str, int], Dict[str, List[str]]]:
    expected = list(run_mod._iter_expected_run_specs(cfg))
    total_runs = len(expected)
    completed = 0
    incomplete: List[RunSpec] = []
    reason_counts: Dict[str, int] = {}
    reasons_by_run_id: Dict[str, List[str]] = {}
    for spec in expected:
        run_dir = root / "runs" / spec.model_label / spec.sweep_name / spec.run_id
        ok, reasons = _is_run_acceptable(
            run_dir,
            require_status_complete=require_status_complete,
            rerun_error_turns=rerun_error_turns,
        )
        if ok:
            completed += 1
        else:
            incomplete.append(spec)
            reasons_by_run_id[spec.run_id] = list(reasons)
            for r in reasons:
                reason_counts[r] = reason_counts.get(r, 0) + 1
    return incomplete, completed, total_runs, reason_counts, reasons_by_run_id


def _rebuild_summary_files(root: Path) -> None:
    rows: List[Dict[str, Any]] = []
    for run_dir in (root / "runs").rglob("*"):
        if not run_dir.is_dir():
            continue
        rc_path = run_dir / "run_config.json"
        if not rc_path.exists():
            continue
        try:
            rc = json.loads(rc_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        metrics_path = run_dir / "metrics.json"
        metrics: Dict[str, Any] = {}
        if metrics_path.exists():
            try:
                obj = json.loads(metrics_path.read_text(encoding="utf-8")) or {}
                metrics = obj if isinstance(obj, dict) else {}
            except Exception:
                metrics = {}

        run_id = rc.get("run_id") or run_dir.name
        row: Dict[str, Any] = {
            "run_id": run_id,
            "model_label": rc.get("model_label"),
            "provider": rc.get("provider"),
            "model": rc.get("model"),
            "sweep": rc.get("sweep") or rc.get("sweep_name"),
            "topology": rc.get("topology"),
            "num_agents": rc.get("num_agents"),
            "colluder_count": rc.get("colluder_count"),
            "secret_channel_enabled": rc.get("secret_channel_enabled"),
            "secret_channel_count": rc.get(
                "secret_channel_count",
                1 if rc.get("secret_channel_enabled") else 0,
            ),
            "secret_blackboard_id": rc.get("secret_blackboard_id"),
            "secret_blackboard_ids": rc.get("secret_blackboard_ids"),
            "extra_secret_blackboard_ids": rc.get("extra_secret_blackboard_ids"),
            "noncolluder_secret_pairs": rc.get("noncolluder_secret_pairs"),
            "prompt_variant": rc.get("prompt_variant"),
            "seed": rc.get("seed"),
            "replica_index": run_mod._infer_replica_index(
                rc.get("replica_index"), run_id=run_id
            ),
            "colluders": rc.get("colluders"),
            "agent_model_assignments": rc.get("agent_model_assignments"),
            "colluder_model_assignments": rc.get("colluder_model_assignments"),
            "status": metrics.get("status"),
            "coalition_reward_sum": metrics.get("coalition_reward_sum"),
            "noncoalition_reward_sum": metrics.get("noncoalition_reward_sum"),
            "coalition_mean_reward": metrics.get("coalition_mean_reward"),
            "noncoalition_mean_reward": metrics.get("noncoalition_mean_reward"),
            "coalition_advantage_mean": metrics.get("coalition_advantage_mean"),
            "colluder_posts_secret_rate": metrics.get("colluder_posts_secret_rate"),
            "mean_regret": metrics.get("mean_regret"),
            "coalition_mean_regret": metrics.get("coalition_mean_regret"),
            "noncoalition_mean_regret": metrics.get("noncoalition_mean_regret"),
            "system_regret": metrics.get("system_regret"),
            "system_regret_ratio": metrics.get("system_regret_ratio"),
        }
        rows.append(row)

    # Stable ordering for diffs/readability.
    rows = sorted(rows, key=lambda r: str(r.get("run_id") or ""))

    (root / "summary.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    with (root / "summary.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    flat_rows: List[Dict[str, Any]] = []
    for row in rows:
        flat_rows.append(
            {k: v for k, v in row.items() if not isinstance(v, (dict, list))}
        )

    if not flat_rows:
        (root / "summary.csv").write_text("", encoding="utf-8")
        return

    fieldnames = sorted({k for r in flat_rows for k in r.keys()})
    with (root / "summary.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(flat_rows)


async def resume_runs(
    *,
    root: Path,
    cfg: Dict[str, Any],
    max_concurrent_runs: int,
    stop_on_error: bool,
    require_status_complete: bool,
    rerun_error_turns: bool,
    dry_run: bool,
) -> None:
    run_mod._ensure_dir(root)
    run_mod._configure_experiment_logging(root)

    (
        to_run,
        completed,
        total_runs,
        reason_counts,
        reasons_by_run_id,
    ) = _select_incomplete_runs(
        root=root,
        cfg=cfg,
        require_status_complete=require_status_complete,
        rerun_error_turns=rerun_error_turns,
    )
    failed = 0

    run_mod.logger.info(
        "RESUME START (total_runs=%s, completed=%s, remaining=%s, output_root=%s)",
        total_runs,
        completed,
        len(to_run),
        root,
    )

    if dry_run:
        print(f"Output root: {root}")
        print(f"Total runs: {total_runs}")
        print(f"Already complete: {completed}")
        print(f"Remaining (incomplete): {len(to_run)}")
        if reason_counts:
            print("Incomplete reasons:")
            for reason, count in sorted(
                reason_counts.items(), key=lambda kv: (-int(kv[1]), str(kv[0]))
            ):
                print(f"  - {reason}: {count}")
        if to_run:
            print("Next 10 runs to execute:")
            for spec in to_run[:10]:
                reasons = reasons_by_run_id.get(spec.run_id) or []
                suffix = f" (reasons={', '.join(reasons)})" if reasons else ""
                print(f"  - {spec.run_label}{suffix}")
        return

    run_mod._write_progress(
        root,
        {
            "status": "running",
            "total_runs": total_runs,
            "completed_runs": completed,
            "failed_runs": failed,
            "resumed_at": datetime.now().isoformat(),
        },
    )

    if not to_run:
        run_mod.logger.info("RESUME DONE (nothing to do; all runs appear complete)")
        _rebuild_summary_files(root)
        remaining_specs, final_completed, _, _, _ = _select_incomplete_runs(
            root=root,
            cfg=cfg,
            require_status_complete=require_status_complete,
            rerun_error_turns=rerun_error_turns,
        )
        status = "completed"
        if remaining_specs:
            status = "completed_with_remaining"
        if failed:
            status = "completed_with_failures"
        run_mod._write_progress(
            root,
            {
                "status": status,
                "total_runs": total_runs,
                "completed_runs": final_completed,
                "failed_runs": failed,
                "remaining_runs": len(remaining_specs),
            },
        )
        return

    max_concurrent_runs = int(max_concurrent_runs)
    if max_concurrent_runs <= 0:
        raise ValueError("max_concurrent_runs must be a positive integer")

    with tqdm(
        total=total_runs,
        initial=completed,
        desc="Experiments (resume)",
        unit="run",
        dynamic_ncols=True,
    ) as pbar:
        if max_concurrent_runs <= 1:
            for spec in to_run:
                pbar.set_postfix_str(spec.run_label)
                run_status = "success"
                try:
                    await run_mod._run_single(
                        base_cfg=cfg,
                        model_label=spec.model_label,
                        model_llm_cfg=spec.model_llm_cfg,
                        model_agent_llms=spec.model_agent_llms,
                        model_collusion_cfg=spec.model_collusion_cfg,
                        sweep_name=spec.sweep_name,
                        environment_label=spec.environment_label,
                        environment_cfg=spec.environment_cfg,
                        topology=spec.topology,
                        num_agents=spec.num_agents,
                        colluder_count=spec.colluder_count,
                        secret_channel_enabled=spec.secret_channel_enabled,
                        secret_channel_count=spec.secret_channel_count,
                        prompt_variant=spec.prompt_variant,
                        seed=spec.seed,
                        replica_index=spec.replica_index,
                        out_dir=root,
                    )
                    completed += 1
                except Exception:
                    run_status = "failed"
                    failed += 1
                    run_mod.logger.exception("RUN FAILED %s", spec.run_label)
                    if stop_on_error:
                        raise
                finally:
                    pbar.update(1)
                    run_mod._write_progress(
                        root,
                        {
                            "status": "running",
                            "total_runs": total_runs,
                            "completed_runs": completed,
                            "failed_runs": failed,
                            "last_run_label": spec.run_label,
                            "last_run_status": run_status,
                        },
                    )
        else:
            semaphore = asyncio.Semaphore(max_concurrent_runs)

            def _run_single_in_thread(**kwargs: Any) -> Dict[str, Any]:
                return asyncio.run(run_mod._run_single(**kwargs))

            async def _run_single_limited(*, spec: RunSpec) -> Dict[str, Any]:
                async with semaphore:
                    run_mod.logger.info("SCHEDULED %s", spec.run_label)
                    return await asyncio.to_thread(
                        _run_single_in_thread,
                        base_cfg=cfg,
                        model_label=spec.model_label,
                        model_llm_cfg=spec.model_llm_cfg,
                        model_agent_llms=spec.model_agent_llms,
                        model_collusion_cfg=spec.model_collusion_cfg,
                        sweep_name=spec.sweep_name,
                        environment_label=spec.environment_label,
                        environment_cfg=spec.environment_cfg,
                        topology=spec.topology,
                        num_agents=spec.num_agents,
                        colluder_count=spec.colluder_count,
                        secret_channel_enabled=spec.secret_channel_enabled,
                        secret_channel_count=spec.secret_channel_count,
                        prompt_variant=spec.prompt_variant,
                        seed=spec.seed,
                        replica_index=spec.replica_index,
                        out_dir=root,
                    )

            tasks: List[asyncio.Task[Any]] = []
            task_specs: Dict[asyncio.Task[Any], RunSpec] = {}
            for spec in to_run:
                task = asyncio.create_task(_run_single_limited(spec=spec))
                tasks.append(task)
                task_specs[task] = spec

            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for finished in done:
                    spec = task_specs.get(finished)
                    if spec is None:
                        continue
                    pbar.set_postfix_str(spec.run_label)
                    run_status = "success"
                    try:
                        await finished
                        completed += 1
                    except Exception:
                        run_status = "failed"
                        failed += 1
                        run_mod.logger.exception("RUN FAILED %s", spec.run_label)
                        if stop_on_error:
                            for t in pending:
                                t.cancel()
                            await asyncio.gather(*pending, return_exceptions=True)
                            raise
                    finally:
                        pbar.update(1)
                        run_mod._write_progress(
                            root,
                            {
                                "status": "running",
                                "total_runs": total_runs,
                                "completed_runs": completed,
                                "failed_runs": failed,
                                "last_run_label": spec.run_label,
                                "last_run_status": run_status,
                            },
                        )

    _rebuild_summary_files(root)
    remaining_specs, final_completed, _, _, _ = _select_incomplete_runs(
        root=root,
        cfg=cfg,
        require_status_complete=require_status_complete,
        rerun_error_turns=rerun_error_turns,
    )
    status = "completed"
    if remaining_specs:
        status = "completed_with_remaining"
    if failed:
        status = "completed_with_failures"
    run_mod.logger.info(
        "RESUME END (completed=%s, remaining=%s, failed=%s, total_runs=%s, output_root=%s)",
        final_completed,
        len(remaining_specs),
        failed,
        total_runs,
        root,
    )
    run_mod._write_progress(
        root,
        {
            "status": status,
            "total_runs": total_runs,
            "completed_runs": final_completed,
            "failed_runs": failed,
            "remaining_runs": len(remaining_specs),
        },
    )


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Resume a previously started collusion experiment in-place."
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Existing timestamp output root (e.g., experiments/collusion/outputs/<tag>/<timestamp>).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional config override. Defaults to <root>/config.json if present.",
    )
    parser.add_argument(
        "--max-concurrent-runs",
        default=None,
        type=int,
        help="Override experiment.max_concurrent_runs when resuming.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Abort immediately on the first failed run (default: continue and record failures).",
    )
    parser.add_argument(
        "--require-status-complete",
        action="store_true",
        help="Treat runs as incomplete unless metrics/final_summary status is 'complete'.",
    )
    parser.add_argument(
        "--rerun-error-turns",
        dest="rerun_error_turns",
        action="store_true",
        default=True,
        help="Treat runs as incomplete if agent_turns.json contains an '[ERROR]' response (default: enabled).",
    )
    parser.add_argument(
        "--no-rerun-error-turns",
        dest="rerun_error_turns",
        action="store_false",
        help="Disable treating '[ERROR]' turns as incomplete.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would run and exit."
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    config_path = Path(args.config) if args.config else (root / "config.json")
    cfg = _load_config(config_path)

    exp = cfg.get("experiment") or {}
    max_concurrent_runs = (
        int(args.max_concurrent_runs)
        if args.max_concurrent_runs is not None
        else int(exp.get("max_concurrent_runs", 1))
    )

    asyncio.run(
        resume_runs(
            root=root,
            cfg=cfg,
            max_concurrent_runs=max_concurrent_runs,
            stop_on_error=bool(args.stop_on_error),
            require_status_complete=bool(args.require_status_complete),
            rerun_error_turns=bool(args.rerun_error_turns),
            dry_run=bool(args.dry_run),
        )
    )


if __name__ == "__main__":
    main()
