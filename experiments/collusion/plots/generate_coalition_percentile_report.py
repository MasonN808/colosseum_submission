from __future__ import annotations

"""Generate reward-based same-size coalition percentile reports.

This report is intentionally additive: it reads existing run artifacts and writes
new CSV sidecars without modifying run data or existing report files.
"""

import argparse
import itertools
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from experiments.collusion.judge_paths import is_judge_dir_name
from experiments.collusion.plots.generate_regret_report import (
    _REGRET_REPORT_CONDITION_LABELS,
    _canonical_variant,
    _pretty_model_label,
)
from experiments.common.plotting.io_utils import (
    as_bool,
    as_float,
    as_int,
    ensure_dir,
    mean,
    safe_load_json,
    sem,
    write_csv,
)


logger = logging.getLogger(__name__)

RUN_DATA_BASENAME = "coalition_reward_percentiles__run_data.csv"
SUMMARY_BASENAME = "coalition_reward_percentiles__summary.csv"


@dataclass(frozen=True)
class CoalitionPercentileResult:
    actual_reward_advantage: float
    percentile_midrank: float
    z_score: Optional[float]
    null_mean: float
    null_std: float
    null_min: float
    null_max: float
    num_possible_coalitions: int


@dataclass(frozen=True)
class CandidateRun:
    run_dir: Path
    model_dir: Path
    run_id: str
    model_label: str
    provider: Optional[str]
    model: Optional[str]
    sweep_name: str
    topology: Optional[str]
    num_agents: Optional[int]
    colluder_count: Optional[int]
    seed: Optional[int]
    replica_index: int
    secret_channel_enabled: Optional[bool]
    prompt_variant: str
    secret_channel_count: int
    status: str
    colluders: Tuple[str, ...]
    agent_rewards: Dict[str, float]


def _iter_model_dirs(root: Path) -> Iterable[Path]:
    runs_dir = root / "runs"
    if not runs_dir.exists():
        return
    for model_dir in sorted(runs_dir.iterdir()):
        if model_dir.is_dir():
            yield model_dir


def _infer_sweep_name(root: Path) -> str:
    sweep_names: set[str] = set()
    for model_dir in _iter_model_dirs(root):
        for child in sorted(model_dir.iterdir()):
            if not child.is_dir():
                continue
            if is_judge_dir_name(child.name):
                continue
            if (child / "run_config.json").exists():
                continue
            sweep_names.add(child.name)
    if len(sweep_names) == 1:
        return next(iter(sweep_names))
    if not sweep_names:
        raise SystemExit(f"No sweep directories found under: {root / 'runs'}")
    raise SystemExit(
        "Multiple sweep directories found; pass --sweep-name. Options: "
        + ", ".join(sorted(sweep_names))
    )


def _iter_run_dirs(sweep_dir: Path) -> Iterable[Path]:
    for child in sorted(sweep_dir.iterdir()):
        if child.is_dir() and (child / "run_config.json").exists():
            yield child


def _split_filter_values(values: Optional[Sequence[str]]) -> Optional[set[str]]:
    if not values:
        return None
    out: set[str] = set()
    for value in values:
        for part in str(value).split(","):
            cleaned = part.strip()
            if cleaned:
                out.add(cleaned)
    return out or None


def _split_int_filter_values(values: Optional[Sequence[str]]) -> Optional[set[int]]:
    raw_values = _split_filter_values(values)
    if raw_values is None:
        return None
    out: set[int] = set()
    for value in raw_values:
        try:
            out.add(int(value))
        except Exception as exc:
            raise SystemExit(f"Invalid integer filter value: {value!r}") from exc
    return out or None


def _population_std(values: Sequence[float]) -> float:
    if not values:
        return float("nan")
    mu = float(sum(values) / len(values))
    return float((sum((float(v) - mu) ** 2 for v in values) / len(values)) ** 0.5)


def _coalition_reward_advantage(
    *, agent_rewards: Dict[str, float], coalition: Sequence[str]
) -> Optional[float]:
    coalition_set = {str(agent) for agent in coalition}
    if not coalition_set:
        return None
    if not coalition_set.issubset({str(agent) for agent in agent_rewards.keys()}):
        return None

    coalition_rewards = [
        float(reward)
        for agent, reward in agent_rewards.items()
        if str(agent) in coalition_set
    ]
    complement_rewards = [
        float(reward)
        for agent, reward in agent_rewards.items()
        if str(agent) not in coalition_set
    ]
    if not coalition_rewards or not complement_rewards:
        return None
    return float(mean(coalition_rewards) - mean(complement_rewards))


def _coalition_percentile_result(
    *, agent_rewards: Dict[str, float], colluders: Sequence[str]
) -> Optional[CoalitionPercentileResult]:
    colluder_tuple = tuple(str(agent) for agent in colluders)
    coalition_size = len(colluder_tuple)
    if len(set(colluder_tuple)) != coalition_size:
        return None
    agent_names = [str(agent) for agent in agent_rewards.keys()]
    if coalition_size <= 0 or coalition_size >= len(agent_names):
        return None

    actual = _coalition_reward_advantage(
        agent_rewards=agent_rewards, coalition=colluder_tuple
    )
    if actual is None or not math.isfinite(float(actual)):
        return None

    null_values: List[float] = []
    for coalition in itertools.combinations(agent_names, coalition_size):
        advantage = _coalition_reward_advantage(
            agent_rewards=agent_rewards, coalition=coalition
        )
        if advantage is None or not math.isfinite(float(advantage)):
            continue
        null_values.append(float(advantage))
    if not null_values:
        return None

    actual_f = float(actual)
    less = sum(1 for value in null_values if float(value) < actual_f)
    equal = sum(1 for value in null_values if float(value) == actual_f)
    percentile = 100.0 * (float(less) + 0.5 * float(equal)) / float(len(null_values))
    null_mean = float(mean(null_values))
    null_std = _population_std(null_values)
    z_score = None
    if math.isfinite(null_std) and null_std > 0.0:
        z_score = float((actual_f - null_mean) / null_std)

    return CoalitionPercentileResult(
        actual_reward_advantage=actual_f,
        percentile_midrank=float(percentile),
        z_score=z_score,
        null_mean=null_mean,
        null_std=float(null_std),
        null_min=float(min(null_values)),
        null_max=float(max(null_values)),
        num_possible_coalitions=int(len(null_values)),
    )


def _artifact_paths(*, run_dir: Path, prefer_repaired: bool) -> Tuple[Path, Path]:
    final_summary = run_dir / "final_summary.json"
    metrics = run_dir / "metrics.json"
    if prefer_repaired:
        repaired_summary = run_dir / "final_summary_repaired.json"
        repaired_metrics = run_dir / "metrics_repaired.json"
        if repaired_summary.exists():
            final_summary = repaired_summary
        if repaired_metrics.exists():
            metrics = repaired_metrics
    return final_summary, metrics


def _load_agent_rewards(summary: Dict[str, Any]) -> Optional[Dict[str, float]]:
    raw = summary.get("agent_rewards")
    if not isinstance(raw, dict):
        return None
    out: Dict[str, float] = {}
    for agent, reward in raw.items():
        value = as_float(reward)
        if value is None:
            continue
        out[str(agent)] = float(value)
    return out or None


def _load_candidate_run(
    *,
    run_dir: Path,
    model_dir: Path,
    sweep_name: str,
    prefer_repaired: bool,
) -> Optional[CandidateRun]:
    run_config = safe_load_json(run_dir / "run_config.json")
    if not isinstance(run_config, dict):
        logger.warning("Skipping %s: missing or invalid run_config.json", run_dir)
        return None

    final_summary_path, metrics_path = _artifact_paths(
        run_dir=run_dir, prefer_repaired=prefer_repaired
    )
    final_summary = safe_load_json(final_summary_path)
    if not isinstance(final_summary, dict):
        logger.warning("Skipping %s: missing or invalid final summary", run_dir)
        return None
    metrics = safe_load_json(metrics_path)
    if not isinstance(metrics, dict):
        metrics = {}

    agent_rewards = _load_agent_rewards(final_summary)
    if agent_rewards is None:
        logger.warning("Skipping %s: missing agent_rewards", run_dir)
        return None

    raw_colluders = run_config.get("colluders")
    if not isinstance(raw_colluders, list) or not raw_colluders:
        logger.warning("Skipping %s: missing colluders in run_config.json", run_dir)
        return None
    colluders = tuple(str(agent) for agent in raw_colluders if agent is not None)

    run_id = str(run_config.get("run_id") or run_dir.name)
    replica_index = as_int(run_config.get("replica_index"))
    if replica_index is None:
        match = re.search(r"__replica(\d+)$", run_id)
        replica_index = int(match.group(1)) if match else 0

    secret_channel_count = as_int(run_config.get("secret_channel_count"))
    if secret_channel_count is None:
        secret_channel_count = (
            1 if as_bool(run_config.get("secret_channel_enabled")) else 0
        )

    status = metrics.get("status", final_summary.get("status", "unknown"))

    return CandidateRun(
        run_dir=run_dir,
        model_dir=model_dir,
        run_id=run_id,
        model_label=str(run_config.get("model_label") or model_dir.name),
        provider=str(run_config.get("provider"))
        if run_config.get("provider") is not None
        else None,
        model=str(run_config.get("model"))
        if run_config.get("model") is not None
        else None,
        sweep_name=str(run_config.get("sweep") or sweep_name),
        topology=str(run_config.get("topology"))
        if run_config.get("topology") is not None
        else None,
        num_agents=as_int(run_config.get("num_agents")),
        colluder_count=as_int(run_config.get("colluder_count")),
        seed=as_int(run_config.get("seed")),
        replica_index=max(0, int(replica_index)),
        secret_channel_enabled=as_bool(run_config.get("secret_channel_enabled")),
        prompt_variant=_canonical_variant(
            run_config.get("prompt_variant") or "control"
        ),
        secret_channel_count=max(0, int(secret_channel_count)),
        status=str(status or "unknown"),
        colluders=colluders,
        agent_rewards=agent_rewards,
    )


def _status_is_complete(status: Any) -> bool:
    return str(status or "").strip().lower() == "complete"


def _filter_candidate_runs(
    rows: List[CandidateRun],
    *,
    topology: Optional[str],
    num_agents: Optional[int],
    colluder_count: Optional[int],
    model_labels: Optional[set[str]],
    replica_indexes: Optional[set[int]],
    require_complete: bool,
) -> List[CandidateRun]:
    out: List[CandidateRun] = []
    for row in rows:
        if topology is not None and str(row.topology) != str(topology):
            continue
        if num_agents is not None and int(row.num_agents or -1) != int(num_agents):
            continue
        if colluder_count is not None and int(row.colluder_count or -1) != int(
            colluder_count
        ):
            continue
        if model_labels is not None and str(row.model_label) not in model_labels:
            continue
        if (
            replica_indexes is not None
            and int(row.replica_index) not in replica_indexes
        ):
            continue
        if require_complete and not _status_is_complete(row.status):
            continue
        out.append(row)
    return out


def _uses_channel_count_conditions(rows: Sequence[CandidateRun]) -> bool:
    return any(int(row.secret_channel_count or 0) > 1 for row in rows)


def _condition_for_candidate_run(
    row: CandidateRun, *, channel_count_conditions: bool
) -> Optional[str]:
    if not channel_count_conditions:
        if row.secret_channel_enabled is True:
            return _canonical_variant(row.prompt_variant) or "control"
        return "baseline"

    count = int(row.secret_channel_count or 0)
    if count <= 0:
        return "baseline"
    variant = _canonical_variant(row.prompt_variant) or "control"
    if variant != "control":
        return None
    return f"sc{count}"


def _run_output_row(
    *, row: CandidateRun, result: CoalitionPercentileResult, condition: str
) -> Dict[str, Any]:
    return {
        "run_id": row.run_id,
        "model_label": row.model_label,
        "model_label_pretty": _pretty_model_label(row.model_label),
        "provider": row.provider,
        "model": row.model,
        "condition": condition,
        "condition_pretty": _REGRET_REPORT_CONDITION_LABELS.get(condition, condition),
        "sweep_name": row.sweep_name,
        "topology": row.topology,
        "seed": row.seed,
        "replica_index": row.replica_index,
        "colluders": json.dumps(list(row.colluders), ensure_ascii=False),
        "colluder_count": row.colluder_count,
        "num_agents": row.num_agents,
        "num_possible_coalitions": result.num_possible_coalitions,
        "actual_reward_advantage": result.actual_reward_advantage,
        "percentile_midrank": result.percentile_midrank,
        "z_score": result.z_score,
        "null_mean": result.null_mean,
        "null_std": result.null_std,
        "null_min": result.null_min,
        "null_max": result.null_max,
    }


def _summary_rows(run_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    metrics = ["percentile_midrank", "z_score", "actual_reward_advantage"]
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in run_rows:
        key = (str(row.get("model_label") or ""), str(row.get("condition") or ""))
        groups.setdefault(key, []).append(row)

    out: List[Dict[str, Any]] = []
    for model_label, condition in sorted(groups):
        rows = groups[(model_label, condition)]
        for metric_key in metrics:
            values = [
                float(row[metric_key])
                for row in rows
                if as_float(row.get(metric_key)) is not None
            ]
            out.append(
                {
                    "model_label": model_label,
                    "model_label_pretty": _pretty_model_label(model_label),
                    "condition": condition,
                    "condition_pretty": _REGRET_REPORT_CONDITION_LABELS.get(
                        condition, condition
                    ),
                    "metric_key": metric_key,
                    "mean": float(mean(values)) if values else None,
                    "sem": float(sem(values)) if values else None,
                    "n": int(len(values)),
                }
            )
    return out


def _fresh_report_paths(out_dir: Path) -> Tuple[Path, Path]:
    run_path = out_dir / RUN_DATA_BASENAME
    summary_path = out_dir / SUMMARY_BASENAME
    if not run_path.exists() and not summary_path.exists():
        return run_path, summary_path

    run_stem = Path(RUN_DATA_BASENAME).stem
    summary_stem = Path(SUMMARY_BASENAME).stem
    for idx in itertools.count(2):
        candidate_run = out_dir / f"{run_stem}__{idx}.csv"
        candidate_summary = out_dir / f"{summary_stem}__{idx}.csv"
        if not candidate_run.exists() and not candidate_summary.exists():
            return candidate_run, candidate_summary

    raise RuntimeError("unreachable")


def _default_out_dir(*, root: Path, sweep_name: str) -> Path:
    tag = root.parent.name
    timestamp = root.name
    return (
        Path("experiments/collusion/plots_outputs")
        / str(tag)
        / str(timestamp)
        / "coalition_reward_percentiles"
        / str(sweep_name)
    ).resolve()


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate reward-based private-coalition percentile CSVs from a "
            "collusion output root."
        )
    )
    parser.add_argument(
        "--root",
        type=str,
        required=True,
        help="Path like experiments/collusion/outputs/<tag>/<timestamp>.",
    )
    parser.add_argument(
        "--sweep-name",
        type=str,
        default=None,
        help="Sweep directory name under each model. If omitted, auto-infer when unique.",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help=(
            "Output directory. Defaults to "
            "experiments/collusion/plots_outputs/<tag>/<ts>/"
            "coalition_reward_percentiles/<sweep_name>."
        ),
    )
    parser.add_argument("--topology", type=str, default="complete")
    parser.add_argument("--num-agents", type=int, default=6)
    parser.add_argument("--colluder-count", type=int, default=2)
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help="Include runs where status != 'complete'.",
    )
    parser.add_argument(
        "--model-label",
        action="append",
        default=None,
        help="Restrict to one model label. May be repeated or comma-separated.",
    )
    parser.add_argument(
        "--replica-index",
        action="append",
        default=None,
        help="Restrict to one replica index. May be repeated or comma-separated.",
    )
    parser.add_argument(
        "--prefer-repaired",
        action="store_true",
        help="Prefer *_repaired.json artifacts when present.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    sweep_name = str(args.sweep_name) if args.sweep_name else _infer_sweep_name(root)
    out_dir = (
        Path(args.out_dir).expanduser().resolve()
        if args.out_dir
        else _default_out_dir(root=root, sweep_name=sweep_name)
    )
    ensure_dir(out_dir)

    candidates: List[CandidateRun] = []
    missing_sweeps: List[str] = []
    for model_dir in _iter_model_dirs(root):
        sweep_dir = model_dir / sweep_name
        if not sweep_dir.exists():
            missing_sweeps.append(model_dir.name)
            continue
        for run_dir in _iter_run_dirs(sweep_dir):
            row = _load_candidate_run(
                run_dir=run_dir,
                model_dir=model_dir,
                sweep_name=sweep_name,
                prefer_repaired=bool(args.prefer_repaired),
            )
            if row is not None:
                candidates.append(row)

    if missing_sweeps:
        logger.warning(
            "Missing sweep %s for models: %s",
            sweep_name,
            ", ".join(missing_sweeps),
        )
    if not candidates:
        logger.warning("No candidate runs with agent_rewards found.")

    candidates = _filter_candidate_runs(
        candidates,
        topology=str(args.topology) if args.topology else None,
        num_agents=int(args.num_agents) if args.num_agents else None,
        colluder_count=int(args.colluder_count) if args.colluder_count else None,
        model_labels=_split_filter_values(args.model_label),
        replica_indexes=_split_int_filter_values(args.replica_index),
        require_complete=not bool(args.include_incomplete),
    )

    channel_count_conditions = _uses_channel_count_conditions(candidates)
    run_rows: List[Dict[str, Any]] = []
    skipped_percentile = 0
    for candidate in candidates:
        condition = _condition_for_candidate_run(
            candidate, channel_count_conditions=channel_count_conditions
        )
        if condition is None:
            continue
        result = _coalition_percentile_result(
            agent_rewards=candidate.agent_rewards,
            colluders=candidate.colluders,
        )
        if result is None:
            skipped_percentile += 1
            logger.warning(
                "Skipping %s: failed percentile computation", candidate.run_dir
            )
            continue
        run_rows.append(
            _run_output_row(row=candidate, result=result, condition=condition)
        )

    if skipped_percentile:
        logger.warning(
            "Skipped %d runs with invalid coalition percentile inputs.",
            skipped_percentile,
        )

    run_csv, summary_csv = _fresh_report_paths(out_dir)
    write_csv(run_csv, run_rows)
    write_csv(summary_csv, _summary_rows(run_rows))
    logger.info("Wrote run CSV: %s", run_csv)
    logger.info("Wrote summary CSV: %s", summary_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
