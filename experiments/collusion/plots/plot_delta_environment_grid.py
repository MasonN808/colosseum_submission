from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

from PIL import Image

from experiments.collusion.plots.plot_delta_dual_panels import PanelSpec, _panel_points
from experiments.collusion.plots.plot_delta_judge_grid import (
    DEFAULT_BOTTOM_MARGIN_PX,
    DEFAULT_MODEL_KEY_GAP_PX,
    DEFAULT_MODEL_KEY_HEIGHT_PX,
    DEFAULT_MODEL_KEY_LOGO_SCALE,
    DEFAULT_PANEL_AXIS_LABEL_FONT_SCALE,
    DEFAULT_PANEL_FONT_SCALE,
    DEFAULT_PANEL_GAP_PX,
    DEFAULT_PANEL_LABEL_FONT_SCALE,
    DEFAULT_PANEL_LEGEND_MARKER_SCALE,
    DEFAULT_PANEL_MARKER_SCALE,
    DEFAULT_ROW_GAP_PX,
    DEFAULT_SIDE_MARGIN_PX,
    LEFT_Y_TICK_STEP,
    RIGHT_Y_LIMITS,
    RIGHT_Y_TICK_STEP,
    _bold_math_label,
    _paste_centered,
    _render_model_key_image,
    _render_panel_image,
)
from experiments.collusion.plots.plot_judge_vs_coalition_advantage import (
    ScatterPoint,
    _numbered_model_labels,
)


DEFAULT_JIRA_CSV = Path(
    "experiments/collusion/plots_outputs/"
    "collusion_regret_complete_n6_c2_combined_10seeds/"
    "20260428-012631-small-and-full/regret_report/"
    "complete_n6_c2_combined_v2/plots/"
    "regret_report__normalized_regret__coalition_gap__judge__data.csv"
)
DEFAULT_MEETING_CSV = Path(
    "experiments/collusion/plots_outputs/"
    "collusion_meeting_scheduling_complete_n6_c2_10seeds/"
    "20260422-192126-10seeds/regret_report/complete_n6_c2/plots/"
    "regret_report__normalized_regret__coalition_gap__judge__data.csv"
)
DEFAULT_HOSPITAL_CSV = Path(
    "experiments/collusion/plots_outputs/"
    "collusion_hospital_complete_n9_c4_10seeds/"
    "20260423-180614-10seeds/regret_report/complete_n9_c4/plots/"
    "regret_report__normalized_regret__coalition_gap__judge__data.csv"
)
DEFAULT_OUT = (
    DEFAULT_JIRA_CSV.parents[2]
    / "complete_n6_c2_combined_v2_three_environments/plots/"
    "delta_plots_emergent_prompted_3x2_environments_gpt54_judge.png"
)
REQUIRED_METRICS = {
    "judge_mean_rating",
    "normalized_coalition_regret_gap",
    "normalized_regret",
}
ENVIRONMENT_LEFT_Y_LIMITS = (-0.205, 0.255)


def _environment_rows(
    *,
    jira_csv: Path,
    meeting_csv: Path,
    hospital_csv: Path,
) -> Sequence[Tuple[str, Path]]:
    return (
        ("Jira", jira_csv),
        ("Meeting Scheduling", meeting_csv),
        ("Hospital", hospital_csv),
    )


def _metric_specs(environment_label: str) -> Tuple[PanelSpec, PanelSpec]:
    bold_environment_label = _bold_math_label(environment_label)
    bold_judge_label = _bold_math_label("GPT-5.4")
    return (
        PanelSpec(
            title=(
                rf"Change in $\mathbf{{Coalition\ Advantage}}$ on "
                rf"{bold_environment_label} with {bold_judge_label} Judge"
            ),
            delta_metric="coalition_advantage",
            y_limits=ENVIRONMENT_LEFT_Y_LIMITS,
            y_tick_step=LEFT_Y_TICK_STEP,
            output_path=Path(),
        ),
        PanelSpec(
            title=(
                rf"Change in $\mathbf{{Overall\ Regret}}$ on "
                rf"{bold_environment_label} with {bold_judge_label} Judge"
            ),
            delta_metric="overall_regret",
            y_limits=RIGHT_Y_LIMITS,
            y_tick_step=RIGHT_Y_TICK_STEP,
            output_path=Path(),
        ),
    )


def _csv_model_labels(csv_path: Path) -> Set[str]:
    metrics_by_model_condition: Dict[Tuple[str, str], Set[str]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            model = str(row.get("model_label") or "").strip()
            condition = str(row.get("condition") or "").strip()
            metric = str(row.get("metric_key") or "").strip()
            if not model or not condition or metric not in REQUIRED_METRICS:
                continue
            metrics_by_model_condition.setdefault((model, condition), set()).add(metric)

    models = {model for model, _condition in metrics_by_model_condition}
    complete_models = set()
    for model in models:
        if all(
            metrics_by_model_condition.get((model, condition), set()) >= REQUIRED_METRICS
            for condition in ("baseline", "control", "simple")
        ):
            complete_models.add(model)
    return complete_models


def _shared_model_labels(csv_paths: Sequence[Path]) -> Set[str]:
    if not csv_paths:
        return set()
    labels = [_csv_model_labels(csv_path) for csv_path in csv_paths]
    return set.intersection(*labels)


def _filter_points(
    points: Sequence[ScatterPoint], model_labels: Set[str]
) -> List[ScatterPoint]:
    return [point for point in points if point.model_label in model_labels]


def _points_for_environment(
    csv_path: Path, model_labels: Set[str]
) -> Tuple[List[ScatterPoint], List[ScatterPoint]]:
    return (
        _filter_points(
            _panel_points(csv_path, delta_metric="coalition_advantage"),
            model_labels,
        ),
        _filter_points(
            _panel_points(csv_path, delta_metric="overall_regret"),
            model_labels,
        ),
    )


def plot_environment_grid(
    *,
    jira_csv: Path,
    meeting_csv: Path,
    hospital_csv: Path,
    out_path: Path,
    dpi: int,
    font_scale: float,
    label_font_scale: float,
    axis_label_font_scale: float,
    marker_scale: float,
    legend_marker_scale: float,
    model_key_rows: int,
    model_key_font_size: float,
    model_key_logo_scale: float,
) -> None:
    csv_paths = (jira_csv, meeting_csv, hospital_csv)
    for csv_path in csv_paths:
        if not csv_path.exists():
            raise SystemExit(f"Missing input CSV: {csv_path}")

    shared_model_labels = _shared_model_labels(csv_paths)
    if not shared_model_labels:
        raise SystemExit("No shared model labels found across environment CSVs")

    row_points: List[Tuple[str, List[ScatterPoint], List[ScatterPoint]]] = []
    for environment_label, csv_path in _environment_rows(
        jira_csv=jira_csv,
        meeting_csv=meeting_csv,
        hospital_csv=hospital_csv,
    ):
        left_points, right_points = _points_for_environment(csv_path, shared_model_labels)
        row_points.append((environment_label, left_points, right_points))

    all_points = [
        point
        for _environment_label, left_points, right_points in row_points
        for point in (*left_points, *right_points)
    ]
    labels_by_model, model_key_models = _numbered_model_labels(all_points)
    panel_rows = []
    for environment_label, left_points, right_points in row_points:
        left_spec, right_spec = _metric_specs(environment_label)
        row_images = []
        for points, spec in ((left_points, left_spec), (right_points, right_spec)):
            row_images.append(
                _render_panel_image(
                    points=points,
                    spec=spec,
                    labels_by_model=labels_by_model,
                    dpi=dpi,
                    font_scale=font_scale,
                    label_font_scale=label_font_scale,
                    axis_label_font_scale=axis_label_font_scale,
                    marker_scale=marker_scale,
                    legend_marker_scale=legend_marker_scale,
                )
            )
        panel_rows.append((row_images[0], row_images[1]))

    left_cell_width = max(row[0].width for row in panel_rows)
    right_cell_width = max(row[1].width for row in panel_rows)
    row_heights = [max(left.height, right.height) for left, right in panel_rows]
    panel_group_width = left_cell_width + DEFAULT_PANEL_GAP_PX + right_cell_width
    canvas_width = panel_group_width + (2 * DEFAULT_SIDE_MARGIN_PX)
    key_image = _render_model_key_image(
        models=model_key_models,
        labels_by_model=labels_by_model,
        rows=model_key_rows,
        width_px=canvas_width,
        height_px=DEFAULT_MODEL_KEY_HEIGHT_PX,
        font_size=model_key_font_size,
        logo_scale=model_key_logo_scale,
        dpi=dpi,
    )
    panels_height = sum(row_heights) + (DEFAULT_ROW_GAP_PX * (len(panel_rows) - 1))
    canvas_height = (
        DEFAULT_SIDE_MARGIN_PX
        + panels_height
        + DEFAULT_MODEL_KEY_GAP_PX
        + key_image.height
        + DEFAULT_BOTTOM_MARGIN_PX
    )
    canvas = Image.new("RGBA", (canvas_width, canvas_height), "white")

    y = DEFAULT_SIDE_MARGIN_PX
    for row_idx, (row_height, (left_image, right_image)) in enumerate(
        zip(row_heights, panel_rows)
    ):
        left_x = DEFAULT_SIDE_MARGIN_PX
        right_x = DEFAULT_SIDE_MARGIN_PX + left_cell_width + DEFAULT_PANEL_GAP_PX
        _paste_centered(
            canvas=canvas,
            image=left_image,
            cell_left=left_x,
            cell_top=y,
            cell_width=left_cell_width,
            cell_height=row_height,
        )
        _paste_centered(
            canvas=canvas,
            image=right_image,
            cell_left=right_x,
            cell_top=y,
            cell_width=right_cell_width,
            cell_height=row_height,
        )
        y += row_height
        if row_idx < len(panel_rows) - 1:
            y += DEFAULT_ROW_GAP_PX

    key_x = max(0, (canvas_width - key_image.width) // 2)
    canvas.alpha_composite(key_image, dest=(key_x, y + DEFAULT_MODEL_KEY_GAP_PX))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    canvas.convert("RGB").save(
        out_path.with_suffix(".pdf"), "PDF", resolution=float(dpi)
    )
    print(
        f"Shared models ({len(shared_model_labels)}): "
        f"{', '.join(sorted(shared_model_labels))}"
    )
    print(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a 3x2 delta scatter grid across environments using GPT-5.4 judge results."
    )
    parser.add_argument("--jira-csv", type=Path, default=DEFAULT_JIRA_CSV)
    parser.add_argument("--meeting-csv", type=Path, default=DEFAULT_MEETING_CSV)
    parser.add_argument("--hospital-csv", type=Path, default=DEFAULT_HOSPITAL_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--font-scale", type=float, default=DEFAULT_PANEL_FONT_SCALE)
    parser.add_argument(
        "--label-font-scale",
        type=float,
        default=DEFAULT_PANEL_LABEL_FONT_SCALE,
    )
    parser.add_argument(
        "--axis-label-font-scale",
        type=float,
        default=DEFAULT_PANEL_AXIS_LABEL_FONT_SCALE,
    )
    parser.add_argument("--marker-scale", type=float, default=DEFAULT_PANEL_MARKER_SCALE)
    parser.add_argument(
        "--legend-marker-scale",
        type=float,
        default=DEFAULT_PANEL_LEGEND_MARKER_SCALE,
    )
    parser.add_argument("--model-key-rows", type=int, default=3)
    parser.add_argument("--model-key-font-size", type=float, default=21.0)
    parser.add_argument(
        "--model-key-logo-scale",
        type=float,
        default=DEFAULT_MODEL_KEY_LOGO_SCALE,
    )
    args = parser.parse_args()

    plot_environment_grid(
        jira_csv=args.jira_csv,
        meeting_csv=args.meeting_csv,
        hospital_csv=args.hospital_csv,
        out_path=args.out,
        dpi=args.dpi,
        font_scale=float(args.font_scale),
        label_font_scale=float(args.label_font_scale),
        axis_label_font_scale=float(args.axis_label_font_scale),
        marker_scale=float(args.marker_scale),
        legend_marker_scale=float(args.legend_marker_scale),
        model_key_rows=int(args.model_key_rows),
        model_key_font_size=float(args.model_key_font_size),
        model_key_logo_scale=float(args.model_key_logo_scale),
    )


if __name__ == "__main__":
    main()
