from __future__ import annotations

import argparse
import math
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from PIL import Image

from experiments.collusion.plots.plot_delta_dual_panels import (
    MODEL_KEY_CONTENT_X_BOUNDS,
    MODEL_KEY_DESIRED_COLUMN_GAP_PX,
    MODEL_KEY_LOGO_GAP_PX,
    MODEL_KEY_MIN_COLUMN_GAP_PX,
    MODEL_KEY_ROW_Y_BOUNDS,
    PanelSpec,
    _model_key_display_name,
    _model_key_points,
    _panel_points,
)
from experiments.collusion.plots.plot_judge_vs_coalition_advantage import (
    GRID_ALPHA,
    GRID_LINEWIDTH_SCALE,
    REFERENCE_ALPHA,
    REFERENCE_LINEWIDTH_SCALE,
    REFERENCE_ZORDER,
    STROKE_SCALE,
    ScatterPoint,
    _add_collision_aware_labels,
    _add_trend_lines,
    _axis_ticks_by_step,
    _delta_collusion_axis_label,
    _delta_judge_score_axis_label,
    _draw_condition_points,
    _logo_path_for_model,
    _normalized_logo_image,
    _x_reference_value,
)


DEFAULT_REPORT_ROOT = Path(
    "experiments/collusion/plots_outputs/"
    "collusion_regret_complete_n6_c2_combined_10seeds/"
    "20260428-012631-small-and-full/regret_report"
)
DEFAULT_GPT54_CSV = (
    DEFAULT_REPORT_ROOT
    / "complete_n6_c2_combined_v2/plots/"
    "regret_report__normalized_regret__coalition_gap__judge__data.csv"
)
DEFAULT_OPUS46_CSV = (
    DEFAULT_REPORT_ROOT
    / "complete_n6_c2_combined_v2__foundry__claude-opus-4-6/plots/"
    "regret_report__normalized_regret__coalition_gap__judge__data.csv"
)
DEFAULT_GPT54_NANO_CSV = (
    DEFAULT_REPORT_ROOT
    / "complete_n6_c2_combined_v2__foundry__gpt-5.4-nano/plots/"
    "regret_report__normalized_regret__coalition_gap__judge__data.csv"
)
DEFAULT_OUT = (
    DEFAULT_REPORT_ROOT
    / "complete_n6_c2_combined_v2_three_judges/plots/"
    "delta_plots_emergent_prompted_3x2_judges.png"
)

LEFT_Y_LIMITS = (-0.155, 0.205)
RIGHT_Y_LIMITS = (-0.155, 0.155)
LEFT_Y_TICK_STEP = 0.05
RIGHT_Y_TICK_STEP = 0.075
LEGEND_FRAME_EDGE = "#bdbdbd"
LEGEND_FRAME_LINEWIDTH = 0.9 * STROKE_SCALE
DEFAULT_PANEL_FONT_SCALE = 1.30
DEFAULT_PANEL_LABEL_FONT_SCALE = 1.25
DEFAULT_PANEL_AXIS_LABEL_FONT_SCALE = 0.9615384615
DEFAULT_PANEL_MARKER_SCALE = 1.25
DEFAULT_PANEL_LEGEND_MARKER_SCALE = 0.80
DEFAULT_MODEL_KEY_LOGO_SCALE = 0.70
DEFAULT_PANEL_FIGSIZE = (8.2, 8.2)
DEFAULT_PANEL_GAP_PX = 360
DEFAULT_ROW_GAP_PX = 320
DEFAULT_MODEL_KEY_GAP_PX = 110
DEFAULT_MODEL_KEY_HEIGHT_PX = 660
DEFAULT_SIDE_MARGIN_PX = 80
DEFAULT_BOTTOM_MARGIN_PX = 55


def _bold_math_label(label: str) -> str:
    return r"$\mathbf{" + label.replace("-", r"\text{-}").replace(" ", r"\ ") + "}$"


def _metric_specs(judge_label: str) -> Tuple[PanelSpec, PanelSpec]:
    bold_judge_label = _bold_math_label(judge_label)
    return (
        PanelSpec(
            title=(
                rf"Change in $\mathbf{{Coalition\ Advantage}}$ on Jira "
                rf"with {bold_judge_label} Judge"
            ),
            delta_metric="coalition_advantage",
            y_limits=LEFT_Y_LIMITS,
            y_tick_step=LEFT_Y_TICK_STEP,
            output_path=Path(),
        ),
        PanelSpec(
            title=(
                rf"Change in $\mathbf{{Overall\ Regret}}$ on Jira "
                rf"with {bold_judge_label} Judge"
            ),
            delta_metric="overall_regret",
            y_limits=RIGHT_Y_LIMITS,
            y_tick_step=RIGHT_Y_TICK_STEP,
            output_path=Path(),
        ),
    )


def _judge_rows(
    *,
    gpt54_csv: Path,
    opus46_csv: Path,
    gpt54nano_csv: Path,
) -> Sequence[Tuple[str, Path]]:
    return (
        ("GPT-5.4", gpt54_csv),
        ("Opus-4.6", opus46_csv),
        ("GPT-5.4-nano", gpt54nano_csv),
    )


def _points_for_row(csv_path: Path) -> Tuple[List[ScatterPoint], List[ScatterPoint]]:
    return (
        _panel_points(csv_path, delta_metric="coalition_advantage"),
        _panel_points(csv_path, delta_metric="overall_regret"),
    )


def _draw_bottom_model_key(
    *,
    fig: plt.Figure,
    models: Sequence[ScatterPoint],
    labels_by_model: Dict[str, str],
    rows: int,
    font_size: float,
    logo_scale: float,
    bounds: Tuple[float, float, float, float],
) -> None:
    if not models:
        return

    rows = max(1, rows)
    columns = max(1, math.ceil(len(models) / rows))
    key_ax = fig.add_axes(bounds)
    key_ax.set_xlim(0.0, 1.0)
    key_ax.set_ylim(0.0, 1.0)
    key_ax.axis("off")

    top_y, bottom_y = MODEL_KEY_ROW_Y_BOUNDS
    row_step = (top_y - bottom_y) / max(1, rows - 1)
    text_artists = []
    for col_idx in range(columns):
        column_models = list(models[col_idx * rows : (col_idx + 1) * rows])
        if not column_models:
            continue
        for row_idx, point in enumerate(column_models):
            y = top_y - (row_idx * row_step)
            number = labels_by_model.get(point.model_label, "")
            text = key_ax.text(
                0.0,
                y,
                f"{number}. {_model_key_display_name(point)}",
                transform=key_ax.transAxes,
                ha="left",
                va="center",
                fontsize=font_size,
                color="#1f1f1f",
            )
            text_artists.append((col_idx, text, point))

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    logo_target_px = font_size * fig.dpi / 72.0 * 1.04 * logo_scale
    logo_gap_px = MODEL_KEY_LOGO_GAP_PX
    column_widths_px = [0.0 for _ in range(columns)]
    for col_idx, text, point in text_artists:
        bbox = text.get_window_extent(renderer)
        item_width_px = bbox.width
        if _logo_path_for_model(point.model_label, point.model_pretty):
            item_width_px += logo_gap_px + logo_target_px
        column_widths_px[col_idx] = max(column_widths_px[col_idx], item_width_px)

    axes_width_px = max(1.0, float(key_ax.bbox.width))
    content_left, content_right = MODEL_KEY_CONTENT_X_BOUNDS
    content_left_px = content_left * axes_width_px
    content_right_px = content_right * axes_width_px
    available_px = max(1.0, content_right_px - content_left_px)
    total_column_width_px = sum(column_widths_px)
    if columns > 1:
        gap_budget_px = (available_px - total_column_width_px) / (columns - 1)
        column_gap_px = min(
            MODEL_KEY_DESIRED_COLUMN_GAP_PX,
            max(MODEL_KEY_MIN_COLUMN_GAP_PX, gap_budget_px),
        )
    else:
        column_gap_px = 0.0
    group_width_px = total_column_width_px + (column_gap_px * max(0, columns - 1))
    group_left_px = content_left_px + max(0.0, (available_px - group_width_px) / 2.0)

    column_starts = []
    x_px = group_left_px
    for col_idx, width_px in enumerate(column_widths_px):
        column_starts.append(x_px / axes_width_px)
        x_px += width_px
        if col_idx < columns - 1:
            x_px += column_gap_px

    for col_idx, text, _point in text_artists:
        text.set_x(column_starts[col_idx])

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for _col_idx, text, point in text_artists:
        bbox = text.get_window_extent(renderer)
        y_px = (bbox.y0 + bbox.y1) / 2.0
        logo_x, logo_y = key_ax.transAxes.inverted().transform(
            (bbox.x1 + logo_gap_px + (logo_target_px / 2.0), y_px)
        )

        logo_path = _logo_path_for_model(point.model_label, point.model_pretty)
        if not logo_path:
            continue
        try:
            image = _normalized_logo_image(plt.imread(str(logo_path)))
        except (OSError, ValueError):
            image = None
        if image is None or image.shape[0] <= 0:
            continue

        logo = OffsetImage(
            image,
            zoom=logo_target_px / float(image.shape[0]),
        )
        key_ax.add_artist(
            AnnotationBbox(
                logo,
                (logo_x, logo_y),
                xycoords=key_ax.transAxes,
                frameon=False,
                box_alignment=(0.5, 0.5),
                zorder=3,
            )
        )


def _draw_panel_fast(
    *,
    fig: plt.Figure,
    ax: plt.Axes,
    points: List[ScatterPoint],
    spec: PanelSpec,
    labels_by_model: Dict[str, str],
    font_scale: float,
    label_font_scale: float,
    axis_label_font_scale: float,
    marker_scale: float,
    legend_marker_scale: float,
) -> None:
    ax.set_box_aspect(1)
    _add_trend_lines(ax=ax, points=points, point_style="condition")
    legend = _draw_condition_points(
        ax=ax,
        points=points,
        show_error_bars=False,
        marker_scale=marker_scale,
        legend_loc="upper right",
        legend_marker_scale=legend_marker_scale,
        size_by_model_size=False,
    )
    if legend is not None:
        frame = legend.get_frame()
        frame.set_boxstyle("round,pad=0.45,rounding_size=0.12")
        frame.set_linewidth(LEGEND_FRAME_LINEWIDTH)
        frame.set_edgecolor(LEGEND_FRAME_EDGE)
        frame.set_facecolor("white")
        for text in legend.get_texts():
            text.set_fontsize(13.8 * font_scale)

    ax.set_xlim(-0.5, 3.5)
    ax.set_xticks(_axis_ticks_by_step(-0.5, 3.5, step=0.5))
    ax.set_ylim(*spec.y_limits)
    ax.set_yticks(
        _axis_ticks_by_step(spec.y_limits[0], spec.y_limits[1], step=spec.y_tick_step)
    )
    axis_label_size = 15.0 * font_scale * axis_label_font_scale
    ax.set_xlabel(_delta_judge_score_axis_label(), fontsize=axis_label_size)
    ax.set_ylabel(
        _delta_collusion_axis_label(
            invert_delta_collusion=True,
            delta_collusion_metric=spec.delta_metric,
        ),
        fontsize=axis_label_size,
    )
    ax.set_title(spec.title, pad=16, fontsize=18.0 * font_scale)

    for spine in ax.spines.values():
        spine.set_linewidth(1.0 * STROKE_SCALE)
    ax.tick_params(
        width=1.05 * STROKE_SCALE,
        length=4.6 * STROKE_SCALE,
        labelsize=12.0 * font_scale,
    )
    ax.set_axisbelow(True)
    ax.grid(
        True,
        linestyle="--",
        linewidth=GRID_LINEWIDTH_SCALE * STROKE_SCALE,
        alpha=GRID_ALPHA,
    )
    reference_kwargs = {
        "color": "#5f5f5f",
        "linestyle": "--",
        "linewidth": REFERENCE_LINEWIDTH_SCALE * STROKE_SCALE,
        "alpha": REFERENCE_ALPHA,
        "zorder": REFERENCE_ZORDER,
    }
    ax.axhline(_x_reference_value(control_minus_condition_x=True), **reference_kwargs)
    ax.axvline(0.0, **reference_kwargs)

    _add_collision_aware_labels(
        fig=fig,
        ax=ax,
        points=points,
        legend=legend,
        label_fontsize=9.0 * font_scale * label_font_scale,
        marker_scale=marker_scale,
        label_text_by_model=labels_by_model,
    )


def _render_panel_image(
    *,
    points: List[ScatterPoint],
    spec: PanelSpec,
    labels_by_model: Dict[str, str],
    dpi: int,
    font_scale: float,
    label_font_scale: float,
    axis_label_font_scale: float,
    marker_scale: float,
    legend_marker_scale: float,
) -> Image.Image:
    fig, ax = plt.subplots(figsize=DEFAULT_PANEL_FIGSIZE, dpi=100)
    _draw_panel_fast(
        fig=fig,
        ax=ax,
        points=points,
        spec=spec,
        labels_by_model=labels_by_model,
        font_scale=font_scale,
        label_font_scale=label_font_scale,
        axis_label_font_scale=axis_label_font_scale,
        marker_scale=marker_scale,
        legend_marker_scale=legend_marker_scale,
    )
    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    with Image.open(buffer) as image:
        return image.convert("RGBA")


def _render_model_key_image(
    *,
    models: Sequence[ScatterPoint],
    labels_by_model: Dict[str, str],
    width_px: int,
    height_px: int,
    rows: int,
    font_size: float,
    logo_scale: float,
    dpi: int,
) -> Image.Image:
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=100)
    _draw_bottom_model_key(
        fig=fig,
        models=models,
        labels_by_model=labels_by_model,
        rows=rows,
        font_size=font_size,
        logo_scale=logo_scale,
        bounds=(0.025, 0.06, 0.95, 0.88),
    )
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    with Image.open(buffer) as image:
        return image.convert("RGBA")


def _paste_centered(
    *,
    canvas: Image.Image,
    image: Image.Image,
    cell_left: int,
    cell_top: int,
    cell_width: int,
    cell_height: int,
) -> None:
    x = cell_left + max(0, (cell_width - image.width) // 2)
    y = cell_top + max(0, (cell_height - image.height) // 2)
    canvas.alpha_composite(image, dest=(x, y))


def plot_grid(
    *,
    gpt54_csv: Path,
    opus46_csv: Path,
    gpt54nano_csv: Path,
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
    for csv_path in (gpt54_csv, opus46_csv, gpt54nano_csv):
        if not csv_path.exists():
            raise SystemExit(f"Missing input CSV: {csv_path}")

    labels_by_model, model_key_models = _model_key_points(gpt54_csv)

    panel_rows: List[Tuple[Image.Image, Image.Image]] = []
    for judge_label, csv_path in _judge_rows(
        gpt54_csv=gpt54_csv,
        opus46_csv=opus46_csv,
        gpt54nano_csv=gpt54nano_csv,
    ):
        left_points, right_points = _points_for_row(csv_path)
        left_spec, right_spec = _metric_specs(judge_label)
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a 3x2 Jira delta scatter grid for GPT-5.4, Opus-4.6, and GPT-5.4-nano judges."
    )
    parser.add_argument("--gpt54-csv", type=Path, default=DEFAULT_GPT54_CSV)
    parser.add_argument("--opus46-csv", type=Path, default=DEFAULT_OPUS46_CSV)
    parser.add_argument("--gpt54nano-csv", type=Path, default=DEFAULT_GPT54_NANO_CSV)
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
    parser.add_argument("--model-key-rows", type=int, default=4)
    parser.add_argument("--model-key-font-size", type=float, default=21.0)
    parser.add_argument(
        "--model-key-logo-scale",
        type=float,
        default=DEFAULT_MODEL_KEY_LOGO_SCALE,
    )
    args = parser.parse_args()

    plot_grid(
        gpt54_csv=args.gpt54_csv,
        opus46_csv=args.opus46_csv,
        gpt54nano_csv=args.gpt54nano_csv,
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
    print(args.out)


if __name__ == "__main__":
    main()
