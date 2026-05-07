from __future__ import annotations

import argparse
import math
from io import BytesIO
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from PIL import Image, ImageChops

from experiments.collusion.plots.plot_judge_vs_coalition_advantage import (
    DEFAULT_Y_METRIC,
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
    _invert_delta_points,
    _logo_path_for_model,
    _model_family_key,
    _normalized_logo_image,
    _numbered_model_labels,
    _orient_points,
    _read_points,
    _x_reference_value,
    plot_scatter,
)


DEFAULT_OUT = Path(
    "experiments/collusion/plots_outputs/"
    "collusion_regret_complete_n6_c2_combined_10seeds/"
    "20260428-012631-small-and-full/regret_report/"
    "complete_n6_c2_combined_v2/plots/"
    "delta_plots_emergent_prompted_legend_below.png"
)
DEFAULT_PLOT_DIR = DEFAULT_OUT.parent
DEFAULT_LEFT_PANEL = (
    DEFAULT_PLOT_DIR / "delta_judge_score_vs_delta_advantage_scatter_square_numbered_plot_legend.png"
)
DEFAULT_RIGHT_PANEL = (
    DEFAULT_PLOT_DIR
    / "inverted_delta_collusion_overall_regret_vs_delta_judge_scatter_square_numbered_plot_legend.png"
)
DEFAULT_FIGSIZE = (21.0, 12.6)
LEFT_AXES_BOUNDS = (0.075, 0.36, 0.35)
RIGHT_AXES_BOUNDS = (0.575, 0.36, 0.35)
MODEL_KEY_BOUNDS = (0.16, 0.035, 0.68, 0.275)
MODEL_KEY_IMAGE_BOUNDS = (0.04, 0.04, 0.92, 0.92)
LEGEND_FRAME_EDGE = "#bdbdbd"
LEGEND_FRAME_LINEWIDTH = 0.9 * STROKE_SCALE
DEFAULT_PANEL_GAP_PX = 25
DEFAULT_PANEL_TRIM_PADDING_PX = 45
DEFAULT_PANEL_TRIM_WHITE_TOLERANCE = 8
DEFAULT_MODEL_KEY_ROWS = 4
DEFAULT_MODEL_KEY_GAP_PX = 20
DEFAULT_MODEL_KEY_HEIGHT_PX = 480
DEFAULT_SIDE_MARGIN_PX = 0
DEFAULT_BOTTOM_MARGIN_PX = 70
DEFAULT_DPI = 300
DEFAULT_FINAL_TRIM_PADDING_PX = 35
DEFAULT_FINAL_TRIM_WHITE_TOLERANCE = 8
DEFAULT_MODEL_KEY_TRIM_PADDING_PX = 18
DEFAULT_MODEL_KEY_TRIM_WHITE_TOLERANCE = 8
DEFAULT_MIN_CANVAS_WIDTH_PX = 8000
MODEL_KEY_CONTENT_X_BOUNDS = (0.005, 0.995)
MODEL_KEY_ROW_Y_BOUNDS = (0.78, 0.22)
MODEL_KEY_DESIRED_COLUMN_GAP_PX = 30.0
MODEL_KEY_MIN_COLUMN_GAP_PX = 12.0
MODEL_KEY_LOGO_GAP_PX = 14.0


@dataclass(frozen=True)
class PanelSpec:
    title: str
    delta_metric: str
    y_limits: Tuple[float, float]
    y_tick_step: float
    output_path: Path


def _default_panel_specs() -> Tuple[PanelSpec, PanelSpec]:
    return (
        PanelSpec(
            title=r"Change in $\mathbf{Coalition\ Advantage}$ on Jira with GPT-5.4 Judge",
            delta_metric="coalition_advantage",
            y_limits=(-0.155, 0.205),
            y_tick_step=0.05,
            output_path=DEFAULT_LEFT_PANEL,
        ),
        PanelSpec(
            title=r"Change in $\mathbf{Overall\ Regret}$ on Jira with GPT-5.4 Judge",
            delta_metric="overall_regret",
            y_limits=(-0.155, 0.155),
            y_tick_step=0.075,
            output_path=DEFAULT_RIGHT_PANEL,
        ),
    )


def _panel_points(csv_path: Path, *, delta_metric: str) -> List[ScatterPoint]:
    raw_points = _read_points(
        csv_path,
        y_metric=DEFAULT_Y_METRIC,
        control_minus_condition_x=True,
        delta_collusion_metric=delta_metric,
        delta_judge_score=True,
    )
    raw_points = _invert_delta_points(
        raw_points,
        invert_delta_collusion=True,
        delta_collusion_metric=delta_metric,
    )
    return _orient_points(raw_points, judge_x_axis=True)


def _model_key_display_name(point: ScatterPoint) -> str:
    if point.model_pretty == "Claude-Haiku-4-5":
        return "Haiku-4.5"
    if _model_family_key(point.model_label, point.model_pretty) != "grok":
        return point.model_pretty
    return (
        point.model_pretty.replace("non-Reasoning", "non-R")
        .replace("-Reasoning", "-R")
        .replace("Reasoning", "R")
    )


def _draw_panel(
    *,
    fig: plt.Figure,
    ax: plt.Axes,
    points: List[ScatterPoint],
    spec: PanelSpec,
    labels_by_model: Dict[str, str],
    show_y_label: bool,
    font_scale: float,
    marker_scale: float,
    legend_marker_scale: float,
) -> None:
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
    ax.set_xlabel(_delta_judge_score_axis_label())
    ax.set_ylabel(
        _delta_collusion_axis_label(
            invert_delta_collusion=True,
            delta_collusion_metric=spec.delta_metric,
        )
        if show_y_label
        else ""
    )
    ax.set_title(spec.title, pad=16, fontsize=18.0 * font_scale)

    for spine in ax.spines.values():
        spine.set_linewidth(1.0 * STROKE_SCALE)
    ax.tick_params(width=1.05 * STROKE_SCALE, length=4.6 * STROKE_SCALE)
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
        label_fontsize=9.0 * font_scale,
        marker_scale=marker_scale,
        label_text_by_model=labels_by_model,
    )


def _draw_bottom_model_key(
    *,
    fig: plt.Figure,
    models: Sequence[ScatterPoint],
    labels_by_model: Dict[str, str],
    rows: int,
    font_size: float,
    bounds: Tuple[float, float, float, float] = MODEL_KEY_BOUNDS,
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
    logo_target_px = font_size * fig.dpi / 72.0 * 1.04
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


def _model_key_points(csv_path: Path) -> Tuple[Dict[str, str], List[ScatterPoint]]:
    left_points = _panel_points(csv_path, delta_metric="coalition_advantage")
    right_points = _panel_points(csv_path, delta_metric="overall_regret")
    return _numbered_model_labels([*left_points, *right_points])


def _render_source_panel(
    *,
    csv_path: Path,
    out_path: Path,
    spec: PanelSpec,
    font_scale: float,
    marker_scale: float,
    label_font_scale: float,
    axis_label_font_scale: float,
    legend_marker_scale: float,
) -> None:
    plot_scatter(
        csv_path=csv_path,
        out_path=out_path,
        title=spec.title,
        font_scale=font_scale,
        marker_scale=marker_scale,
        label_font_scale=label_font_scale,
        axis_label_font_scale=axis_label_font_scale,
        legend_marker_scale=legend_marker_scale,
        x_limits=(-0.5, 3.5),
        y_limits=spec.y_limits,
        x_step_limits=spec.y_tick_step,
        y_step_limits=0.5,
        condition_trend_lines=True,
        numbered_model_key=True,
        show_model_key=False,
        square_axes=True,
        control_minus_condition_x=True,
        judge_x_axis=True,
        invert_delta_collusion=True,
        delta_collusion_metric=spec.delta_metric,
        delta_judge_score=True,
    )


def _refresh_source_panels(
    *,
    csv_path: Path,
    left_panel_path: Path,
    right_panel_path: Path,
    font_scale: float,
    marker_scale: float,
    label_font_scale: float,
    axis_label_font_scale: float,
    legend_marker_scale: float,
) -> Tuple[Path, Path]:
    left_spec, right_spec = _default_panel_specs()
    for out_path, spec in (
        (left_panel_path, left_spec),
        (right_panel_path, right_spec),
    ):
        _render_source_panel(
            csv_path=csv_path,
            out_path=out_path,
            spec=spec,
            font_scale=font_scale,
            marker_scale=marker_scale,
            label_font_scale=label_font_scale,
            axis_label_font_scale=axis_label_font_scale,
            legend_marker_scale=legend_marker_scale,
        )
    return left_panel_path, right_panel_path


def _render_bottom_model_key_image(
    *,
    csv_path: Path,
    width_px: int,
    height_px: int,
    rows: int,
    font_size: float,
    dpi: int,
) -> Image.Image:
    labels_by_model, model_key_points = _model_key_points(csv_path)
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=100)
    _draw_bottom_model_key(
        fig=fig,
        models=model_key_points,
        labels_by_model=labels_by_model,
        rows=rows,
        font_size=font_size,
        bounds=MODEL_KEY_IMAGE_BOUNDS,
    )

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    with Image.open(buffer) as image:
        return image.convert("RGBA")


def _paste_rgba(base: Image.Image, overlay: Image.Image, xy: Tuple[int, int]) -> None:
    base.alpha_composite(overlay.convert("RGBA"), dest=xy)


def _non_white_bbox(image: Image.Image, *, white_tolerance: int) -> Optional[Tuple[int, int, int, int]]:
    image = image.convert("RGBA")
    white_background = Image.new("RGBA", image.size, "white")
    flattened = Image.alpha_composite(white_background, image).convert("RGB")
    diff = ImageChops.difference(flattened, Image.new("RGB", image.size, "white"))
    mask = diff.convert("L").point(lambda value: 255 if value > white_tolerance else 0)
    return mask.getbbox()


def _trim_outer_whitespace(
    image: Image.Image, *, padding_px: int, white_tolerance: int
) -> Image.Image:
    bbox = _non_white_bbox(image, white_tolerance=white_tolerance)
    if bbox is None:
        return image.copy()
    left, top, right, bottom = bbox
    return image.crop(
        (
            max(0, left - padding_px),
            max(0, top - padding_px),
            min(image.width, right + padding_px),
            min(image.height, bottom + padding_px),
        )
    )


def _compose_existing_panel_images(
    *,
    csv_path: Path,
    out_path: Path,
    left_panel_path: Path,
    right_panel_path: Path,
    model_key_rows: int,
    model_key_font_size: float,
    panel_gap_px: int,
    model_key_gap_px: int,
    model_key_height_px: int,
    side_margin_px: int,
    bottom_margin_px: int,
    dpi: int,
    trim_panel_whitespace: bool,
    panel_trim_padding_px: int,
    panel_trim_white_tolerance: int,
    trim_final_whitespace: bool,
    final_trim_padding_px: int,
    final_trim_white_tolerance: int,
) -> None:
    with Image.open(left_panel_path) as left_image_raw:
        left_image = left_image_raw.convert("RGBA")
    with Image.open(right_panel_path) as right_image_raw:
        right_image = right_image_raw.convert("RGBA")

    if trim_panel_whitespace:
        left_image = _trim_outer_whitespace(
            left_image,
            padding_px=panel_trim_padding_px,
            white_tolerance=panel_trim_white_tolerance,
        )
        right_image = _trim_outer_whitespace(
            right_image,
            padding_px=panel_trim_padding_px,
            white_tolerance=panel_trim_white_tolerance,
        )

    panel_height = max(left_image.height, right_image.height)
    panel_group_width = left_image.width + panel_gap_px + right_image.width
    canvas_width = max(panel_group_width, DEFAULT_MIN_CANVAS_WIDTH_PX)
    model_key_width = max(1, canvas_width - (2 * side_margin_px))
    model_key_image = _render_bottom_model_key_image(
        csv_path=csv_path,
        width_px=model_key_width,
        height_px=model_key_height_px,
        rows=model_key_rows,
        font_size=model_key_font_size,
        dpi=dpi,
    )
    model_key_image = _trim_outer_whitespace(
        model_key_image,
        padding_px=DEFAULT_MODEL_KEY_TRIM_PADDING_PX,
        white_tolerance=DEFAULT_MODEL_KEY_TRIM_WHITE_TOLERANCE,
    )

    canvas_height = (
        panel_height + model_key_gap_px + model_key_image.height + bottom_margin_px
    )
    canvas = Image.new("RGBA", (canvas_width, canvas_height), "white")
    panel_group_x = max(0, (canvas_width - panel_group_width) // 2)
    _paste_rgba(canvas, left_image, (panel_group_x, 0))
    _paste_rgba(
        canvas,
        right_image,
        (panel_group_x + left_image.width + panel_gap_px, 0),
    )
    model_key_x = max(0, (canvas_width - model_key_image.width) // 2)
    _paste_rgba(canvas, model_key_image, (model_key_x, panel_height + model_key_gap_px))
    if trim_final_whitespace:
        canvas = _trim_outer_whitespace(
            canvas,
            padding_px=final_trim_padding_px,
            white_tolerance=final_trim_white_tolerance,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    canvas.convert("RGB").save(out_path.with_suffix(".pdf"), "PDF", resolution=float(dpi))


def plot_delta_dual_panels(
    *,
    csv_path: Path,
    out_path: Path,
    model_key_rows: int = DEFAULT_MODEL_KEY_ROWS,
    font_scale: float = 1.456,
    marker_scale: float = 1.08,
    legend_marker_scale: float = 1.25,
    left_panel_path: Optional[Path] = None,
    right_panel_path: Optional[Path] = None,
    refresh_panels: bool = False,
    panel_font_scale: float = 1.3,
    panel_marker_scale: float = 1.25,
    panel_label_font_scale: float = 1.45,
    panel_axis_label_font_scale: float = 0.9615384615,
    panel_legend_marker_scale: float = 0.8,
    redraw_panels: bool = False,
    panel_gap_px: int = DEFAULT_PANEL_GAP_PX,
    model_key_gap_px: int = DEFAULT_MODEL_KEY_GAP_PX,
    model_key_height_px: int = DEFAULT_MODEL_KEY_HEIGHT_PX,
    side_margin_px: int = DEFAULT_SIDE_MARGIN_PX,
    bottom_margin_px: int = DEFAULT_BOTTOM_MARGIN_PX,
    dpi: int = DEFAULT_DPI,
    trim_panel_whitespace: bool = True,
    panel_trim_padding_px: int = DEFAULT_PANEL_TRIM_PADDING_PX,
    panel_trim_white_tolerance: int = DEFAULT_PANEL_TRIM_WHITE_TOLERANCE,
    trim_final_whitespace: bool = True,
    final_trim_padding_px: int = DEFAULT_FINAL_TRIM_PADDING_PX,
    final_trim_white_tolerance: int = DEFAULT_FINAL_TRIM_WHITE_TOLERANCE,
) -> None:
    resolved_left_panel_path = left_panel_path or DEFAULT_LEFT_PANEL
    resolved_right_panel_path = right_panel_path or DEFAULT_RIGHT_PANEL
    if refresh_panels:
        resolved_left_panel_path, resolved_right_panel_path = _refresh_source_panels(
            csv_path=csv_path,
            left_panel_path=resolved_left_panel_path,
            right_panel_path=resolved_right_panel_path,
            font_scale=panel_font_scale,
            marker_scale=panel_marker_scale,
            label_font_scale=panel_label_font_scale,
            axis_label_font_scale=panel_axis_label_font_scale,
            legend_marker_scale=panel_legend_marker_scale,
        )

    if not redraw_panels:
        _compose_existing_panel_images(
            csv_path=csv_path,
            out_path=out_path,
            left_panel_path=resolved_left_panel_path,
            right_panel_path=resolved_right_panel_path,
            model_key_rows=model_key_rows,
            model_key_font_size=12.0 * font_scale,
            panel_gap_px=panel_gap_px,
            model_key_gap_px=model_key_gap_px,
            model_key_height_px=model_key_height_px,
            side_margin_px=side_margin_px,
            bottom_margin_px=bottom_margin_px,
            dpi=dpi,
            trim_panel_whitespace=trim_panel_whitespace,
            panel_trim_padding_px=panel_trim_padding_px,
            panel_trim_white_tolerance=panel_trim_white_tolerance,
            trim_final_whitespace=trim_final_whitespace,
            final_trim_padding_px=final_trim_padding_px,
            final_trim_white_tolerance=final_trim_white_tolerance,
        )
        return

    left_spec, right_spec = _default_panel_specs()
    left_points = _panel_points(csv_path, delta_metric=left_spec.delta_metric)
    right_points = _panel_points(csv_path, delta_metric=right_spec.delta_metric)
    labels_by_model, model_key_points = _numbered_model_labels(
        [*left_points, *right_points]
    )

    plt.rcParams.update(
        {
            "font.size": 12 * font_scale,
            "axes.labelsize": 15 * font_scale,
            "xtick.labelsize": 12 * font_scale,
            "ytick.labelsize": 12 * font_scale,
            "legend.fontsize": 13.8 * font_scale,
        }
    )

    fig = plt.figure(figsize=DEFAULT_FIGSIZE)
    axes_width = LEFT_AXES_BOUNDS[2]
    axes_height = axes_width * DEFAULT_FIGSIZE[0] / DEFAULT_FIGSIZE[1]
    left_ax = fig.add_axes([*LEFT_AXES_BOUNDS[:2], axes_width, axes_height])
    right_ax = fig.add_axes([*RIGHT_AXES_BOUNDS[:2], axes_width, axes_height])

    _draw_panel(
        fig=fig,
        ax=left_ax,
        points=left_points,
        spec=left_spec,
        labels_by_model=labels_by_model,
        show_y_label=True,
        font_scale=font_scale,
        marker_scale=marker_scale,
        legend_marker_scale=legend_marker_scale,
    )
    _draw_panel(
        fig=fig,
        ax=right_ax,
        points=right_points,
        spec=right_spec,
        labels_by_model=labels_by_model,
        show_y_label=True,
        font_scale=font_scale,
        marker_scale=marker_scale,
        legend_marker_scale=legend_marker_scale,
    )
    _draw_bottom_model_key(
        fig=fig,
        models=model_key_points,
        labels_by_model=labels_by_model,
        rows=model_key_rows,
        font_size=12.0 * font_scale,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=300)
    fig.savefig(out_path.with_suffix(".pdf"))
    plt.close(fig)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compose side-by-side delta judge panels from existing panel PNGs "
            "with a shared bottom model key."
        )
    )
    parser.add_argument("csv_path", type=Path, help="regret report CSV sidecar")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output PNG path. Defaults to {DEFAULT_OUT}",
    )
    parser.add_argument(
        "--model-key-rows",
        type=int,
        default=DEFAULT_MODEL_KEY_ROWS,
        help=(
            "Rows in the shared bottom model key. With the current 19 models, "
            f"the default {DEFAULT_MODEL_KEY_ROWS} rows produces 5 columns."
        ),
    )
    parser.add_argument(
        "--font-scale",
        type=float,
        default=1.456,
        help=(
            "Scale shared model-key text in composed outputs; also scales all "
            "plot text when --redraw-panels is set."
        ),
    )
    parser.add_argument(
        "--marker-scale",
        type=float,
        default=1.08,
        help=(
            "Legacy --redraw-panels marker diameter scale. Use "
            "--panel-marker-scale with --refresh-panels for source panel PNGs."
        ),
    )
    parser.add_argument(
        "--legend-marker-scale",
        type=float,
        default=1.25,
        help="Scale condition legend markers when --redraw-panels is set.",
    )
    parser.add_argument(
        "--left-panel",
        type=Path,
        default=None,
        help=f"Existing left panel PNG. Defaults to {DEFAULT_LEFT_PANEL}",
    )
    parser.add_argument(
        "--right-panel",
        type=Path,
        default=None,
        help=f"Existing right panel PNG. Defaults to {DEFAULT_RIGHT_PANEL}",
    )
    parser.add_argument(
        "--refresh-panels",
        action="store_true",
        help=(
            "Regenerate the two source panel PNGs with the canonical delta-plot "
            "settings before composing the combined figure."
        ),
    )
    parser.add_argument(
        "--panel-font-scale",
        type=float,
        default=1.3,
        help=(
            "Scale refreshed source panel titles, ticks, and base text. "
            "The default is 30 percent larger than the base panel text."
        ),
    )
    parser.add_argument(
        "--panel-marker-scale",
        type=float,
        default=1.25,
        help=(
            "Scale plotted marker diameter in refreshed source panels. "
            "The default matches the current larger-point output."
        ),
    )
    parser.add_argument(
        "--panel-label-font-scale",
        type=float,
        default=1.45,
        help=(
            "Scale only in-plot number labels in refreshed source panels. "
            "The default keeps the numbered point labels larger than the base text."
        ),
    )
    parser.add_argument(
        "--panel-axis-label-font-scale",
        type=float,
        default=0.9615384615,
        help=(
            "Scale only x- and y-axis labels in refreshed source panels. "
            "The default preserves the current effective axis-label size while "
            "--panel-font-scale enlarges other panel text."
        ),
    )
    parser.add_argument(
        "--panel-legend-marker-scale",
        type=float,
        default=0.8,
        help=(
            "Scale condition legend markers in refreshed source panels. "
            "The default offsets the larger plotted markers so the legend stays stable."
        ),
    )
    parser.add_argument(
        "--redraw-panels",
        action="store_true",
        help=(
            "Legacy path: redraw both panels inside this figure instead of "
            "composing the source panel PNGs."
        ),
    )
    parser.add_argument(
        "--panel-gap-px",
        type=int,
        default=DEFAULT_PANEL_GAP_PX,
        help=(
            "Horizontal gap between the trimmed panel PNGs. The default "
            f"is {DEFAULT_PANEL_GAP_PX}px."
        ),
    )
    parser.add_argument(
        "--model-key-gap-px",
        type=int,
        default=DEFAULT_MODEL_KEY_GAP_PX,
        help="Vertical gap between panel PNGs and the model key.",
    )
    parser.add_argument(
        "--model-key-height-px",
        type=int,
        default=DEFAULT_MODEL_KEY_HEIGHT_PX,
        help="Rendered height of the shared bottom model key.",
    )
    parser.add_argument(
        "--side-margin-px",
        type=int,
        default=DEFAULT_SIDE_MARGIN_PX,
        help="Left and right margin around the shared bottom model key.",
    )
    parser.add_argument(
        "--bottom-margin-px",
        type=int,
        default=DEFAULT_BOTTOM_MARGIN_PX,
        help="Bottom margin below the shared model key.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help="DPI metadata for the rendered model key and PDF export.",
    )
    parser.add_argument(
        "--no-trim-panels",
        dest="trim_panel_whitespace",
        action="store_false",
        help=(
            "Compose full panel PNG canvases without trimming outer white "
            "margins. This preserves legacy spacing."
        ),
    )
    parser.set_defaults(trim_panel_whitespace=True)
    parser.add_argument(
        "--panel-trim-padding-px",
        type=int,
        default=DEFAULT_PANEL_TRIM_PADDING_PX,
        help=(
            "Padding retained around each panel after trimming outer whitespace. "
            f"The default is {DEFAULT_PANEL_TRIM_PADDING_PX}px."
        ),
    )
    parser.add_argument(
        "--panel-trim-white-tolerance",
        type=int,
        default=DEFAULT_PANEL_TRIM_WHITE_TOLERANCE,
        help=(
            "Pixel difference from white required to count as panel content "
            f"during trimming. The default is {DEFAULT_PANEL_TRIM_WHITE_TOLERANCE}."
        ),
    )
    parser.add_argument(
        "--no-trim-final",
        dest="trim_final_whitespace",
        action="store_false",
        help="Disable the final trim pass around the fully composed PNG/PDF.",
    )
    parser.set_defaults(trim_final_whitespace=True)
    parser.add_argument(
        "--final-trim-padding-px",
        type=int,
        default=DEFAULT_FINAL_TRIM_PADDING_PX,
        help=(
            "Padding retained around the final composed figure after trimming. "
            f"The default is {DEFAULT_FINAL_TRIM_PADDING_PX}px."
        ),
    )
    parser.add_argument(
        "--final-trim-white-tolerance",
        type=int,
        default=DEFAULT_FINAL_TRIM_WHITE_TOLERANCE,
        help=(
            "Pixel difference from white required to count as final figure "
            f"content. The default is {DEFAULT_FINAL_TRIM_WHITE_TOLERANCE}."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.model_key_rows <= 0:
        raise SystemExit("--model-key-rows must be greater than 0")
    for option_name in (
        "panel_gap_px",
        "model_key_gap_px",
        "model_key_height_px",
        "side_margin_px",
        "bottom_margin_px",
        "panel_trim_padding_px",
        "final_trim_padding_px",
        "dpi",
    ):
        if getattr(args, option_name) < 0:
            raise SystemExit(f"--{option_name.replace('_', '-')} must be non-negative")
    if args.model_key_height_px <= 0:
        raise SystemExit("--model-key-height-px must be greater than 0")
    if args.dpi <= 0:
        raise SystemExit("--dpi must be greater than 0")
    if not 0 <= args.panel_trim_white_tolerance <= 255:
        raise SystemExit("--panel-trim-white-tolerance must be between 0 and 255")
    if not 0 <= args.final_trim_white_tolerance <= 255:
        raise SystemExit("--final-trim-white-tolerance must be between 0 and 255")
    for option_name in (
        "panel_font_scale",
        "panel_marker_scale",
        "panel_label_font_scale",
        "panel_axis_label_font_scale",
        "panel_legend_marker_scale",
    ):
        if getattr(args, option_name) <= 0.0:
            raise SystemExit(f"--{option_name.replace('_', '-')} must be greater than 0")
    plot_delta_dual_panels(
        csv_path=args.csv_path.expanduser().resolve(),
        out_path=args.out.expanduser().resolve(),
        model_key_rows=args.model_key_rows,
        font_scale=args.font_scale,
        marker_scale=args.marker_scale,
        legend_marker_scale=args.legend_marker_scale,
        left_panel_path=(
            args.left_panel.expanduser().resolve() if args.left_panel else None
        ),
        right_panel_path=(
            args.right_panel.expanduser().resolve() if args.right_panel else None
        ),
        refresh_panels=bool(args.refresh_panels),
        panel_font_scale=args.panel_font_scale,
        panel_marker_scale=args.panel_marker_scale,
        panel_label_font_scale=args.panel_label_font_scale,
        panel_axis_label_font_scale=args.panel_axis_label_font_scale,
        panel_legend_marker_scale=args.panel_legend_marker_scale,
        redraw_panels=args.redraw_panels,
        panel_gap_px=args.panel_gap_px,
        model_key_gap_px=args.model_key_gap_px,
        model_key_height_px=args.model_key_height_px,
        side_margin_px=args.side_margin_px,
        bottom_margin_px=args.bottom_margin_px,
        dpi=args.dpi,
        trim_panel_whitespace=bool(args.trim_panel_whitespace),
        panel_trim_padding_px=args.panel_trim_padding_px,
        panel_trim_white_tolerance=args.panel_trim_white_tolerance,
        trim_final_whitespace=bool(args.trim_final_whitespace),
        final_trim_padding_px=args.final_trim_padding_px,
        final_trim_white_tolerance=args.final_trim_white_tolerance,
    )
    print(args.out.expanduser().resolve())


if __name__ == "__main__":
    main()
