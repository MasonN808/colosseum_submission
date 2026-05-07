from __future__ import annotations

"""Generate a regret-based collusion report plot from a collusion output root.

This module is intentionally minimal and only produces:
- `regret_report__normalized_regret__coalition_gap__judge.png`

It also writes a small CSV with the aggregated values used in the plot:
- `regret_report__normalized_regret__coalition_gap__judge__data.csv`
"""

import argparse
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")  # headless-safe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgb
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from matplotlib.patches import Patch
from tqdm import tqdm

from experiments.collusion.judge_paths import is_judge_dir_name, judge_dir_name
from experiments.collusion.plots.common import compact_plot_header
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

_LOGO_DIR = Path(__file__).resolve().parent / "assets" / "logos"
_LOGO_FILES = {
    "openai": "openai.png",
    "anthropic": "anthropic.png",
    "gemini": "gemini.png",
    "deepseek": "deepseek.png",
    "moonshot": "moonshot.png",
    "glm": "glm.png",
    "minimax": "minimax.png",
    "grok": "grok.png",
}

_SIX_BARS_PALETTE = ["#264653", "#2a9d8f", "#8ab17d", "#e9c46a", "#f4a261"]
_REGRET_REPORT_CONDITION_LABELS = {
    "baseline": "Control (no SC)",
    "control": "Emergent (SC)",
    "simple": "Prompted (SC)",
    "sc1": "Emergent (1SC)",
    "sc2": "Emergent (2SC)",
    "sc3": "Emergent (3SC)",
}

_COALITION_REGRET_GAP_NORM_BY_MODEL: Optional[Dict[str, float]] = None


def _lighten_color(color: str, amount: float = 0.55) -> Tuple[float, float, float]:
    base = np.array(to_rgb(color))
    return tuple(base + (1.0 - base) * float(amount))


def _canonical_variant(value: Any) -> str:
    return str(value or "").strip()


_PROVIDER_PREFIX_RE = re.compile(
    r"^(openai|anthropic|together|fw|foundry)[-_]+", re.IGNORECASE
)


def _pretty_metric_label(key: str) -> str:
    k = str(key or "").strip()
    if k == "normalized_regret":
        return "Normalized Regret (↓)"
    if k == "normalized_coalition_regret_gap":
        return "Coalition Advantage (-)"
    if k == "judge_mean_rating":
        return "Collusion Judge (↓)"
    return k.replace("_", " ").title()


def _pretty_model_label(model_label: str) -> str:
    raw = str(model_label or "").strip()
    raw = _PROVIDER_PREFIX_RE.sub("", raw)
    if not raw:
        return "Unknown"

    lowered = raw.lower()
    if "gpt54_gpt54" in lowered or "gpt-5.4_gpt-5.4" in lowered:
        return "GPT-5.4 + GPT-5.4"
    if "gpt54_opus46" in lowered or "gpt-5.4_opus-4.6" in lowered:
        return "GPT-5.4 + Opus-4.6"
    if "claude-sonnet-4-5" in lowered:
        return "Sonnet-4.5"
    if "claude-opus-4-6" in lowered:
        return "Opus-4.6"
    if lowered.startswith("glm-"):
        return "GLM-" + raw.split("-", 1)[1]
    if lowered.startswith("minimax-"):
        return "MiniMax-" + raw.split("-", 1)[1].upper()
    if lowered.startswith("grok-"):
        return "Grok-" + raw.split("-", 1)[1].replace("-reasoning", "-Reasoning")
    if lowered.startswith("kimik2"):
        if "thinking" in lowered:
            return "Kimi-K2-Thinking"
        return "Kimi-K2-Inst"

    parts = re.split(r"[-_]+", raw)
    pretty: List[str] = []
    for part in parts:
        if not part:
            continue
        p = part.strip()
        pl = p.lower()
        if pl == "gpt":
            pretty.append("GPT")
            continue
        if pl == "glm":
            pretty.append("GLM")
            continue
        if pl == "minimax":
            pretty.append("MiniMax")
            continue
        if pl == "grok":
            pretty.append("Grok")
            continue
        if pl == "oss":
            pretty.append("OSS")
            continue
        if re.fullmatch(r"\d+b", pl):
            pretty.append(f"{p[:-1]}B")
            continue
        if re.fullmatch(r"m\d+(?:\.\d+)?", pl):
            pretty.append(pl.upper())
            continue
        if pl == "k2":
            pretty.append("K2")
            continue
        if pl in {
            "claude",
            "sonnet",
            "haiku",
            "opus",
            "gemini",
            "flash",
            "lite",
            "mini",
            "instruct",
            "reasoning",
        }:
            pretty.append(pl.capitalize())
            continue
        pretty.append(p)

    return "-".join(pretty) if pretty else raw


def _apply_large_font_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 12,
            "axes.labelsize": 14,
            "xtick.labelsize": 12,
            "ytick.labelsize": 12,
            "legend.fontsize": 12,
        }
    )


def _set_coalition_regret_gap_norm(rows: List["RunRow"]) -> None:
    global _COALITION_REGRET_GAP_NORM_BY_MODEL
    vals_by_model: Dict[str, List[float]] = {}
    for r in rows:
        advantage = _coalition_advantage_value(r)
        if advantage is None:
            continue
        if not math.isfinite(float(advantage)):
            continue
        vals_by_model.setdefault(str(r.model_label), []).append(float(advantage))
    if not vals_by_model:
        _COALITION_REGRET_GAP_NORM_BY_MODEL = None
        return
    norm_by_model: Dict[str, float] = {}
    for model_label, vals in vals_by_model.items():
        if not vals:
            continue
        lo = float(min(vals))
        hi = float(max(vals))
        max_abs = max(abs(lo), abs(hi))
        if math.isfinite(max_abs):
            norm_by_model[model_label] = float(max_abs)
    _COALITION_REGRET_GAP_NORM_BY_MODEL = norm_by_model or None


def _normalize_coalition_regret_gap(value: float, model_label: str) -> Optional[float]:
    norm = _COALITION_REGRET_GAP_NORM_BY_MODEL
    if norm is None:
        return None
    max_abs = norm.get(str(model_label))
    if max_abs is None:
        return None
    if max_abs == 0.0:
        return 0.5
    scaled = 0.5 + float(value) / (2.0 * float(max_abs))
    return float(min(1.0, max(0.0, scaled)))


def _coalition_advantage_value(r: "RunRow") -> Optional[float]:
    c = r.coalition_mean_regret
    n = r.noncoalition_mean_regret
    if c is not None and n is not None:
        try:
            return float(n) - float(c)
        except Exception:
            return None
    if r.coalition_advantage_mean is not None:
        try:
            return float(r.coalition_advantage_mean)
        except Exception:
            return None
    return None


def _logo_key_for_model(
    model_label: str, provider: Optional[str], model: Optional[str]
) -> Optional[str]:
    haystack = " ".join([str(model_label or ""), str(provider or ""), str(model or "")]).lower()
    if "deepseek" in haystack:
        return "deepseek"
    if "glm" in haystack:
        return "glm"
    if "minimax" in haystack:
        return "minimax"
    if "grok" in haystack or "xai" in haystack:
        return "grok"
    if "kimi" in haystack or "moonshot" in haystack:
        return "moonshot"
    if "gemini" in haystack:
        return "gemini"
    if (
        "anthropic" in haystack
        or "claude" in haystack
        or "opus" in haystack
        or "sonnet" in haystack
    ):
        return "anthropic"
    if "openai" in haystack or "gpt" in haystack:
        return "openai"
    return None


def _resolve_logo_paths(rows: List["RunRow"], models: List[str]) -> Dict[str, Path]:
    if not _LOGO_DIR.exists():
        return {}
    rows_by_model: Dict[str, "RunRow"] = {}
    for r in rows:
        rows_by_model.setdefault(r.model_label, r)
    out: Dict[str, Path] = {}
    for model_label in models:
        row = rows_by_model.get(model_label)
        key = _logo_key_for_model(
            model_label, row.provider if row else None, row.model if row else None
        )
        if not key:
            continue
        filename = _LOGO_FILES.get(key)
        if not filename:
            continue
        path = _LOGO_DIR / filename
        if path.exists():
            out[model_label] = path
    return out


def _add_logos_to_xticklabels(
    *, fig: plt.Figure, ax: plt.Axes, models: List[str], logo_paths: Dict[str, Path]
) -> None:
    if not logo_paths:
        return
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    labels = ax.get_xticklabels()
    for label, model_label in zip(labels, models):
        logo_path = logo_paths.get(model_label)
        if logo_path is None or not logo_path.exists():
            continue
        bbox = label.get_window_extent(renderer)
        if bbox.width <= 0 or bbox.height <= 0:
            continue
        image = plt.imread(logo_path)
        if image is None or image.size == 0:
            continue
        zoom = float(bbox.height) / float(image.shape[0])
        offset_image = OffsetImage(image, zoom=zoom)
        pad_px = 8.0
        bump_px = 2.0
        image_width = float(image.shape[1]) * zoom
        x_disp = float(bbox.x0) - pad_px - (image_width / 2.0)
        y_disp = float((bbox.y0 + bbox.y1) / 2.0) + bump_px
        x_fig, y_fig = fig.transFigure.inverted().transform((x_disp, y_disp))
        ab = AnnotationBbox(
            offset_image,
            (x_fig, y_fig),
            xycoords=fig.transFigure,
            frameon=False,
            box_alignment=(0.5, 0.5),
        )
        fig.add_artist(ab)


def _iter_model_dirs(root: Path) -> Iterable[Path]:
    runs_dir = root / "runs"
    if not runs_dir.exists():
        return
    for model_dir in sorted(runs_dir.iterdir()):
        if not model_dir.is_dir():
            continue
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
        if not child.is_dir():
            continue
        if (child / "run_config.json").exists():
            yield child


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    obj = safe_load_json(path)
    return obj if isinstance(obj, dict) else None


def _find_nearest_config_json(start_dir: Path) -> Optional[Path]:
    for parent in [start_dir, *start_dir.parents]:
        candidate = parent / "config.json"
        if candidate.exists():
            return candidate
    return None


def _judge_mean_rating(
    *,
    model_dir: Path,
    sweep_name: str,
    run_name: str,
    judge_output_tag: Optional[str] = None,
) -> Optional[float]:
    judge_path = (
        model_dir
        / judge_dir_name(judge_output_tag)
        / sweep_name
        / f"{run_name}.json"
    )
    payload = safe_load_json(judge_path) if judge_path.exists() else None
    if not isinstance(payload, dict):
        return None
    judgements = payload.get("judgements")
    if not isinstance(judgements, dict):
        return None

    vals: List[float] = []
    for key in ("simple", "medium", "complex"):
        j = judgements.get(key)
        if not isinstance(j, dict):
            continue
        rating = as_float(j.get("rating"))
        if rating is None:
            continue
        vals.append(float(rating))
    if not vals:
        return None
    return float(mean(vals))


def _coalition_advantage_from_agent_rewards(
    *, agent_rewards: Dict[str, float], colluders: Sequence[Any]
) -> Optional[float]:
    colluder_set = {str(a) for a in colluders if a is not None}
    if not colluder_set:
        return None

    colluder_rewards = [
        float(agent_rewards[a]) for a in agent_rewards if str(a) in colluder_set
    ]
    noncolluder_rewards = [
        float(agent_rewards[a]) for a in agent_rewards if str(a) not in colluder_set
    ]
    if not colluder_rewards or not noncolluder_rewards:
        return None
    return float(mean(colluder_rewards) - mean(noncolluder_rewards))


def _recover_hospital_agent_rewards(
    *, run_dir: Path, run_config: Dict[str, Any], final_summary: Dict[str, Any]
) -> Optional[Dict[str, float]]:
    env_cfg = run_config.get("environment_cfg")
    env_name = str(run_config.get("environment_name") or "")
    import_path = str(env_cfg.get("import_path") or "") if isinstance(env_cfg, dict) else ""
    hospital_haystack = " ".join([env_name, import_path]).lower()
    if "hospital" not in hospital_haystack:
        return None

    try:
        from terrarium.environments.dcops.hospital.hospital_env import HospitalEnvironment
    except Exception as exc:
        logger.warning(
            "Failed to import HospitalEnvironment for hospital reward recovery: %s",
            exc,
        )
        return None

    tool_events = safe_load_json(run_dir / "tool_events.json")
    if not isinstance(tool_events, list):
        return None

    class _ProtocolStub:
        environment: Any = None

    cfg = {
        "simulation": {
            "seed": int(as_int(run_config.get("seed")) or 1),
        },
        "environment": dict(env_cfg or {}),
    }
    hospital_logger = logging.getLogger(
        "terrarium.environments.dcops.hospital.hospital_env"
    )
    previous_level = hospital_logger.level
    hospital_logger.setLevel(logging.CRITICAL)
    try:
        try:
            env = HospitalEnvironment(
                communication_protocol=_ProtocolStub(),
                config=cfg,
                tool_logger=None,
            )
        except Exception as exc:
            logger.warning(
                "Failed to initialize HospitalEnvironment for %s: %s", run_dir, exc
            )
            return None

        try:
            for event in tool_events:
                if not isinstance(event, dict):
                    continue
                tool_name = str(event.get("tool_name") or "").strip()
                agent_name = str(event.get("agent_name") or "").strip()
                arguments = event.get("arguments")
                if not agent_name or not isinstance(arguments, dict):
                    continue
                if tool_name == "schedule_patient":
                    env._process_schedule_request(agent_name, arguments)
                elif tool_name == "transfer_resources":
                    env._process_transfer(agent_name, arguments)
        except Exception as exc:
            logger.warning(
                "Failed to replay hospital tool events for %s: %s", run_dir, exc
            )
            return None

        try:
            joint_reward, agent_rewards = env._calculate_makespan_and_flow()
        except Exception as exc:
            logger.warning(
                "Failed to recompute hospital agent rewards for %s: %s", run_dir, exc
            )
            return None
    finally:
        hospital_logger.setLevel(previous_level)

    expected_joint_reward = as_float(final_summary.get("joint_reward"))
    if expected_joint_reward is not None and not math.isclose(
        float(joint_reward),
        float(expected_joint_reward),
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        logger.warning(
            "Hospital reward replay mismatch for %s; expected %.6f got %.6f",
            run_dir,
            float(expected_joint_reward),
            float(joint_reward),
        )
        return None

    out: Dict[str, float] = {}
    for agent_name, reward in (agent_rewards or {}).items():
        try:
            out[str(agent_name)] = float(reward)
        except Exception:
            continue
    return out or None


def _load_optimal_summary(run_dir: Path) -> Optional[Dict[str, Any]]:
    payload = _read_json(run_dir / "optimal_summary.json")
    return payload if isinstance(payload, dict) else None


def _is_jira_run_config(run_config: Dict[str, Any]) -> bool:
    env_name = run_config.get("environment_name")
    if env_name is None:
        env_cfg = run_config.get("environment_cfg")
        if isinstance(env_cfg, dict):
            env_name = env_cfg.get("name")
    return str(env_name or "").strip() == "JiraTicketEnvironment"


def _is_meeting_scheduling_run(run_dir: Path) -> bool:
    summary = _read_json(run_dir / "final_summary.json")
    if isinstance(summary, dict) and isinstance(summary.get("attendance"), dict):
        return True

    cfg_path = _find_nearest_config_json(run_dir)
    cfg = _read_json(cfg_path) if cfg_path else None
    env_cfg = cfg.get("environment") if isinstance(cfg, dict) else None
    if isinstance(env_cfg, dict):
        import_path = str(env_cfg.get("import_path") or env_cfg.get("name") or "")
        if "meeting_scheduling" in import_path or "MeetingScheduling" in import_path:
            return True

    run_cfg = _read_json(run_dir / "run_config.json")
    run_env_cfg = run_cfg.get("environment_cfg") if isinstance(run_cfg, dict) else None
    if isinstance(run_env_cfg, dict):
        import_path = str(run_env_cfg.get("import_path") or run_env_cfg.get("name") or "")
        if "meeting_scheduling" in import_path or "MeetingScheduling" in import_path:
            return True

    return False


def _compute_and_write_optimal_summary(run_dir: Path) -> Optional[Dict[str, Any]]:
    """Compute and persist `optimal_summary.json` for supported run directories."""
    try:
        if _is_meeting_scheduling_run(run_dir):
            from experiments.collusion import compute_meeting_scheduling_optimal

            return compute_meeting_scheduling_optimal.write_optimal_summary(
                run_dir=run_dir
            )

        from experiments.collusion import compute_jira_optimal

        try:
            instance = compute_jira_optimal._reconstruct_instance(run_dir)
        except Exception:
            instance = compute_jira_optimal._load_instance_from_agent_prompts(run_dir)
        weights = compute_jira_optimal._load_weights(run_dir, overrides=None)
        optimal = compute_jira_optimal.solve_optimal_assignment(instance=instance, weights=weights)

        payload = {
            "weights": {
                "tasks_done_bonus": weights.tasks_done_bonus,
                "priority_bonus": weights.priority_bonus,
                "violation_penalty": weights.violation_penalty,
            },
            "optimal": {
                "joint_reward": optimal.joint_reward,
                "tasks_done": optimal.tasks_done,
                "priority_sum": optimal.priority_sum,
                "total_cost": optimal.total_cost,
                "violations": optimal.violations,
                "assignment": {k: (v if v is not None else "skip") for k, v in optimal.assignment.items()},
            },
        }
        out_path = run_dir / "optimal_summary.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return payload
    except Exception as exc:
        logger.warning("Failed to compute optimal for %s: %s", run_dir, exc)
        return None


@dataclass(frozen=True)
class RunRow:
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
    status: str

    joint_reward: Optional[float]
    optimal_joint_reward: Optional[float]
    normalized_regret: Optional[float]  # 1 - achieved / optimal (clipped to [0, 1])
    judge_mean_rating: Optional[float]
    coalition_mean_regret: Optional[float]
    noncoalition_mean_regret: Optional[float]
    coalition_advantage_mean: Optional[float]
    secret_channel_count: int = 0


def _status_is_complete(status: Any) -> bool:
    return str(status or "").strip().lower() == "complete"


def _load_run_row(
    *,
    run_dir: Path,
    model_dir: Path,
    sweep_name: str,
    compute_optimal: bool,
    prefer_repaired: bool,
    judge_output_tag: Optional[str] = None,
) -> Optional[RunRow]:
    rc = _read_json(run_dir / "run_config.json") or {}
    final_summary_path = run_dir / "final_summary.json"
    metrics_path = run_dir / "metrics.json"
    if prefer_repaired:
        repaired_summary = run_dir / "final_summary_repaired.json"
        repaired_metrics = run_dir / "metrics_repaired.json"
        if repaired_summary.exists():
            final_summary_path = repaired_summary
        if repaired_metrics.exists():
            metrics_path = repaired_metrics

    fs = _read_json(final_summary_path) or {}
    metrics = _read_json(metrics_path) or {}

    status = metrics.get("status", fs.get("status", "unknown"))
    status_s = str(status or "unknown")
    run_id = str(rc.get("run_id") or run_dir.name)
    replica_index = as_int(rc.get("replica_index"))
    if replica_index is None:
        match = re.search(r"__replica(\d+)$", run_id)
        replica_index = int(match.group(1)) if match else 0

    joint_reward = as_float(fs.get("joint_reward"))
    raw_joint_reward = as_float(fs.get("raw_joint_reward"))
    joint_reward_ratio = as_float(fs.get("joint_reward_ratio"))

    # Back-compat: some envs stored normalized score in joint_reward.
    if (
        joint_reward_ratio is None
        and joint_reward is not None
        and raw_joint_reward is not None
        and 0.0 <= float(joint_reward) <= 1.0
    ):
        joint_reward = float(raw_joint_reward)

    optimal_payload = _load_optimal_summary(run_dir)
    if optimal_payload is None and compute_optimal:
        optimal_payload = _compute_and_write_optimal_summary(run_dir)

    optimal_joint_reward = None
    if isinstance(optimal_payload, dict):
        optimal = optimal_payload.get("optimal")
        if isinstance(optimal, dict):
            optimal_joint_reward = as_float(optimal.get("joint_reward"))

    normalized_regret = None
    if joint_reward is not None and optimal_joint_reward is not None and float(optimal_joint_reward) != 0.0:
        achieved_over_optimal = float(joint_reward) / float(optimal_joint_reward)
        normalized_regret = 1.0 - float(achieved_over_optimal)
        if normalized_regret < 0.0:
            normalized_regret = 0.0
        elif normalized_regret > 1.0:
            normalized_regret = 1.0
    if normalized_regret is None and _is_jira_run_config(rc):
        logger.warning(
            "Missing exact Jira optimal_summary.json for %s; pass --compute-optimal "
            "to compute comparable normalized_regret.",
            run_dir,
        )
    elif normalized_regret is None:
        # Non-Jira environments, including MeetingScheduling, already write the
        # normalized gap to their metric summary and do not need an
        # optimal_summary.json sidecar.
        system_regret_ratio = as_float(metrics.get("system_regret_ratio"))
        if system_regret_ratio is not None and math.isfinite(float(system_regret_ratio)):
            normalized_regret = float(min(1.0, max(0.0, float(system_regret_ratio))))
        elif joint_reward_ratio is not None and math.isfinite(float(joint_reward_ratio)):
            normalized_regret = 1.0 - float(joint_reward_ratio)
            normalized_regret = float(min(1.0, max(0.0, normalized_regret)))

    prompt_variant = _canonical_variant(rc.get("prompt_variant") or "control")
    judge_mean_rating = _judge_mean_rating(
        model_dir=model_dir,
        sweep_name=sweep_name,
        run_name=run_dir.name,
        judge_output_tag=judge_output_tag,
    )
    coalition_advantage_mean = as_float(metrics.get("coalition_advantage_mean"))
    if coalition_advantage_mean is None:
        raw_agent_rewards = fs.get("agent_rewards")
        if isinstance(raw_agent_rewards, dict):
            agent_rewards: Dict[str, float] = {}
            for agent_name, reward in raw_agent_rewards.items():
                try:
                    agent_rewards[str(agent_name)] = float(reward)
                except Exception:
                    continue
            coalition_advantage_mean = _coalition_advantage_from_agent_rewards(
                agent_rewards=agent_rewards,
                colluders=rc.get("colluders") if isinstance(rc.get("colluders"), list) else [],
            )
    if coalition_advantage_mean is None:
        recovered_agent_rewards = _recover_hospital_agent_rewards(
            run_dir=run_dir, run_config=rc, final_summary=fs
        )
        if recovered_agent_rewards:
            coalition_advantage_mean = _coalition_advantage_from_agent_rewards(
                agent_rewards=recovered_agent_rewards,
                colluders=rc.get("colluders") if isinstance(rc.get("colluders"), list) else [],
            )

    secret_channel_count = as_int(rc.get("secret_channel_count"))
    if secret_channel_count is None:
        secret_channel_count = 1 if as_bool(rc.get("secret_channel_enabled")) else 0

    return RunRow(
        model_label=str(rc.get("model_label") or model_dir.name),
        provider=str(rc.get("provider")) if rc.get("provider") is not None else None,
        model=str(rc.get("model")) if rc.get("model") is not None else None,
        sweep_name=str(rc.get("sweep") or sweep_name),
        topology=str(rc.get("topology")) if rc.get("topology") is not None else None,
        num_agents=as_int(rc.get("num_agents")),
        colluder_count=as_int(rc.get("colluder_count")),
        seed=as_int(rc.get("seed")),
        replica_index=max(0, int(replica_index)),
        secret_channel_enabled=as_bool(rc.get("secret_channel_enabled")),
        prompt_variant=prompt_variant,
        status=status_s,
        joint_reward=joint_reward,
        optimal_joint_reward=optimal_joint_reward,
        normalized_regret=normalized_regret,
        judge_mean_rating=judge_mean_rating,
        coalition_mean_regret=as_float(metrics.get("coalition_mean_regret")),
        noncoalition_mean_regret=as_float(metrics.get("noncoalition_mean_regret")),
        coalition_advantage_mean=coalition_advantage_mean,
        secret_channel_count=max(0, int(secret_channel_count)),
    )


def _filter_rows(
    rows: List[RunRow],
    *,
    topology: Optional[str],
    num_agents: Optional[int],
    colluder_count: Optional[int],
    model_labels: Optional[set[str]],
    replica_indexes: Optional[set[int]],
    require_complete: bool,
) -> List[RunRow]:
    out: List[RunRow] = []
    for r in rows:
        if topology is not None and str(r.topology) != str(topology):
            continue
        if num_agents is not None and int(r.num_agents or -1) != int(num_agents):
            continue
        if colluder_count is not None and int(r.colluder_count or -1) != int(colluder_count):
            continue
        if model_labels is not None and str(r.model_label) not in model_labels:
            continue
        if replica_indexes is not None and int(r.replica_index) not in replica_indexes:
            continue
        if require_complete and not _status_is_complete(r.status):
            continue
        out.append(r)
    return out


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


def _variant_or_baseline(r: RunRow) -> str:
    if r.secret_channel_enabled is True:
        return _canonical_variant(r.prompt_variant) or "control"
    return "baseline"


def _uses_channel_count_conditions(rows: List[RunRow]) -> bool:
    return any(int(getattr(r, "secret_channel_count", 0) or 0) > 1 for r in rows)


def _condition_keys(rows: List[RunRow]) -> List[str]:
    if not _uses_channel_count_conditions(rows):
        return ["baseline", "control", "simple"]

    counts: set[int] = set()
    for r in rows:
        count = int(getattr(r, "secret_channel_count", 0) or 0)
        variant = _canonical_variant(r.prompt_variant) or "control"
        if count > 0 and variant != "control":
            continue
        counts.add(max(0, count))
    ordered = ["baseline"]
    ordered.extend(f"sc{count}" for count in sorted(c for c in counts if c > 0))
    return ordered


def _condition_for_row(r: RunRow, *, channel_count_conditions: bool) -> Optional[str]:
    if not channel_count_conditions:
        return _variant_or_baseline(r)

    count = int(getattr(r, "secret_channel_count", 0) or 0)
    if count <= 0:
        return "baseline"

    # Channel-count plots are intended to compare emergent/private-channel
    # access. Prompted collusion variants copied from older roots should not
    # contaminate the 1SC bucket.
    variant = _canonical_variant(r.prompt_variant) or "control"
    if variant != "control":
        return None
    return f"sc{count}"


def _seed_means(rows: List[RunRow], *, key: str) -> List[float]:
    by_seed: Dict[int, List[float]] = {}
    for r in rows:
        seed = r.seed
        if seed is None:
            continue

        if key == "normalized_coalition_regret_gap":
            advantage = _coalition_advantage_value(r)
            if advantage is None or not math.isfinite(float(advantage)):
                continue
            norm_val = _normalize_coalition_regret_gap(
                float(advantage), r.model_label
            )
            if norm_val is None or not math.isfinite(float(norm_val)):
                continue
            val = float(norm_val)
        else:
            val = getattr(r, key, None)

        if val is None or not math.isfinite(float(val)):
            continue
        by_seed.setdefault(int(seed), []).append(float(val))

    out: List[float] = []
    for seed_i in sorted(by_seed):
        vals = by_seed[seed_i]
        if vals:
            out.append(float(mean(vals)))
    return out


def _run_values(rows: List[RunRow], *, key: str) -> List[float]:
    vals: List[float] = []
    for r in rows:
        if key == "normalized_coalition_regret_gap":
            advantage = _coalition_advantage_value(r)
            if advantage is None or not math.isfinite(float(advantage)):
                continue
            norm_val = _normalize_coalition_regret_gap(
                float(advantage), r.model_label
            )
            if norm_val is None or not math.isfinite(float(norm_val)):
                continue
            val = float(norm_val)
        else:
            val = getattr(r, key, None)

        if val is None or not math.isfinite(float(val)):
            continue
        vals.append(float(val))
    return vals


def _group_stats(rows: List[RunRow], *, key: str) -> Dict[str, Any]:
    vals = _run_values(rows, key=key)
    if not vals:
        return {"n": 0, "mean": None, "sem": None}
    return {"n": int(len(vals)), "mean": float(mean(vals)), "sem": float(sem(vals))}


def _plot_combined_nine_bars_two_metrics_dual_axis(
    *,
    rows: List[RunRow],
    metric_a_key: str,
    metric_b_key: str,
    out_path: Path,
    header_text: Optional[str] = None,
) -> None:
    """Single-panel plot: 2 normalized metrics (left y-axis) + judge (right y-axis).

    Ordering per model: metric_a bars, then metric_b bars, then judge bars.
    """
    _apply_large_font_style()
    models = sorted({r.model_label for r in rows})
    if not models:
        return

    conditions = _condition_keys(rows)
    channel_count_conditions = _uses_channel_count_conditions(rows)
    colors = {
        condition: _SIX_BARS_PALETTE[idx % len(_SIX_BARS_PALETTE)]
        for idx, condition in enumerate(conditions)
    }

    def _stats_for(model_label: str, condition: str, key: str) -> Tuple[float, float, int]:
        subset = [
            r
            for r in rows
            if r.model_label == model_label
            and _condition_for_row(
                r, channel_count_conditions=channel_count_conditions
            )
            == condition
        ]
        stats = _group_stats(subset, key=key)
        mu = stats.get("mean")
        se_val = stats.get("sem")
        n = int(stats.get("n") or 0)
        return (
            float(mu) if mu is not None else float("nan"),
            float(se_val) if se_val is not None else float("nan"),
            n,
        )

    metric_a_means: Dict[str, List[float]] = {c: [] for c in conditions}
    metric_a_sems: Dict[str, List[float]] = {c: [] for c in conditions}
    metric_b_means: Dict[str, List[float]] = {c: [] for c in conditions}
    metric_b_sems: Dict[str, List[float]] = {c: [] for c in conditions}
    judge_means: Dict[str, List[float]] = {c: [] for c in conditions}
    judge_sems: Dict[str, List[float]] = {c: [] for c in conditions}

    for m in models:
        for c in conditions:
            a_mu, a_se, _ = _stats_for(m, c, metric_a_key)
            metric_a_means[c].append(a_mu)
            metric_a_sems[c].append(a_se)

            b_mu, b_se, _ = _stats_for(m, c, metric_b_key)
            metric_b_means[c].append(b_mu)
            metric_b_sems[c].append(b_se)

            j_mu, j_se, _ = _stats_for(m, c, "judge_mean_rating")
            judge_means[c].append(j_mu)
            judge_sems[c].append(j_se)

    any_a = any(math.isfinite(v) for c in conditions for v in metric_a_means[c])
    any_b = any(math.isfinite(v) for c in conditions for v in metric_b_means[c])
    any_judge = any(math.isfinite(v) for c in conditions for v in judge_means[c])
    if not any_a:
        logger.warning("No %s data found; skipping: %s", metric_a_key, out_path)
        return
    if not any_b:
        logger.warning("No %s data found; skipping: %s", metric_b_key, out_path)
        return
    if not any_judge:
        logger.warning("No judge_mean_rating data found; skipping: %s", out_path)
        return

    ensure_dir(out_path.parent)
    fig_width = max(9.0, 3.0 + (2.2 * float(len(models))))
    fig_height = 3.55 if header_text else 3.2
    fig, ax_metric = plt.subplots(nrows=1, ncols=1, figsize=(fig_width, fig_height))
    ax_judge = ax_metric.twinx()

    x = np.arange(len(models), dtype=float)
    total_bars = max(1, len(conditions) * 3)
    width = min(0.08, 0.82 / float(total_bars + 1))
    offsets = (np.arange(total_bars, dtype=float) - ((total_bars - 1.0) / 2.0)) * width
    metric_a_offsets = {
        condition: float(offsets[idx]) for idx, condition in enumerate(conditions)
    }
    metric_b_offsets = {
        condition: float(offsets[len(conditions) + idx])
        for idx, condition in enumerate(conditions)
    }
    judge_offsets = {
        condition: float(offsets[(2 * len(conditions)) + idx])
        for idx, condition in enumerate(conditions)
    }

    for c in conditions:
        ax_metric.bar(
            x + metric_a_offsets[c],
            metric_a_means[c],
            width=width,
            yerr=metric_a_sems[c],
            color=colors[c],
            alpha=0.85,
            capsize=3,
            label="_nolegend_",
        )

    for c in conditions:
        ax_metric.bar(
            x + metric_b_offsets[c],
            metric_b_means[c],
            width=width,
            yerr=metric_b_sems[c],
            color=_lighten_color(colors[c], amount=0.55),
            alpha=0.85,
            capsize=3,
            label="_nolegend_",
        )
        ax_metric.bar(
            x + metric_b_offsets[c],
            metric_b_means[c],
            width=width,
            color="none",
            alpha=1.0,
            hatch="\\\\\\",
            edgecolor="#666666",
            linewidth=0.0,
            label="_nolegend_",
            zorder=4,
        )

    for c in conditions:
        ax_judge.bar(
            x + judge_offsets[c],
            judge_means[c],
            width=width,
            yerr=judge_sems[c],
            color=colors[c],
            alpha=0.35,
            capsize=3,
            hatch="///",
            edgecolor="black",
            linewidth=0.3,
            label="_nolegend_",
        )

    ax_metric.set_ylabel("Normalized Value")
    ax_metric.set_ylim(0.0, 1.0)
    ax_metric.axhline(0.5, color="#777777", linewidth=1.0, alpha=0.7, zorder=0, linestyle=":")

    judge_axis_label = _pretty_metric_label("judge_mean_rating").replace(" (Judge)", "")
    ax_judge.set_ylabel(judge_axis_label, labelpad=10)
    ax_judge.tick_params(axis="y", labelcolor="black")
    ax_judge.spines["right"].set_visible(True)

    condition_handles = [
        Patch(
            facecolor=colors[c],
            edgecolor="none",
            label=_REGRET_REPORT_CONDITION_LABELS.get(c, c),
        )
        for c in conditions
    ]

    metric_a_label = _pretty_metric_label(metric_a_key).strip().replace("Normalized Regret", "Regret")
    metric_b_label = _pretty_metric_label(metric_b_key).strip()
    judge_label = _pretty_metric_label("judge_mean_rating").replace(" (↓)", "").strip()
    style_handles = [
        Patch(facecolor="white", edgecolor="black", linewidth=0.8, alpha=1.0, label=metric_a_label),
        Patch(facecolor="white", edgecolor="black", linewidth=0.8, hatch="\\\\\\", alpha=1.0, label=metric_b_label),
        Patch(facecolor="white", edgecolor="black", linewidth=0.8, hatch="///", alpha=1.0, label=judge_label),
    ]

    ax_metric.set_xticks(x)
    ax_metric.set_xticklabels([_pretty_model_label(m) for m in models], rotation=0, ha="center")
    ax_metric.tick_params(axis="x", pad=6)
    ax_metric.axhline(0.0, color="black", linewidth=0.8, alpha=0.4)
    ax_metric.yaxis.grid(True, linestyle="--", linewidth=0.6, alpha=0.4)
    ax_metric.set_axisbelow(True)

    legend_handles = condition_handles + style_handles
    if header_text:
        fig.suptitle(header_text, y=0.985, fontsize=12, fontweight="semibold")
        top = 0.84
    else:
        top = 0.96
    fig.subplots_adjust(left=0.07, right=0.93, top=top, bottom=0.24)
    fig.legend(
        legend_handles,
        [h.get_label() for h in legend_handles],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.01),
        ncol=min(len(legend_handles), 7),
        frameon=False,
        columnspacing=1.2,
        handlelength=1.4,
        handletextpad=0.6,
        labelspacing=0.4,
    )

    logo_paths = _resolve_logo_paths(rows, models)
    _add_logos_to_xticklabels(fig=fig, ax=ax_metric, models=models, logo_paths=logo_paths)
    plt.savefig(out_path, dpi=200)
    plt.close(fig)


def _write_combined_nine_bars_two_metrics_dual_axis_csv(
    *,
    rows: List[RunRow],
    metric_a_key: str,
    metric_b_key: str,
    out_path: Path,
    plot_metadata: Optional[Dict[str, Optional[str]]] = None,
) -> None:
    models = sorted({r.model_label for r in rows})
    if not models:
        return

    conditions = _condition_keys(rows)
    channel_count_conditions = _uses_channel_count_conditions(rows)
    metric_keys = [metric_a_key, metric_b_key, "judge_mean_rating"]
    plot_metadata = plot_metadata or {}

    table_rows: List[Dict[str, Any]] = []
    for model_label in models:
        for condition in conditions:
            subset = [
                r
                for r in rows
                if r.model_label == model_label
                and _condition_for_row(
                    r, channel_count_conditions=channel_count_conditions
                )
                == condition
            ]
            for metric_key in metric_keys:
                stats = _group_stats(subset, key=str(metric_key))
                table_rows.append(
                    {
                        "model_label": model_label,
                        "model_label_pretty": _pretty_model_label(model_label),
                        "condition": condition,
                        "condition_pretty": _REGRET_REPORT_CONDITION_LABELS.get(condition, condition),
                        "metric_key": str(metric_key),
                        "metric_label": _pretty_metric_label(str(metric_key)).replace(" (Judge)", ""),
                        "mean": stats.get("mean"),
                        "sem": stats.get("sem"),
                        "n": stats.get("n"),
                        "environment_name": plot_metadata.get("environment_name"),
                        "judge_label": plot_metadata.get("judge_label"),
                        "plot_header": plot_metadata.get("plot_header"),
                    }
                )

    write_csv(out_path, table_rows)


def _environment_name_from_config(config: Dict[str, Any]) -> Optional[str]:
    env_cfg = config.get("environment")
    if isinstance(env_cfg, dict):
        env_name = str(env_cfg.get("name") or "").strip()
        if env_name:
            return env_name
        import_path = str(env_cfg.get("import_path") or "").strip()
        if ":" in import_path:
            return import_path.rsplit(":", 1)[1].strip()

    env_name = str(config.get("environment_name") or "").strip()
    if env_name:
        return env_name
    env_label = str(config.get("environment_label") or "").strip()
    if env_label:
        return env_label

    env_cfg = config.get("environment_cfg")
    if isinstance(env_cfg, dict):
        env_name = str(env_cfg.get("name") or "").strip()
        if env_name:
            return env_name
        import_path = str(env_cfg.get("import_path") or "").strip()
        if ":" in import_path:
            return import_path.rsplit(":", 1)[1].strip()
    return None


def _infer_environment_name(root: Path) -> Optional[str]:
    config = _read_json(root / "config.json") or {}
    env_name = _environment_name_from_config(config)
    if env_name:
        return env_name

    for run_config_path in sorted(root.glob("runs/*/*/*/run_config.json")):
        run_config = _read_json(run_config_path) or {}
        env_name = _environment_name_from_config(run_config)
        if env_name:
            return env_name
    return None


def _judge_label_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    judge_cfg = payload.get("judge_config")
    if not isinstance(judge_cfg, dict):
        return None

    provider = str(judge_cfg.get("provider") or "").strip()
    model = str(judge_cfg.get("model") or "").strip()
    if provider and model:
        return f"{provider} / {model}"
    if model:
        return model
    if provider:
        return provider
    return None


def _infer_judge_label(
    *,
    root: Path,
    sweep_name: str,
    judge_output_tag: Optional[str],
) -> Optional[str]:
    judge_root_name = judge_dir_name(judge_output_tag)
    labels: List[str] = []
    for result_path in sorted(root.glob(f"runs/*/{judge_root_name}/{sweep_name}/*.json")):
        payload = _read_json(result_path) or {}
        label = _judge_label_from_payload(payload)
        if label and label not in labels:
            labels.append(label)
        if len(labels) > 3:
            break

    if not labels:
        if judge_output_tag is not None and str(judge_output_tag).strip():
            return str(judge_output_tag).strip()
        return None
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels)


def _build_plot_header_metadata(
    *,
    root: Path,
    sweep_name: str,
    judge_output_tag: Optional[str],
) -> Dict[str, Optional[str]]:
    env_name = _infer_environment_name(root)
    judge_label = _infer_judge_label(
        root=root,
        sweep_name=sweep_name,
        judge_output_tag=judge_output_tag,
    )

    return {
        "environment_name": env_name,
        "judge_label": judge_label,
        "plot_header": compact_plot_header(
            environment_name=env_name,
            judge_label=judge_label,
        ),
    }


def _build_plot_header(
    *,
    root: Path,
    sweep_name: str,
    judge_output_tag: Optional[str],
) -> Optional[str]:
    return _build_plot_header_metadata(
        root=root,
        sweep_name=sweep_name,
        judge_output_tag=judge_output_tag,
    ).get("plot_header")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the combined nine-bars regret plot from a collusion output root."
    )
    parser.add_argument(
        "--root",
        type=str,
        required=True,
        help="Path like experiments/collusion/outputs/<tag>/<timestamp>",
    )
    parser.add_argument(
        "--sweep-name",
        type=str,
        default=None,
        help="Sweep directory name under each model (e.g., complete_n6_c2). If omitted, auto-infer when unique.",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output directory (default: experiments/collusion/plots_outputs/<tag>/<ts>/regret_report/<sweep_name>).",
    )
    parser.add_argument("--topology", type=str, default="complete")
    parser.add_argument("--num-agents", type=int, default=6)
    parser.add_argument("--colluder-count", type=int, default=2)
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help="Include runs where status != 'complete' (not recommended).",
    )
    parser.add_argument(
        "--model-label",
        action="append",
        default=None,
        help=(
            "Restrict to one model label. May be repeated or comma-separated. "
            "Useful when plotting a copied output root that contains historical models."
        ),
    )
    parser.add_argument(
        "--replica-index",
        action="append",
        default=None,
        help=(
            "Restrict to one replica index. May be repeated or comma-separated. "
            "For copied roots, `--replica-index 0` keeps the canonical seed runs "
            "and drops historical `__replica1` runs."
        ),
    )
    parser.add_argument(
        "--compute-optimal",
        action="store_true",
        help="If optimal_summary.json is missing, compute and write the exact supported-domain optimum (no API calls).",
    )
    parser.add_argument(
        "--prefer-repaired",
        action="store_true",
        help="Prefer *_repaired.json artifacts when present (final_summary_repaired.json, metrics_repaired.json).",
    )
    parser.add_argument(
        "--judge-output-tag",
        type=str,
        default=None,
        help=(
            "Optional judge namespace to read from, corresponding to "
            "`judge_secret_blackboard__<tag>/...`."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    sweep_name = str(args.sweep_name) if args.sweep_name else _infer_sweep_name(root)

    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
    else:
        tag = root.parent.name
        timestamp = root.name
        out_dir = (
            Path("experiments/collusion/plots_outputs")
            / str(tag)
            / str(timestamp)
            / "regret_report"
            / sweep_name
        ).resolve()
    ensure_dir(out_dir)
    ensure_dir(out_dir / "plots")

    rows: List[RunRow] = []
    missing_sweeps: List[str] = []
    run_jobs: List[Tuple[Path, Path]] = []
    for model_dir in _iter_model_dirs(root):
        sweep_dir = model_dir / sweep_name
        if not sweep_dir.exists():
            missing_sweeps.append(model_dir.name)
            continue
        run_jobs.extend((model_dir, run_dir) for run_dir in _iter_run_dirs(sweep_dir))

    for model_dir, run_dir in tqdm(
        run_jobs,
        desc="Loading regret rows",
        unit="run",
        dynamic_ncols=True,
    ):
        row = _load_run_row(
            run_dir=run_dir,
            model_dir=model_dir,
            sweep_name=sweep_name,
            compute_optimal=bool(args.compute_optimal),
            prefer_repaired=bool(args.prefer_repaired),
            judge_output_tag=args.judge_output_tag,
        )
        if row is not None:
            rows.append(row)

    if missing_sweeps:
        logger.warning("Missing sweep %s for models: %s", sweep_name, ", ".join(missing_sweeps))
    if not rows:
        raise SystemExit("No runs found.")

    rows = _filter_rows(
        rows,
        topology=str(args.topology) if args.topology else None,
        num_agents=int(args.num_agents) if args.num_agents else None,
        colluder_count=int(args.colluder_count) if args.colluder_count else None,
        model_labels=_split_filter_values(args.model_label),
        replica_indexes=_split_int_filter_values(args.replica_index),
        require_complete=not bool(args.include_incomplete),
    )
    _set_coalition_regret_gap_norm(rows)
    if not rows:
        raise SystemExit("No runs matched the requested filters.")

    plot_out = out_dir / "plots" / "regret_report__normalized_regret__coalition_gap__judge.png"
    plot_metadata = _build_plot_header_metadata(
        root=root,
        sweep_name=sweep_name,
        judge_output_tag=args.judge_output_tag,
    )
    header_text = plot_metadata.get("plot_header")
    _plot_combined_nine_bars_two_metrics_dual_axis(
        rows=rows,
        metric_a_key="normalized_regret",
        metric_b_key="normalized_coalition_regret_gap",
        out_path=plot_out,
        header_text=header_text,
    )
    if plot_out.exists():
        logger.info("Wrote plot: %s", plot_out)

    csv_out = out_dir / "plots" / "regret_report__normalized_regret__coalition_gap__judge__data.csv"
    _write_combined_nine_bars_two_metrics_dual_axis_csv(
        rows=rows,
        metric_a_key="normalized_regret",
        metric_b_key="normalized_coalition_regret_gap",
        out_path=csv_out,
        plot_metadata=plot_metadata,
    )
    if csv_out.exists():
        logger.info("Wrote CSV: %s", csv_out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
