from __future__ import annotations

import re
from typing import Optional

DEFAULT_JUDGE_DIR_NAME = "judge_secret_blackboard"


def sanitize_judge_output_tag(tag: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(tag or "").strip())
    cleaned = cleaned.strip("._-")
    if not cleaned:
        raise ValueError(
            "Judge output tag must contain at least one alphanumeric character."
        )
    return cleaned


def judge_dir_name(judge_output_tag: Optional[str] = None) -> str:
    if judge_output_tag is None or not str(judge_output_tag).strip():
        return DEFAULT_JUDGE_DIR_NAME
    return (
        f"{DEFAULT_JUDGE_DIR_NAME}__"
        f"{sanitize_judge_output_tag(str(judge_output_tag))}"
    )


def is_judge_dir_name(name: str) -> bool:
    value = str(name or "").strip()
    return value == DEFAULT_JUDGE_DIR_NAME or value.startswith(
        f"{DEFAULT_JUDGE_DIR_NAME}__"
    )


def derive_judge_output_tag(
    *,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
) -> Optional[str]:
    parts = [
        sanitize_judge_output_tag(part)
        for part in [judge_provider, judge_model]
        if part is not None and str(part).strip()
    ]
    if not parts:
        return None
    return "__".join(parts)
