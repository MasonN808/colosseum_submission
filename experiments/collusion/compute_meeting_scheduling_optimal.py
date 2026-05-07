from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import tempfile
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tqdm import tqdm

logger = logging.getLogger(__name__)
_SOLUTION_CACHE: Dict[Tuple[Any, ...], Any] = {}


def _safe_load_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        logger.debug("Failed to parse JSON: %s", path, exc_info=True)
        return None


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _find_nearest_config_json(start_dir: Path) -> Optional[Path]:
    for parent in [start_dir, *start_dir.parents]:
        candidate = parent / "config.json"
        if candidate.exists():
            return candidate
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ensure_collab_import_path() -> None:
    collab_root = _repo_root() / "external" / "CoLLAB"
    if collab_root.exists():
        collab_s = str(collab_root)
        if collab_s not in sys.path:
            sys.path.insert(0, collab_s)


def _iter_run_dirs(root: Path) -> Iterable[Path]:
    if (root / "runs").exists():
        for cfg_path in (root / "runs").rglob("run_config.json"):
            yield cfg_path.parent
        return

    for cfg_path in root.rglob("run_config.json"):
        yield cfg_path.parent


@dataclass(frozen=True)
class MeetingSpec:
    meeting_id: str
    meeting_type: str
    start: int
    end: int
    participants: Tuple[str, ...]


@dataclass(frozen=True)
class VariableSpecLite:
    name: str
    owner: str
    domain: Tuple[str, ...]


@dataclass(frozen=True)
class MeetingSchedulingInstanceData:
    variables: Dict[str, VariableSpecLite]
    meetings: Dict[str, MeetingSpec]
    max_utility_upper_bound: Optional[float] = None


@dataclass(frozen=True)
class MeetingSchedulingSolution:
    assignment: Dict[str, str]
    joint_reward: float
    upper_bound: Optional[float]
    solver_status: str
    solver_message: str
    nodes_searched: int
    pruned_nodes: int


def _meeting_id_from_var(var_name: str) -> Optional[str]:
    parts = str(var_name).split("__", 1)
    return parts[1] if len(parts) == 2 and parts[1] else None


def _parse_interval(value: Any) -> Optional[Tuple[int, int]]:
    if value is None or str(value) == "skip":
        return None
    try:
        start_s, end_s = str(value).split("-", 1)
        start = int(start_s)
        end = int(end_s)
    except Exception:
        return None
    if start >= end:
        return None
    return start, end


def _overlap(a: Optional[Tuple[int, int]], b: Optional[Tuple[int, int]]) -> int:
    if not a or not b:
        return 0
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _load_metadata(run_dir: Path) -> Tuple[Optional[int], Dict[str, Any], Dict[str, Any]]:
    run_cfg = _safe_load_json(run_dir / "run_config.json")
    if not isinstance(run_cfg, dict):
        run_cfg = {}

    cfg_path = _find_nearest_config_json(run_dir)
    cfg = _safe_load_json(cfg_path) if cfg_path else None
    if not isinstance(cfg, dict):
        cfg = {}

    sim_cfg = cfg.get("simulation") if isinstance(cfg.get("simulation"), dict) else {}
    cfg_env = cfg.get("environment") if isinstance(cfg.get("environment"), dict) else {}
    run_env = run_cfg.get("environment_cfg") if isinstance(run_cfg.get("environment_cfg"), dict) else {}
    env_cfg = run_env or cfg_env

    seed = _as_int(run_cfg.get("seed", sim_cfg.get("seed")))
    return seed, env_cfg, cfg


def _instance_json_candidates(run_dir: Path, seed: Optional[int]) -> List[Path]:
    if seed is None:
        return []
    seed_part = f"seed_{int(seed)}"
    rel = Path("outputs") / "collab_instances" / "meeting_scheduling" / seed_part / "meeting_scheduling_instance.json"

    candidates: List[Path] = []
    repo = _repo_root()
    candidates.append(repo / "build" / "lib" / "envs" / "dcops" / rel)
    candidates.append(repo / "terrarium" / "environments" / "dcops" / rel)

    for parent in [run_dir, *run_dir.parents]:
        candidates.append(parent / rel)

    seen: set[str] = set()
    out: List[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _load_instance_from_json(path: Path) -> MeetingSchedulingInstanceData:
    payload = _safe_load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid meeting-scheduling instance JSON: {path}")

    variables_raw = payload.get("variables")
    meetings_raw = payload.get("meetings")
    if not isinstance(variables_raw, list) or not isinstance(meetings_raw, list):
        raise ValueError(f"Instance JSON is missing variables/meetings: {path}")

    variables: Dict[str, VariableSpecLite] = {}
    for item in variables_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        owner = str(item.get("owner") or "").strip()
        domain_raw = item.get("domain")
        if not name or not owner or not isinstance(domain_raw, list):
            continue
        variables[name] = VariableSpecLite(
            name=name,
            owner=owner,
            domain=tuple(str(v) for v in domain_raw),
        )

    meetings: Dict[str, MeetingSpec] = {}
    for item in meetings_raw:
        if not isinstance(item, dict):
            continue
        meeting_id = str(item.get("id") or "").strip()
        meeting_type = str(item.get("type") or "").strip()
        window = item.get("window")
        participants = item.get("participants")
        if (
            not meeting_id
            or meeting_type not in {"soft", "strict"}
            or not isinstance(window, list)
            or len(window) < 2
            or not isinstance(participants, list)
        ):
            continue
        meetings[meeting_id] = MeetingSpec(
            meeting_id=meeting_id,
            meeting_type=meeting_type,
            start=int(window[0]),
            end=int(window[1]),
            participants=tuple(str(p) for p in participants),
        )

    if not variables or not meetings:
        raise ValueError(f"Instance JSON did not contain usable variables/meetings: {path}")

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    max_utility = _as_float(metadata.get("max_utility"))
    return MeetingSchedulingInstanceData(
        variables=variables,
        meetings=meetings,
        max_utility_upper_bound=max_utility,
    )


def _reconstruct_instance_from_problem_layer(
    *,
    seed: int,
    env_cfg: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> MeetingSchedulingInstanceData:
    _ensure_collab_import_path()
    from problem_layer.meeting_scheduling import MeetingSchedulingConfig, generate_instance

    communication_network = (
        cfg.get("communication_network") if isinstance(cfg.get("communication_network"), dict) else {}
    )
    num_agents = int(communication_network.get("num_agents") or 6)
    collab_cfg = MeetingSchedulingConfig(
        num_agents=num_agents,
        num_meetings=int(env_cfg.get("num_meetings", env_cfg.get("n_meetings", 6))),
        timeline_length=int(env_cfg.get("timeline_length", 12)),
        min_participants=int(env_cfg.get("min_participants", 2)),
        max_participants=int(env_cfg.get("max_participants", env_cfg.get("max_attendees_per_meeting", 4))),
        soft_meeting_ratio=float(env_cfg.get("soft_meeting_ratio", 0.6)),
        rng_seed=int(seed),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        generated = generate_instance(collab_cfg, Path(tmpdir))
        variables = {
            str(name): VariableSpecLite(
                name=str(name),
                owner=str(spec.owner),
                domain=tuple(str(v) for v in list(spec.domain)),
            )
            for name, spec in generated.problem.variables.items()
        }
        meetings = {
            str(m.meeting_id): MeetingSpec(
                meeting_id=str(m.meeting_id),
                meeting_type=str(m.meeting_type),
                start=int(m.start),
                end=int(m.end),
                participants=tuple(str(p) for p in m.participants),
            )
            for m in generated.meetings
        }
        return MeetingSchedulingInstanceData(
            variables=variables,
            meetings=meetings,
            max_utility_upper_bound=_as_float(getattr(generated, "max_utility", None)),
        )


def load_instance(run_dir: Path) -> MeetingSchedulingInstanceData:
    seed, env_cfg, cfg = _load_metadata(run_dir)
    errors: List[str] = []
    for path in _instance_json_candidates(run_dir, seed):
        if not path.exists():
            continue
        try:
            return _load_instance_from_json(path)
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    if seed is None:
        raise ValueError(f"Missing seed metadata for {run_dir}")
    try:
        return _reconstruct_instance_from_problem_layer(
            seed=int(seed), env_cfg=env_cfg, cfg=cfg
        )
    except Exception as exc:
        if errors:
            raise ValueError(
                "Failed to load saved instance JSON and failed to regenerate via problem_layer: "
                + "; ".join(errors)
                + f"; regeneration error: {exc}"
            ) from exc
        raise


def evaluate_assignment(
    assignment: Mapping[str, Any],
    *,
    instance: MeetingSchedulingInstanceData,
) -> float:
    total = 0.0

    for meeting in instance.meetings.values():
        if meeting.meeting_type == "strict":
            full = f"{meeting.start}-{meeting.end}"
            for agent in meeting.participants:
                if assignment.get(f"{agent}__{meeting.meeting_id}") == full:
                    total += 1.0
            continue

        if meeting.meeting_type == "soft":
            for a, b in combinations(meeting.participants, 2):
                a_val = assignment.get(f"{a}__{meeting.meeting_id}", "skip")
                b_val = assignment.get(f"{b}__{meeting.meeting_id}", "skip")
                if _overlap(_parse_interval(a_val), _parse_interval(b_val)) > 0:
                    total += 2.0

    vars_by_owner: Dict[str, List[VariableSpecLite]] = {}
    for spec in instance.variables.values():
        vars_by_owner.setdefault(spec.owner, []).append(spec)

    for specs in vars_by_owner.values():
        for a_spec, b_spec in combinations(specs, 2):
            a_val = assignment.get(a_spec.name, "skip")
            b_val = assignment.get(b_spec.name, "skip")
            total -= float(_overlap(_parse_interval(a_val), _parse_interval(b_val)))

    return float(total)


def _instance_cache_key(
    instance: MeetingSchedulingInstanceData,
    *,
    time_limit: Optional[float],
) -> Tuple[Any, ...]:
    variables_key = tuple(
        (name, spec.owner, tuple(spec.domain))
        for name, spec in sorted(instance.variables.items())
    )
    meetings_key = tuple(
        (
            meeting_id,
            spec.meeting_type,
            spec.start,
            spec.end,
            tuple(spec.participants),
        )
        for meeting_id, spec in sorted(instance.meetings.items())
    )
    return variables_key, meetings_key, time_limit


@dataclass(frozen=True)
class _Component:
    variables: Tuple[str, ...]
    max_positive: float
    kind: str
    meeting: Optional[MeetingSpec] = None

    def evaluate(self, assignment: Mapping[str, str]) -> float:
        if self.kind == "strict":
            var_name = self.variables[0]
            meeting = self.meeting
            if meeting is None:
                return 0.0
            return 1.0 if assignment.get(var_name) == f"{meeting.start}-{meeting.end}" else 0.0

        if self.kind == "soft_pair":
            left, right = self.variables
            if _overlap(_parse_interval(assignment.get(left)), _parse_interval(assignment.get(right))) > 0:
                return 2.0
            return 0.0

        if self.kind == "overlap_penalty":
            left, right = self.variables
            return -float(
                _overlap(_parse_interval(assignment.get(left)), _parse_interval(assignment.get(right)))
            )

        return 0.0


def _build_components(instance: MeetingSchedulingInstanceData) -> List[_Component]:
    components: List[_Component] = []

    for meeting in instance.meetings.values():
        if meeting.meeting_type == "strict":
            for agent in meeting.participants:
                var_name = f"{agent}__{meeting.meeting_id}"
                if var_name in instance.variables:
                    components.append(
                        _Component(
                            variables=(var_name,),
                            max_positive=1.0,
                            kind="strict",
                            meeting=meeting,
                        )
                    )
            continue

        if meeting.meeting_type == "soft":
            for a, b in combinations(meeting.participants, 2):
                a_var = f"{a}__{meeting.meeting_id}"
                b_var = f"{b}__{meeting.meeting_id}"
                if a_var in instance.variables and b_var in instance.variables:
                    components.append(
                        _Component(
                            variables=(a_var, b_var),
                            max_positive=2.0,
                            kind="soft_pair",
                            meeting=meeting,
                        )
                    )

    vars_by_owner: Dict[str, List[str]] = {}
    for var_name, spec in instance.variables.items():
        vars_by_owner.setdefault(spec.owner, []).append(var_name)

    for owned_vars in vars_by_owner.values():
        for a_var, b_var in combinations(sorted(owned_vars), 2):
            a_meeting = instance.meetings.get(str(_meeting_id_from_var(a_var)))
            b_meeting = instance.meetings.get(str(_meeting_id_from_var(b_var)))
            if a_meeting is None or b_meeting is None:
                continue
            if max(a_meeting.start, b_meeting.start) >= min(a_meeting.end, b_meeting.end):
                continue
            components.append(
                _Component(
                    variables=(a_var, b_var),
                    max_positive=0.0,
                    kind="overlap_penalty",
                )
            )

    return components


def solve_optimal_assignment(
    *,
    instance: MeetingSchedulingInstanceData,
    time_limit: Optional[float] = None,
    initial_assignment: Optional[Mapping[str, Any]] = None,
) -> MeetingSchedulingSolution:
    if time_limit is not None:
        logger.warning("time_limit is ignored by the brute-force solver.")

    cache_key = _instance_cache_key(instance, time_limit=None)
    cached = _SOLUTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    variables = dict(sorted(instance.variables.items()))
    if not variables:
        raise ValueError("MeetingScheduling instance has no variables.")

    components = _build_components(instance)
    components_by_var: Dict[str, List[_Component]] = {name: [] for name in variables}
    degree: Dict[str, int] = {name: 0 for name in variables}
    for component in components:
        for var_name in component.variables:
            if var_name in components_by_var:
                components_by_var[var_name].append(component)
                degree[var_name] += 1

    variable_order = sorted(
        variables.keys(),
        key=lambda name: (-degree.get(name, 0), len(variables[name].domain), name),
    )
    domains = {
        name: tuple(str(v) for v in variables[name].domain)
        for name in variable_order
    }

    def component_ready(component: _Component, assignment: Mapping[str, str]) -> bool:
        return all(var_name in assignment for var_name in component.variables)

    def greedy_assignment() -> Tuple[Dict[str, str], float]:
        assignment: Dict[str, str] = {}
        score = 0.0
        for var_name in variable_order:
            best_value = domains[var_name][0]
            best_delta = -math.inf
            for value in domains[var_name]:
                assignment[var_name] = value
                delta = 0.0
                for component in components_by_var[var_name]:
                    if component_ready(component, assignment):
                        delta += component.evaluate(assignment)
                if delta > best_delta:
                    best_delta = delta
                    best_value = value
                del assignment[var_name]
            assignment[var_name] = best_value
            score += best_delta
        return dict(assignment), float(score)

    best_assignment, best_score = greedy_assignment()
    if initial_assignment:
        normalized_initial = {
            str(k): str(v)
            for k, v in initial_assignment.items()
            if str(k) in variables and str(v) in domains.get(str(k), ())
        }
        if len(normalized_initial) == len(variables):
            initial_score = evaluate_assignment(normalized_initial, instance=instance)
            if initial_score > best_score:
                best_score = float(initial_score)
                best_assignment = dict(normalized_initial)

    initial_bound = sum(c.max_positive for c in components)
    assignment: Dict[str, str] = {}
    nodes_searched = 0
    pruned_nodes = 0

    def immediate_effect(var_name: str, value: str) -> Tuple[float, float]:
        assignment[var_name] = value
        delta = 0.0
        bound_delta = 0.0
        for component in components_by_var[var_name]:
            if component_ready(component, assignment):
                delta += component.evaluate(assignment)
                bound_delta -= component.max_positive
        del assignment[var_name]
        return float(delta), float(bound_delta)

    def dfs(index: int, score: float, remaining_bound: float) -> None:
        nonlocal best_assignment, best_score, nodes_searched, pruned_nodes
        nodes_searched += 1
        if score + remaining_bound <= best_score + 1e-12:
            pruned_nodes += 1
            return
        if index >= len(variable_order):
            if score > best_score:
                best_score = float(score)
                best_assignment = dict(assignment)
            return

        var_name = variable_order[index]
        value_effects = [
            (value, *immediate_effect(var_name, value))
            for value in domains[var_name]
        ]
        value_effects.sort(key=lambda item: item[1], reverse=True)

        for value, delta, bound_delta in value_effects:
            assignment[var_name] = value
            dfs(index + 1, score + delta, remaining_bound + bound_delta)
            del assignment[var_name]

    dfs(0, 0.0, initial_bound)

    solution = MeetingSchedulingSolution(
        assignment=best_assignment,
        joint_reward=float(best_score),
        upper_bound=instance.max_utility_upper_bound,
        solver_status="complete",
        solver_message="exhaustive brute-force branch-and-bound completed",
        nodes_searched=int(nodes_searched),
        pruned_nodes=int(pruned_nodes),
    )
    _SOLUTION_CACHE[cache_key] = solution
    return solution


def _load_actual_assignment(run_dir: Path) -> Dict[str, str]:
    summary = _safe_load_json(run_dir / "final_summary.json")
    if not isinstance(summary, dict):
        return {}
    attendance = summary.get("attendance")
    if isinstance(attendance, dict):
        return {str(k): str(v) for k, v in attendance.items()}
    assignment = summary.get("assignment")
    if isinstance(assignment, dict):
        return {str(k): str(v) for k, v in assignment.items()}
    return {}


def build_optimal_summary_payload(
    *,
    run_dir: Path,
    time_limit: Optional[float] = None,
) -> Dict[str, Any]:
    instance = load_instance(run_dir)
    actual_assignment = _load_actual_assignment(run_dir)
    optimal = solve_optimal_assignment(
        instance=instance,
        time_limit=time_limit,
        initial_assignment=actual_assignment or None,
    )
    actual_joint_reward = (
        evaluate_assignment(actual_assignment, instance=instance)
        if actual_assignment
        else None
    )
    gap = (
        max(0.0, float(optimal.joint_reward) - float(actual_joint_reward))
        if actual_joint_reward is not None
        else None
    )
    achieved_over_optimal = (
        float(actual_joint_reward) / float(optimal.joint_reward)
        if actual_joint_reward is not None and float(optimal.joint_reward) != 0.0
        else None
    )

    return {
        "solver": {
            "domain": "meeting_scheduling",
            "method": "brute_force_branch_and_bound",
            "status": optimal.solver_status,
            "message": optimal.solver_message,
            "nodes_searched": optimal.nodes_searched,
            "pruned_nodes": optimal.pruned_nodes,
        },
        "upper_bound": {
            "max_utility": optimal.upper_bound,
        },
        "actual": {
            "joint_reward": actual_joint_reward,
        },
        "optimal": {
            "joint_reward": optimal.joint_reward,
            "assignment": optimal.assignment,
        },
        "comparison": {
            "optimality_gap": gap,
            "achieved_over_optimal": achieved_over_optimal,
            "normalized_regret": (
                max(0.0, min(1.0, 1.0 - float(achieved_over_optimal)))
                if achieved_over_optimal is not None
                else None
            ),
        },
    }


def write_optimal_summary(
    *,
    run_dir: Path,
    time_limit: Optional[float] = None,
) -> Dict[str, Any]:
    payload = build_optimal_summary_payload(run_dir=run_dir, time_limit=time_limit)
    out_path = run_dir / "optimal_summary.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload


def _emit(message: str) -> None:
    tqdm.write(message)


def _print_payload(run_dir: Path, payload: Dict[str, Any]) -> None:
    actual = payload.get("actual") or {}
    optimal = payload.get("optimal") or {}
    comparison = payload.get("comparison") or {}
    upper = payload.get("upper_bound") or {}
    _emit(f"run_dir: {run_dir}")
    _emit(f"  actual_joint_reward: {actual.get('joint_reward')}")
    _emit(f"  optimal_joint_reward: {optimal.get('joint_reward')}")
    _emit(f"  max_utility_upper_bound: {upper.get('max_utility')}")
    _emit(f"  optimality_gap: {comparison.get('optimality_gap')}")
    _emit(f"  achieved_over_optimal: {comparison.get('achieved_over_optimal')}")
    _emit(f"  normalized_regret: {comparison.get('normalized_regret')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute the exact optimal joint reward for MeetingScheduling runs."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-dir", type=Path)
    group.add_argument("--root", type=Path)
    parser.add_argument("--write-json", action="store_true")
    parser.add_argument(
        "--time-limit",
        type=float,
        default=None,
        help="Accepted for compatibility; ignored by the exhaustive brute-force solver.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    run_dirs = [Path(args.run_dir)] if args.run_dir else sorted(set(_iter_run_dirs(Path(args.root))))
    if not run_dirs:
        raise FileNotFoundError("No run directories found.")

    successes = 0
    failures: List[Tuple[Path, str]] = []
    for run_dir in tqdm(
        run_dirs,
        desc="Computing optimal summaries",
        unit="run",
        dynamic_ncols=True,
    ):
        try:
            payload = (
                write_optimal_summary(run_dir=run_dir, time_limit=args.time_limit)
                if args.write_json
                else build_optimal_summary_payload(run_dir=run_dir, time_limit=args.time_limit)
            )
            _print_payload(run_dir, payload)
            if args.write_json:
                _emit(f"  wrote: {run_dir / 'optimal_summary.json'}")
            successes += 1
        except Exception as exc:
            failures.append((run_dir, str(exc)))
            logger.warning("Failed for %s: %s", run_dir, exc)

    print(f"\nprocessed_runs: {len(run_dirs)}")
    print(f"successful: {successes}")
    print(f"failed: {len(failures)}")
    if failures:
        print("failures:")
        for run_dir, err in failures[:20]:
            print(f"  - {run_dir}: {err}")
        if len(failures) > 20:
            print(f"  ... ({len(failures) - 20} more)")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
