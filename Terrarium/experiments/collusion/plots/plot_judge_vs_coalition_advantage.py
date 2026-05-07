from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from matplotlib.patches import Ellipse, FancyBboxPatch
from matplotlib.lines import Line2D
from matplotlib.transforms import Bbox

from experiments.collusion.plots.generate_regret_report import (
    _LOGO_DIR,
    _LOGO_FILES,
    _logo_key_for_model,
)
from experiments.collusion.plots.common import (
    compact_environment_label,
    compact_judge_label,
    compact_legacy_plot_header,
)


CONDITION_ORDER = ["baseline", "control", "simple", "sc1", "sc2", "sc3"]
CONDITION_LABELS = {
    "baseline": "Control",
    "control": "Emergent",
    "simple": "Prompted",
    "sc1": "Emergent (1SC)",
    "sc2": "Emergent (2SC)",
    "sc3": "Emergent (3SC)",
}
CONDITION_COLORS = {
    "baseline": "#264653",
    "control": "#2a9d8f",
    "simple": "#8ab17d",
    "sc1": "#2a9d8f",
    "sc2": "#e9c46a",
    "sc3": "#f4a261",
}
CONDITION_MARKERS = {
    "baseline": "o",
    "control": "s",
    "simple": "^",
    "sc1": "s",
    "sc2": "D",
    "sc3": "P",
}
POINT_STYLES = ("condition", "condition-model-size", "model-size", "model-family")
POINT_STYLE_ALIASES = {
    "condition": "condition",
    "conditions": "condition",
    "condition-size": "condition-model-size",
    "condition_size": "condition-model-size",
    "condition-model-size": "condition-model-size",
    "condition_model_size": "condition-model-size",
    "condition-size-by-model": "condition-model-size",
    "condition_size_by_model": "condition-model-size",
    "size": "model-size",
    "model-size": "model-size",
    "model_size": "model-size",
    "family": "model-family",
    "model-family": "model-family",
    "model_family": "model-family",
}
MODEL_SIZE_ORDER = ("big", "small")
MODEL_SIZE_LABELS = {"big": "Big", "small": "Small"}
MODEL_SIZE_COLORS = {
    "big": CONDITION_COLORS["control"],
    "small": CONDITION_COLORS["simple"],
}
MODEL_FAMILY_ORDER = (
    "openai",
    "anthropic",
    "gemini",
    "deepseek",
    "moonshot",
    "glm",
    "minimax",
    "grok",
    "unknown",
)
CONDITION_MARKER_AREA = 150.0
MODEL_SIZE_MARKER_AREAS = {"big": 230.0, "small": 95.0}
LOGO_MARKER_TARGET_PX = 22.0
FALLBACK_FAMILY_MARKER_AREA = 115.0
STROKE_SCALE = 1.25
GRID_LINEWIDTH_SCALE = 1.75
GRID_ALPHA = 0.55
REFERENCE_LINEWIDTH_SCALE = 3.05
REFERENCE_ALPHA = 0.92
REFERENCE_ZORDER = 2.6
TREND_LINE_ALPHA = 0.72
TREND_LINE_DARKEN_FACTOR = 0.55
DEFAULT_FIGSIZE = (12.0, 7.0)
PORTRAIT_FIGSIZE = (7.0, 12.0)
DELTA_AXIS_PAD = 0.04
DELTA_AXIS_MIN_PAD = 0.012
STEPPED_AXIS_PAD_FRACTION = 0.10

X_METRIC = "normalized_coalition_regret_gap"
DEFAULT_DELTA_COLLUSION_METRIC = X_METRIC
CONTROL_MINUS_CONDITION_X_LABEL = "Control - Condition Coalition Advantage"
DELTA_COLLUSION_LABEL = r"$\Delta$-Advantage"
DELTA_JUDGE_SCORE_LABEL = r"$\Delta$-Judge-Score"
MORE_COLLUSION_LABEL = "More"
LESS_COLLUSION_LABEL = "Less"
DELTA_REGRET_LABEL = r"$\Delta$-Regret"
LESS_REGRET_LABEL = "Less"
MORE_REGRET_LABEL = "More"
COALITION_ADVANTAGE_LABEL = "Coalition Advantage (0-1)"
LESS_ADVANTAGE_LABEL = "Less"
MORE_ADVANTAGE_LABEL = "More"
COALITION_ADVANTAGE_RANGE = (0.0, 1.0)
DELTA_COLLUSION_METRIC_ALIASES = {
    "coalition": X_METRIC,
    "coalition_advantage": X_METRIC,
    "coalition_gap": X_METRIC,
    "normalized_coalition_regret_gap": X_METRIC,
    "overall_regret": "normalized_regret",
    "regret": "normalized_regret",
    "normalized_regret": "normalized_regret",
}
DELTA_COLLUSION_METRIC_LABELS = {
    X_METRIC: "Coalition Advantage",
    "normalized_regret": "Overall Regret",
}
DELTA_COLLUSION_METRIC_NAME_PARTS = {
    X_METRIC: "coalition_advantage",
    "normalized_regret": "overall_regret",
}
DEFAULT_Y_METRIC = "judge_mean_rating"
Y_METRIC_ALIASES = {
    "judge": "judge_mean_rating",
    "judge_mean_rating": "judge_mean_rating",
    "regret": "normalized_regret",
    "overall_regret": "normalized_regret",
    "normalized_regret": "normalized_regret",
}
Y_METRIC_LABELS = {
    "judge_mean_rating": "Collusion Judge Score (0-5)",
    "normalized_regret": "Overall Regret (0-1)",
}
Y_METRIC_RANGES = {
    "judge_mean_rating": (0.0, 5.0),
    "normalized_regret": (0.0, 1.0),
}


@dataclass(frozen=True)
class MetricValue:
    mean: float
    sem: Optional[float]


@dataclass(frozen=True)
class ScatterPoint:
    model_label: str
    model_pretty: str
    condition: str
    x: MetricValue
    y: MetricValue


@dataclass(frozen=True)
class PlacedLabel:
    text: plt.Text
    anchor: Tuple[float, float]


@dataclass(frozen=True)
class TrendLineGroup:
    key: str
    color: str
    points: Tuple[ScatterPoint, ...]


def _delta_collusion_axis_label(
    *,
    invert_delta_collusion: bool,
    delta_collusion_metric: str = DEFAULT_DELTA_COLLUSION_METRIC,
    metric_label: Optional[str] = None,
) -> str:
    delta_collusion_metric = _normalize_delta_collusion_metric(delta_collusion_metric)
    if delta_collusion_metric == "normalized_regret":
        left_label = MORE_REGRET_LABEL
        right_label = LESS_REGRET_LABEL
        delta_label = DELTA_REGRET_LABEL
    else:
        left_label = (
            LESS_ADVANTAGE_LABEL if invert_delta_collusion else MORE_ADVANTAGE_LABEL
        )
        right_label = (
            MORE_ADVANTAGE_LABEL if invert_delta_collusion else LESS_ADVANTAGE_LABEL
        )
        delta_label = DELTA_COLLUSION_LABEL
    if metric_label:
        delta_label = rf"{delta_label} ({metric_label})"
    return (
        rf"{left_label} $\leftarrow$ "
        rf"{delta_label} "
        rf"$\rightarrow$ {right_label}"
    )


def _coalition_advantage_axis_label() -> str:
    return (
        rf"{LESS_ADVANTAGE_LABEL} $\leftarrow$ "
        rf"{COALITION_ADVANTAGE_LABEL} "
        rf"$\rightarrow$ {MORE_ADVANTAGE_LABEL}"
    )


def _delta_judge_score_axis_label() -> str:
    return (
        rf"{LESS_COLLUSION_LABEL} $\leftarrow$ "
        rf"{DELTA_JUDGE_SCORE_LABEL} "
        rf"$\rightarrow$ {MORE_COLLUSION_LABEL}"
    )


def _legend_location(
    legend_loc: Optional[str], *, control_minus_condition_x: bool
) -> str:
    if legend_loc:
        return legend_loc
    return "upper right" if control_minus_condition_x else "upper left"


def _as_float(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _normalize_y_metric(value: str) -> str:
    key = str(value or "").strip().lower().replace("-", "_")
    metric = Y_METRIC_ALIASES.get(key)
    if metric is None:
        choices = ", ".join(sorted(Y_METRIC_ALIASES))
        raise SystemExit(f"Unsupported y metric {value!r}. Expected one of: {choices}")
    return metric


def _normalize_delta_collusion_metric(value: str) -> str:
    key = str(value or "").strip().lower().replace("-", "_")
    metric = DELTA_COLLUSION_METRIC_ALIASES.get(key)
    if metric is None:
        choices = ", ".join(sorted(DELTA_COLLUSION_METRIC_ALIASES))
        raise SystemExit(
            f"Unsupported delta collusion metric {value!r}. Expected one of: {choices}"
        )
    return metric


def _delta_collusion_metric_label(metric: str) -> str:
    metric = _normalize_delta_collusion_metric(metric)
    return DELTA_COLLUSION_METRIC_LABELS.get(
        metric, metric.replace("_", " ").title()
    )


def _delta_collusion_metric_name_part(metric: str) -> str:
    metric = _normalize_delta_collusion_metric(metric)
    return DELTA_COLLUSION_METRIC_NAME_PARTS.get(metric, metric.replace("_", ""))


def _parse_figsize(value: Optional[str]) -> Optional[Tuple[float, float]]:
    if value is None:
        return None
    raw = str(value).strip().lower().replace(",", "x")
    parts = [part.strip() for part in raw.split("x") if part.strip()]
    if len(parts) != 2:
        raise SystemExit("--figsize must use WIDTHxHEIGHT, for example 7x12")
    try:
        width, height = (float(parts[0]), float(parts[1]))
    except ValueError as exc:
        raise SystemExit("--figsize values must be numeric") from exc
    if width <= 0.0 or height <= 0.0:
        raise SystemExit("--figsize values must be greater than 0")
    return width, height


def _parse_axis_limits(
    value: Optional[str], *, option_name: str
) -> Optional[Tuple[float, float]]:
    if value is None:
        return None
    raw = str(value).strip().replace(":", ",")
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise SystemExit(f"{option_name} must use MIN,MAX, for example -0.5,3.5")
    try:
        lower, upper = (float(parts[0]), float(parts[1]))
    except ValueError as exc:
        raise SystemExit(f"{option_name} values must be numeric") from exc
    if not math.isfinite(lower) or not math.isfinite(upper):
        raise SystemExit(f"{option_name} values must be finite")
    if lower >= upper:
        raise SystemExit(f"{option_name} minimum must be less than maximum")
    return lower, upper


def _normalize_point_style(value: str) -> str:
    key = str(value or "").strip().lower()
    point_style = POINT_STYLE_ALIASES.get(key)
    if point_style is None:
        choices = ", ".join(POINT_STYLES)
        raise SystemExit(f"Unsupported point style {value!r}. Expected one of: {choices}")
    return point_style


def _model_size_key(model_label: str, model_pretty: Optional[str] = None) -> str:
    haystack = " ".join([str(model_label or ""), str(model_pretty or "")]).lower()
    if "grok-4-1-fast" in haystack:
        return "small"
    if re.search(r"(?<![a-z0-9])(mini|nano|flash|haiku)(?![a-z0-9])", haystack):
        return "small"
    return "big"


def _model_family_key(
    model_label: str, model_pretty: Optional[str] = None
) -> Optional[str]:
    return _logo_key_for_model(model_label, None, model_pretty)


def _logo_path_for_model(
    model_label: str, model_pretty: Optional[str] = None
) -> Optional[Path]:
    key = _model_family_key(model_label, model_pretty)
    if not key:
        return None
    filename = _LOGO_FILES.get(key)
    if not filename:
        return None
    path = _LOGO_DIR / filename
    return path if path.exists() else None


def _output_name_for_point_style(base_name: str, point_style: str) -> str:
    point_style = _normalize_point_style(point_style)
    if point_style == "condition":
        return base_name
    path = Path(base_name)
    suffix = point_style.replace("-", "_")
    return f"{path.stem}__{suffix}{path.suffix}"


def _default_output_name(
    y_metric: str,
    *,
    control_minus_condition_x: bool = False,
    judge_x_axis: bool = False,
    invert_delta_collusion: bool = False,
    point_style: str = "condition",
    delta_collusion_metric: str = DEFAULT_DELTA_COLLUSION_METRIC,
    delta_judge_score: bool = False,
) -> str:
    point_style = _normalize_point_style(point_style)
    delta_metric_part = _delta_collusion_metric_name_part(delta_collusion_metric)
    judge_part = "delta_judge" if delta_judge_score else "judge"
    if invert_delta_collusion and judge_x_axis and control_minus_condition_x:
        if y_metric == "normalized_regret":
            return _output_name_for_point_style(
                f"inverted_delta_collusion_{delta_metric_part}_vs_regret_scatter.png",
                point_style,
            )
        return _output_name_for_point_style(
            f"inverted_delta_collusion_{delta_metric_part}_vs_{judge_part}_scatter.png",
            point_style,
        )
    if invert_delta_collusion and control_minus_condition_x:
        if y_metric == "normalized_regret":
            return _output_name_for_point_style(
                f"regret_vs_inverted_delta_collusion_{delta_metric_part}_scatter.png",
                point_style,
            )
        return _output_name_for_point_style(
            f"{judge_part}_vs_inverted_delta_collusion_{delta_metric_part}_scatter.png",
            point_style,
        )
    if judge_x_axis and control_minus_condition_x:
        if y_metric == "normalized_regret":
            return _output_name_for_point_style(
                f"control_minus_condition_{delta_metric_part}_vs_regret_scatter.png",
                point_style,
            )
        return _output_name_for_point_style(
            f"control_minus_condition_{delta_metric_part}_vs_{judge_part}_scatter.png",
            point_style,
        )
    if judge_x_axis:
        if y_metric == "normalized_regret":
            return _output_name_for_point_style(
                "coalition_advantage_vs_regret_scatter.png", point_style
            )
        return _output_name_for_point_style(
            f"coalition_advantage_vs_{judge_part}_scatter.png", point_style
        )
    if control_minus_condition_x:
        if y_metric == "normalized_regret":
            return _output_name_for_point_style(
                f"regret_vs_control_minus_condition_{delta_metric_part}_scatter.png",
                point_style,
            )
        if delta_judge_score:
            return _output_name_for_point_style(
                f"delta_judge_vs_control_minus_condition_{delta_metric_part}_scatter.png",
                point_style,
            )
        return _output_name_for_point_style(
            f"judge_vs_control_minus_condition_{delta_metric_part}_scatter.png",
            point_style,
        )
    if y_metric == "normalized_regret":
        return _output_name_for_point_style(
            "regret_vs_coalition_advantage_scatter.png", point_style
        )
    if delta_judge_score:
        return _output_name_for_point_style(
            "delta_judge_vs_coalition_advantage_scatter.png", point_style
        )
    return _output_name_for_point_style(
        "judge_vs_coalition_advantage_scatter.png", point_style
    )


def _y_axis_limits(
    points: List[ScatterPoint],
    *,
    y_metric: str,
    y_step_limits: Optional[float] = None,
    delta_judge_score: bool = False,
    symmetric_delta_axis: bool = False,
) -> Tuple[float, float]:
    if delta_judge_score:
        values = [p.y.mean for p in points]
        if symmetric_delta_axis:
            return _symmetric_delta_axis_limits(values, step=y_step_limits)
        if y_step_limits is not None:
            return _stepped_delta_axis_limits(values, step=y_step_limits)
        return _unbounded_axis_limits(
            [*values, 0.0],
            pad=DELTA_AXIS_PAD,
            min_pad=DELTA_AXIS_MIN_PAD,
        )

    metric_range = Y_METRIC_RANGES.get(y_metric)
    if y_step_limits is not None and metric_range is not None:
        return _stepped_axis_limits(
            (p.y.mean for p in points),
            step=y_step_limits,
            floor=metric_range[0],
            ceil=metric_range[1],
        )
    if metric_range is not None:
        return metric_range
    return _axis_limits(
        (p.y.mean for p in points),
        floor=0.0,
        ceil=1.0,
        pad=0.12,
        min_pad=0.1,
    )


def _x_axis_limits(
    points: List[ScatterPoint],
    *,
    x_step_limits: Optional[float],
    control_minus_condition_x: bool = False,
    symmetric_delta_axis: bool = False,
) -> Tuple[float, float]:
    if control_minus_condition_x:
        return _delta_x_axis_limits(
            points,
            x_step_limits=x_step_limits,
            symmetric_delta_axis=symmetric_delta_axis,
        )
    if x_step_limits is None:
        xmin, xmax = _axis_limits(
            (p.x.mean for p in points),
            floor=COALITION_ADVANTAGE_RANGE[0],
            ceil=COALITION_ADVANTAGE_RANGE[1],
            pad=0.2,
            min_pad=0.065,
        )
        midpoint = sum(COALITION_ADVANTAGE_RANGE) / 2.0
        return min(xmin, midpoint), max(xmax, midpoint)
    return _stepped_axis_limits(
        (p.x.mean for p in points),
        step=x_step_limits,
        floor=COALITION_ADVANTAGE_RANGE[0],
        ceil=COALITION_ADVANTAGE_RANGE[1],
    )


def _delta_x_axis_limits(
    points: List[ScatterPoint],
    *,
    x_step_limits: Optional[float],
    symmetric_delta_axis: bool = False,
) -> Tuple[float, float]:
    values = [p.x.mean for p in points]
    if symmetric_delta_axis:
        return _symmetric_delta_axis_limits(values, step=x_step_limits)
    if x_step_limits is not None:
        return _stepped_delta_axis_limits(values, step=x_step_limits)

    return _unbounded_axis_limits(
        [*values, 0.0],
        pad=DELTA_AXIS_PAD,
        min_pad=DELTA_AXIS_MIN_PAD,
    )


def _read_points(
    csv_path: Path,
    *,
    y_metric: str,
    control_minus_condition_x: bool = False,
    delta_collusion_metric: str = DEFAULT_DELTA_COLLUSION_METRIC,
    delta_judge_score: bool = False,
) -> List[ScatterPoint]:
    x_metric = (
        _normalize_delta_collusion_metric(delta_collusion_metric)
        if control_minus_condition_x
        else X_METRIC
    )
    rows_by_key: Dict[Tuple[str, str, str], MetricValue] = {}
    model_pretty: Dict[str, str] = {}

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            metric = str(row.get("metric_key") or "").strip()
            if metric not in {x_metric, y_metric}:
                continue
            condition = str(row.get("condition") or "").strip()
            model = str(row.get("model_label") or "").strip()
            if not condition or not model:
                continue
            mean = _as_float(row.get("mean"))
            if mean is None:
                continue
            sem = _as_float(row.get("sem"))
            rows_by_key[(model, condition, metric)] = MetricValue(mean=mean, sem=sem)
            pretty = str(row.get("model_label_pretty") or "").strip()
            if pretty:
                model_pretty[model] = pretty

    if control_minus_condition_x:
        return _control_minus_condition_points(
            rows_by_key,
            model_pretty=model_pretty,
            y_metric=y_metric,
            delta_collusion_metric=x_metric,
            delta_judge_score=delta_judge_score,
        )

    points: List[ScatterPoint] = []
    models = sorted({key[0] for key in rows_by_key})
    for model in models:
        baseline_y = rows_by_key.get((model, "baseline", y_metric))
        for condition in CONDITION_ORDER:
            if delta_judge_score and condition == "baseline":
                continue
            x = rows_by_key.get((model, condition, x_metric))
            y = rows_by_key.get((model, condition, y_metric))
            if x is None or y is None:
                continue
            if delta_judge_score:
                if baseline_y is None:
                    continue
                y = _condition_minus_baseline_metric(y, baseline_y)
            points.append(
                ScatterPoint(
                    model_label=model,
                    model_pretty=model_pretty.get(model, model),
                    condition=condition,
                    x=x,
                    y=y,
                )
            )
    return points


def _invert_delta_points(
    points: List[ScatterPoint],
    *,
    invert_delta_collusion: bool,
    delta_collusion_metric: str = DEFAULT_DELTA_COLLUSION_METRIC,
) -> List[ScatterPoint]:
    if not invert_delta_collusion:
        return points
    delta_collusion_metric = _normalize_delta_collusion_metric(delta_collusion_metric)
    if delta_collusion_metric == "normalized_regret":
        # For regret deltas, baseline - condition is already positive when regret improves.
        return points

    return [
        ScatterPoint(
            model_label=point.model_label,
            model_pretty=point.model_pretty,
            condition=point.condition,
            x=MetricValue(mean=-point.x.mean, sem=point.x.sem),
            y=point.y,
        )
        for point in points
    ]


def _orient_points(points: List[ScatterPoint], *, judge_x_axis: bool) -> List[ScatterPoint]:
    if not judge_x_axis:
        return points

    return [
        ScatterPoint(
            model_label=point.model_label,
            model_pretty=point.model_pretty,
            condition=point.condition,
            x=point.y,
            y=point.x,
        )
        for point in points
    ]


def _control_minus_condition_points(
    rows_by_key: Dict[Tuple[str, str, str], MetricValue],
    *,
    model_pretty: Dict[str, str],
    y_metric: str,
    delta_collusion_metric: str,
    delta_judge_score: bool = False,
) -> List[ScatterPoint]:
    delta_collusion_metric = _normalize_delta_collusion_metric(delta_collusion_metric)
    points: List[ScatterPoint] = []
    models = sorted({key[0] for key in rows_by_key})
    for model in models:
        baseline_x = rows_by_key.get((model, "baseline", delta_collusion_metric))
        baseline_y = rows_by_key.get((model, "baseline", y_metric))
        if baseline_x is None:
            continue
        for condition in ("control", "simple"):
            condition_x = rows_by_key.get((model, condition, delta_collusion_metric))
            y = rows_by_key.get((model, condition, y_metric))
            if condition_x is None or y is None:
                continue
            if delta_judge_score:
                if baseline_y is None:
                    continue
                y = _condition_minus_baseline_metric(y, baseline_y)
            points.append(
                ScatterPoint(
                    model_label=model,
                    model_pretty=model_pretty.get(model, model),
                    condition=condition,
                    x=MetricValue(
                        mean=baseline_x.mean - condition_x.mean,
                        sem=_combined_sem(baseline_x.sem, condition_x.sem),
                    ),
                    y=y,
                )
            )
    return points


def _condition_minus_baseline_metric(
    condition: MetricValue, baseline: MetricValue
) -> MetricValue:
    return MetricValue(
        mean=condition.mean - baseline.mean,
        sem=_combined_sem(condition.sem, baseline.sem),
    )


def _combined_sem(first: Optional[float], second: Optional[float]) -> Optional[float]:
    if first is None and second is None:
        return None
    return math.sqrt(float(first or 0.0) ** 2 + float(second or 0.0) ** 2)


def _axis_limits(
    values: Iterable[float], *, floor: float, ceil: float, pad: float, min_pad: float
) -> Tuple[float, float]:
    vals = [float(v) for v in values if math.isfinite(float(v))]
    if not vals:
        return floor, ceil
    lo = min(vals)
    hi = max(vals)
    if lo == hi:
        lo -= min_pad
        hi += min_pad
    else:
        spread = hi - lo
        axis_pad = max(spread * pad, min_pad)
        lo -= axis_pad
        hi += axis_pad
    return max(floor, lo), min(ceil, hi)


def _unbounded_axis_limits(
    values: Iterable[float], *, pad: float, min_pad: float
) -> Tuple[float, float]:
    vals = [float(v) for v in values if math.isfinite(float(v))]
    if not vals:
        return -1.0, 1.0
    lo = min(vals)
    hi = max(vals)
    if lo == hi:
        lo -= min_pad
        hi += min_pad
    else:
        spread = hi - lo
        axis_pad = max(spread * pad, min_pad)
        lo -= axis_pad
        hi += axis_pad
    return lo, hi


def _stepped_axis_limits(
    values: Iterable[float], *, step: float, floor: float, ceil: float
) -> Tuple[float, float]:
    if step <= 0.0:
        raise SystemExit("--x-step-limits must be greater than 0")

    vals = [float(v) for v in values if math.isfinite(float(v))]
    if not vals:
        return floor, ceil

    lo = math.floor(min(vals) / step) * step
    hi = math.ceil(max(vals) / step) * step
    if math.isclose(lo, hi):
        lo -= step
        hi += step
    axis_pad = step * STEPPED_AXIS_PAD_FRACTION
    lo -= axis_pad
    hi += axis_pad
    return max(floor, lo), min(ceil, hi)


def _stepped_delta_axis_limits(
    values: Iterable[float], *, step: float
) -> Tuple[float, float]:
    if step <= 0.0:
        raise SystemExit("--x-step-limits must be greater than 0")

    vals = [float(v) for v in values if math.isfinite(float(v))]
    vals.append(0.0)
    lo = math.floor(min(vals) / step) * step
    hi = math.ceil(max(vals) / step) * step
    if math.isclose(lo, hi):
        lo -= step
        hi += step
    axis_pad = step * STEPPED_AXIS_PAD_FRACTION
    lo -= axis_pad
    hi += axis_pad
    return lo, hi


def _symmetric_delta_axis_limits(
    values: Iterable[float], *, step: Optional[float] = None
) -> Tuple[float, float]:
    vals = [abs(float(v)) for v in values if math.isfinite(float(v))]
    vals.append(0.0)
    radius = max(vals)
    if step is not None:
        if step <= 0.0:
            raise SystemExit("--x-step-limits must be greater than 0")
        radius = math.ceil(radius / step) * step
        if math.isclose(radius, 0.0):
            radius = step
        radius += step * STEPPED_AXIS_PAD_FRACTION
    else:
        radius = max(radius * (1.0 + DELTA_AXIS_PAD), DELTA_AXIS_MIN_PAD)
    return -radius, radius


def _axis_ticks_by_step(xmin: float, xmax: float, *, step: float) -> List[float]:
    start = math.ceil((xmin - 1e-9) / step) * step
    stop = math.floor((xmax + 1e-9) / step) * step
    count = int(round((stop - start) / step)) + 1
    return [start + (idx * step) for idx in range(max(0, count))]


def _auto_title(csv_path: Path) -> str:
    metadata_title = _title_from_csv_metadata(csv_path)
    if metadata_title:
        return metadata_title

    parts = csv_path.parts
    env_name = _environment_name_from_output_config(csv_path)
    if env_name:
        return _format_scatter_title(environment_name=env_name) or _format_environment_title(
            env_name
        )

    for idx, part in enumerate(parts):
        if part == "plots_outputs" and idx + 2 < len(parts):
            env = _environment_name_from_experiment(parts[idx + 1])
            return _format_scatter_title(environment_name=env) or _format_environment_title(
                env
            )
    return _format_scatter_title(environment_name=csv_path.parent.name) or _format_environment_title(
        csv_path.parent.name
    )


def _mathtext_bold(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    cleaned = cleaned.replace("\\", "")
    cleaned = cleaned.replace(" ", r"\ ")
    cleaned = cleaned.replace("-", r"{-}")
    return rf"$\mathbf{{{cleaned}}}$"


def _format_scatter_title(
    *, environment_name: Any = None, judge_label: Any = None
) -> Optional[str]:
    env = compact_environment_label(environment_name)
    judge = compact_judge_label(judge_label)
    if env and judge:
        return f"{_mathtext_bold(env)} with {_mathtext_bold(judge)} Judge"
    if env:
        return _mathtext_bold(env)
    if judge:
        return f"{_mathtext_bold(judge)} Judge"
    return None


def _scatter_title_from_legacy_header(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None

    environment_name = None
    judge_label = None
    for part in raw.split("|"):
        key, sep, val = part.strip().partition("=")
        if not sep:
            key, sep, val = part.strip().partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        val = val.strip()
        if key == "environment":
            environment_name = val
        elif key == "judge":
            judge_label = val

    return _format_scatter_title(
        environment_name=environment_name,
        judge_label=judge_label,
    ) or compact_legacy_plot_header(value)


def _title_from_csv_metadata(csv_path: Path) -> Optional[str]:
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle), None)
    except (OSError, StopIteration):
        return None

    if not row:
        return None

    env_name = str(row.get("environment_name") or "").strip()
    judge_label = str(row.get("judge_label") or "").strip()
    title = _format_scatter_title(
        environment_name=env_name,
        judge_label=judge_label,
    )
    if title:
        return title

    return _scatter_title_from_legacy_header(row.get("plot_header"))


def _environment_name_from_output_config(csv_path: Path) -> Optional[str]:
    parts = list(csv_path.resolve().parts)
    try:
        idx = parts.index("plots_outputs")
    except ValueError:
        return None
    if idx + 2 >= len(parts):
        return None

    output_root = Path(*parts[:idx], "outputs", parts[idx + 1], parts[idx + 2])
    if not output_root.exists():
        return None

    for config_path in output_root.glob("runs/*/*/*/run_config.json"):
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                config = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        env_name = _environment_name_from_config(config)
        if env_name:
            return env_name
    return None


def _environment_name_from_config(config: Dict[str, Any]) -> Optional[str]:
    env_label = str(config.get("environment_label") or "").strip()
    if env_label:
        return env_label

    env_name = str(config.get("environment_name") or "").strip()
    if env_name:
        return env_name

    env_cfg = config.get("environment_cfg")
    if not isinstance(env_cfg, dict):
        return None

    cfg_name = str(env_cfg.get("name") or "").strip()
    if cfg_name:
        return cfg_name

    import_path = str(env_cfg.get("import_path") or "").strip()
    if ":" in import_path:
        return import_path.rsplit(":", 1)[1].strip()
    return None


def _environment_name_from_experiment(experiment: str) -> str:
    lowered = str(experiment or "").lower()
    if "meeting_scheduling" in lowered:
        return "MeetingScheduling"
    if "jira" in lowered or lowered.startswith("collusion_regret"):
        return "JiraTicket"
    if "hospital" in lowered:
        return "Hospital"
    if "smart_grid" in lowered or "smartgrid" in lowered:
        return "SmartGrid"
    if "personal_assistant" in lowered:
        return "PersonalAssistant"
    return experiment


def _format_environment_title(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.rsplit(":", 1)[-1]
    raw = raw.rsplit(".", 1)[-1]
    raw = re.sub(r"(Choice)?Environment$", "", raw)
    raw = raw.replace("_", " ").replace("-", " ")
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw.title()


def _point_offset_to_data(
    fig: plt.Figure, ax: plt.Axes, xy: Tuple[float, float], offset: Tuple[float, float]
) -> Tuple[float, float]:
    x_px, y_px = ax.transData.transform(xy)
    dx_px = offset[0] * fig.dpi / 72.0
    dy_px = offset[1] * fig.dpi / 72.0
    return tuple(ax.transData.inverted().transform((x_px + dx_px, y_px + dy_px)))


def _expanded_bbox(bbox: Bbox, *, pad_x: float = 4.0, pad_y: float = 3.0) -> Bbox:
    return Bbox.from_extents(
        bbox.x0 - pad_x,
        bbox.y0 - pad_y,
        bbox.x1 + pad_x,
        bbox.y1 + pad_y,
    )


def _bbox_overlap_area(b1: Bbox, b2: Bbox) -> float:
    overlap_x = min(b1.x1, b2.x1) - max(b1.x0, b2.x0)
    overlap_y = min(b1.y1, b2.y1) - max(b1.y0, b2.y0)
    if overlap_x <= 0.0 or overlap_y <= 0.0:
        return 0.0
    return float(overlap_x * overlap_y)


def _bbox_overlap_shift(b1: Bbox, b2: Bbox, *, pad: float = 2.0) -> Optional[Tuple[float, float]]:
    overlap_x = min(b1.x1 - b2.x0, b2.x1 - b1.x0)
    overlap_y = min(b1.y1 - b2.y0, b2.y1 - b1.y0)
    if overlap_x <= 0 or overlap_y <= 0:
        return None

    c1x = (b1.x0 + b1.x1) / 2.0
    c1y = (b1.y0 + b1.y1) / 2.0
    c2x = (b2.x0 + b2.x1) / 2.0
    c2y = (b2.y0 + b2.y1) / 2.0
    if overlap_x < overlap_y:
        direction = -1.0 if c1x < c2x else 1.0
        return direction * (overlap_x / 2.0 + pad), 0.0
    direction = -1.0 if c1y < c2y else 1.0
    return 0.0, direction * (overlap_y / 2.0 + pad)


def _static_anchor_bboxes(
    ax: plt.Axes, anchors: Iterable[Tuple[float, float]], *, radius_px: float = 12.0
) -> List[Bbox]:
    boxes: List[Bbox] = []
    for xy in anchors:
        x_px, y_px = ax.transData.transform(xy)
        boxes.append(
            Bbox.from_extents(
                x_px - radius_px,
                y_px - radius_px,
                x_px + radius_px,
                y_px + radius_px,
            )
        )
    return boxes


def _candidate_label_offsets(
    condition: str, *, seed_text: str = "", offset_scale: float = 1.0
) -> List[Tuple[float, float]]:
    preferred = {
        "baseline": [(8, 7), (-8, 7), (8, -7)],
        "control": [(8, -7), (-8, -7), (8, 7)],
        "simple": [(7, 8), (-7, 8), (7, -8)],
        "sc1": [(8, -7), (-8, -7), (8, 7)],
        "sc2": [(8, 7), (-8, 7), (8, -7)],
        "sc3": [(7, 8), (-7, 8), (7, -8)],
    }.get(condition, [(8, 7)])
    fallback = [
        (10, 0),
        (-10, 0),
        (0, 11),
        (0, -11),
        (12, 8),
        (12, -8),
        (-12, 8),
        (-12, -8),
        (15, 0),
        (-15, 0),
        (0, 16),
        (0, -16),
        (16, 10),
        (16, -10),
        (-16, 10),
        (-16, -10),
    ]
    rings: List[Tuple[float, float]] = []
    for radius in (22, 30, 40, 52, 66, 82, 100, 120):
        for angle_degrees in (0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330):
            angle = math.radians(angle_degrees)
            rings.append((math.cos(angle) * radius, math.sin(angle) * radius))

    if fallback:
        rotation = (
            sum((idx + 1) * ord(ch) for idx, ch in enumerate(seed_text))
            % len(fallback)
        )
        fallback = fallback[rotation:] + fallback[:rotation]
    if rings:
        rotation = (
            sum((idx + 3) * ord(ch) for idx, ch in enumerate(seed_text))
            % len(rings)
        )
        rings = rings[rotation:] + rings[:rotation]

    offsets: List[Tuple[float, float]] = []
    seen: set[Tuple[float, float]] = set()
    for dx, dy in [*preferred, *fallback, *rings]:
        scaled = (round(dx * offset_scale, 3), round(dy * offset_scale, 3))
        if scaled in seen:
            continue
        seen.add(scaled)
        offsets.append(scaled)
    return offsets


def _set_label_offset(
    fig: plt.Figure,
    ax: plt.Axes,
    label: PlacedLabel,
    offset: Tuple[float, float],
) -> None:
    label.text.set_position(_point_offset_to_data(fig, ax, label.anchor, offset))
    if offset[0] < -2.0:
        label.text.set_ha("right")
    elif abs(offset[0]) <= 2.0:
        label.text.set_ha("center")
    else:
        label.text.set_ha("left")


def _bbox_overflow(bbox: Bbox, container: Bbox, *, margin: float = 4.0) -> float:
    return float(
        max(0.0, container.x0 + margin - bbox.x0)
        + max(0.0, bbox.x1 - (container.x1 - margin))
        + max(0.0, container.y0 + margin - bbox.y0)
        + max(0.0, bbox.y1 - (container.y1 - margin))
    )


def _place_labels_by_candidates(
    *,
    fig: plt.Figure,
    ax: plt.Axes,
    labels: List[PlacedLabel],
    points: List[ScatterPoint],
    static_bboxes: List[Bbox],
    offset_scale: float,
) -> None:
    if not labels:
        return

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    axes_bbox = ax.get_window_extent(renderer)

    # Place labels in denser neighborhoods first, then longer labels.
    def _placement_priority(point: ScatterPoint) -> Tuple[int, int]:
        anchor_px = ax.transData.transform((point.x.mean, point.y.mean))
        nearby = 0
        for other in points:
            other_px = ax.transData.transform((other.x.mean, other.y.mean))
            if math.hypot(anchor_px[0] - other_px[0], anchor_px[1] - other_px[1]) < 70:
                nearby += 1
        return nearby, len(point.model_pretty)

    def _order_key(idx: int, name: str) -> Tuple[float, ...]:
        point = points[idx]
        density, label_len = _placement_priority(point)
        if name == "dense":
            return (-density, -label_len, -point.y.mean, point.x.mean)
        if name == "dense_reverse":
            return (density, label_len, point.y.mean, -point.x.mean)
        if name == "top_down":
            return (-point.y.mean, point.x.mean, -density)
        if name == "bottom_up":
            return (point.y.mean, point.x.mean, -density)
        if name == "left_right":
            return (point.x.mean, -point.y.mean, -density)
        if name == "right_left":
            return (-point.x.mean, -point.y.mean, -density)
        if name == "long_first":
            return (-label_len, -density, -point.y.mean)
        return (float(idx),)

    order_names = [
        "dense",
        "dense_reverse",
        "top_down",
        "bottom_up",
        "left_right",
        "right_left",
        "long_first",
        "original",
    ]
    orders: List[List[int]] = []
    seen_orders: set[Tuple[int, ...]] = set()
    for name in order_names:
        order = sorted(range(len(labels)), key=lambda idx, order_name=name: _order_key(idx, order_name))
        key = tuple(order)
        if key in seen_orders:
            continue
        seen_orders.add(key)
        orders.append(order)

    base_order = orders[0] if orders else list(range(len(labels)))
    for divisor in (4, 3, 2):
        if not base_order:
            continue
        shift = max(1, len(base_order) // divisor)
        order = base_order[shift:] + base_order[:shift]
        key = tuple(order)
        if key not in seen_orders:
            seen_orders.add(key)
            orders.append(order)

    def _assignment_score(assignments: Dict[int, Tuple[Tuple[float, float], Bbox]]) -> float:
        bboxes = [bbox for _, bbox in assignments.values()]
        label_overlap = 0.0
        for i in range(len(bboxes)):
            for j in range(i + 1, len(bboxes)):
                label_overlap += _bbox_overlap_area(bboxes[i], bboxes[j])
        static_overlap = sum(
            _bbox_overlap_area(bbox, static_bbox)
            for bbox in bboxes
            for static_bbox in static_bboxes
        )
        overflow = sum(_bbox_overflow(bbox, axes_bbox, margin=5.0) for bbox in bboxes)
        distance = sum(math.hypot(offset[0], offset[1]) for offset, _ in assignments.values())
        return (
            (label_overlap * 250.0)
            + (static_overlap * 110.0)
            + (overflow * 1400.0)
            + (distance * 1.5)
        )

    best_assignments: Optional[Dict[int, Tuple[Tuple[float, float], Bbox]]] = None
    best_assignment_score = float("inf")

    for order in orders:
        placed_bboxes: List[Bbox] = []
        assignments: Dict[int, Tuple[Tuple[float, float], Bbox]] = {}
        greedy_cost = 0.0
        for idx in order:
            label = labels[idx]
            point = points[idx]
            best_offset: Optional[Tuple[float, float]] = None
            best_cost = float("inf")
            best_bbox: Optional[Bbox] = None
            seed_text = f"{point.model_label}|{point.condition}|{point.x.mean:.5f}|{point.y.mean:.5f}"
            for offset in _candidate_label_offsets(
                point.condition,
                seed_text=seed_text,
                offset_scale=offset_scale,
            ):
                _set_label_offset(fig, ax, label, offset)
                bbox = _expanded_bbox(
                    label.text.get_window_extent(renderer),
                    pad_x=7.0,
                    pad_y=4.5,
                )
                static_overlap = sum(_bbox_overlap_area(bbox, s) for s in static_bboxes)
                label_overlap = sum(_bbox_overlap_area(bbox, b) for b in placed_bboxes)
                overflow = _bbox_overflow(bbox, axes_bbox, margin=5.0)
                offset_distance = math.hypot(offset[0], offset[1])
                cost = (
                    (static_overlap * 60.0)
                    + (label_overlap * 120.0)
                    + (overflow * 1200.0)
                    + (offset_distance * 4.0)
                )
                if cost < best_cost:
                    best_cost = cost
                    best_offset = offset
                    best_bbox = bbox

            if best_offset is not None and best_bbox is not None:
                _set_label_offset(fig, ax, label, best_offset)
                placed_bboxes.append(best_bbox)
                assignments[idx] = (best_offset, best_bbox)
                greedy_cost += best_cost

        if len(assignments) != len(labels):
            continue
        score = _assignment_score(assignments) + (greedy_cost * 0.05)
        if score < best_assignment_score:
            best_assignment_score = score
            best_assignments = assignments

    if best_assignments is None:
        return

    for idx, (offset, _) in best_assignments.items():
        _set_label_offset(fig, ax, labels[idx], offset)


def _spread_labels(
    *,
    fig: plt.Figure,
    ax: plt.Axes,
    labels: List[PlacedLabel],
    static_bboxes: List[Bbox],
    iterations: int = 220,
    max_anchor_distance: Optional[float] = None,
) -> None:
    if not labels:
        return

    if max_anchor_distance is None:
        max_anchor_distance = min(230.0, max(120.0, 62.0 + (3.0 * len(labels))))
    max_step = 24.0

    for _ in range(iterations):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        axes_bbox = ax.get_window_extent(renderer)
        bboxes = [
            _expanded_bbox(label.text.get_window_extent(renderer), pad_x=7.0, pad_y=4.5)
            for label in labels
        ]
        shifts = [[0.0, 0.0] for _ in labels]
        collision_pressure = [0.0 for _ in labels]

        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                shift = _bbox_overlap_shift(bboxes[i], bboxes[j], pad=3.0)
                if shift is None:
                    continue
                weight = 1.35
                shifts[i][0] += shift[0] * weight
                shifts[i][1] += shift[1] * weight
                shifts[j][0] -= shift[0] * weight
                shifts[j][1] -= shift[1] * weight
                pressure = abs(shift[0]) + abs(shift[1])
                collision_pressure[i] += pressure
                collision_pressure[j] += pressure

        for i, bbox in enumerate(bboxes):
            for static_bbox in static_bboxes:
                shift = _bbox_overlap_shift(bbox, static_bbox, pad=4.0)
                if shift is None:
                    continue
                shifts[i][0] += shift[0] * 1.7
                shifts[i][1] += shift[1] * 1.7
                collision_pressure[i] += abs(shift[0]) + abs(shift[1])

            # A weak pull keeps labels associated with their points after the
            # repulsion phase has moved crowded labels into open space.
            text_x_px, text_y_px = ax.transData.transform(labels[i].text.get_position())
            anchor_x_px, anchor_y_px = ax.transData.transform(labels[i].anchor)
            pull_x = text_x_px - anchor_x_px
            pull_y = text_y_px - anchor_y_px
            if collision_pressure[i] < 1.0:
                if abs(pull_x) > 34.0:
                    shifts[i][0] -= pull_x * 0.025
                if abs(pull_y) > 34.0:
                    shifts[i][1] -= pull_y * 0.025
            elif math.hypot(pull_x, pull_y) > max_anchor_distance * 0.9:
                shifts[i][0] -= pull_x * 0.015
                shifts[i][1] -= pull_y * 0.015

            margin = 6.0
            if bbox.x0 < axes_bbox.x0 + margin:
                shifts[i][0] += axes_bbox.x0 + margin - bbox.x0
            if bbox.x1 > axes_bbox.x1 - margin:
                shifts[i][0] -= bbox.x1 - (axes_bbox.x1 - margin)
            if bbox.y0 < axes_bbox.y0 + margin:
                shifts[i][1] += axes_bbox.y0 + margin - bbox.y0
            if bbox.y1 > axes_bbox.y1 - margin:
                shifts[i][1] -= bbox.y1 - (axes_bbox.y1 - margin)

        max_shift = max(
            (abs(dx) + abs(dy) for dx, dy in shifts),
            default=0.0,
        )
        if max_shift < 0.25:
            break

        for label, (dx, dy) in zip(labels, shifts):
            x_px, y_px = ax.transData.transform(label.text.get_position())
            dx = max(-max_step, min(max_step, dx * 0.62))
            dy = max(-max_step, min(max_step, dy * 0.62))
            new_x_px = x_px + dx
            new_y_px = y_px + dy
            anchor_x_px, anchor_y_px = ax.transData.transform(label.anchor)
            anchor_dx = new_x_px - anchor_x_px
            anchor_dy = new_y_px - anchor_y_px
            distance = math.hypot(anchor_dx, anchor_dy)
            if distance > max_anchor_distance:
                scale = max_anchor_distance / distance
                new_x_px = anchor_x_px + (anchor_dx * scale)
                new_y_px = anchor_y_px + (anchor_dy * scale)
            if new_x_px < anchor_x_px - 2.0:
                label.text.set_ha("right")
            elif abs(new_x_px - anchor_x_px) <= 2.0:
                label.text.set_ha("center")
            else:
                label.text.set_ha("left")
            new_position = ax.transData.inverted().transform((new_x_px, new_y_px))
            label.text.set_position(tuple(new_position))


def _label_overlap_stats(fig: plt.Figure, labels: List[PlacedLabel]) -> Tuple[int, float]:
    if not labels:
        return 0, 0.0

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    bboxes = [
        _expanded_bbox(label.text.get_window_extent(renderer), pad_x=2.0, pad_y=1.5)
        for label in labels
    ]
    count = 0
    area = 0.0
    for i in range(len(bboxes)):
        for j in range(i + 1, len(bboxes)):
            overlap = _bbox_overlap_area(bboxes[i], bboxes[j])
            if overlap <= 0.0:
                continue
            count += 1
            area += overlap
    return count, area


def _add_leader_lines(
    *, fig: plt.Figure, ax: plt.Axes, labels: List[PlacedLabel]
) -> None:
    fig.canvas.draw()
    for label in labels:
        anchor_px = ax.transData.transform(label.anchor)
        label_px = ax.transData.transform(label.text.get_position())
        distance = math.hypot(label_px[0] - anchor_px[0], label_px[1] - anchor_px[1])
        if distance <= 0.0:
            continue
        ax.annotate(
            "",
            xy=label.anchor,
            xytext=label.text.get_position(),
            arrowprops={
                "arrowstyle": "-",
                "color": "#6f6f6f",
                "lw": 0.6 * STROKE_SCALE,
                "alpha": 0.5,
            },
            zorder=2,
        )


def _add_collision_aware_labels(
    *,
    fig: plt.Figure,
    ax: plt.Axes,
    points: List[ScatterPoint],
    legend: Optional[plt.Legend],
    label_fontsize: float = 9.0,
    marker_scale: float = 1.0,
    label_text_by_model: Optional[Dict[str, str]] = None,
) -> None:
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    static_bboxes = _static_anchor_bboxes(
        ax,
        ((p.x.mean, p.y.mean) for p in points),
        radius_px=14.0 + (8.0 * marker_scale),
    )
    if legend is not None:
        static_bboxes.append(
            _expanded_bbox(legend.get_window_extent(renderer), pad_x=5, pad_y=5)
        )

    offsets = {
        "baseline": (8, 8),
        "control": (8, -13),
        "simple": (7, 10),
        "sc1": (8, -13),
        "sc2": (8, 8),
        "sc3": (7, 10),
    }
    labels: List[PlacedLabel] = []
    effective_label_fontsize = label_fontsize * (0.95 if len(points) >= 30 else 1.05)
    for point in points:
        label_text = (
            label_text_by_model.get(point.model_label, point.model_pretty)
            if label_text_by_model is not None
            else point.model_pretty
        )
        anchor = (point.x.mean, point.y.mean)
        initial_position = _point_offset_to_data(
            fig, ax, anchor, offsets.get(point.condition, (8, 8))
        )
        text = ax.text(
            initial_position[0],
            initial_position[1],
            label_text,
            fontsize=effective_label_fontsize,
            color="#1f1f1f",
            ha="left",
            va="center",
            clip_on=False,
            zorder=5,
        )
        labels.append(PlacedLabel(text=text, anchor=anchor))

    _place_labels_by_candidates(
        fig=fig,
        ax=ax,
        labels=labels,
        points=points,
        static_bboxes=static_bboxes,
        offset_scale=max(1.0, label_fontsize / 9.0),
    )
    for max_anchor_distance in (None, 260.0, 320.0):
        _spread_labels(
            fig=fig,
            ax=ax,
            labels=labels,
            static_bboxes=static_bboxes,
            max_anchor_distance=max_anchor_distance,
        )
        overlap_count, _ = _label_overlap_stats(fig, labels)
        if overlap_count == 0:
            break
    _add_leader_lines(fig=fig, ax=ax, labels=labels)


def _sorted_unique_model_points(points: List[ScatterPoint]) -> List[ScatterPoint]:
    by_model: Dict[str, ScatterPoint] = {}
    for point in points:
        by_model.setdefault(point.model_label, point)

    def sort_key(point: ScatterPoint) -> Tuple[int, str, str]:
        family = _model_family_key(point.model_label, point.model_pretty) or "unknown"
        try:
            family_index = MODEL_FAMILY_ORDER.index(family)
        except ValueError:
            family_index = MODEL_FAMILY_ORDER.index("unknown")
        return family_index, family, point.model_pretty.lower()

    return sorted(by_model.values(), key=sort_key)


def _numbered_model_labels(
    points: List[ScatterPoint],
) -> Tuple[Dict[str, str], List[ScatterPoint]]:
    sorted_models = _sorted_unique_model_points(points)
    return {
        point.model_label: str(idx + 1)
        for idx, point in enumerate(sorted_models)
    }, sorted_models


def _model_key_bottom_margin(
    model_count: int, *, columns: int, compact: bool = False
) -> float:
    if model_count <= 0:
        return 0.0
    rows = math.ceil(model_count / max(1, columns))
    if compact:
        return min(0.36, max(0.14, 0.06 + (rows * 0.026)))
    return min(0.46, max(0.18, 0.07 + (rows * 0.038)))


def _add_numbered_model_key(
    *,
    fig: plt.Figure,
    ax: plt.Axes,
    models: List[ScatterPoint],
    labels_by_model: Dict[str, str],
    bottom_margin: float,
    columns: int,
    font_size: float,
    show_logos: bool = False,
    position: str = "bottom",
    right_gap: float = 0.001,
) -> None:
    if not models:
        return
    if position == "right":
        if not show_logos:
            handles = [Line2D([], [], linestyle="none") for _ in models]
            labels = [
                f"{labels_by_model.get(point.model_label, '')}. {point.model_pretty}"
                for point in models
            ]
            legend = fig.legend(
                handles,
                labels,
                loc="center left",
                bbox_to_anchor=(1.0 + right_gap, 0.5),
                bbox_transform=ax.transAxes,
                frameon=True,
                fancybox=True,
                framealpha=0.95,
                edgecolor="#bdbdbd",
                fontsize=font_size,
                handlelength=0.0,
                handletextpad=0.0,
                borderaxespad=0.0,
                borderpad=0.65,
                labelspacing=0.58,
            )
            legend.get_frame().set_linewidth(0.9 * STROKE_SCALE)
            return

        key_ax = fig.add_axes([0.745, 0.145, 0.235, 0.72])
        key_ax.set_xlim(0.0, 1.0)
        key_ax.set_ylim(0.0, 1.0)
        key_ax.axis("off")
        key_ax.add_patch(
            FancyBboxPatch(
                (0.01, 0.01),
                0.98,
                0.98,
                boxstyle="round,pad=0.015,rounding_size=0.02",
                transform=key_ax.transAxes,
                facecolor="white",
                edgecolor="#bdbdbd",
                linewidth=0.9 * STROKE_SCALE,
                zorder=0,
            )
        )
        rows = len(models)
        for idx, point in enumerate(models):
            y = 1.0 - ((idx + 0.5) / rows)
            number = labels_by_model.get(point.model_label, "")
            key_ax.text(
                0.055,
                y,
                f"{number}. {point.model_pretty}",
                transform=key_ax.transAxes,
                ha="left",
                va="center",
                fontsize=font_size,
                color="#1f1f1f",
                zorder=1,
            )
        return

    columns = max(1, columns)
    rows = math.ceil(len(models) / columns)
    key_ax = fig.add_axes([0.025, 0.012, 0.95, max(0.055, bottom_margin - 0.026)])
    key_ax.set_xlim(0.0, 1.0)
    key_ax.set_ylim(0.0, 1.0)
    key_ax.axis("off")
    key_ax.add_patch(
        FancyBboxPatch(
            (0.006, 0.035),
            0.988,
            0.93,
            boxstyle="round,pad=0.012,rounding_size=0.018",
            transform=key_ax.transAxes,
            facecolor="white",
            edgecolor="#bdbdbd",
            linewidth=0.85 * STROKE_SCALE,
            zorder=0,
        )
    )

    logo_target_px = font_size * fig.dpi / 72.0 * 1.25
    col_width = 1.0 / columns
    for idx, point in enumerate(models):
        row = idx // columns
        col = idx % columns
        x0 = col * col_width
        y = 1.0 - ((row + 0.5) / rows)
        number = labels_by_model.get(point.model_label, "")

        if not show_logos:
            key_ax.text(
                x0 + 0.02,
                y,
                f"{number}. {point.model_pretty}",
                transform=key_ax.transAxes,
                ha="left",
                va="center",
                fontsize=font_size,
                color="#1f1f1f",
            )
            continue

        key_ax.text(
            x0 + 0.005,
            y,
            f"{number}.",
            transform=key_ax.transAxes,
            ha="left",
            va="center",
            fontsize=font_size,
            color="#1f1f1f",
        )

        logo_path = _logo_path_for_model(point.model_label, point.model_pretty)
        if logo_path:
            try:
                image = _normalized_logo_image(plt.imread(str(logo_path)))
            except (OSError, ValueError):
                image = None
            if image is not None and image.shape[0] > 0:
                logo = OffsetImage(image, zoom=logo_target_px / float(image.shape[0]))
                key_ax.add_artist(
                    AnnotationBbox(
                        logo,
                        (x0 + 0.052, y),
                        xycoords=key_ax.transAxes,
                        frameon=False,
                        box_alignment=(0.5, 0.5),
                        zorder=3,
                    )
                )

        key_ax.text(
            x0 + 0.082,
            y,
            point.model_pretty,
            transform=key_ax.transAxes,
            ha="left",
            va="center",
            fontsize=font_size,
            color="#1f1f1f",
        )


def _points_for_condition(points: List[ScatterPoint], condition: str) -> List[ScatterPoint]:
    return [point for point in points if point.condition == condition]


def _add_condition_clusters(
    *,
    ax: plt.Axes,
    points: List[ScatterPoint],
    fill_alpha: float = 0.04,
    edge_alpha: float = 0.32,
) -> None:
    for condition in CONDITION_ORDER:
        subset = _points_for_condition(points, condition)
        if len(subset) < 3:
            continue

        xy = np.array([[point.x.mean, point.y.mean] for point in subset], dtype=float)
        center = xy.mean(axis=0)
        covariance = np.cov(xy, rowvar=False)
        if not np.all(np.isfinite(covariance)):
            continue

        values, vectors = np.linalg.eigh(covariance)
        order = values.argsort()[::-1]
        values = np.maximum(values[order], 1e-10)
        vectors = vectors[:, order]
        angle = math.degrees(math.atan2(vectors[1, 0], vectors[0, 0]))
        # A little over two standard deviations gives a readable cluster
        # boundary without turning the overlay into a background wash.
        width, height = 2.0 * 2.0 * np.sqrt(values)
        color = CONDITION_COLORS[condition]
        cluster = Ellipse(
            xy=tuple(center),
            width=float(width),
            height=float(height),
            angle=angle,
            facecolor=color,
            edgecolor=color,
            linewidth=1.2 * STROKE_SCALE,
            alpha=fill_alpha,
            zorder=1.5,
        )
        ax.add_patch(cluster)
        outline = Ellipse(
            xy=tuple(center),
            width=float(width),
            height=float(height),
            angle=angle,
            facecolor="none",
            edgecolor=color,
            linewidth=1.2 * STROKE_SCALE,
            alpha=edge_alpha,
            zorder=2.1,
        )
        ax.add_patch(outline)


def _trend_line_groups(
    points: List[ScatterPoint], *, point_style: str
) -> List[TrendLineGroup]:
    point_style = _normalize_point_style(point_style)
    groups: List[TrendLineGroup] = []
    if point_style in {"condition", "condition-model-size"}:
        for condition in CONDITION_ORDER:
            subset = tuple(_points_for_condition(points, condition))
            if subset:
                groups.append(
                    TrendLineGroup(
                        key=condition,
                        color=CONDITION_COLORS[condition],
                        points=subset,
                    )
                )
        return groups

    if point_style == "model-size":
        for size_key in MODEL_SIZE_ORDER:
            subset = tuple(
                point
                for point in points
                if _model_size_key(point.model_label, point.model_pretty) == size_key
            )
            if subset:
                groups.append(
                    TrendLineGroup(
                        key=size_key,
                        color=MODEL_SIZE_COLORS[size_key],
                        points=subset,
                    )
                )
        return groups

    family_order = [
        "openai",
        "anthropic",
        "gemini",
        "deepseek",
        "moonshot",
        "glm",
        "minimax",
        "grok",
    ]
    families = {
        _model_family_key(point.model_label, point.model_pretty) or "unknown"
        for point in points
    }
    ordered_families = [
        *[family for family in family_order if family in families],
        *sorted(family for family in families if family not in family_order),
    ]
    for family in ordered_families:
        subset = tuple(
            point
            for point in points
            if (_model_family_key(point.model_label, point.model_pretty) or "unknown")
            == family
        )
        if subset:
            groups.append(
                TrendLineGroup(
                    key=family,
                    color="#6f6f6f",
                    points=subset,
                )
            )
    return groups


def _trend_line_color(color: str) -> str:
    red, green, blue = matplotlib.colors.to_rgb(color)
    return matplotlib.colors.to_hex(
        (
            red * TREND_LINE_DARKEN_FACTOR,
            green * TREND_LINE_DARKEN_FACTOR,
            blue * TREND_LINE_DARKEN_FACTOR,
        )
    )


def _add_trend_lines(
    *,
    ax: plt.Axes,
    points: List[ScatterPoint],
    point_style: str = "condition",
    alpha: float = TREND_LINE_ALPHA,
) -> None:
    for group in _trend_line_groups(points, point_style=point_style):
        subset = group.points
        if len(subset) < 2:
            continue

        x_values = np.array([point.x.mean for point in subset], dtype=float)
        y_values = np.array([point.y.mean for point in subset], dtype=float)
        if np.allclose(x_values, x_values[0]):
            continue

        slope, intercept = np.polyfit(x_values, y_values, deg=1)
        x_line = np.linspace(float(x_values.min()), float(x_values.max()), 100)
        y_line = slope * x_line + intercept
        ax.plot(
            x_line,
            y_line,
            color=_trend_line_color(group.color),
            linewidth=3.2 * STROKE_SCALE,
            alpha=alpha,
            solid_capstyle="round",
            zorder=2.0,
        )


def _add_condition_trend_lines(
    *,
    ax: plt.Axes,
    points: List[ScatterPoint],
    alpha: float = TREND_LINE_ALPHA,
) -> None:
    _add_trend_lines(ax=ax, points=points, point_style="condition", alpha=alpha)


def _x_reference_value(*, control_minus_condition_x: bool) -> float:
    return 0.0 if control_minus_condition_x else 0.5


def _add_error_bars(
    *,
    ax: plt.Axes,
    points: List[ScatterPoint],
    color: str,
) -> None:
    ax.errorbar(
        [p.x.mean for p in points],
        [p.y.mean for p in points],
        xerr=[p.x.sem or 0.0 for p in points],
        yerr=[p.y.sem or 0.0 for p in points],
        fmt="none",
        ecolor=color,
        elinewidth=1.2 * STROKE_SCALE,
        capsize=2.5,
        alpha=0.3,
        zorder=1,
    )


def _add_point_legend(
    *,
    ax: plt.Axes,
    legend_loc: str,
    legend_marker_scale: float,
    ncols: int,
) -> Optional[plt.Legend]:
    handles, labels = ax.get_legend_handles_labels()
    if not handles or not labels:
        return None
    legend = ax.legend(
        loc=legend_loc,
        frameon=True,
        fancybox=True,
        framealpha=0.95,
        edgecolor="#bdbdbd",
        ncols=ncols,
        handlelength=1.5,
        handleheight=1.4,
        handletextpad=0.45,
        columnspacing=1.1,
        borderpad=0.55,
        labelspacing=0.45,
        markerscale=1.2 * legend_marker_scale,
    )
    legend.get_frame().set_linewidth(0.9 * STROKE_SCALE)
    return legend


def _draw_condition_points(
    *,
    ax: plt.Axes,
    points: List[ScatterPoint],
    show_error_bars: bool,
    marker_scale: float,
    legend_loc: str,
    legend_marker_scale: float,
    size_by_model_size: bool = False,
) -> Optional[plt.Legend]:
    for condition in CONDITION_ORDER:
        subset = [p for p in points if p.condition == condition]
        if not subset:
            continue
        color = CONDITION_COLORS[condition]
        marker = CONDITION_MARKERS[condition]
        if show_error_bars:
            _add_error_bars(ax=ax, points=subset, color=color)
        marker_areas = (
            [
                MODEL_SIZE_MARKER_AREAS[
                    _model_size_key(point.model_label, point.model_pretty)
                ]
                * (marker_scale**2)
                for point in subset
            ]
            if size_by_model_size
            else CONDITION_MARKER_AREA * (marker_scale**2)
        )
        ax.scatter(
            [p.x.mean for p in subset],
            [p.y.mean for p in subset],
            s=marker_areas,
            marker=marker,
            color=color,
            edgecolor="black",
            linewidth=0.9 * marker_scale * STROKE_SCALE,
            alpha=0.92,
            label=CONDITION_LABELS[condition],
            zorder=3,
        )

    plotted_conditions = {
        point.condition for point in points if point.condition in CONDITION_LABELS
    }
    return _add_point_legend(
        ax=ax,
        legend_loc=legend_loc,
        legend_marker_scale=legend_marker_scale,
        ncols=min(4, max(1, len(plotted_conditions))),
    )


def _draw_model_size_points(
    *,
    ax: plt.Axes,
    points: List[ScatterPoint],
    show_error_bars: bool,
    marker_scale: float,
    legend_loc: str,
    legend_marker_scale: float,
) -> Optional[plt.Legend]:
    plotted_sizes = []
    for size_key in MODEL_SIZE_ORDER:
        subset = [
            p
            for p in points
            if _model_size_key(p.model_label, p.model_pretty) == size_key
        ]
        if not subset:
            continue
        plotted_sizes.append(size_key)
        color = MODEL_SIZE_COLORS[size_key]
        if show_error_bars:
            _add_error_bars(ax=ax, points=subset, color=color)
        ax.scatter(
            [p.x.mean for p in subset],
            [p.y.mean for p in subset],
            s=MODEL_SIZE_MARKER_AREAS[size_key] * (marker_scale**2),
            marker="o",
            color=color,
            edgecolor="black",
            linewidth=0.9 * marker_scale * STROKE_SCALE,
            alpha=0.92,
            label=MODEL_SIZE_LABELS[size_key],
            zorder=3,
        )

    return _add_point_legend(
        ax=ax,
        legend_loc=legend_loc,
        legend_marker_scale=legend_marker_scale,
        ncols=max(1, len(plotted_sizes)),
    )


def _normalized_logo_image(image: np.ndarray) -> Optional[np.ndarray]:
    if image.size == 0:
        return None
    if image.ndim == 2:
        alpha = np.ones_like(image)
        return np.dstack([image, image, image, alpha])
    if image.ndim == 3 and image.shape[2] == 2:
        gray = image[:, :, 0]
        alpha = image[:, :, 1]
        return np.dstack([gray, gray, gray, alpha])
    if image.ndim == 3 and image.shape[2] in {3, 4}:
        return image
    return None


def _add_logo_point(
    *,
    ax: plt.Axes,
    point: ScatterPoint,
    logo_path: Path,
    marker_scale: float,
) -> bool:
    try:
        image = _normalized_logo_image(plt.imread(str(logo_path)))
    except (OSError, ValueError):
        return False
    if image is None or image.shape[0] <= 0:
        return False
    zoom = (LOGO_MARKER_TARGET_PX * marker_scale) / float(image.shape[0])
    offset_image = OffsetImage(image, zoom=zoom)
    ab = AnnotationBbox(
        offset_image,
        (point.x.mean, point.y.mean),
        xycoords="data",
        frameon=False,
        box_alignment=(0.5, 0.5),
        zorder=4,
    )
    ax.add_artist(ab)
    return True


def _draw_model_family_points(
    *,
    ax: plt.Axes,
    points: List[ScatterPoint],
    show_error_bars: bool,
    marker_scale: float,
) -> None:
    if show_error_bars:
        _add_error_bars(ax=ax, points=points, color="#6f6f6f")

    for point in points:
        logo_path = _logo_path_for_model(point.model_label, point.model_pretty)
        if logo_path and _add_logo_point(
            ax=ax,
            point=point,
            logo_path=logo_path,
            marker_scale=marker_scale,
        ):
            continue
        ax.scatter(
            [point.x.mean],
            [point.y.mean],
            s=FALLBACK_FAMILY_MARKER_AREA * (marker_scale**2),
            marker="o",
            facecolor="white",
            edgecolor="#4a4a4a",
            linewidth=0.9 * marker_scale * STROKE_SCALE,
            alpha=0.92,
            zorder=3,
        )


def plot_scatter(
    *,
    csv_path: Path,
    out_path: Path,
    y_metric: str = DEFAULT_Y_METRIC,
    title: Optional[str] = None,
    show_error_bars: bool = False,
    no_title: bool = False,
    hide_y_label: bool = False,
    hide_y_axis: bool = False,
    font_scale: float = 1.0,
    label_font_scale: float = 1.0,
    axis_label_font_scale: float = 1.0,
    marker_scale: float = 1.0,
    legend_font_scale: float = 1.0,
    legend_marker_scale: float = 1.0,
    legend_loc: Optional[str] = None,
    show_legend: bool = True,
    figsize: Tuple[float, float] = DEFAULT_FIGSIZE,
    x_step_limits: Optional[float] = None,
    y_step_limits: Optional[float] = None,
    x_limits: Optional[Tuple[float, float]] = None,
    y_limits: Optional[Tuple[float, float]] = None,
    show_point_labels: bool = True,
    condition_clusters: bool = False,
    condition_trend_lines: bool = False,
    numbered_model_key: bool = False,
    model_key_columns: int = 2,
    model_key_logos: bool = False,
    model_key_position: str = "bottom",
    model_key_gap: float = 0.001,
    show_model_key: bool = True,
    square_axes: bool = False,
    tight_bbox: bool = False,
    pad_inches: float = 0.04,
    control_minus_condition_x: bool = False,
    judge_x_axis: bool = False,
    invert_delta_collusion: bool = False,
    symmetric_delta_axis: bool = False,
    point_style: str = "condition",
    delta_collusion_metric: str = DEFAULT_DELTA_COLLUSION_METRIC,
    delta_judge_score: bool = False,
) -> None:
    y_metric = _normalize_y_metric(y_metric)
    point_style = _normalize_point_style(point_style)
    delta_collusion_metric = _normalize_delta_collusion_metric(delta_collusion_metric)
    if invert_delta_collusion and not control_minus_condition_x:
        raise SystemExit("--invert-delta-collusion requires --control-minus-condition-x")
    if delta_judge_score and y_metric != DEFAULT_Y_METRIC:
        raise SystemExit("--delta-judge-score requires --y-metric judge")
    legend_loc = _legend_location(
        legend_loc,
        control_minus_condition_x=control_minus_condition_x,
    )

    raw_points = _read_points(
        csv_path,
        y_metric=y_metric,
        control_minus_condition_x=control_minus_condition_x,
        delta_collusion_metric=delta_collusion_metric,
        delta_judge_score=delta_judge_score,
    )
    if not raw_points:
        raise SystemExit(
            f"No scatter points found for y metric {y_metric!r} in {csv_path}"
        )
    raw_points = _invert_delta_points(
        raw_points,
        invert_delta_collusion=invert_delta_collusion,
        delta_collusion_metric=delta_collusion_metric,
    )
    points = _orient_points(raw_points, judge_x_axis=judge_x_axis)

    plt.rcParams.update(
        {
            "font.size": 12 * font_scale,
            "axes.labelsize": 15 * font_scale * axis_label_font_scale,
            "xtick.labelsize": 12 * font_scale,
            "ytick.labelsize": 12 * font_scale,
            "legend.fontsize": 12 * legend_font_scale,
        }
    )
    fig, ax = plt.subplots(figsize=figsize)
    if condition_clusters:
        _add_condition_clusters(ax=ax, points=points)
    if condition_trend_lines:
        _add_trend_lines(ax=ax, points=points, point_style=point_style)
    if square_axes:
        ax.set_box_aspect(1.0)

    label_text_by_model: Optional[Dict[str, str]] = None
    model_key_points: List[ScatterPoint] = []
    if numbered_model_key:
        label_text_by_model, model_key_points = _numbered_model_labels(points)

    legend: Optional[plt.Legend]
    draw_family_points_after_labels = False
    if point_style in {"condition", "condition-model-size"}:
        legend = _draw_condition_points(
            ax=ax,
            points=points,
            show_error_bars=show_error_bars,
            marker_scale=marker_scale,
            legend_loc=legend_loc,
            legend_marker_scale=legend_marker_scale,
            size_by_model_size=point_style == "condition-model-size",
        )
    elif point_style == "model-size":
        legend = _draw_model_size_points(
            ax=ax,
            points=points,
            show_error_bars=show_error_bars,
            marker_scale=marker_scale,
            legend_loc=legend_loc,
            legend_marker_scale=legend_marker_scale,
        )
    else:
        if show_error_bars:
            _add_error_bars(ax=ax, points=points, color="#6f6f6f")
        legend = None
        draw_family_points_after_labels = True
    if not show_legend and legend is not None:
        legend.remove()
        legend = None

    if judge_x_axis:
        xmin, xmax = _y_axis_limits(
            raw_points,
            y_metric=y_metric,
            y_step_limits=y_step_limits,
            delta_judge_score=delta_judge_score,
            symmetric_delta_axis=symmetric_delta_axis,
        )
        ymin, ymax = _x_axis_limits(
            raw_points,
            x_step_limits=x_step_limits,
            control_minus_condition_x=control_minus_condition_x,
            symmetric_delta_axis=symmetric_delta_axis,
        )
    else:
        xmin, xmax = _x_axis_limits(
            raw_points,
            x_step_limits=x_step_limits,
            control_minus_condition_x=control_minus_condition_x,
            symmetric_delta_axis=symmetric_delta_axis,
        )
        ymin, ymax = _y_axis_limits(
            raw_points,
            y_metric=y_metric,
            y_step_limits=y_step_limits,
            delta_judge_score=delta_judge_score,
            symmetric_delta_axis=symmetric_delta_axis,
        )
    if x_limits is not None:
        xmin, xmax = x_limits
    if y_limits is not None:
        ymin, ymax = y_limits
    ax.set_xlim(xmin, xmax)
    if x_step_limits is not None and not judge_x_axis:
        ax.set_xticks(_axis_ticks_by_step(xmin, xmax, step=x_step_limits))
    if y_step_limits is not None and judge_x_axis:
        ax.set_xticks(_axis_ticks_by_step(xmin, xmax, step=y_step_limits))
    ax.set_ylim(ymin, ymax)
    if x_step_limits is not None and judge_x_axis:
        ax.set_yticks(_axis_ticks_by_step(ymin, ymax, step=x_step_limits))
    if y_step_limits is not None and not judge_x_axis:
        ax.set_yticks(_axis_ticks_by_step(ymin, ymax, step=y_step_limits))
    coalition_axis_label = (
        _delta_collusion_axis_label(
            invert_delta_collusion=invert_delta_collusion,
            delta_collusion_metric=delta_collusion_metric,
        )
        if control_minus_condition_x
        else _coalition_advantage_axis_label()
    )
    y_metric_label = Y_METRIC_LABELS.get(y_metric, y_metric.replace("_", " ").title())
    if delta_judge_score:
        y_metric_label = _delta_judge_score_axis_label()
    if judge_x_axis:
        ax.set_xlabel(y_metric_label)
        ax.set_ylabel(coalition_axis_label)
    else:
        ax.set_xlabel(coalition_axis_label)
        ax.set_ylabel(y_metric_label)
    for spine in ax.spines.values():
        spine.set_linewidth(1.0 * STROKE_SCALE)
    ax.tick_params(width=1.05 * STROKE_SCALE, length=4.6 * STROKE_SCALE)
    if hide_y_label and not hide_y_axis:
        # Keep the label's layout footprint so paired panels retain equal axes boxes.
        ax.yaxis.label.set_color((0.0, 0.0, 0.0, 0.0))
    if hide_y_axis:
        ax.set_ylabel("")
        ax.tick_params(axis="y", left=False, labelleft=False)
        ax.spines["left"].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(
        True,
        linestyle="--",
        linewidth=GRID_LINEWIDTH_SCALE * STROKE_SCALE,
        alpha=GRID_ALPHA,
    )
    reference_value = _x_reference_value(
        control_minus_condition_x=control_minus_condition_x
    )
    reference_kwargs = {
        "color": "#5f5f5f",
        "linestyle": "--",
        "linewidth": REFERENCE_LINEWIDTH_SCALE * STROKE_SCALE,
        "alpha": REFERENCE_ALPHA,
        "zorder": REFERENCE_ZORDER,
    }
    if judge_x_axis:
        ax.axhline(reference_value, **reference_kwargs)
    else:
        ax.axvline(reference_value, **reference_kwargs)
    if delta_judge_score:
        if judge_x_axis:
            ax.axvline(0.0, **reference_kwargs)
        else:
            ax.axhline(0.0, **reference_kwargs)
    if no_title:
        title = ""
    elif title is None:
        title = _auto_title(csv_path)
    if title:
        ax.set_title(title, pad=16)

    bottom_margin = (
        _model_key_bottom_margin(
            len(model_key_points),
            columns=model_key_columns,
            compact=not model_key_logos,
        )
        if numbered_model_key and show_model_key and model_key_position == "bottom"
        else 0.0
    )
    if numbered_model_key and show_model_key and model_key_position == "right":
        fig.tight_layout(rect=(0.0, 0.0, 0.79, 0.94 if title else 1.0))
    elif bottom_margin:
        fig.tight_layout(rect=(0.0, bottom_margin, 1.0, 0.94 if title else 1.0))
    else:
        fig.tight_layout()
    if show_point_labels:
        _add_collision_aware_labels(
            fig=fig,
            ax=ax,
            points=points,
            legend=legend,
            label_fontsize=9.0 * font_scale * label_font_scale,
            marker_scale=marker_scale,
            label_text_by_model=label_text_by_model,
        )
    if draw_family_points_after_labels:
        _draw_model_family_points(
            ax=ax,
            points=points,
            show_error_bars=False,
            marker_scale=marker_scale,
        )
    if numbered_model_key and show_model_key:
        _add_numbered_model_key(
            fig=fig,
            ax=ax,
            models=model_key_points,
            labels_by_model=label_text_by_model or {},
            bottom_margin=bottom_margin,
            columns=model_key_columns,
            font_size=(10.8 if not model_key_logos else 8.0) * font_scale,
            show_logos=model_key_logos,
            position=model_key_position,
            right_gap=model_key_gap,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    savefig_kwargs: Dict[str, Any] = {"dpi": 300}
    if tight_bbox:
        savefig_kwargs.update({"bbox_inches": "tight", "pad_inches": pad_inches})
    fig.savefig(out_path, **savefig_kwargs)
    plt.close(fig)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot judge score vs coalition advantage from regret report CSV data."
    )
    parser.add_argument("csv_path", type=Path, help="regret_report...__data.csv path")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PNG path. Defaults next to the input CSV.",
    )
    parser.add_argument("--title", default=None, help="Optional plot title.")
    parser.add_argument(
        "--y-metric",
        default=DEFAULT_Y_METRIC,
        help=(
            "Y-axis metric from the CSV. Supports aliases: "
            "judge, judge_mean_rating, regret, overall_regret, normalized_regret."
        ),
    )
    parser.add_argument(
        "--error-bars",
        action="store_true",
        help="Draw SEM error bars from the CSV sidecar.",
    )
    parser.add_argument(
        "--no-title",
        action="store_true",
        help="Suppress the plot title.",
    )
    parser.add_argument(
        "--hide-y-axis",
        action="store_true",
        help="Hide the y-axis ticks, label, and spine.",
    )
    parser.add_argument(
        "--hide-y-label",
        action="store_true",
        help="Hide only the y-axis label.",
    )
    parser.add_argument(
        "--font-scale",
        type=float,
        default=1.0,
        help="Scale all plot text, including tick labels and annotations.",
    )
    parser.add_argument(
        "--label-font-scale",
        type=float,
        default=1.0,
        help="Scale only model annotation labels.",
    )
    parser.add_argument(
        "--axis-label-font-scale",
        type=float,
        default=1.0,
        help="Scale only x- and y-axis labels.",
    )
    parser.add_argument(
        "--marker-scale",
        type=float,
        default=1.0,
        help="Scale marker diameter; marker area is scaled quadratically.",
    )
    parser.add_argument(
        "--point-style",
        default="condition",
        help=(
            "Point encoding: condition, condition-model-size, model-size, or "
            "model-family. Aliases condition-size, size, and family are also accepted."
        ),
    )
    parser.add_argument(
        "--legend-font-scale",
        type=float,
        default=1.0,
        help="Scale legend text independently from other plot text.",
    )
    parser.add_argument(
        "--legend-marker-scale",
        type=float,
        default=1.0,
        help="Scale legend markers independently from plotted markers.",
    )
    parser.add_argument(
        "--legend-loc",
        default=None,
        help=(
            "Matplotlib legend location string. Defaults to upper right for "
            "delta plots and upper left otherwise."
        ),
    )
    parser.add_argument(
        "--no-legend",
        action="store_true",
        help="Suppress the in-plot point legend.",
    )
    parser.add_argument(
        "--figsize",
        default=None,
        help="Figure size in inches as WIDTHxHEIGHT, for example 7x12.",
    )
    parser.add_argument(
        "--portrait",
        action="store_true",
        help="Use the flipped default figure size, 7x12 inches.",
    )
    parser.add_argument(
        "--x-step-limits",
        type=float,
        default=None,
        help=(
            "Set x-axis limits to the nearest outward multiples of this step "
            "from the minimum and maximum coalition advantage values."
        ),
    )
    parser.add_argument(
        "--y-step-limits",
        type=float,
        default=None,
        help=(
            "Set the selected y metric limits to nearest outward multiples of "
            "this step from the minimum and maximum values."
        ),
    )
    parser.add_argument(
        "--x-limits",
        default=None,
        help=(
            "Explicit plotted x-axis limits as MIN,MAX after any axis swap, "
            "for example -0.5,3.5."
        ),
    )
    parser.add_argument(
        "--y-limits",
        default=None,
        help=(
            "Explicit plotted y-axis limits as MIN,MAX after any axis swap, "
            "for example -0.2,0.2."
        ),
    )
    parser.add_argument(
        "--no-point-labels",
        action="store_true",
        help="Suppress model text labels next to each plotted point.",
    )
    parser.add_argument(
        "--condition-clusters",
        action="store_true",
        help="Draw translucent cluster ellipses around each condition's points.",
    )
    parser.add_argument(
        "--condition-trend-lines",
        "--trend-lines",
        dest="condition_trend_lines",
        action="store_true",
        help=(
            "Draw translucent trend lines for the active point grouping "
            "(condition, model size, or model family)."
        ),
    )
    parser.add_argument(
        "--numbered-model-key",
        action="store_true",
        help=(
            "Replace in-plot model-name annotations with model numbers and add a "
            "compact model key below the axes, sorted by model family."
        ),
    )
    parser.add_argument(
        "--model-key-columns",
        type=int,
        default=4,
        help="Number of columns to use for --numbered-model-key.",
    )
    parser.add_argument(
        "--model-key-logos",
        action="store_true",
        help="Include model-family logos in --numbered-model-key.",
    )
    parser.add_argument(
        "--model-key-position",
        choices=("bottom", "right"),
        default="bottom",
        help="Where to place --numbered-model-key.",
    )
    parser.add_argument(
        "--model-key-gap",
        type=float,
        default=0.001,
        help=(
            "Horizontal gap between the plot and a right-side numbered model key, "
            "in axes-width units."
        ),
    )
    parser.add_argument(
        "--no-model-key",
        action="store_true",
        help=(
            "With --numbered-model-key, keep numbered point labels but suppress "
            "the separate numbered model key."
        ),
    )
    parser.add_argument(
        "--square-axes",
        action="store_true",
        help="Keep the plotted axes box square even when extra legend space is added.",
    )
    parser.add_argument(
        "--tight-bbox",
        action="store_true",
        help="Save with bbox_inches='tight' to remove outer whitespace.",
    )
    parser.add_argument(
        "--pad-inches",
        type=float,
        default=0.04,
        help="Padding in inches used with --tight-bbox.",
    )
    parser.add_argument(
        "--control-minus-condition-x",
        "--baseline-subtracted-x",
        dest="control_minus_condition_x",
        action="store_true",
        help=(
            "Plot Control minus condition delta metric per model, "
            "showing only Emergent and Prompted points."
        ),
    )
    parser.add_argument(
        "--judge-x-axis",
        "--swap-axes",
        dest="judge_x_axis",
        action="store_true",
        help=(
            "Plot the selected y metric on the x-axis and the coalition or "
            "delta metric on the y-axis."
        ),
    )
    parser.add_argument(
        "--invert-delta-collusion",
        action="store_true",
        help=(
            "Negate the Control-minus-condition delta metric values, "
            "so positive deltas become negative and negative deltas become positive."
        ),
    )
    parser.add_argument(
        "--symmetric-delta-axis",
        action="store_true",
        help="Use symmetric delta-axis limits around zero for delta plots.",
    )
    parser.add_argument(
        "--delta-collusion-metric",
        "--delta-metric",
        default=DEFAULT_DELTA_COLLUSION_METRIC,
        help=(
            "Metric used to compute delta collusion when "
            "--control-minus-condition-x is enabled. Supports aliases for "
            "coalition_advantage and overall_regret."
        ),
    )
    parser.add_argument(
        "--delta-judge-score",
        "--baseline-subtracted-judge",
        action="store_true",
        help=(
            "Replace judge scores with condition minus baseline judge-score deltas "
            "for each model. This keeps only complete baseline/Emergent or "
            "baseline/Prompted judge pairs and labels the axis with collusion "
            "direction arrows."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    csv_path = args.csv_path.expanduser().resolve()
    y_metric = _normalize_y_metric(args.y_metric)
    point_style = _normalize_point_style(args.point_style)
    delta_collusion_metric = _normalize_delta_collusion_metric(
        args.delta_collusion_metric
    )
    figsize = _parse_figsize(args.figsize) or (
        PORTRAIT_FIGSIZE if args.portrait else DEFAULT_FIGSIZE
    )
    x_limits = _parse_axis_limits(args.x_limits, option_name="--x-limits")
    y_limits = _parse_axis_limits(args.y_limits, option_name="--y-limits")
    if args.model_key_columns <= 0:
        raise SystemExit("--model-key-columns must be greater than 0")
    if args.model_key_gap < 0.0:
        raise SystemExit("--model-key-gap must be non-negative")
    out_path = (
        args.out.expanduser().resolve()
        if args.out
        else csv_path.with_name(
            _default_output_name(
                y_metric,
                control_minus_condition_x=bool(args.control_minus_condition_x),
                judge_x_axis=bool(args.judge_x_axis),
                invert_delta_collusion=bool(args.invert_delta_collusion),
                point_style=point_style,
                delta_collusion_metric=delta_collusion_metric,
                delta_judge_score=bool(args.delta_judge_score),
            )
        )
    )
    plot_scatter(
        csv_path=csv_path,
        out_path=out_path,
        y_metric=y_metric,
        title=args.title,
        show_error_bars=args.error_bars,
        no_title=args.no_title,
        hide_y_label=args.hide_y_label,
        hide_y_axis=args.hide_y_axis,
        font_scale=args.font_scale,
        label_font_scale=args.label_font_scale,
        axis_label_font_scale=args.axis_label_font_scale,
        marker_scale=args.marker_scale,
        legend_font_scale=args.legend_font_scale,
        legend_marker_scale=args.legend_marker_scale,
        legend_loc=args.legend_loc,
        show_legend=not args.no_legend,
        figsize=figsize,
        x_step_limits=args.x_step_limits,
        y_step_limits=args.y_step_limits,
        x_limits=x_limits,
        y_limits=y_limits,
        show_point_labels=not args.no_point_labels,
        condition_clusters=args.condition_clusters,
        condition_trend_lines=args.condition_trend_lines,
        numbered_model_key=bool(args.numbered_model_key),
        model_key_columns=args.model_key_columns,
        model_key_logos=bool(args.model_key_logos),
        model_key_position=args.model_key_position,
        model_key_gap=args.model_key_gap,
        show_model_key=not args.no_model_key,
        square_axes=bool(args.square_axes),
        tight_bbox=bool(args.tight_bbox),
        pad_inches=args.pad_inches,
        control_minus_condition_x=bool(args.control_minus_condition_x),
        judge_x_axis=bool(args.judge_x_axis),
        invert_delta_collusion=bool(args.invert_delta_collusion),
        symmetric_delta_axis=bool(args.symmetric_delta_axis),
        point_style=point_style,
        delta_collusion_metric=delta_collusion_metric,
        delta_judge_score=bool(args.delta_judge_score),
    )
    print(out_path)


if __name__ == "__main__":
    main()
