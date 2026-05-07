# Commands

Run each command as a single line.

## Jira resume config

Rebuild the Jira resume config with:
- `runs_per_seed = 2`
- `max_concurrent_runs = 3`
- no Claude `api_style` override

```bash
jq '.experiment.runs_per_seed = 2 | .experiment.max_concurrent_runs = 3 | (.llm_models[] | select(.label=="claude-opus-4-6") | .llm.foundry |= del(.api_style))' /home/smahmud/Documents/Terrarium/experiments/collusion/outputs/collusion_regret_complete_n6_c2/20260416-191730/config.json > /tmp/collusion_regret_resume_retry.json
```

Verify the temp config exists:

```bash
ls -l /tmp/collusion_regret_resume_retry.json
```

Preview what resume will do:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.resume --root /home/smahmud/Documents/Terrarium/experiments/collusion/outputs/collusion_regret_complete_n6_c2/20260416-191730 --config /tmp/collusion_regret_resume_retry.json --max-concurrent-runs 3 --dry-run
```

Resume the Jira run:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.resume --root /home/smahmud/Documents/Terrarium/experiments/collusion/outputs/collusion_regret_complete_n6_c2/20260416-191730 --config /tmp/collusion_regret_resume_retry.json --max-concurrent-runs 3
```

## Jira post-processing

Re-run the judge:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.judge_blackboards --root /home/smahmud/Documents/Terrarium/experiments/collusion/outputs/collusion_regret_complete_n6_c2/20260416-191730 --max-concurrent 16
```

Regenerate the Jira regret report:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.plots.generate_regret_report --root /home/smahmud/Documents/Terrarium/experiments/collusion/outputs/collusion_regret_complete_n6_c2/20260416-191730 --sweep-name complete_n6_c2 --compute-optimal
```

## MeetingScheduling run

Run the MeetingScheduling complete `n=6`, `c=2` collusion sweep:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.run --config /home/smahmud/Documents/Terrarium/experiments/collusion/configs collusion_meeting_scheduling_complete_n6_c2_regret_models_foundry_additions.yaml
```

## MeetingScheduling post-processing

Set the output root from the completed MeetingScheduling run:

```bash
ROOT=/home/smahmud/Documents/Terrarium/experiments/collusion/outputs/collusion_meeting_scheduling_complete_n6_c2/20260421-214700
```

## MeetingScheduling resume with more seed diversity

Set the output root from the completed MeetingScheduling run:

```bash
ROOT=/home/smahmud/Documents/Terrarium/experiments/collusion/outputs/collusion_meeting_scheduling_complete_n6_c2/20260421-214700
```

Rebuild the MeetingScheduling resume config with seeds `1..5` and `runs_per_seed = 3`:

```bash
jq '.experiment.seeds = [1,2,3,4,5] | .experiment.runs_per_seed = 3 | .experiment.max_concurrent_runs = 6' "$ROOT/config.json" > /tmp/collusion_meeting_scheduling_5seeds_3runs.json
```

Verify the temp config exists:

```bash
ls -l /tmp/collusion_meeting_scheduling_5seeds_3runs.json
```

Preview what resume will run:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.resume --root "$ROOT" --config /tmp/collusion_meeting_scheduling_5seeds_3runs.json --max-concurrent-runs 6 --dry-run
```

Resume the MeetingScheduling run:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.resume --root "$ROOT" --config /tmp/collusion_meeting_scheduling_5seeds_3runs.json --max-concurrent-runs 6
```

After resume finishes, re-run the MeetingScheduling post-processing commands below so judge scores, optimal summaries, and the plot include the new seeds.

Run the collusion judge:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.judge_blackboards --root "$ROOT" --max-concurrent 16
```

Backfill `optimal_summary.json` files for MeetingScheduling. This reconstructs the per-run max utility from `joint_reward / joint_reward_ratio` so the regret plot has the same metadata shape as Jira runs:

```bash
env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -c 'import json, pathlib; root=pathlib.Path("'"$ROOT"'"); [((p.parent/"optimal_summary.json").write_text(json.dumps({"optimal":{"joint_reward": float(fs["joint_reward"]) / float(fs["joint_reward_ratio"])}}, indent=2)+"\n"), print(p.parent/"optimal_summary.json")) for p in root.glob("runs/*/complete_n6_c2/*/final_summary.json") for fs in [json.loads(p.read_text())] if fs.get("joint_reward") is not None and fs.get("joint_reward_ratio") not in (None, 0)]'
```

Generate the MeetingScheduling regret report plot:

```bash
MPLCONFIGDIR=/tmp/matplotlib-cache env PYTHONPATH=.:build/lib /home/smahmud/Documents/Terrarium/.venv/bin/python -m experiments.collusion.plots.generate_regret_report --root "$ROOT" --sweep-name complete_n6_c2 --topology complete --num-agents 6 --colluder-count 2
```

The plot is written to:

```text
/home/smahmud/Documents/Terrarium/experiments/collusion/plots_outputs/collusion_meeting_scheduling_complete_n6_c2/20260421-214700/regret_report/complete_n6_c2/plots/regret_report__normalized_regret__coalition_gap__judge.png
```
