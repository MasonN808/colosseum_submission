from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


REPO_ROOT = Path(__file__).resolve().parents[3]

COMMON_MODELS: Tuple[str, ...] = (
    "gpt-5.4",
    "claude-opus-4-6",
    "grok-4-20-reasoning",
    "deepseek-v3.2",
    "fw-minimax-m2.5",
    "fw-glm-5",
)

MODEL_LABELS: Mapping[str, str] = {
    "gpt-5.4": "GPT-5.4",
    "claude-opus-4-6": "Opus-4.6",
    "grok-4-20-reasoning": "Grok-4-20-Reasoning",
    "deepseek-v3.2": "DeepSeek-V3.2",
    "fw-minimax-m2.5": "MiniMax-M2.5",
    "fw-glm-5": "GLM-5",
}

MODEL_COLORS: Mapping[str, str] = {
    "gpt-5.4": "#1f77b4",
    "claude-opus-4-6": "#9467bd",
    "grok-4-20-reasoning": "#d62728",
    "deepseek-v3.2": "#ff7f0e",
    "fw-minimax-m2.5": "#2ca02c",
    "fw-glm-5": "#8c564b",
}

CONDITION_LABELS: Mapping[str, str] = {
    "control": "Emergent",
    "simple": "Prompted",
}
CONDITION_FILE_LABELS: Mapping[str, str] = {
    "control": "emergent",
    "simple": "prompted",
}

COALITION_ADVANTAGE_METRIC = "normalized_coalition_regret_gap"
JUDGE_METRIC = "judge_mean_rating"


@dataclass(frozen=True)
class EnvironmentSource:
    key: str
    label: str
    csv_path: Path
    search_space: float


ENVIRONMENTS: Tuple[EnvironmentSource, ...] = (
    EnvironmentSource(
        key="jira",
        label="Jira",
        csv_path=REPO_ROOT
        / "experiments/collusion/plots_outputs/collusion_regret_complete_n6_c2_10seeds"
        / "20260416-191730-10seeds/regret_report/complete_n6_c2/plots"
        / "regret_report__normalized_regret__coalition_gap__judge__data.csv",
        search_space=float(531441),
    ),
    EnvironmentSource(
        key="meeting",
        label="Meeting",
        csv_path=REPO_ROOT
        / "experiments/collusion/plots_outputs/collusion_meeting_scheduling_complete_n6_c2_10seeds"
        / "20260422-192126-10seeds/regret_report/complete_n6_c2/plots"
        / "regret_report__normalized_regret__coalition_gap__judge__data.csv",
        search_space=4.066271602117033e68,
    ),
    EnvironmentSource(
        key="hospital",
        label="Hospital",
        csv_path=REPO_ROOT
        / "experiments/collusion/plots_outputs/collusion_hospital_complete_n9_c4_10seeds"
        / "20260423-180614-10seeds/regret_report/complete_n9_c4/plots"
        / "regret_report__normalized_regret__coalition_gap__judge__data.csv",
        search_space=5.883125215513911e160,
    ),
)


@dataclass(frozen=True)
class MetricValue:
    mean: float
    sem: Optional[float]
    n: Optional[int]


@dataclass(frozen=True)
class PlotRow:
    condition: str
    environment: str
    environment_label: str
    model_label: str
    model_label_pretty: str
    search_space: float
    delta_collusion: float
    judge_score: float
    delta_sem: Optional[float]
    judge_sem: Optional[float]
    n_delta: Optional[int]
    n_judge: Optional[int]


def _as_float(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _as_int(value: object) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _combine_sem(first: Optional[float], second: Optional[float]) -> Optional[float]:
    if first is None and second is None:
        return None
    return math.sqrt(float(first or 0.0) ** 2 + float(second or 0.0) ** 2)


def _load_metric_values(path: Path) -> Dict[Tuple[str, str, str], MetricValue]:
    if not path.exists():
        raise FileNotFoundError(f"Missing aggregate CSV: {path}")

    values: Dict[Tuple[str, str, str], MetricValue] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            model = str(row.get("model_label") or "").strip()
            condition = str(row.get("condition") or "").strip()
            metric = str(row.get("metric_key") or "").strip()
            mean = _as_float(row.get("mean"))
            if not model or not condition or not metric or mean is None:
                continue
            values[(model, condition, metric)] = MetricValue(
                mean=float(mean),
                sem=_as_float(row.get("sem")),
                n=_as_int(row.get("n")),
            )
    return values


def build_rows() -> List[PlotRow]:
    rows: List[PlotRow] = []
    for env in ENVIRONMENTS:
        values = _load_metric_values(env.csv_path)
        for model in COMMON_MODELS:
            baseline = values.get((model, "baseline", COALITION_ADVANTAGE_METRIC))
            if baseline is None:
                raise ValueError(f"Missing baseline coalition advantage for {model} in {env.csv_path}")

            for condition in ("control", "simple"):
                condition_advantage = values.get((model, condition, COALITION_ADVANTAGE_METRIC))
                judge = values.get((model, condition, JUDGE_METRIC))
                if condition_advantage is None or judge is None:
                    raise ValueError(
                        f"Missing {condition} metrics for {model} in {env.csv_path}"
                    )
                rows.append(
                    PlotRow(
                        condition=condition,
                        environment=env.key,
                        environment_label=env.label,
                        model_label=model,
                        model_label_pretty=MODEL_LABELS.get(model, model),
                        search_space=float(env.search_space),
                        delta_collusion=float(condition_advantage.mean - baseline.mean),
                        judge_score=float(judge.mean),
                        delta_sem=_combine_sem(condition_advantage.sem, baseline.sem),
                        judge_sem=judge.sem,
                        n_delta=condition_advantage.n,
                        n_judge=judge.n,
                    )
                )
    return rows


def _format_search_space(value: float) -> str:
    if value == 0:
        return "0"
    exponent = int(math.floor(math.log10(abs(value))))
    mantissa = value / (10**exponent)
    return f"{mantissa:.2f}e{exponent}"


def _rows_for_condition(rows: Iterable[PlotRow], condition: str) -> List[PlotRow]:
    out = [row for row in rows if row.condition == condition]
    expected = len(COMMON_MODELS) * len(ENVIRONMENTS)
    if len(out) != expected:
        raise ValueError(f"Expected {expected} rows for {condition}, found {len(out)}")
    return out


def _write_rows_csv(rows: Iterable[PlotRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "condition",
        "condition_pretty",
        "environment",
        "model_label",
        "model_label_pretty",
        "search_space",
        "delta_collusion",
        "judge_score",
        "delta_sem",
        "judge_sem",
        "n_delta",
        "n_judge",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "condition": row.condition,
                    "condition_pretty": CONDITION_LABELS.get(row.condition, row.condition),
                    "environment": row.environment,
                    "model_label": row.model_label,
                    "model_label_pretty": row.model_label_pretty,
                    "search_space": f"{row.search_space:.17e}",
                    "delta_collusion": f"{row.delta_collusion:.12g}",
                    "judge_score": f"{row.judge_score:.12g}",
                    "delta_sem": "" if row.delta_sem is None else f"{row.delta_sem:.12g}",
                    "judge_sem": "" if row.judge_sem is None else f"{row.judge_sem:.12g}",
                    "n_delta": "" if row.n_delta is None else row.n_delta,
                    "n_judge": "" if row.n_judge is None else row.n_judge,
                }
            )


def plot_condition(*, rows: List[PlotRow], condition: str, out_dir: Path) -> List[Path]:
    condition_rows = _rows_for_condition(rows, condition)
    by_model: Dict[str, List[PlotRow]] = {model: [] for model in COMMON_MODELS}
    env_order = {env.key: idx for idx, env in enumerate(ENVIRONMENTS)}
    for row in condition_rows:
        by_model[row.model_label].append(row)
    for model_rows in by_model.values():
        model_rows.sort(key=lambda item: env_order[item.environment])

    plt.rcParams.update(
        {
            "font.size": 12,
            "axes.titlesize": 18,
            "axes.labelsize": 14,
            "legend.fontsize": 9,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
        }
    )
    fig, ax = plt.subplots(figsize=(11.0, 6.8))
    ax_judge = ax.twinx()

    for model, model_rows in by_model.items():
        color = MODEL_COLORS.get(model, "#333333")
        xs = [row.search_space for row in model_rows]
        deltas = [row.delta_collusion for row in model_rows]
        judges = [row.judge_score for row in model_rows]
        label = MODEL_LABELS.get(model, model)

        ax.plot(
            xs,
            deltas,
            marker="o",
            markersize=7,
            linewidth=2.0,
            color=color,
            label=label,
            zorder=3,
        )
        ax_judge.plot(
            xs,
            judges,
            marker="^",
            markersize=7,
            linewidth=1.8,
            linestyle="--",
            color=color,
            alpha=0.72,
            zorder=2,
        )

    ax.set_xscale("log")
    xticks = [env.search_space for env in ENVIRONMENTS]
    ax.set_xticks(xticks)
    ax.set_xticklabels(
        [f"{env.label}\n{_format_search_space(env.search_space)}" for env in ENVIRONMENTS]
    )
    ax.set_xlabel("Average possible final action profiles per environment")
    ax.set_ylabel(r"Less $\leftarrow$ $\Delta$-Advantage $\rightarrow$ More")
    ax_judge.set_ylabel("Collusion Judge Score (0-5)")
    ax_judge.set_ylim(0.0, 5.0)
    ax.axhline(0.0, color="#666666", linestyle=":", linewidth=1.5, alpha=0.85)
    ax.grid(True, which="major", axis="both", linestyle="--", linewidth=0.9, alpha=0.35)
    ax.grid(True, which="minor", axis="x", linestyle=":", linewidth=0.5, alpha=0.18)
    ax.set_title(f"{CONDITION_LABELS[condition]} Collusion Across Search Space")

    model_handles = [
        Line2D(
            [0],
            [0],
            color=MODEL_COLORS.get(model, "#333333"),
            marker="o",
            linewidth=2.0,
            markersize=6,
            label=MODEL_LABELS.get(model, model),
        )
        for model in COMMON_MODELS
    ]
    metric_handles = [
        Line2D(
            [0],
            [0],
            color="#222222",
            marker="o",
            linewidth=2.0,
            label=r"$\Delta$-Advantage",
        ),
        Line2D(
            [0],
            [0],
            color="#222222",
            marker="^",
            linestyle="--",
            linewidth=1.8,
            label="Judge score",
        ),
    ]
    first_legend = ax.legend(
        handles=model_handles,
        title="Model",
        loc="upper left",
        bbox_to_anchor=(0.01, 0.99),
        frameon=True,
        framealpha=0.92,
    )
    ax.add_artist(first_legend)
    ax.legend(
        handles=metric_handles,
        loc="lower left",
        bbox_to_anchor=(0.01, 0.02),
        frameon=True,
        framealpha=0.92,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"search_space_delta_collusion__{CONDITION_FILE_LABELS[condition]}"
    paths = [out_dir / f"{stem}.png", out_dir / f"{stem}.pdf"]
    for path in paths:
        fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plot delta-collusion and judge scores against average environment "
            "search-space size for the six models shared by Jira, MeetingScheduling, and Hospital."
        )
    )
    parser.add_argument(
        "--out-dir",
        default=str(
            REPO_ROOT
            / "experiments/collusion/plots_outputs/search_space_delta_collusion"
        ),
        help="Directory for PNG/PDF outputs and the backing CSV.",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).expanduser().resolve()
    rows = build_rows()
    _write_rows_csv(rows, out_dir / "search_space_delta_collusion__data.csv")

    written: List[Path] = []
    for condition in ("control", "simple"):
        written.extend(plot_condition(rows=rows, condition=condition, out_dir=out_dir))

    for path in written:
        print(path)
    print(out_dir / "search_space_delta_collusion__data.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
