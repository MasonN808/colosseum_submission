from __future__ import annotations

"""Backward-compatible wrapper for the renamed regret report module."""

from experiments.collusion.plots.generate_regret_report import *  # noqa: F401,F403
from experiments.collusion.plots.generate_regret_report import (
    RunRow,
    _compute_and_write_optimal_summary,
    _load_run_row,
    _seed_means,
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())
