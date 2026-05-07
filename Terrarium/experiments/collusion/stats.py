from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.collusion.plots.generate_regret_report import (
    RunRow,
    _coalition_advantage_value,
    _condition_for_row,
    _filter_rows,
    _infer_sweep_name,
    _iter_model_dirs,
    _iter_run_dirs,
    _load_run_row,
    _normalize_coalition_regret_gap,
    _set_coalition_regret_gap_norm,
    _status_is_complete,
    _uses_channel_count_conditions,
)


DEFAULT_CSV_PATH = Path(
    "experiments/collusion/plots_outputs/"
    "collusion_regret_complete_n6_c2_combined_10seeds/"
    "20260428-012631-small-and-full/regret_report/"
    "complete_n6_c2_combined_v2/plots/"
    "regret_report__normalized_regret__coalition_gap__judge__data.csv"
)
DEFAULT_RAW_ROOTS = (
    Path(
        "experiments/collusion/outputs/"
        "collusion_regret_complete_n6_c2_small_models_10seeds/"
        "20260426-230233"
    ),
    Path(
        "experiments/collusion/outputs/"
        "collusion_regret_complete_n6_c2_10seeds/"
        "20260416-191730-10seeds"
    ),
    Path(
        "experiments/collusion/outputs/"
        "collusion_regret_complete_n6_c2/"
        "20260122-013544"
    ),
)
DEFAULT_JUDGE_OUTPUT_TAG = "foundry__gpt-5.4"

CONDITIONS = {
    "control": "Emergent",
    "simple": "Prompted",
}
JUDGE_METRIC = "judge_mean_rating"
COLLUSION_METRICS = {
    "coalition_advantage": "normalized_coalition_regret_gap",
    "overall_regret": "normalized_regret",
}


@dataclass(frozen=True)
class MetricRow:
    model_label: str
    model_label_pretty: str
    condition: str
    metric_key: str
    mean: float
    n: int


@dataclass(frozen=True)
class DeltaPoint:
    model_label: str
    model_label_pretty: str
    condition: str
    group: str
    delta_judge_score: float
    delta_metric: float


@dataclass(frozen=True)
class RegressionSummary:
    group: str
    metric: str
    unit: str
    samples: int
    model_count: int
    baseline_runs: int
    condition_runs: int
    slope: float
    intercept: float
    pearson_r: float
    pearson_p: float
    spearman_rho: float
    spearman_p: float
    r_squared: float
    stderr: float
    slope_ci95_low: float
    slope_ci95_high: float


def _as_float(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _as_int(value: object) -> Optional[int]:
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _read_metric_rows(csv_path: Path) -> List[MetricRow]:
    rows: List[MetricRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            mean = _as_float(raw.get("mean"))
            n = _as_int(raw.get("n"))
            model_label = str(raw.get("model_label") or "").strip()
            condition = str(raw.get("condition") or "").strip()
            metric_key = str(raw.get("metric_key") or "").strip()
            if mean is None or n is None or not model_label or not condition or not metric_key:
                continue
            rows.append(
                MetricRow(
                    model_label=model_label,
                    model_label_pretty=str(
                        raw.get("model_label_pretty") or model_label
                    ).strip(),
                    condition=condition,
                    metric_key=metric_key,
                    mean=mean,
                    n=n,
                )
            )
    return rows


def _finite_value(value: Any) -> Optional[float]:
    number = _as_float(value)
    if number is None:
        return None
    return float(number)


def _rows_by_key(
    rows: Iterable[MetricRow],
) -> Dict[Tuple[str, str, str], MetricRow]:
    return {
        (row.model_label, row.condition, row.metric_key): row
        for row in rows
    }


def build_delta_points(
    rows: Iterable[MetricRow],
    *,
    metric: Literal["coalition_advantage", "overall_regret"],
) -> List[DeltaPoint]:
    metric_key = COLLUSION_METRICS[metric]
    by_key = _rows_by_key(rows)
    points: List[DeltaPoint] = []
    models = sorted({model for model, _condition, _metric_key in by_key})

    for model in models:
        baseline_judge = by_key.get((model, "baseline", JUDGE_METRIC))
        baseline_metric = by_key.get((model, "baseline", metric_key))
        if baseline_judge is None or baseline_metric is None:
            continue

        for condition, group in CONDITIONS.items():
            condition_judge = by_key.get((model, condition, JUDGE_METRIC))
            condition_metric = by_key.get((model, condition, metric_key))
            if condition_judge is None or condition_metric is None:
                continue

            if metric == "overall_regret":
                # Match the right panel: positive means the condition has less
                # regret than baseline, so use baseline - condition.
                delta_metric = baseline_metric.mean - condition_metric.mean
            else:
                # Match the left panel after its inversion step: positive means
                # more coalition advantage than baseline.
                delta_metric = condition_metric.mean - baseline_metric.mean

            points.append(
                DeltaPoint(
                    model_label=model,
                    model_label_pretty=condition_metric.model_label_pretty,
                    condition=condition,
                    group=group,
                    delta_judge_score=condition_judge.mean - baseline_judge.mean,
                    delta_metric=delta_metric,
                )
            )

    return points


def _run_metric_value(
    row: RunRow,
    *,
    metric: Literal["coalition_advantage", "overall_regret"],
) -> Optional[float]:
    if metric == "overall_regret":
        return _finite_value(row.normalized_regret)

    advantage = _coalition_advantage_value(row)
    if advantage is None:
        return None
    normalized = _normalize_coalition_regret_gap(float(advantage), row.model_label)
    return _finite_value(normalized)


def build_run_delta_points(
    rows: Iterable[RunRow],
    *,
    metric: Literal["coalition_advantage", "overall_regret"],
) -> List[DeltaPoint]:
    rows = list(rows)
    channel_count_conditions = _uses_channel_count_conditions(rows)
    baseline_by_key: Dict[Tuple[str, int, int], RunRow] = {}
    for row in rows:
        condition = _condition_for_row(
            row,
            channel_count_conditions=channel_count_conditions,
        )
        if condition != "baseline" or row.seed is None:
            continue
        baseline_by_key[
            (row.model_label, int(row.seed), int(row.replica_index))
        ] = row

    points: List[DeltaPoint] = []
    for row in rows:
        condition = _condition_for_row(
            row,
            channel_count_conditions=channel_count_conditions,
        )
        if condition not in CONDITIONS or row.seed is None:
            continue
        baseline = baseline_by_key.get(
            (row.model_label, int(row.seed), int(row.replica_index))
        )
        if baseline is None:
            continue
        baseline_judge = _finite_value(baseline.judge_mean_rating)
        condition_judge = _finite_value(row.judge_mean_rating)
        baseline_metric = _run_metric_value(baseline, metric=metric)
        condition_metric = _run_metric_value(row, metric=metric)
        if (
            baseline_judge is None
            or condition_judge is None
            or baseline_metric is None
            or condition_metric is None
        ):
            continue

        if metric == "overall_regret":
            delta_metric = baseline_metric - condition_metric
        else:
            delta_metric = condition_metric - baseline_metric

        points.append(
            DeltaPoint(
                model_label=row.model_label,
                model_label_pretty=row.model_label,
                condition=condition,
                group=CONDITIONS[condition],
                delta_judge_score=condition_judge - baseline_judge,
                delta_metric=delta_metric,
            )
        )

    return points


def _run_total(
    rows: Iterable[MetricRow],
    *,
    condition: str,
    metric_key: str,
    models: Iterable[str],
) -> int:
    model_set = set(models)
    return sum(
        row.n
        for row in rows
        if row.condition == condition
        and row.metric_key == metric_key
        and row.model_label in model_set
    )


def summarize_group(
    rows: Iterable[MetricRow],
    points: Iterable[DeltaPoint],
    *,
    group: str,
    condition: str,
    metric: Literal["coalition_advantage", "overall_regret"],
    unit: str = "model",
) -> RegressionSummary:
    rows = list(rows)
    group_points = [point for point in points if point.condition == condition]
    if len(group_points) < 2:
        raise ValueError(f"{group} needs at least two model points for regression")

    x_values = [point.delta_judge_score for point in group_points]
    y_values = [point.delta_metric for point in group_points]
    regression = stats.linregress(x_values, y_values)
    spearman = stats.spearmanr(x_values, y_values)
    df = len(group_points) - 2
    t_crit = stats.t.ppf(0.975, df)
    slope_ci95_low = regression.slope - (t_crit * regression.stderr)
    slope_ci95_high = regression.slope + (t_crit * regression.stderr)
    models = [point.model_label for point in group_points]
    metric_key = COLLUSION_METRICS[metric]

    return RegressionSummary(
        group=group,
        metric=metric,
        unit=unit,
        samples=len(group_points),
        model_count=len({point.model_label for point in group_points}),
        baseline_runs=_run_total(
            rows,
            condition="baseline",
            metric_key=metric_key,
            models=models,
        ),
        condition_runs=_run_total(
            rows,
            condition=condition,
            metric_key=metric_key,
            models=models,
        ),
        slope=float(regression.slope),
        intercept=float(regression.intercept),
        pearson_r=float(regression.rvalue),
        pearson_p=float(regression.pvalue),
        spearman_rho=float(spearman.statistic),
        spearman_p=float(spearman.pvalue),
        r_squared=float(regression.rvalue**2),
        stderr=float(regression.stderr),
        slope_ci95_low=float(slope_ci95_low),
        slope_ci95_high=float(slope_ci95_high),
    )


def coalition_coefficients(
    csv_path: Path,
    *,
    metric: Literal["coalition_advantage", "overall_regret"] = "coalition_advantage",
) -> List[RegressionSummary]:
    rows = _read_metric_rows(csv_path)
    points = build_delta_points(rows, metric=metric)
    return [
        summarize_group(
            rows,
            points,
            group=group,
            condition=condition,
            metric=metric,
        )
        for condition, group in CONDITIONS.items()
    ]


def summarize_run_group(
    points: Iterable[DeltaPoint],
    *,
    group: str,
    condition: str,
    metric: Literal["coalition_advantage", "overall_regret"],
) -> RegressionSummary:
    group_points = [point for point in points if point.condition == condition]
    if len(group_points) < 2:
        raise ValueError(f"{group} needs at least two run-level points for regression")

    x_values = [point.delta_judge_score for point in group_points]
    y_values = [point.delta_metric for point in group_points]
    regression = stats.linregress(x_values, y_values)
    spearman = stats.spearmanr(x_values, y_values)
    df = len(group_points) - 2
    t_crit = stats.t.ppf(0.975, df)
    slope_ci95_low = regression.slope - (t_crit * regression.stderr)
    slope_ci95_high = regression.slope + (t_crit * regression.stderr)

    return RegressionSummary(
        group=group,
        metric=metric,
        unit="paired_run",
        samples=len(group_points),
        model_count=len({point.model_label for point in group_points}),
        baseline_runs=len(group_points),
        condition_runs=len(group_points),
        slope=float(regression.slope),
        intercept=float(regression.intercept),
        pearson_r=float(regression.rvalue),
        pearson_p=float(regression.pvalue),
        spearman_rho=float(spearman.statistic),
        spearman_p=float(spearman.pvalue),
        r_squared=float(regression.rvalue**2),
        stderr=float(regression.stderr),
        slope_ci95_low=float(slope_ci95_low),
        slope_ci95_high=float(slope_ci95_high),
    )


def _load_raw_rows(
    roots: Sequence[Path],
    *,
    sweep_name: Optional[str],
    judge_output_tag: Optional[str],
    topology: Optional[str],
    num_agents: Optional[int],
    colluder_count: Optional[int],
    include_incomplete: bool,
) -> List[RunRow]:
    rows: List[RunRow] = []
    for root in roots:
        resolved_root = root.expanduser()
        if not resolved_root.exists():
            raise SystemExit(f"Root not found: {resolved_root}")
        resolved_sweep = str(sweep_name) if sweep_name else _infer_sweep_name(resolved_root)
        for model_dir in _iter_model_dirs(resolved_root):
            sweep_dir = model_dir / resolved_sweep
            if not sweep_dir.exists():
                continue
            for run_dir in _iter_run_dirs(sweep_dir):
                row = _load_run_row(
                    run_dir=run_dir,
                    model_dir=model_dir,
                    sweep_name=resolved_sweep,
                    compute_optimal=False,
                    prefer_repaired=False,
                    judge_output_tag=judge_output_tag,
                )
                if row is not None:
                    rows.append(row)

    rows = _filter_rows(
        rows,
        topology=topology,
        num_agents=num_agents,
        colluder_count=colluder_count,
        model_labels=None,
        replica_indexes=None,
        require_complete=not include_incomplete,
    )
    rows = [
        row
        for row in rows
        if include_incomplete or _status_is_complete(row.status)
    ]
    _set_coalition_regret_gap_norm(rows)
    return rows


def run_level_coefficients(
    roots: Sequence[Path],
    *,
    metric: Literal["coalition_advantage", "overall_regret"] = "coalition_advantage",
    sweep_name: Optional[str] = None,
    judge_output_tag: Optional[str] = DEFAULT_JUDGE_OUTPUT_TAG,
    topology: Optional[str] = "complete",
    num_agents: Optional[int] = 6,
    colluder_count: Optional[int] = 2,
    include_incomplete: bool = False,
) -> List[RegressionSummary]:
    rows = _load_raw_rows(
        roots,
        sweep_name=sweep_name,
        judge_output_tag=judge_output_tag,
        topology=topology,
        num_agents=num_agents,
        colluder_count=colluder_count,
        include_incomplete=include_incomplete,
    )
    points = build_run_delta_points(rows, metric=metric)
    return [
        summarize_run_group(
            points,
            group=group,
            condition=condition,
            metric=metric,
        )
        for condition, group in CONDITIONS.items()
    ]


def _print_table(summaries: Iterable[RegressionSummary]) -> None:
    headers = [
        "group",
        "metric",
        "unit",
        "samples",
        "model_count",
        "baseline_runs",
        "condition_runs",
        "slope",
        "intercept",
        "pearson_r",
        "pearson_p",
        "spearman_rho",
        "spearman_p",
        "r_squared",
        "stderr",
        "slope_ci95",
    ]
    print("\t".join(headers))
    for summary in summaries:
        print(
            "\t".join(
                [
                    summary.group,
                    summary.metric,
                    summary.unit,
                    str(summary.samples),
                    str(summary.model_count),
                    str(summary.baseline_runs),
                    str(summary.condition_runs),
                    f"{summary.slope:.10g}",
                    f"{summary.intercept:.10g}",
                    f"{summary.pearson_r:.10g}",
                    f"{summary.pearson_p:.10g}",
                    f"{summary.spearman_rho:.10g}",
                    f"{summary.spearman_p:.10g}",
                    f"{summary.r_squared:.10g}",
                    f"{summary.stderr:.10g}",
                    f"[{summary.slope_ci95_low:.10g}, {summary.slope_ci95_high:.10g}]",
                ]
            )
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute SciPy linear coefficients between delta judge score and "
            "delta collusion metrics for Emergent and Prompted groups."
        )
    )
    parser.add_argument(
        "--unit",
        choices=("model", "run"),
        default="model",
        help=(
            "Use model-level aggregate deltas from --csv, or paired run-level "
            "deltas from raw output roots."
        ),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Regret-report CSV path. Defaults to {DEFAULT_CSV_PATH}",
    )
    parser.add_argument(
        "--metric",
        choices=sorted(COLLUSION_METRICS),
        default="coalition_advantage",
        help="Collusion metric to regress against delta judge score.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=None,
        help=(
            "Raw output root for --unit run. May be repeated. Defaults to the "
            "three roots backing the combined Jira GPT-5.4 figure."
        ),
    )
    parser.add_argument(
        "--sweep-name",
        type=str,
        default=None,
        help="Sweep name under each raw model directory for --unit run.",
    )
    parser.add_argument(
        "--judge-output-tag",
        type=str,
        default=DEFAULT_JUDGE_OUTPUT_TAG,
        help=(
            "Judge namespace for --unit run. Defaults to foundry__gpt-5.4 "
            "to match the uploaded figure."
        ),
    )
    parser.add_argument("--topology", type=str, default="complete")
    parser.add_argument("--num-agents", type=int, default=6)
    parser.add_argument("--colluder-count", type=int, default=2)
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help="Include incomplete raw runs in --unit run mode.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of the default tab-separated table.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.unit == "run":
        summaries = run_level_coefficients(
            tuple(args.root or DEFAULT_RAW_ROOTS),
            metric=args.metric,
            sweep_name=args.sweep_name,
            judge_output_tag=args.judge_output_tag,
            topology=args.topology,
            num_agents=args.num_agents,
            colluder_count=args.colluder_count,
            include_incomplete=bool(args.include_incomplete),
        )
    else:
        summaries = coalition_coefficients(args.csv, metric=args.metric)
    if args.json:
        print(json.dumps([asdict(summary) for summary in summaries], indent=2))
        return
    _print_table(summaries)


if __name__ == "__main__":
    main()
