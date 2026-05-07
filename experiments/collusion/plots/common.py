from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from experiments.common.plotting.io_utils import infer_labels_from_sweep_dir

_PROVIDER_PREFIX_RE = re.compile(
    r"^(openai|anthropic|together|fw|foundry)[\s/_-]+", re.IGNORECASE
)


def canonical_variant(name: Any) -> str:
    return str(name or "").strip()


def default_out_dir(*, sweep_dir: Path, requested_out_dir: Optional[str]) -> Path:
    labels = infer_labels_from_sweep_dir(sweep_dir)
    return (
        Path(requested_out_dir).expanduser().resolve()
        if requested_out_dir
        else Path("experiments/collusion/plots_outputs")
        / labels.experiment_tag
        / labels.timestamp
        / labels.model_label
        / labels.sweep_name
    )


def compact_environment_label(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None

    raw = raw.rsplit(":", 1)[-1]
    raw = raw.rsplit(".", 1)[-1]
    lowered = raw.lower()
    if "jira" in lowered:
        return "Jira"
    if "meeting" in lowered and "schedul" in lowered:
        return "Meeting Scheduling"
    if "hospital" in lowered:
        return "Hospital"
    if "smart" in lowered and "grid" in lowered:
        return "Smart Grid"
    if "personal" in lowered and "assistant" in lowered:
        return "Personal Assistant"

    cleaned = re.sub(r"(Choice)?Environment$", "", raw)
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.title() if cleaned else None


def compact_judge_label(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None

    labels = []
    for part in raw.split(","):
        label = part.strip()
        if not label:
            continue
        if "/" in label:
            label = label.rsplit("/", 1)[-1].strip()
        label = _PROVIDER_PREFIX_RE.sub("", label)
        label = label.replace("__", "-").replace("_", "-")
        lowered = label.lower()
        if lowered == "gpt54":
            labels.append("GPT-5.4")
            continue
        if lowered.startswith("gpt54-"):
            labels.append("GPT-5.4-" + label.split("-", 1)[1])
            continue

        pieces = []
        for piece in re.split(r"[-\s]+", label):
            if not piece:
                continue
            pl = piece.lower()
            if pl == "gpt":
                pieces.append("GPT")
            elif pl == "glm":
                pieces.append("GLM")
            elif pl == "minimax":
                pieces.append("MiniMax")
            elif pl in {"k2", "m2.5"}:
                pieces.append(pl.upper())
            elif pl in {
                "claude",
                "sonnet",
                "haiku",
                "opus",
                "gemini",
                "flash",
                "lite",
                "mini",
                "reasoning",
                "non",
            }:
                pieces.append(pl.capitalize())
            else:
                pieces.append(piece)
        labels.append("-".join(pieces) if pieces else label)

    return ", ".join(labels) if labels else None


def compact_plot_header(
    *, environment_name: Any = None, judge_label: Any = None
) -> Optional[str]:
    parts = []
    env = compact_environment_label(environment_name)
    if env:
        parts.append(f"Environment={env}")
    judge = compact_judge_label(judge_label)
    if judge:
        parts.append(f"Judge={judge}")
    return " | ".join(parts) if parts else None


def compact_legacy_plot_header(value: Any) -> Optional[str]:
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

    return compact_plot_header(
        environment_name=environment_name,
        judge_label=judge_label,
    ) or raw
