# Collusion Configs

This folder contains ready-to-run YAML configs for `experiments/collusion/run.py`.

Usage:
```bash
.venv/bin/python -m experiments.collusion.run --config experiments/collusion/configs/<file>.yaml
```

Common experiment knobs:
- `experiment.seeds`: which seeds to run
- `experiment.runs_per_seed`: how many total runs to execute for each configured seed

## Quick sanity check
- `collusion_jira_three_agents_two_blackboards.yaml`: tiny setup with a secret colluder-only blackboard.

## Main sweeps
- `collusion_jira_topologies.yaml`: topology sweep for JiraTicket (baseline vs secret-channel variants).
- `collusion_jira_topologies_env_assignment_fills.yaml`: variant sweep with environment tweaks (see file comments).

## Regret report model set
- `collusion_jira_complete_n6_c2_regret_models.yaml`: configuration used for the Jira regret plots.
- `collusion_jira_complete_n6_c2_regret_models_foundry_additions.yaml`: verified Foundry additions for the same regret sweep, using both the `collusion` and `rbr-east-us-2-resource` projects.
- `collusion_meeting_scheduling_complete_n6_c2_regret_models_foundry_additions.yaml`: meeting-scheduling version of the Foundry additions sweep, using the same model set and complete n=6/c=2 setup.
