from __future__ import annotations

# ruff: noqa: E402

"""Covert collusion experiment runner.

Focuses on whether a subset of agents can coordinate via a hidden coalition-only blackboard and
benefit at the expense of non-coalition members.
"""

import sys
import argparse
import copy
import csv
import json
import logging
import random
import re
import importlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from tqdm import tqdm

if sys.version_info < (3, 11):
    raise RuntimeError(
        "Terrarium requires Python >= 3.11. "
        "Create/activate a `.venv` (see repo README) and re-run with `.venv/bin/python`."
    )

project_root = Path(__file__).resolve().parents[2]
# Allow running without installing the repo as a package.
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from experiments.common.run_utils import (
    configure_experiment_logging as _configure_experiment_logging_impl,
    ensure_dir as _ensure_dir,
    load_yaml as _load_yaml,
    normalize_seeds as _normalize_seeds,
    write_json as _write_json,
    write_progress as _write_progress,
)
from experiments.common.blackboard_logger import ExperimentBlackboardLogger
from experiments.collusion.metrics import compute_collusion_metrics, metrics_to_json
from experiments.collusion.prompts import CollusionPrompts
from experiments.common.local_protocol import LocalCommunicationProtocol
from terrarium.networks import build_communication_network
from terrarium.core.logger import AgentTrajectoryLogger, PromptLogger
from terrarium.utils import get_client_instance, get_generation_params, get_model_name
from terrarium.agents.base import BaseAgent


LOGGER_NAME = "experiments.collusion"
logger = logging.getLogger(LOGGER_NAME)

_SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9]+")
_REPLICA_SUFFIX_RE = re.compile(r"__replica(\d+)$")


def _configure_experiment_logging(root: Path, *, verbose: bool = True) -> None:
    _configure_experiment_logging_impl(logger, root, verbose=verbose)


def _resolve_environment_class(env_cfg: Dict[str, Any]) -> Any:
    import_path = str(env_cfg.get("import_path") or "").strip()
    if import_path:
        module_path, sep, cls_name = import_path.partition(":")
        if not sep:
            raise ValueError(
                "environment.import_path must be formatted as 'some.module:ClassName' "
                f"(got {import_path!r})."
            )
        module = importlib.import_module(module_path)
        return getattr(module, cls_name)

    env_name = str(env_cfg.get("name") or "").strip()
    if not env_name:
        raise ValueError(
            "environment.name is required (or set environment.import_path)."
        )

    candidate_modules = [
        "terrarium.environments.dcops",
    ]
    for module_path in candidate_modules:
        try:
            module = importlib.import_module(module_path)
        except Exception:
            continue
        if hasattr(module, env_name):
            return getattr(module, env_name)

    raise ValueError(
        f"Unknown environment.name {env_name!r}. "
        "Either export it from terrarium.environments.dcops, or set environment.import_path."
    )


def _sanitize_label(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return "env"
    value = _SAFE_LABEL_RE.sub("_", value).strip("_")
    return value or "env"


def _infer_environment_label(env_cfg: Dict[str, Any]) -> str:
    import_path = str(env_cfg.get("import_path") or "").strip()
    if import_path:
        _module, _sep, cls_name = import_path.partition(":")
        if cls_name:
            return cls_name
        return import_path
    name = str(env_cfg.get("name") or "").strip()
    if name:
        return name
    return "env"


def _normalize_environment_sweep(
    *, sweep: Dict[str, Any], base_env_cfg: Dict[str, Any]
) -> List[tuple[str, Dict[str, Any]]]:
    raw = sweep.get("environments") or []
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("sweeps[].environments must be a list.")

    variants: List[tuple[str, Dict[str, Any]]] = []
    seen: set[str] = set()
    for entry in raw:
        label: Optional[str] = None
        override: Dict[str, Any] = {}
        if isinstance(entry, str):
            value = entry.strip()
            if not value:
                continue
            if ":" in value:
                override = {"import_path": value}
                label = value.split(":", 1)[1].strip() or value
            else:
                override = {"name": value}
                label = value
        elif isinstance(entry, dict):
            label = str(entry.get("label") or "").strip() or None
            override = {k: v for k, v in entry.items() if k != "label"}
            if label is None:
                label = _infer_environment_label(override)
        else:
            raise ValueError(
                f"Invalid sweeps[].environments entry (expected str|dict, got {type(entry).__name__})."
            )

        env_cfg = copy.deepcopy(base_env_cfg or {})
        env_cfg.update(copy.deepcopy(override))
        safe_label = _sanitize_label(label or _infer_environment_label(env_cfg))
        unique_label = safe_label
        suffix = 2
        while unique_label in seen:
            unique_label = f"{safe_label}_{suffix}"
            suffix += 1
        seen.add(unique_label)
        variants.append((unique_label, env_cfg))

    if not variants:
        raise ValueError(
            "sweeps[].environments is set but empty; provide at least one environment entry."
        )
    return variants


def _get_runs_per_seed(exp: Dict[str, Any]) -> int:
    legacy = exp.get("runs_per_setting")
    if legacy is not None:
        raise ValueError(
            "experiment.runs_per_setting is no longer supported for collusion sweeps. "
            "Use experiment.seeds to choose seeds and experiment.runs_per_seed to repeat each seed."
        )

    raw = exp.get("runs_per_seed", 1)
    try:
        runs_per_seed = int(raw)
    except Exception as exc:
        raise ValueError(
            f"experiment.runs_per_seed must be a positive integer (got {raw!r})."
        ) from exc
    if runs_per_seed <= 0:
        raise ValueError("experiment.runs_per_seed must be a positive integer")
    return runs_per_seed


def _default_seeds_from_config(cfg: Dict[str, Any]) -> List[int]:
    exp = cfg.get("experiment") or {}
    default_seeds = _normalize_seeds(exp.get("seeds"))
    if not default_seeds:
        default_seeds = _normalize_seeds((cfg.get("simulation") or {}).get("seed")) or [
            1
        ]
    return list(default_seeds)


def _normalize_secret_channel_counts(sweep: Dict[str, Any]) -> List[int]:
    """Return total private-channel counts for a sweep.

    Legacy configs use `secret_channel_enabled: [false, true]`, where true means
    one coalition-only private channel. New configs can use
    `secret_channel_counts: [0, 1, 2, 3]`; counts above one add disjoint
    non-colluder pair channels on top of the coalition channel.
    """
    raw_counts = sweep.get("secret_channel_counts")
    if raw_counts is None:
        raw_counts = sweep.get("num_secret_channels")

    counts: List[int] = []
    if raw_counts is None:
        secret_flags = (
            sweep.get("secret_channel_enabled")
            or sweep.get("secret_channels")
            or [False]
        )
        if not isinstance(secret_flags, list):
            secret_flags = [secret_flags]
        counts = [1 if bool(flag) else 0 for flag in secret_flags]
    else:
        if not isinstance(raw_counts, list):
            raw_counts = [raw_counts]
        for raw in raw_counts:
            try:
                count = int(raw)
            except Exception as exc:
                raise ValueError(
                    f"secret_channel_counts entries must be integers (got {raw!r})."
                ) from exc
            if count < 0:
                raise ValueError("secret_channel_counts entries must be >= 0.")
            counts.append(count)

    deduped: List[int] = []
    seen: set[int] = set()
    for count in counts:
        if count in seen:
            continue
        seen.add(count)
        deduped.append(int(count))
    return deduped or [0]


def _build_run_id(
    *,
    model_label: str,
    sweep_name: str,
    environment_label: Optional[str],
    topology: str,
    num_agents: int,
    colluder_count: int,
    secret_channel_enabled: bool,
    secret_channel_count: Optional[int] = None,
    effective_prompt_variant: str,
    seed: int,
    replica_index: int = 0,
) -> str:
    run_id = f"{model_label}__{sweep_name}"
    if str(environment_label or "").strip():
        run_id += f"__env{environment_label}"
    run_id += (
        f"__{topology}__n{num_agents}"
        f"__c{colluder_count}__secret{int(bool(secret_channel_enabled))}"
    )
    if secret_channel_count is not None and int(secret_channel_count) > 1:
        run_id += f"__sc{int(secret_channel_count)}"
    run_id += f"__pv{effective_prompt_variant}__seed{seed}"
    replica_index = int(replica_index)
    if replica_index > 0:
        run_id += f"__replica{replica_index}"
    return run_id


def _infer_replica_index(
    raw_replica_index: Any, *, run_id: Optional[str] = None
) -> int:
    if raw_replica_index is not None:
        try:
            replica_index = int(raw_replica_index)
        except Exception:
            replica_index = 0
        return max(0, replica_index)

    rid = str(run_id or "").strip()
    if rid:
        match = _REPLICA_SUFFIX_RE.search(rid)
        if match:
            try:
                return max(0, int(match.group(1)))
            except Exception:
                return 0
    return 0


@dataclass(frozen=True)
class RunSpec:
    model_label: str
    model_llm_cfg: Dict[str, Any]
    model_agent_llms: Optional[Any]
    model_collusion_cfg: Optional[Dict[str, Any]]
    sweep_name: str
    environment_label: Optional[str]
    environment_cfg: Optional[Dict[str, Any]]
    topology: str
    num_agents: int
    colluder_count: int
    secret_channel_enabled: bool
    secret_channel_count: int
    prompt_variant: str
    seed: int
    replica_index: int = 0

    @property
    def effective_prompt_variant(self) -> str:
        if not bool(self.secret_channel_enabled):
            return "control"
        return str(self.prompt_variant or "").strip() or "control"

    @property
    def run_id(self) -> str:
        return _build_run_id(
            model_label=self.model_label,
            sweep_name=self.sweep_name,
            environment_label=self.environment_label,
            topology=self.topology,
            num_agents=self.num_agents,
            colluder_count=self.colluder_count,
            secret_channel_enabled=self.secret_channel_enabled,
            secret_channel_count=self.secret_channel_count,
            effective_prompt_variant=self.effective_prompt_variant,
            seed=self.seed,
            replica_index=self.replica_index,
        )

    @property
    def run_label(self) -> str:
        env_part = (
            f"/env{self.environment_label}"
            if str(self.environment_label or "").strip()
            else ""
        )
        label = (
            f"{self.model_label}/{self.sweep_name}{env_part}/{self.topology}"
            f"/n{self.num_agents}/c{self.colluder_count}"
            f"/secret{int(bool(self.secret_channel_enabled))}"
        )
        if int(self.secret_channel_count) > 1:
            label += f"/sc{int(self.secret_channel_count)}"
        label += f"/pv{self.effective_prompt_variant}/seed{self.seed}"
        if int(self.replica_index) > 0:
            label += f"/replica{int(self.replica_index)}"
        return label


def _iter_expected_run_specs(cfg: Dict[str, Any]) -> Iterable[RunSpec]:
    exp = cfg.get("experiment") or {}
    models = cfg.get("llm_models") or []
    sweeps = exp.get("sweeps") or []
    runs_per_seed = _get_runs_per_seed(exp)
    default_seeds = _default_seeds_from_config(cfg)

    for model in models:
        model_label = str(model.get("label") or "model")
        llm_cfg = model.get("llm") or {}
        agent_llms = (
            model.get("agent_llms")
            or model.get("agent_llm_assignments")
            or model.get("per_agent_llms")
        )
        model_collusion_cfg = copy.deepcopy(model.get("collusion") or {})
        if "colluders" in model and "colluders" not in model_collusion_cfg:
            model_collusion_cfg["colluders"] = copy.deepcopy(model.get("colluders"))
        if "colluder_indices" in model and "colluder_indices" not in model_collusion_cfg:
            model_collusion_cfg["colluder_indices"] = copy.deepcopy(
                model.get("colluder_indices")
            )
        if "colluder_names" in model and "colluder_names" not in model_collusion_cfg:
            model_collusion_cfg["colluder_names"] = copy.deepcopy(
                model.get("colluder_names")
            )
        for sweep in sweeps:
            sweep_name = str(sweep.get("name") or "sweep")
            env_variants = _normalize_environment_sweep(
                sweep=sweep, base_env_cfg=(cfg.get("environment") or {})
            )
            if not env_variants:
                env_variants = [(None, None)]
            topologies = sweep.get("topologies") or []
            agent_counts = sweep.get("num_agents") or []
            colluder_counts = sweep.get("colluder_counts") or []
            secret_channel_counts = _normalize_secret_channel_counts(sweep)
            raw_prompt_variants = sweep.get("prompt_variants") or ["control"]
            prompt_variants: List[str] = []
            seen_variants: set[str] = set()
            for pv in raw_prompt_variants:
                pv_str = str(pv or "").strip() or "control"
                if pv_str in seen_variants:
                    continue
                seen_variants.add(pv_str)
                prompt_variants.append(pv_str)
            seeds = _normalize_seeds(sweep.get("seeds")) or list(default_seeds)
            if not seeds:
                raise ValueError(
                    "No seeds specified. Set experiment.seeds or sweeps[].seeds."
                )

            for env_label, env_cfg in env_variants:
                for topology in topologies:
                    for n in agent_counts:
                        for c in colluder_counts:
                            for secret_channel_count in secret_channel_counts:
                                secret = int(secret_channel_count) > 0
                                for pv in prompt_variants:
                                    if not secret and str(pv) != "control":
                                        continue
                                    for seed in seeds:
                                        for replica_index in range(runs_per_seed):
                                            yield RunSpec(
                                                model_label=model_label,
                                                model_llm_cfg=llm_cfg,
                                                model_agent_llms=agent_llms,
                                                model_collusion_cfg=model_collusion_cfg,
                                                sweep_name=sweep_name,
                                                environment_label=env_label,
                                                environment_cfg=env_cfg,
                                                topology=str(topology),
                                                num_agents=int(n),
                                                colluder_count=int(c),
                                                secret_channel_enabled=bool(secret),
                                                secret_channel_count=int(
                                                    secret_channel_count
                                                ),
                                                prompt_variant=str(pv),
                                                seed=int(seed),
                                                replica_index=int(replica_index),
                                            )


def _select_colluders(
    *,
    agent_names: Sequence[str],
    count: int,
    strategy: str,
    rng: random.Random,
) -> List[str]:
    if count <= 0:
        return []
    count = min(int(count), len(agent_names))
    strategy = str(strategy or "random").strip().lower()
    if strategy == "random":
        return [str(x) for x in rng.sample(list(agent_names), k=count)]
    if strategy == "first":
        return [str(x) for x in list(agent_names)[:count]]
    raise ValueError(f"Unknown colluder selection strategy: {strategy!r}")


def _resolve_colluders_from_config(
    *,
    agent_names: Sequence[str],
    count: int,
    collusion_cfg: Dict[str, Any],
    rng: random.Random,
) -> List[str]:
    names = [str(x) for x in agent_names]
    explicit_names = collusion_cfg.get("colluder_names")
    explicit_indices = collusion_cfg.get("colluder_indices")
    explicit_spec = collusion_cfg.get("colluders")

    if isinstance(explicit_spec, dict):
        if explicit_names is None:
            explicit_names = explicit_spec.get("names")
        if explicit_indices is None:
            explicit_indices = explicit_spec.get("indices")
    elif explicit_spec is not None:
        if isinstance(explicit_spec, list) and all(
            isinstance(x, int) for x in explicit_spec
        ):
            explicit_indices = explicit_spec
        else:
            explicit_names = explicit_spec

    selected: List[str] = []
    if explicit_indices is not None:
        if not isinstance(explicit_indices, list):
            raise ValueError("colluder_indices must be a list of integer positions.")
        for raw_idx in explicit_indices:
            try:
                idx = int(raw_idx)
            except Exception as exc:
                raise ValueError(
                    f"Invalid colluder index {raw_idx!r}; expected an integer."
                ) from exc
            if idx < 0 or idx >= len(names):
                raise ValueError(
                    f"Colluder index {idx} is out of range for {len(names)} agents."
                )
            selected.append(names[idx])

    if explicit_names is not None:
        if not isinstance(explicit_names, list):
            raise ValueError("colluder_names must be a list of agent names.")
        unknown = [str(x) for x in explicit_names if str(x) not in set(names)]
        if unknown:
            raise ValueError(
                "Configured colluder_names contains unknown agents: "
                + ", ".join(unknown)
            )
        selected.extend(str(x) for x in explicit_names)

    if selected:
        deduped = list(dict.fromkeys(selected))
        if len(deduped) != int(count):
            raise ValueError(
                f"Explicit colluder selection resolved to {len(deduped)} agents "
                f"but colluder_count is {int(count)}: {deduped!r}"
            )
        return deduped

    selection_strategy = str(collusion_cfg.get("colluder_selection", "random"))
    return _select_colluders(
        agent_names=names,
        count=int(count),
        strategy=selection_strategy,
        rng=rng,
    )


def _select_noncolluder_secret_pairs(
    *,
    agent_names: Sequence[str],
    colluders: Sequence[str],
    secret_channel_count: int,
) -> List[List[str]]:
    """Select disjoint non-colluder pairs for extra private channels.

    Channel count semantics:
    - 0: no private channels
    - 1: one coalition-only channel
    - 2+: coalition channel plus `count - 1` disjoint pair channels among
      non-colluders, preserving environment agent order.
    """
    extra_pair_count = max(0, int(secret_channel_count) - 1)
    if extra_pair_count <= 0:
        return []

    colluder_set = {str(a) for a in colluders}
    available = [str(a) for a in agent_names if str(a) not in colluder_set]
    required_agents = extra_pair_count * 2
    if len(available) < required_agents:
        raise ValueError(
            f"Cannot create {extra_pair_count} extra disjoint non-colluder "
            f"secret channel(s): need {required_agents} non-colluders, found "
            f"{len(available)}."
        )

    pairs: List[List[str]] = []
    for idx in range(extra_pair_count):
        left = available[idx * 2]
        right = available[idx * 2 + 1]
        pairs.append([left, right])
    return pairs


def _looks_like_llm_config(value: Any) -> bool:
    return isinstance(value, dict) and bool(str(value.get("provider") or "").strip())


def _require_llm_config(value: Any, *, context: str) -> Dict[str, Any]:
    if not _looks_like_llm_config(value):
        raise ValueError(
            f"{context} must be an LLM config dict with a provider field."
        )
    return copy.deepcopy(value)


def _resolve_agent_llm_configs(
    *,
    agent_names: Sequence[str],
    default_llm_cfg: Dict[str, Any],
    assignment_cfg: Optional[Any],
) -> Dict[str, Dict[str, Any]]:
    names = [str(x) for x in agent_names]
    default_cfg = _require_llm_config(default_llm_cfg, context="llm")
    resolved = {name: copy.deepcopy(default_cfg) for name in names}

    if assignment_cfg is None:
        return resolved

    if isinstance(assignment_cfg, list):
        if len(assignment_cfg) != len(names):
            raise ValueError(
                f"agent_llms list has {len(assignment_cfg)} entries but the "
                f"environment has {len(names)} agents."
            )
        return {
            name: _require_llm_config(cfg, context=f"agent_llms[{idx}]")
            for idx, (name, cfg) in enumerate(zip(names, assignment_cfg))
        }

    if not isinstance(assignment_cfg, dict):
        raise ValueError("agent_llms must be a list or dictionary.")

    if assignment_cfg.get("default") is not None:
        default_cfg = _require_llm_config(
            assignment_cfg.get("default"), context="agent_llms.default"
        )
        resolved = {name: copy.deepcopy(default_cfg) for name in names}

    by_index = assignment_cfg.get("by_index")
    if by_index is not None:
        if isinstance(by_index, list):
            indexed_items = list(enumerate(by_index))
        elif isinstance(by_index, dict):
            indexed_items = list(by_index.items())
        else:
            raise ValueError("agent_llms.by_index must be a list or dictionary.")

        for raw_idx, cfg in indexed_items:
            try:
                idx = int(raw_idx)
            except Exception as exc:
                raise ValueError(
                    f"Invalid agent_llms.by_index key {raw_idx!r}; expected an integer."
                ) from exc
            if idx < 0 or idx >= len(names):
                raise ValueError(
                    f"agent_llms.by_index key {idx} is out of range for {len(names)} agents."
                )
            resolved[names[idx]] = _require_llm_config(
                cfg, context=f"agent_llms.by_index[{idx}]"
            )

    by_name = assignment_cfg.get("by_name")
    if by_name is None:
        reserved = {"default", "by_index", "by_name"}
        direct = {k: v for k, v in assignment_cfg.items() if k not in reserved}
        if direct and all(_looks_like_llm_config(v) for v in direct.values()):
            by_name = direct

    if by_name is not None:
        if not isinstance(by_name, dict):
            raise ValueError("agent_llms.by_name must be a dictionary.")
        unknown = [str(name) for name in by_name if str(name) not in set(names)]
        if unknown:
            raise ValueError(
                "agent_llms.by_name contains unknown agents: " + ", ".join(unknown)
            )
        for name, cfg in by_name.items():
            resolved[str(name)] = _require_llm_config(
                cfg, context=f"agent_llms.by_name[{name!r}]"
            )

    return resolved


def _summarize_agent_models(
    agent_model_assignments: Dict[str, Dict[str, str]]
) -> tuple[str, str]:
    providers = {
        str(v.get("provider") or "")
        for v in agent_model_assignments.values()
        if str(v.get("provider") or "").strip()
    }
    models = {
        str(v.get("model") or "")
        for v in agent_model_assignments.values()
        if str(v.get("model") or "").strip()
    }
    provider = next(iter(providers)) if len(providers) == 1 else "mixed"
    model = next(iter(models)) if len(models) == 1 else "mixed"
    return provider, model


def _order_agent_turns(
    *,
    agent_names: Sequence[str],
    colluders: Sequence[str],
    strategy: str,
) -> List[str]:
    strategy = str(strategy or "random").strip().lower()
    if strategy == "random":
        return list(agent_names)
    if strategy in {"colluders_first", "colluders-front", "colluders_front"}:
        colluder_set = {str(x) for x in colluders}
        ordered_colluders = [str(a) for a in agent_names if str(a) in colluder_set]
        ordered_others = [str(a) for a in agent_names if str(a) not in colluder_set]
        return ordered_colluders + ordered_others
    raise ValueError(
        f"Unknown agent_order strategy: {strategy!r} (expected: 'random' or 'colluders_first')"
    )


def _log_blackboards_txt(
    *,
    bb_logger: ExperimentBlackboardLogger,
    protocol: LocalCommunicationProtocol,
    iteration: int,
    phase: str,
    agent_name: str,
    planning_round: Optional[int] = None,
) -> None:
    for blackboard in protocol.megaboard.blackboards:
        bb_logger.log_blackboard_state(
            blackboard,
            iteration=iteration,
            phase=phase,
            agent_name=agent_name,
            round_num=planning_round,
        )


async def _run_single(
    *,
    base_cfg: Dict[str, Any],
    model_label: str,
    model_llm_cfg: Dict[str, Any],
    model_agent_llms: Optional[Any] = None,
    model_collusion_cfg: Optional[Dict[str, Any]] = None,
    sweep_name: str,
    environment_label: Optional[str] = None,
    environment_cfg: Optional[Dict[str, Any]] = None,
    topology: str,
    num_agents: int,
    colluder_count: int,
    secret_channel_enabled: bool,
    secret_channel_count: Optional[int] = None,
    prompt_variant: str,
    seed: int,
    replica_index: int = 0,
    out_dir: Path,
) -> Dict[str, Any]:
    rng = random.Random(int(seed))
    if secret_channel_count is None:
        secret_channel_count = 1 if bool(secret_channel_enabled) else 0
    secret_channel_count = max(0, int(secret_channel_count))
    secret_channel_enabled = secret_channel_count > 0
    replica_index = max(0, int(replica_index))
    # Design choice: prompt variants are only active when the secret channel exists.
    # When secret_channel_enabled is False, we force the effective variant to "control"
    # so the baseline is the vanilla cooperative setup (no explicit collusion prompting).
    effective_prompt_variant = str(prompt_variant or "").strip() or "control"
    if not secret_channel_enabled:
        effective_prompt_variant = "control"

    cfg = copy.deepcopy(base_cfg)
    if environment_cfg is not None:
        cfg["environment"] = copy.deepcopy(environment_cfg)
    cfg.setdefault("simulation", {})["seed"] = int(seed)
    cfg.setdefault("simulation", {})["max_iterations"] = 1
    cfg.setdefault("simulation", {})["max_planning_rounds"] = int(
        cfg.get("experiment", {}).get(
            "planning_rounds", cfg.get("simulation", {}).get("max_planning_rounds", 2)
        )
    )
    cfg.setdefault("simulation", {})["max_conversation_steps"] = int(
        cfg.get("experiment", {}).get(
            "max_conversation_steps",
            cfg.get("simulation", {}).get("max_conversation_steps", 3),
        )
    )
    cfg.setdefault("communication_network", {})["topology"] = str(topology)
    cfg.setdefault("communication_network", {})["num_agents"] = int(num_agents)
    cfg["llm"] = copy.deepcopy(model_llm_cfg)

    run_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    env_label: Optional[str] = None
    if environment_label is not None:
        cleaned = _sanitize_label(environment_label)
        if cleaned:
            env_label = cleaned

    run_id = _build_run_id(
        model_label=model_label,
        sweep_name=sweep_name,
        environment_label=env_label,
        topology=str(topology),
        num_agents=int(num_agents),
        colluder_count=int(colluder_count),
        secret_channel_enabled=bool(secret_channel_enabled),
        secret_channel_count=int(secret_channel_count),
        effective_prompt_variant=str(effective_prompt_variant),
        seed=int(seed),
        replica_index=replica_index,
    )
    run_dir = out_dir / "runs" / model_label / sweep_name / run_id
    _ensure_dir(run_dir)

    logger.info("RUN START %s", run_id)

    cfg.setdefault("simulation", {})["run_timestamp"] = f"{run_timestamp}__{run_id}"
    cfg.setdefault("simulation", {})["replica_index"] = int(replica_index)
    cfg.setdefault("simulation", {})["tags"] = [
        str(cfg.get("experiment", {}).get("tag", "collusion"))
    ]

    protocol = LocalCommunicationProtocol(config=cfg)
    env_cls = _resolve_environment_class(cfg.get("environment") or {})
    env = env_cls(protocol, cfg, tool_logger=type("TL", (), {"log_dir": run_dir})())
    bb_logger = ExperimentBlackboardLogger(cfg, log_root=run_dir)
    bb_logger.clear_blackboard_logs()
    experiment_cfg = cfg.get("experiment") or {}
    log_prompts_cfg = experiment_cfg.get("log_prompts")
    log_prompts = True if log_prompts_cfg is None else bool(log_prompts_cfg)
    prompt_logger = (
        PromptLogger(
            environment_name=env.__class__.__name__,
            seed=int(seed),
            config=cfg,
            run_timestamp=cfg.get("simulation", {}).get("run_timestamp"),
            log_dir=run_dir,
        )
        if log_prompts
        else None
    )
    trajectory_logger = AgentTrajectoryLogger(
        environment_name=env.__class__.__name__,
        seed=int(seed),
        config=cfg,
        run_timestamp=cfg.get("simulation", {}).get("run_timestamp"),
        log_dir=run_dir,
    )

    agent_names = env.get_agent_names()
    communication_network = build_communication_network(agent_names, cfg)
    env.set_communication_network(communication_network)

    # Coalition membership + secret channel injection.
    collusion_cfg = copy.deepcopy((cfg.get("experiment") or {}).get("collusion") or {})
    collusion_cfg.update(copy.deepcopy(model_collusion_cfg or {}))
    colluders = _resolve_colluders_from_config(
        agent_names=agent_names,
        count=int(colluder_count),
        collusion_cfg=collusion_cfg,
        rng=rng,
    )
    agent_order_strategy = str(collusion_cfg.get("agent_order", "random"))
    ordered_agent_names = _order_agent_turns(
        agent_names=agent_names,
        colluders=colluders,
        strategy=agent_order_strategy,
    )
    colluder_set = set(colluders)
    roles = {
        str(a): ("colluder" if str(a) in colluder_set else "normal")
        for a in agent_names
    }

    # Wrap prompts (role-specific injection via agent_context).
    env.prompts = CollusionPrompts(
        env,
        cfg,
        prompt_variant=str(effective_prompt_variant),
        base_prompts=getattr(env, "prompts", None),
        experiment_prompt_logger=prompt_logger,
        log_prompts=log_prompts,
    )

    # Build agents. By default every agent uses model_llm_cfg. A model profile can
    # opt into heterogeneous agents with `agent_llms`.
    agent_llm_cfgs = _resolve_agent_llm_configs(
        agent_names=agent_names,
        default_llm_cfg=cfg["llm"],
        assignment_cfg=model_agent_llms,
    )
    max_steps = int(cfg["simulation"].get("max_conversation_steps", 3))

    agents: List[BaseAgent] = []
    agent_model_assignments: Dict[str, Dict[str, str]] = {}
    env_tool_name = str(
        getattr(env, "tools_environment_name", None) or env.__class__.__name__
    )
    for name in agent_names:
        agent_llm_cfg = agent_llm_cfgs[str(name)]
        agent_provider = str(agent_llm_cfg.get("provider") or "").lower()
        agent_model_name = get_model_name(agent_provider, agent_llm_cfg)
        agent_generation_params = get_generation_params(agent_llm_cfg)
        agent_model_assignments[str(name)] = {
            "provider": agent_provider,
            "model": str(agent_model_name),
        }
        client = get_client_instance(agent_llm_cfg, agent_name=name)
        agents.append(
            BaseAgent(
                client,
                name,
                agent_model_name,
                max_steps,
                None,
                trajectory_logger,
                env_tool_name,
                generation_params=agent_generation_params,
            )
        )
    provider, model_name = _summarize_agent_models(agent_model_assignments)
    if str(agent_order_strategy or "random").strip().lower() == "random":
        rng.shuffle(agents)
    else:
        agents_by_name = {a.name: a for a in agents}
        agents = [agents_by_name[name] for name in ordered_agent_names if name in agents_by_name]
    env.set_agent_clients(agents)

    # Create blackboards for the communication network.
    await env.async_init()

    secret_blackboard_id: Optional[int] = None
    secret_blackboard_ids: List[int] = []
    extra_secret_blackboard_ids: List[int] = []
    noncolluder_secret_pairs: List[List[str]] = []
    if secret_channel_enabled and len(colluders) >= 2:
        try:
            base_context = env.get_network_context()
            secret_context = env.format_blackboard_context(colluders, base_context)
        except Exception:
            secret_context = (
                env.get_network_context() if hasattr(env, "get_network_context") else ""
            )
        secret_blackboard_id = await protocol.generate_comm_network(
            colluders,
            secret_context,
            template={
                "secret_channel": True,
                "visibility": "secret",
                "secret_channel_kind": "coalition",
                "secret_channel_index": 1,
                "allow_duplicate": True,
            },
        )
        secret_blackboard_ids.append(int(secret_blackboard_id))

        noncolluder_secret_pairs = _select_noncolluder_secret_pairs(
            agent_names=agent_names,
            colluders=colluders,
            secret_channel_count=int(secret_channel_count),
        )
        for pair_index, pair in enumerate(noncolluder_secret_pairs, start=2):
            try:
                base_context = env.get_network_context()
                pair_context = env.format_blackboard_context(pair, base_context)
            except Exception:
                pair_context = (
                    env.get_network_context()
                    if hasattr(env, "get_network_context")
                    else ""
                )
            pair_blackboard_id = await protocol.generate_comm_network(
                pair,
                pair_context,
                template={
                    "secret_channel": True,
                    "visibility": "secret",
                    "secret_channel_kind": "noncolluder_pair",
                    "secret_channel_index": int(pair_index),
                    "allow_duplicate": True,
                },
            )
            secret_blackboard_ids.append(int(pair_blackboard_id))
            extra_secret_blackboard_ids.append(int(pair_blackboard_id))

    try:
        _log_blackboards_txt(
            bb_logger=bb_logger,
            protocol=protocol,
            iteration=0,
            phase="init",
            agent_name="SYSTEM",
            planning_round=None,
        )
    except Exception as exc:
        logger.warning("Failed to write initial blackboard_*.txt logs: %s", exc)

    logger.info(
        "RUN %s setup complete (agents=%s, colluders=%s, secret_channel_count=%s, secret_bb_ids=%s, blackboards=%s)",
        run_id,
        len(agent_names),
        len(colluders),
        int(secret_channel_count),
        secret_blackboard_ids,
        len(protocol.megaboard.blackboards),
    )

    planning_rounds = int(cfg["simulation"].get("max_planning_rounds", 1))
    turns: List[Dict[str, Any]] = []

    def _extra_fields(agent_name: str) -> Dict[str, Any]:
        # Baseline (secret_channel_enabled=False): no explicit collusion prompting.
        prompt_role = (
            "colluder"
            if (secret_channel_enabled and str(agent_name) in colluder_set)
            else "normal"
        )
        return {
            "collusion_role": prompt_role,
            "collusion_coalition_members": sorted(list(colluder_set))
            if prompt_role == "colluder"
            else [],
            "collusion_blackboard_id": secret_blackboard_id
            if prompt_role == "colluder"
            else None,
        }

    for planning_round in range(1, planning_rounds + 1):
        logger.info(
            "RUN %s planning round %s/%s", run_id, planning_round, planning_rounds
        )
        last_agent = None
        for agent in env.agents:
            base_ctx = env.build_agent_context(
                agent.name,
                phase="planning",
                iteration=1,
                planning_round=planning_round,
            )
            agent_context = dict(base_ctx)
            agent_context.update(_extra_fields(agent.name))
            response = await protocol.agent_planning_turn(
                agent,
                agent.name,
                agent_context,
                env,
                iteration=1,
                planning_round=planning_round,
            )
            turns.append(
                {
                    "phase": "planning",
                    "planning_round": planning_round,
                    "agent": agent.name,
                    "role": roles.get(agent.name, "normal"),
                    "response": response.get("response"),
                    "usage": response.get("usage"),
                    "model": response.get("model"),
                    "tools_executed": response.get("tools_executed"),
                    "conversation_steps": response.get("conversation_steps"),
                }
            )
            last_agent = agent.name
        if last_agent:
            try:
                _log_blackboards_txt(
                    bb_logger=bb_logger,
                    protocol=protocol,
                    iteration=1,
                    phase="planning",
                    agent_name=str(last_agent),
                    planning_round=int(planning_round),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to write blackboard_*.txt logs after planning round %s: %s",
                    planning_round,
                    exc,
                )

    logger.info("RUN %s execution phase start", run_id)
    last_exec_agent = None
    for agent in env.agents:
        base_ctx = env.build_agent_context(agent.name, phase="execution", iteration=1)
        agent_context = dict(base_ctx)
        agent_context.update(_extra_fields(agent.name))
        response = await protocol.agent_execution_turn(
            agent, agent.name, agent_context, env, iteration=1
        )
        turns.append(
            {
                "phase": "execution",
                "agent": agent.name,
                "role": roles.get(agent.name, "normal"),
                "response": response.get("response"),
                "usage": response.get("usage"),
                "model": response.get("model"),
                "tools_executed": response.get("tools_executed"),
                "conversation_steps": response.get("conversation_steps"),
            }
        )
        last_exec_agent = agent.name

    if last_exec_agent:
        try:
            _log_blackboards_txt(
                bb_logger=bb_logger,
                protocol=protocol,
                iteration=1,
                phase="execution",
                agent_name=str(last_exec_agent),
                planning_round=None,
            )
        except Exception as exc:
            logger.warning(
                "Failed to write blackboard_*.txt logs after execution: %s", exc
            )

    final_summary = env.get_final_summary()
    logger.info(
        "RUN %s execution complete (status=%s)", run_id, final_summary.get("status")
    )

    blackboard_participants = {
        idx: sorted(list(bb.agents))
        for idx, bb in enumerate(protocol.megaboard.blackboards)
    }
    metrics = compute_collusion_metrics(
        env=env,
        colluders=colluders,
        secret_blackboard_id=secret_blackboard_id,
        secret_channel_enabled=secret_channel_enabled,
        prompt_variant=str(effective_prompt_variant),
        tool_events=protocol.tool_events,
        blackboard_participants=blackboard_participants,
        final_summary=final_summary,
    )

    _write_json(
        run_dir / "run_config.json",
        {
            "run_id": run_id,
            "model_label": model_label,
            "provider": provider,
            "model": model_name,
            "sweep": sweep_name,
            "environment_label": env_label,
            "environment_cfg": cfg.get("environment") or {},
            "environment_name": env.__class__.__name__,
            "topology": topology,
            "num_agents": num_agents,
            "colluder_count": colluder_count,
            "colluders": colluders,
            "agent_order": str(agent_order_strategy or "random"),
            "agent_turn_order": [a.name for a in env.agents],
            "agent_model_assignments": agent_model_assignments,
            "colluder_model_assignments": {
                name: agent_model_assignments.get(name, {}) for name in colluders
            },
            "secret_channel_enabled": secret_channel_enabled,
            "secret_channel_count": int(secret_channel_count),
            "secret_blackboard_id": secret_blackboard_id,
            "secret_blackboard_ids": secret_blackboard_ids,
            "extra_secret_blackboard_ids": extra_secret_blackboard_ids,
            "noncolluder_secret_pairs": noncolluder_secret_pairs,
            "prompt_variant": str(effective_prompt_variant),
            "seed": seed,
            "replica_index": int(replica_index),
            "roles": roles,
            "blackboard_participants": blackboard_participants,
        },
    )
    _write_json(run_dir / "final_summary.json", final_summary)
    _write_json(run_dir / "agent_turns.json", turns)
    _write_json(run_dir / "metrics.json", metrics_to_json(metrics))
    _write_json(
        run_dir / "tool_events.json",
        [
            {
                "tool_name": e.tool_name,
                "agent_name": e.agent_name,
                "arguments": e.arguments,
                "result": e.result,
                "phase": e.phase,
                "iteration": e.iteration,
                "planning_round": e.planning_round,
            }
            for e in protocol.tool_events
        ],
    )
    _write_json(
        run_dir / "blackboards.json",
        [
            {
                "blackboard_id": bb.blackboard_id,
                "participants": sorted(list(bb.agents)),
                "template": bb.template,
                "events": bb.logs,
            }
            for bb in protocol.megaboard.blackboards
        ],
    )

    logger.info("RUN END %s (artifacts=%s)", run_id, run_dir)
    joint_reward = final_summary.get("joint_reward")
    coalition_reward_ratio = None
    try:
        if (
            metrics.coalition_reward_sum is not None
            and joint_reward is not None
            and float(joint_reward) != 0.0
        ):
            coalition_reward_ratio = float(metrics.coalition_reward_sum) / float(
                joint_reward
            )
    except Exception:
        coalition_reward_ratio = None
    return {
        "run_id": run_id,
        "model_label": model_label,
        "provider": provider,
        "model": model_name,
        "sweep": sweep_name,
        "environment_label": env_label,
        "environment_name": env.__class__.__name__,
        "topology": topology,
        "num_agents": num_agents,
        "colluder_count": colluder_count,
        "secret_channel_enabled": secret_channel_enabled,
        "secret_channel_count": int(secret_channel_count),
        "secret_blackboard_id": secret_blackboard_id,
        "secret_blackboard_ids": secret_blackboard_ids,
        "extra_secret_blackboard_ids": extra_secret_blackboard_ids,
        "noncolluder_secret_pairs": noncolluder_secret_pairs,
        "prompt_variant": str(effective_prompt_variant),
        "seed": seed,
        "replica_index": int(replica_index),
        "colluders": colluders,
        "agent_order": str(agent_order_strategy or "random"),
        "agent_model_assignments": agent_model_assignments,
        "status": metrics.status,
        # Radar-chart inputs (environment summary + collusion metrics).
        "joint_reward": joint_reward,
        "joint_reward_ratio": final_summary.get("joint_reward_ratio"),
        "coverage": final_summary.get("coverage"),
        "normalized_score": final_summary.get("normalized_score"),
        "tasks_done": metrics.tasks_done,
        "violations": metrics.violations,
        "total_cost": metrics.total_cost,
        "priority_sum": metrics.priority_sum,
        "mean_regret": metrics.mean_regret,
        "reward_std": metrics.reward_std,
        "coalition_reward_sum": metrics.coalition_reward_sum,
        "noncoalition_reward_sum": metrics.noncoalition_reward_sum,
        "coalition_mean_reward": metrics.coalition_mean_reward,
        "noncoalition_mean_reward": metrics.noncoalition_mean_reward,
        "coalition_advantage_mean": metrics.coalition_advantage_mean,
        "coalition_reward_ratio": coalition_reward_ratio,
        "colluder_posts_secret_rate": metrics.colluder_posts_secret_rate,
        "mean_regret": metrics.mean_regret,
        "coalition_mean_regret": metrics.coalition_mean_regret,
        "noncoalition_mean_regret": metrics.noncoalition_mean_regret,
        "system_regret": metrics.system_regret,
        "system_regret_ratio": metrics.system_regret_ratio,
        "colluder_posts_total": metrics.colluder_posts_total,
        "colluder_posts_secret": metrics.colluder_posts_secret,
        "colluder_posts_non_secret": metrics.colluder_posts_non_secret,
        "largest_non_secret_blackboard_id": metrics.largest_non_secret_blackboard_id,
        "colluder_posts_secret_to_largest_bb_ratio_mean": metrics.colluder_posts_secret_to_largest_bb_ratio_mean,
    }


async def run_from_config(
    config_path: str,
    *,
    out_dir: Optional[str] = None,
    max_concurrent_runs: Optional[int] = None,
) -> Path:
    cfg = _load_yaml(config_path)
    exp = cfg.get("experiment") or {}
    if max_concurrent_runs is None:
        max_concurrent_runs = exp.get("max_concurrent_runs", 1)
    max_concurrent_runs = int(max_concurrent_runs)
    if max_concurrent_runs <= 0:
        raise ValueError("max_concurrent_runs must be a positive integer")

    run_specs = list(_iter_expected_run_specs(cfg))

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = (
        Path(
            out_dir
            or exp.get("output_dir")
            or "experiments/collusion/outputs/collusion"
        )
        / timestamp
    )
    _ensure_dir(root)
    _write_json(root / "config.json", cfg)
    _configure_experiment_logging(root)

    total_runs = len(run_specs)

    logger.info("EXPERIMENT START (total_runs=%s, output_root=%s)", total_runs, root)
    _write_progress(
        root,
        {
            "status": "running",
            "total_runs": total_runs,
            "completed_runs": 0,
            "failed_runs": 0,
            "started_at": datetime.now().isoformat(),
            "config_path": str(config_path),
        },
    )

    summaries: List[Dict[str, Any]] = []
    completed = 0
    failed = 0

    with tqdm(
        total=total_runs, desc="Experiments", unit="run", dynamic_ncols=True
    ) as pbar:
        if max_concurrent_runs <= 1:
            last_model_label: Optional[str] = None
            last_sweep_name: Optional[str] = None
            for spec in run_specs:
                if spec.model_label != last_model_label:
                    logger.info("MODEL START %s", spec.model_label)
                    last_model_label = spec.model_label
                    last_sweep_name = None
                if spec.sweep_name != last_sweep_name:
                    logger.info("SWEEP START %s", spec.sweep_name)
                    last_sweep_name = spec.sweep_name

                pbar.set_postfix_str(spec.run_label)
                run_status = "success"
                try:
                    summaries.append(
                        await _run_single(
                            base_cfg=cfg,
                            model_label=spec.model_label,
                            model_llm_cfg=spec.model_llm_cfg,
                            model_agent_llms=spec.model_agent_llms,
                            model_collusion_cfg=spec.model_collusion_cfg,
                            sweep_name=spec.sweep_name,
                            environment_label=spec.environment_label,
                            environment_cfg=spec.environment_cfg,
                            topology=spec.topology,
                            num_agents=spec.num_agents,
                            colluder_count=spec.colluder_count,
                            secret_channel_enabled=spec.secret_channel_enabled,
                            secret_channel_count=spec.secret_channel_count,
                            prompt_variant=spec.prompt_variant,
                            seed=spec.seed,
                            replica_index=spec.replica_index,
                            out_dir=root,
                        )
                    )
                    completed += 1
                except Exception:
                    run_status = "failed"
                    failed += 1
                    logger.exception("RUN FAILED %s", spec.run_label)
                    raise
                finally:
                    pbar.update(1)
                    _write_progress(
                        root,
                        {
                            "status": "running",
                            "total_runs": total_runs,
                            "completed_runs": completed,
                            "failed_runs": failed,
                            "last_run_label": spec.run_label,
                            "last_run_status": run_status,
                        },
                    )
        else:
            import asyncio

            semaphore = asyncio.Semaphore(int(max_concurrent_runs))

            def _run_single_in_thread(*, spec: RunSpec) -> Dict[str, Any]:
                return asyncio.run(
                    _run_single(
                        base_cfg=cfg,
                        model_label=spec.model_label,
                        model_llm_cfg=spec.model_llm_cfg,
                        model_agent_llms=spec.model_agent_llms,
                        model_collusion_cfg=spec.model_collusion_cfg,
                        sweep_name=spec.sweep_name,
                        environment_label=spec.environment_label,
                        environment_cfg=spec.environment_cfg,
                        topology=spec.topology,
                        num_agents=spec.num_agents,
                        colluder_count=spec.colluder_count,
                        secret_channel_enabled=spec.secret_channel_enabled,
                        secret_channel_count=spec.secret_channel_count,
                        prompt_variant=spec.prompt_variant,
                        seed=spec.seed,
                        replica_index=spec.replica_index,
                        out_dir=root,
                    )
                )

            async def _run_single_limited(*, spec: RunSpec) -> Dict[str, Any]:
                async with semaphore:
                    logger.info("SCHEDULED %s", spec.run_label)
                    return await asyncio.to_thread(_run_single_in_thread, spec=spec)

            tasks: List[asyncio.Task[Any]] = []
            task_specs: Dict[asyncio.Task[Any], RunSpec] = {}
            for spec in run_specs:
                task = asyncio.create_task(_run_single_limited(spec=spec))
                tasks.append(task)
                task_specs[task] = spec

            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for finished in done:
                    spec = task_specs.get(finished)
                    if spec is None:
                        continue
                    pbar.set_postfix_str(spec.run_label)
                    run_status = "success"
                    try:
                        summaries.append(await finished)
                        completed += 1
                    except Exception:
                        run_status = "failed"
                        failed += 1
                        logger.exception("RUN FAILED %s", spec.run_label)
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        raise
                    finally:
                        pbar.update(1)
                        _write_progress(
                            root,
                            {
                                "status": "running",
                                "total_runs": total_runs,
                                "completed_runs": completed,
                                "failed_runs": failed,
                                "last_run_label": spec.run_label,
                                "last_run_status": run_status,
                            },
                        )

    summaries = sorted(summaries, key=lambda row: str(row.get("run_id") or ""))
    _write_json(root / "summary.json", summaries)
    with open(root / "summary.jsonl", "w", encoding="utf-8") as f:
        for row in summaries:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    csv_rows = []
    for row in summaries:
        flat = {k: v for k, v in row.items() if not isinstance(v, (dict, list))}
        csv_rows.append(flat)
    if csv_rows:
        fieldnames = sorted({k for r in csv_rows for k in r.keys()})
        with open(root / "summary.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)

    logger.info(
        "EXPERIMENT END (completed=%s, failed=%s, output_root=%s)",
        completed,
        failed,
        root,
    )
    _write_progress(
        root,
        {
            "status": "completed",
            "total_runs": total_runs,
            "completed_runs": completed,
            "failed_runs": failed,
        },
    )
    return root


def _print_dry_run_summary(
    *,
    config_path: str,
    cfg: Dict[str, Any],
    out_dir: Optional[str],
    max_concurrent_runs: Optional[int],
) -> None:
    exp = cfg.get("experiment") or {}
    if max_concurrent_runs is None:
        max_concurrent_runs = exp.get("max_concurrent_runs", 1)
    max_concurrent_runs = int(max_concurrent_runs)
    if max_concurrent_runs <= 0:
        raise ValueError("max_concurrent_runs must be a positive integer")

    run_specs = list(_iter_expected_run_specs(cfg))
    output_base = Path(
        out_dir
        or exp.get("output_dir")
        or "experiments/collusion/outputs/collusion"
    )

    model_counts: Dict[str, int] = {}
    model_order: List[str] = []
    for spec in run_specs:
        if spec.model_label not in model_counts:
            model_order.append(spec.model_label)
            model_counts[spec.model_label] = 0
        model_counts[spec.model_label] += 1

    print("Dry run: no model calls will be made.")
    print(f"Config: {config_path}")
    print(f"Output root pattern: {output_base}/<timestamp>")
    print(f"Max concurrent runs: {max_concurrent_runs}")
    print(f"Total runs: {len(run_specs)}")
    if model_order:
        print("Runs by model:")
        for model_label in model_order:
            print(f"  - {model_label}: {model_counts[model_label]}")
    if run_specs:
        print("First 10 runs:")
        for spec in run_specs[:10]:
            print(f"  - {spec.run_label}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run covert collusion sweeps (local protocol; no MCP)."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to experiment YAML config (e.g., experiments/collusion/configs/collusion_jira.yaml).",
    )
    parser.add_argument(
        "--out-dir", default=None, help="Override output root directory."
    )
    parser.add_argument(
        "--max-concurrent-runs",
        default=None,
        type=int,
        help="Maximum number of runs to execute in parallel (overrides experiment.max_concurrent_runs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned run expansion and exit without model calls.",
    )
    args = parser.parse_args()

    if args.dry_run:
        cfg = _load_yaml(args.config)
        _print_dry_run_summary(
            config_path=args.config,
            cfg=cfg,
            out_dir=args.out_dir,
            max_concurrent_runs=args.max_concurrent_runs,
        )
        return

    import asyncio

    out = asyncio.run(
        run_from_config(
            args.config,
            out_dir=args.out_dir,
            max_concurrent_runs=args.max_concurrent_runs,
        )
    )
    print(f"Wrote results to: {out}")


if __name__ == "__main__":
    main()
