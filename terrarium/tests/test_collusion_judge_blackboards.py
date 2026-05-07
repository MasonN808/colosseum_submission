import unittest
import json
import math
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from experiments.collusion.judge_paths import derive_judge_output_tag, judge_dir_name
from experiments.collusion.plots.generate_regret_report import (
    _build_plot_header,
    _judge_mean_rating,
)
from experiments.collusion.plots.plot_judge_vs_coalition_advantage import (
    CONDITION_COLORS,
    MetricValue,
    PlacedLabel,
    ScatterPoint,
    TREND_LINE_ALPHA,
    _add_leader_lines,
    _add_trend_lines,
    _coalition_advantage_axis_label,
    _default_output_name,
    _delta_collusion_axis_label,
    _delta_judge_score_axis_label,
    _invert_delta_points,
    _legend_location,
    _model_family_key,
    _model_size_key,
    _numbered_model_labels,
    _parse_axis_limits,
    _orient_points,
    _parse_figsize,
    _read_points,
    _title_from_csv_metadata,
    _trend_line_groups,
    _x_axis_limits,
    _x_reference_value,
    _y_axis_limits,
)
from experiments.collusion.plots.plot_sweep import build_rows
from experiments.collusion.judge_blackboards import (
    _apply_judge_overrides,
    _format_blackboard_entry_transcript,
    _judge_model_banner_lines,
    resolve_judge_config,
)
from experiments.common.plotting.load_runs import LoadedRun


class CollusionJudgeConfigTests(unittest.TestCase):
    def test_format_blackboard_entry_transcript_reads_hospital_broadcast_actions(self):
        transcript, participants, has_communication = _format_blackboard_entry_transcript(
            {
                "blackboard_id": "1",
                "participants": ["AgentA", "AgentB"],
                "events": [
                    {
                        "agent": "SYSTEM",
                        "kind": "context",
                        "payload": {"message": "Private coordination board."},
                    },
                    {
                        "agent": "AgentA",
                        "kind": "action_executed",
                        "payload": {
                            "action_params": {
                                "broadcast_message": {
                                    "AgentA": {
                                        "message": "SURPLUS PPE at General_Hospital.",
                                        "rationale": "Unused inventory is available.",
                                    }
                                }
                            }
                        },
                    },
                ],
            }
        )

        self.assertEqual(participants, ["AgentA", "AgentB"])
        self.assertTrue(has_communication)
        self.assertIn("SYSTEM: Private coordination board.", transcript)
        self.assertIn("AgentA: SURPLUS PPE at General_Hospital.", transcript)

    def test_resolve_judge_config_reuses_matching_model_profile(self):
        experiment_cfg = {
            "llm_models": [
                {
                    "label": "gpt-5.4",
                    "llm": {
                        "provider": "foundry",
                        "foundry": {
                            "model": "gpt-5.4",
                            "base_model": "gpt-5.4",
                            "params": {
                                "max_tokens": 1500,
                                "temperature": 0.7,
                                "reasoning_effort": "minimal",
                                "verbosity": "low",
                                "tool_choice": "required",
                            },
                        },
                    },
                }
            ]
        }
        run_cfg = {
            "model_label": "gpt-5.4",
            "provider": "foundry",
            "model": "gpt-5.4",
        }

        judge_cfg = resolve_judge_config(
            run_cfg=run_cfg,
            experiment_cfg=experiment_cfg,
            model_llm_map={"gpt-5.4": experiment_cfg["llm_models"][0]["llm"]},
        )

        self.assertEqual(judge_cfg.provider, "foundry")
        self.assertEqual(judge_cfg.model, "gpt-5.4")
        self.assertEqual(judge_cfg.request_params["reasoning_effort"], "minimal")
        self.assertEqual(judge_cfg.request_params["verbosity"], "low")
        self.assertEqual(judge_cfg.request_params["max_output_tokens"], 256)
        self.assertEqual(judge_cfg.request_params["temperature"], 0.0)
        self.assertNotIn("tool_choice", judge_cfg.request_params)

    def test_resolve_judge_config_falls_back_to_run_summary(self):
        run_cfg = {
            "model_label": "fw-glm-5",
            "provider": "foundry",
            "model": "FW-GLM-5",
        }

        judge_cfg = resolve_judge_config(run_cfg=run_cfg)

        self.assertEqual(judge_cfg.provider, "foundry")
        self.assertEqual(judge_cfg.model, "FW-GLM-5")
        self.assertEqual(judge_cfg.request_params["max_output_tokens"], 256)
        self.assertEqual(judge_cfg.request_params["temperature"], 0.0)

    def test_resolve_judge_config_supports_provider_and_model_overrides(self):
        run_cfg = {
            "model_label": "openai-gpt-4o-mini",
            "provider": "openai",
            "model": "gpt-4o-mini",
        }

        judge_cfg = resolve_judge_config(
            run_cfg=run_cfg,
            judge_provider="foundry",
            judge_model="gpt-4.1-mini-2025-04-14",
            max_output_tokens=128,
            temperature=0.2,
        )

        self.assertEqual(judge_cfg.provider, "foundry")
        self.assertEqual(judge_cfg.model, "gpt-4.1-mini-2025-04-14")
        self.assertEqual(judge_cfg.request_params["max_output_tokens"], 128)
        self.assertEqual(judge_cfg.request_params["temperature"], 0.2)

    def test_resolve_judge_config_supports_foundry_env_var_overrides(self):
        experiment_cfg = {
            "llm_models": [
                {
                    "label": "claude-opus-4-6",
                    "llm": {
                        "provider": "foundry",
                        "foundry": {
                            "project_endpoint_env_var": "AI_FOUNDRY_RBR_EAST_US_2_PROJECT_ENDPOINT",
                            "api_key_env_var": "AI_FOUNDRY_RBR_EAST_US_2_API_KEY",
                            "model": "claude-opus-4-6",
                        },
                    },
                }
            ]
        }
        run_cfg = {
            "model_label": "claude-opus-4-6",
            "provider": "foundry",
            "model": "claude-opus-4-6",
        }

        judge_cfg = resolve_judge_config(
            run_cfg=run_cfg,
            experiment_cfg=experiment_cfg,
            model_llm_map={"claude-opus-4-6": experiment_cfg["llm_models"][0]["llm"]},
            judge_provider="foundry",
            judge_model="gpt-5.4",
            judge_project_endpoint_env_var="TEMP_FOUNDRY_PROJECT_ENDPOINT",
            judge_api_key_env_var="TEMP_FOUNDRY_API_KEY",
            judge_auth_mode="api_key",
        )

        self.assertEqual(judge_cfg.provider, "foundry")
        self.assertEqual(judge_cfg.model, "gpt-5.4")

    def test_judge_model_override_drops_inherited_foundry_api_style(self):
        llm_cfg = _apply_judge_overrides(
            base_llm_cfg={
                "provider": "foundry",
                "foundry": {
                    "project_endpoint_env_var": "AI_FOUNDRY_PROJECT_ENDPOINT",
                    "api_key_env_var": "AI_FOUNDRY_API_KEY",
                    "api_style": "chat_completions",
                    "base_model": "grok-4-20-reasoning",
                    "model": "grok-4-20-reasoning",
                    "params": {"max_tokens": 1500},
                },
            },
            run_cfg={
                "model_label": "grok-4-20-reasoning",
                "provider": "foundry",
                "model": "grok-4-20-reasoning",
            },
            judge_provider="foundry",
            judge_model="claude-opus-4-6",
        )

        self.assertEqual(llm_cfg["foundry"]["model"], "claude-opus-4-6")
        self.assertNotIn("api_style", llm_cfg["foundry"])
        self.assertNotIn("base_model", llm_cfg["foundry"])
        self.assertEqual(llm_cfg["foundry"]["params"], {"max_tokens": 1500})

    def test_judge_model_banner_lines_report_resolved_model_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "runs" / "gpt-5.4" / "complete_n9_c4" / "run-a"
            run_dir.mkdir(parents=True)
            (run_dir / "run_config.json").write_text(
                json.dumps(
                    {
                        "model_label": "gpt-5.4",
                        "provider": "foundry",
                        "model": "gpt-5.4",
                    }
                ),
                encoding="utf-8",
            )

            lines = _judge_model_banner_lines(run_dirs=[run_dir])

        self.assertEqual(lines, ["Judge model: gpt-5.4"])

    def test_banner_and_helper_support_tagged_judge_outputs(self):
        tag = derive_judge_output_tag(
            judge_provider="foundry",
            judge_model="gpt-4.1-mini-2025-04-14",
        )
        self.assertEqual(
            tag,
            "foundry__gpt-4.1-mini-2025-04-14",
        )
        self.assertEqual(
            judge_dir_name(tag),
            "judge_secret_blackboard__foundry__gpt-4.1-mini-2025-04-14",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "runs" / "gpt-5.4" / "complete_n9_c4" / "run-a"
            run_dir.mkdir(parents=True)
            (run_dir / "run_config.json").write_text(
                json.dumps(
                    {
                        "model_label": "gpt-5.4",
                        "provider": "foundry",
                        "model": "gpt-5.4",
                    }
                ),
                encoding="utf-8",
            )

            lines = _judge_model_banner_lines(run_dirs=[run_dir], judge_output_tag=tag)

        self.assertEqual(
            lines,
            [
                "Judge output dir: judge_secret_blackboard__foundry__gpt-4.1-mini-2025-04-14",
                "Judge model: gpt-5.4",
            ],
        )

    def test_plot_readers_can_load_tagged_judge_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "runs" / "gpt-5.4" / "complete_n9_c4" / "run-a"
            run_dir.mkdir(parents=True)
            judge_dir = (
                run_dir.parent.parent
                / judge_dir_name("foundry__gpt41mini")
                / run_dir.parent.name
            )
            judge_dir.mkdir(parents=True)
            (judge_dir / f"{run_dir.name}.json").write_text(
                json.dumps(
                    {
                        "judgements": {
                            "simple": {"rating": 1},
                            "medium": {"rating": 3},
                            "complex": {"rating": 5},
                        }
                    }
                ),
                encoding="utf-8",
            )

            loaded_run = LoadedRun(
                run_dir=run_dir,
                run_config={
                    "run_id": "run-a",
                    "seed": 1,
                    "replica_index": 0,
                    "topology": "complete",
                    "colluder_count": 4,
                    "secret_channel_enabled": True,
                    "prompt_variant": "simple",
                },
                final_summary={"joint_reward": 10.0, "joint_reward_ratio": 0.5},
                metrics={"coalition_mean_regret": 0.1, "noncoalition_mean_regret": 0.3},
                judge_results=None,
                survey_responses=None,
                tool_events=None,
                agent_turns=None,
                blackboards=None,
            )

            rows = build_rows([loaded_run], judge_output_tag="foundry__gpt41mini")
            rating = _judge_mean_rating(
                model_dir=run_dir.parent.parent,
                sweep_name=run_dir.parent.name,
                run_name=run_dir.name,
                judge_output_tag="foundry__gpt41mini",
            )

        self.assertEqual(rows[0]["judge_mean_rating"], 3.0)
        self.assertEqual(rating, 3.0)

    def test_regret_report_header_includes_environment_and_judge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config.json").write_text(
                json.dumps({"environment": {"name": "JiraTicketEnvironment"}}),
                encoding="utf-8",
            )
            judge_dir = (
                root
                / "runs"
                / "model-a"
                / judge_dir_name("foundry__gpt-5.4-nano")
                / "complete_n6_c2"
            )
            judge_dir.mkdir(parents=True)
            (judge_dir / "run-a.json").write_text(
                json.dumps(
                    {
                        "judge_config": {
                            "provider": "foundry",
                            "model": "gpt-5.4-nano",
                        }
                    }
                ),
                encoding="utf-8",
            )

            header = _build_plot_header(
                root=root,
                sweep_name="complete_n6_c2",
                judge_output_tag="foundry__gpt-5.4-nano",
            )

        self.assertEqual(
            header,
            "Environment=Jira | Judge=GPT-5.4-nano",
        )

    def test_scatter_title_uses_regret_report_csv_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "regret_report__normalized_regret__coalition_gap__judge__data.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "model_label,condition,metric_key,mean,plot_header",
                        "gpt-5.4,control,judge_mean_rating,3.0,Environment: JiraTicketEnvironment | Judge: foundry / gpt-5.4-nano",
                    ]
                ),
                encoding="utf-8",
            )

            title = _title_from_csv_metadata(csv_path)

        self.assertEqual(
            title,
            r"$\mathbf{Jira}$ with $\mathbf{GPT{-}5.4{-}nano}$ Judge",
        )

    def test_scatter_figsize_parser_accepts_width_height(self):
        self.assertEqual(_parse_figsize("7x12"), (7.0, 12.0))
        self.assertEqual(_parse_figsize("6.5,4"), (6.5, 4.0))

    def test_scatter_axis_limit_parser_accepts_min_max(self):
        self.assertEqual(
            _parse_axis_limits("-0.5,3.5", option_name="--x-limits"),
            (-0.5, 3.5),
        )
        self.assertEqual(
            _parse_axis_limits("-0.2:0.2", option_name="--y-limits"),
            (-0.2, 0.2),
        )

    def test_scatter_model_size_key_classifies_current_plot_models(self):
        cases = {
            "gpt-5.4": "big",
            "gpt-5.4-mini": "small",
            "gpt-5.4-nano": "small",
            "gemini-3.1-flash-lite": "small",
            "claude-haiku-4-5": "small",
            "grok-4-1-fast-reasoning": "small",
            "grok-4-20-reasoning": "big",
            "fw-minimax-m2.5": "big",
        }

        for model_label, expected in cases.items():
            with self.subTest(model_label=model_label):
                self.assertEqual(_model_size_key(model_label), expected)

    def test_scatter_model_family_key_infers_logo_families(self):
        cases = {
            "gpt-5.4": "openai",
            "anthropic-claude-sonnet-4-5": "anthropic",
            "opus-4.6": "anthropic",
            "sonnet-4.5": "anthropic",
            "gemini-3.1-flash-lite": "gemini",
            "deepseek-v3.2": "deepseek",
            "fw-glm-5": "glm",
            "fw-minimax-m2.5": "minimax",
            "grok-4-20-reasoning": "grok",
            "together-kimik2-thinking": "moonshot",
        }

        for model_label, expected in cases.items():
            with self.subTest(model_label=model_label):
                self.assertEqual(_model_family_key(model_label), expected)

    def test_scatter_numbered_model_labels_sort_by_family(self):
        points = [
            ScatterPoint(
                model_label="fw-glm-5",
                model_pretty="GLM-5",
                condition="control",
                x=MetricValue(mean=0.0, sem=None),
                y=MetricValue(mean=0.0, sem=None),
            ),
            ScatterPoint(
                model_label="gpt-5.4",
                model_pretty="GPT-5.4",
                condition="control",
                x=MetricValue(mean=0.0, sem=None),
                y=MetricValue(mean=0.0, sem=None),
            ),
            ScatterPoint(
                model_label="gemini-2.5-flash",
                model_pretty="Gemini-2.5-Flash",
                condition="simple",
                x=MetricValue(mean=0.0, sem=None),
                y=MetricValue(mean=0.0, sem=None),
            ),
            ScatterPoint(
                model_label="gpt-5.4",
                model_pretty="GPT-5.4",
                condition="simple",
                x=MetricValue(mean=0.0, sem=None),
                y=MetricValue(mean=0.0, sem=None),
            ),
        ]

        labels, sorted_models = _numbered_model_labels(points)

        self.assertEqual(
            [point.model_label for point in sorted_models],
            ["gpt-5.4", "gemini-2.5-flash", "fw-glm-5"],
        )
        self.assertEqual(labels, {
            "gpt-5.4": "1",
            "gemini-2.5-flash": "2",
            "fw-glm-5": "3",
        })

    def test_scatter_default_output_name_includes_point_style_suffix(self):
        self.assertEqual(
            _default_output_name("judge_mean_rating", point_style="condition"),
            "judge_vs_coalition_advantage_scatter.png",
        )
        self.assertEqual(
            _default_output_name("judge_mean_rating", point_style="model-size"),
            "judge_vs_coalition_advantage_scatter__model_size.png",
        )
        self.assertEqual(
            _default_output_name("judge_mean_rating", point_style="condition-size"),
            "judge_vs_coalition_advantage_scatter__condition_model_size.png",
        )
        self.assertEqual(
            _default_output_name(
                "judge_mean_rating",
                control_minus_condition_x=True,
                point_style="model-family",
            ),
            "judge_vs_control_minus_condition_coalition_advantage_scatter__model_family.png",
        )
        self.assertEqual(
            _default_output_name(
                "judge_mean_rating",
                control_minus_condition_x=True,
                judge_x_axis=True,
                invert_delta_collusion=True,
            ),
            "inverted_delta_collusion_coalition_advantage_vs_judge_scatter.png",
        )
        self.assertEqual(
            _default_output_name(
                "judge_mean_rating",
                control_minus_condition_x=True,
                judge_x_axis=True,
                invert_delta_collusion=True,
                delta_collusion_metric="overall_regret",
                point_style="condition-size",
            ),
            "inverted_delta_collusion_overall_regret_vs_judge_scatter__condition_model_size.png",
        )
        self.assertEqual(
            _default_output_name(
                "judge_mean_rating",
                control_minus_condition_x=True,
                judge_x_axis=True,
                invert_delta_collusion=True,
                delta_judge_score=True,
            ),
            "inverted_delta_collusion_coalition_advantage_vs_delta_judge_scatter.png",
        )

    def test_scatter_delta_collusion_axis_label_describes_direction(self):
        self.assertEqual(
            _delta_collusion_axis_label(invert_delta_collusion=False),
            r"More $\leftarrow$ $\Delta$-Advantage $\rightarrow$ Less",
        )
        self.assertEqual(
            _delta_collusion_axis_label(invert_delta_collusion=True),
            r"Less $\leftarrow$ $\Delta$-Advantage $\rightarrow$ More",
        )
        self.assertEqual(
            _delta_collusion_axis_label(
                invert_delta_collusion=True,
                delta_collusion_metric="overall_regret",
            ),
            r"More $\leftarrow$ $\Delta$-Regret $\rightarrow$ Less",
        )
        self.assertEqual(
            _delta_collusion_axis_label(
                invert_delta_collusion=False,
                delta_collusion_metric="overall_regret",
            ),
            r"More $\leftarrow$ $\Delta$-Regret $\rightarrow$ Less",
        )
        self.assertEqual(
            _delta_collusion_axis_label(
                invert_delta_collusion=True,
                metric_label="Coalition Advantage",
            ),
            r"Less $\leftarrow$ $\Delta$-Advantage (Coalition Advantage) $\rightarrow$ More",
        )

    def test_scatter_coalition_advantage_axis_label_describes_direction(self):
        self.assertEqual(
            _coalition_advantage_axis_label(),
            r"Less $\leftarrow$ Coalition Advantage (0-1) $\rightarrow$ More",
        )

    def test_scatter_delta_judge_score_axis_label_describes_direction(self):
        self.assertEqual(
            _delta_judge_score_axis_label(),
            r"Less $\leftarrow$ $\Delta$-Judge-Score $\rightarrow$ More",
        )

    def test_scatter_delta_plots_default_legend_to_upper_right(self):
        self.assertEqual(
            _legend_location(None, control_minus_condition_x=True),
            "upper right",
        )
        self.assertEqual(
            _legend_location(None, control_minus_condition_x=False),
            "upper left",
        )
        self.assertEqual(
            _legend_location("lower left", control_minus_condition_x=True),
            "lower left",
        )

    def test_scatter_trend_line_groups_follow_active_point_style(self):
        points = [
            ScatterPoint(
                model_label="gpt-5.4",
                model_pretty="GPT-5.4",
                condition="control",
                x=MetricValue(mean=1.0, sem=None),
                y=MetricValue(mean=0.1, sem=None),
            ),
            ScatterPoint(
                model_label="gpt-5.4-mini",
                model_pretty="GPT-5.4-Mini",
                condition="control",
                x=MetricValue(mean=2.0, sem=None),
                y=MetricValue(mean=0.2, sem=None),
            ),
            ScatterPoint(
                model_label="grok-4-20-reasoning",
                model_pretty="Grok-4-20-Reasoning",
                condition="simple",
                x=MetricValue(mean=3.0, sem=None),
                y=MetricValue(mean=0.3, sem=None),
            ),
            ScatterPoint(
                model_label="grok-4-1-fast-reasoning",
                model_pretty="Grok-4-1-fast-Reasoning",
                condition="simple",
                x=MetricValue(mean=4.0, sem=None),
                y=MetricValue(mean=0.4, sem=None),
            ),
        ]

        condition_groups = {
            group.key: {point.model_label for point in group.points}
            for group in _trend_line_groups(points, point_style="condition")
        }
        condition_size_groups = {
            group.key: {point.model_label for point in group.points}
            for group in _trend_line_groups(points, point_style="condition-model-size")
        }
        size_groups = {
            group.key: {point.model_label for point in group.points}
            for group in _trend_line_groups(points, point_style="model-size")
        }
        family_groups = {
            group.key: {point.model_label for point in group.points}
            for group in _trend_line_groups(points, point_style="model-family")
        }

        self.assertEqual(
            condition_groups,
            {
                "control": {"gpt-5.4", "gpt-5.4-mini"},
                "simple": {"grok-4-20-reasoning", "grok-4-1-fast-reasoning"},
            },
        )
        self.assertEqual(condition_size_groups, condition_groups)
        self.assertEqual(
            size_groups,
            {
                "big": {"gpt-5.4", "grok-4-20-reasoning"},
                "small": {"gpt-5.4-mini", "grok-4-1-fast-reasoning"},
            },
        )
        self.assertEqual(
            family_groups,
            {
                "openai": {"gpt-5.4", "gpt-5.4-mini"},
                "grok": {"grok-4-20-reasoning", "grok-4-1-fast-reasoning"},
            },
        )

    def test_scatter_trend_lines_use_darker_prominent_style(self):
        points = [
            ScatterPoint(
                model_label="model-a",
                model_pretty="Model A",
                condition="control",
                x=MetricValue(mean=1.0, sem=None),
                y=MetricValue(mean=0.1, sem=None),
            ),
            ScatterPoint(
                model_label="model-b",
                model_pretty="Model B",
                condition="control",
                x=MetricValue(mean=2.0, sem=None),
                y=MetricValue(mean=0.2, sem=None),
            ),
        ]

        fig, ax = plt.subplots()
        try:
            _add_trend_lines(ax=ax, points=points, point_style="condition")

            self.assertEqual(len(ax.lines), 1)
            line = ax.lines[0]
            self.assertEqual(line.get_alpha(), TREND_LINE_ALPHA)
            self.assertLess(
                sum(matplotlib.colors.to_rgb(line.get_color())),
                sum(matplotlib.colors.to_rgb(CONDITION_COLORS["control"])),
            )
        finally:
            plt.close(fig)

    def test_scatter_control_minus_condition_x_subtracts_per_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "scatter_data.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "model_label,model_label_pretty,condition,metric_key,mean,sem",
                        "model-a,Model A,baseline,normalized_coalition_regret_gap,0.70,0.03",
                        "model-a,Model A,control,normalized_coalition_regret_gap,0.40,0.04",
                        "model-a,Model A,control,judge_mean_rating,1.20,0.10",
                        "model-a,Model A,simple,normalized_coalition_regret_gap,0.90,0.05",
                        "model-a,Model A,simple,judge_mean_rating,2.30,0.20",
                        "model-b,Model B,baseline,normalized_coalition_regret_gap,0.20,0.01",
                        "model-b,Model B,control,normalized_coalition_regret_gap,0.10,0.02",
                        "model-b,Model B,control,judge_mean_rating,3.00,0.30",
                        "model-b,Model B,simple,normalized_coalition_regret_gap,0.50,0.03",
                        "model-b,Model B,simple,judge_mean_rating,4.00,0.40",
                    ]
                ),
                encoding="utf-8",
            )

            points = _read_points(
                csv_path,
                y_metric="judge_mean_rating",
                control_minus_condition_x=True,
            )

        by_key = {(p.model_label, p.condition): p for p in points}
        self.assertEqual(set(by_key), {
            ("model-a", "control"),
            ("model-a", "simple"),
            ("model-b", "control"),
            ("model-b", "simple"),
        })
        self.assertAlmostEqual(by_key[("model-a", "control")].x.mean, 0.30)
        self.assertAlmostEqual(by_key[("model-a", "simple")].x.mean, -0.20)
        self.assertAlmostEqual(by_key[("model-b", "control")].x.mean, 0.10)
        self.assertAlmostEqual(by_key[("model-b", "simple")].x.mean, -0.30)
        self.assertEqual(by_key[("model-a", "control")].y.mean, 1.20)
        self.assertEqual(by_key[("model-a", "simple")].y.mean, 2.30)
        self.assertAlmostEqual(
            by_key[("model-a", "control")].x.sem,
            math.sqrt((0.03**2) + (0.04**2)),
        )

    def test_scatter_delta_judge_score_subtracts_baseline_per_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "scatter_data.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "model_label,model_label_pretty,condition,metric_key,mean,sem",
                        "model-a,Model A,baseline,normalized_coalition_regret_gap,0.70,0.03",
                        "model-a,Model A,baseline,judge_mean_rating,1.00,0.06",
                        "model-a,Model A,control,normalized_coalition_regret_gap,0.40,0.04",
                        "model-a,Model A,control,judge_mean_rating,1.20,0.10",
                        "model-a,Model A,simple,normalized_coalition_regret_gap,0.90,0.05",
                        "model-a,Model A,simple,judge_mean_rating,0.60,0.08",
                        "model-b,Model B,baseline,normalized_coalition_regret_gap,0.20,0.01",
                        "model-b,Model B,baseline,judge_mean_rating,2.40,0.20",
                        "model-b,Model B,control,normalized_coalition_regret_gap,0.10,0.02",
                        "model-b,Model B,control,judge_mean_rating,3.00,0.30",
                    ]
                ),
                encoding="utf-8",
            )

            points = _read_points(
                csv_path,
                y_metric="judge_mean_rating",
                control_minus_condition_x=True,
                delta_judge_score=True,
            )

        by_key = {(p.model_label, p.condition): p for p in points}
        self.assertEqual(
            set(by_key),
            {
                ("model-a", "control"),
                ("model-a", "simple"),
                ("model-b", "control"),
            },
        )
        self.assertAlmostEqual(by_key[("model-a", "control")].y.mean, 0.20)
        self.assertAlmostEqual(by_key[("model-a", "simple")].y.mean, -0.40)
        self.assertAlmostEqual(by_key[("model-b", "control")].y.mean, 0.60)
        self.assertAlmostEqual(
            by_key[("model-a", "control")].y.sem,
            math.sqrt((0.10**2) + (0.06**2)),
        )
        self.assertAlmostEqual(by_key[("model-a", "control")].x.mean, 0.30)

    def test_scatter_control_minus_condition_x_can_use_overall_regret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "scatter_data.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "model_label,model_label_pretty,condition,metric_key,mean,sem",
                        "model-a,Model A,baseline,normalized_regret,0.20,0.03",
                        "model-a,Model A,control,normalized_regret,0.35,0.04",
                        "model-a,Model A,control,judge_mean_rating,1.20,0.10",
                        "model-a,Model A,simple,normalized_regret,0.10,0.05",
                        "model-a,Model A,simple,judge_mean_rating,2.30,0.20",
                    ]
                ),
                encoding="utf-8",
            )

            points = _read_points(
                csv_path,
                y_metric="judge_mean_rating",
                control_minus_condition_x=True,
                delta_collusion_metric="overall_regret",
            )

        by_key = {(p.model_label, p.condition): p for p in points}
        self.assertEqual(set(by_key), {("model-a", "control"), ("model-a", "simple")})
        self.assertAlmostEqual(by_key[("model-a", "control")].x.mean, -0.15)
        self.assertAlmostEqual(by_key[("model-a", "simple")].x.mean, 0.10)
        self.assertAlmostEqual(
            by_key[("model-a", "control")].x.sem,
            math.sqrt((0.03**2) + (0.04**2)),
        )

    def test_scatter_control_minus_condition_x_skips_incomplete_pairs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "scatter_data.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "model_label,model_label_pretty,condition,metric_key,mean,sem",
                        "missing-baseline,Missing,control,normalized_coalition_regret_gap,0.40,",
                        "missing-baseline,Missing,control,judge_mean_rating,1.20,",
                        "missing-y,Missing Y,baseline,normalized_coalition_regret_gap,0.70,",
                        "missing-y,Missing Y,control,normalized_coalition_regret_gap,0.40,",
                        "complete,Complete,baseline,normalized_coalition_regret_gap,0.70,",
                        "complete,Complete,control,normalized_coalition_regret_gap,0.40,",
                        "complete,Complete,control,judge_mean_rating,1.20,",
                    ]
                ),
                encoding="utf-8",
            )

            points = _read_points(
                csv_path,
                y_metric="judge_mean_rating",
                control_minus_condition_x=True,
            )

        self.assertEqual(
            [(p.model_label, p.condition) for p in points],
            [("complete", "control")],
        )
        self.assertAlmostEqual(points[0].x.mean, 0.30)

    def test_scatter_axes_label_known_ranges_without_forcing_full_x_view(self):
        points = [
            ScatterPoint(
                model_label="gpt-5.4",
                model_pretty="GPT-5.4",
                condition="control",
                x=MetricValue(mean=0.48, sem=None),
                y=MetricValue(mean=3.0, sem=None),
            )
        ]

        xmin, xmax = _x_axis_limits(points, x_step_limits=0.05)
        self.assertAlmostEqual(xmin, 0.445)
        self.assertAlmostEqual(xmax, 0.505)
        self.assertEqual(
            _y_axis_limits(points, y_metric="judge_mean_rating"),
            (0.0, 5.0),
        )
        self.assertEqual(
            _y_axis_limits(points, y_metric="normalized_regret"),
            (0.0, 1.0),
        )
        ymin, ymax = _y_axis_limits(
            points,
            y_metric="judge_mean_rating",
            y_step_limits=0.5,
        )
        self.assertAlmostEqual(ymin, 2.45)
        self.assertAlmostEqual(ymax, 3.55)

        delta_ymin, delta_ymax = _y_axis_limits(
            [
                ScatterPoint(
                    model_label="model-a",
                    model_pretty="Model A",
                    condition="control",
                    x=MetricValue(mean=0.0, sem=None),
                    y=MetricValue(mean=-0.7, sem=None),
                ),
                ScatterPoint(
                    model_label="model-b",
                    model_pretty="Model B",
                    condition="simple",
                    x=MetricValue(mean=0.0, sem=None),
                    y=MetricValue(mean=1.2, sem=None),
                ),
            ],
            y_metric="judge_mean_rating",
            y_step_limits=0.5,
            delta_judge_score=True,
            symmetric_delta_axis=True,
        )
        self.assertAlmostEqual(delta_ymin, -1.55)
        self.assertAlmostEqual(delta_ymax, 1.55)

    def test_scatter_delta_x_axis_includes_zero_and_negative_values(self):
        points = [
            ScatterPoint(
                model_label="model-a",
                model_pretty="Model A",
                condition="control",
                x=MetricValue(mean=-0.2, sem=None),
                y=MetricValue(mean=1.0, sem=None),
            ),
            ScatterPoint(
                model_label="model-b",
                model_pretty="Model B",
                condition="simple",
                x=MetricValue(mean=0.1, sem=None),
                y=MetricValue(mean=2.0, sem=None),
            ),
        ]

        xmin, xmax = _x_axis_limits(
            points,
            x_step_limits=None,
            control_minus_condition_x=True,
        )

        self.assertLess(xmin, -0.2)
        self.assertGreater(xmax, 0.1)
        self.assertLess(xmax - xmin, 0.35)
        sym_min, sym_max = _x_axis_limits(
            points,
            x_step_limits=0.075,
            control_minus_condition_x=True,
            symmetric_delta_axis=True,
        )
        self.assertAlmostEqual(sym_min, -0.2325)
        self.assertAlmostEqual(sym_max, 0.2325)
        self.assertEqual(_x_reference_value(control_minus_condition_x=True), 0.0)
        self.assertEqual(_x_reference_value(control_minus_condition_x=False), 0.5)

    def test_scatter_invert_delta_points_negates_delta_only(self):
        points = [
            ScatterPoint(
                model_label="model-a",
                model_pretty="Model A",
                condition="control",
                x=MetricValue(mean=-0.2, sem=0.03),
                y=MetricValue(mean=1.0, sem=0.10),
            )
        ]

        inverted = _invert_delta_points(points, invert_delta_collusion=True)

        self.assertEqual(inverted[0].x.mean, 0.2)
        self.assertEqual(inverted[0].x.sem, 0.03)
        self.assertEqual(inverted[0].y, points[0].y)
        regret_delta = _invert_delta_points(
            points,
            invert_delta_collusion=True,
            delta_collusion_metric="overall_regret",
        )
        self.assertEqual(regret_delta[0].x.mean, -0.2)
        self.assertEqual(regret_delta[0].x.sem, 0.03)
        self.assertEqual(regret_delta[0].y, points[0].y)

    def test_scatter_judge_x_axis_orientation_swaps_metrics(self):
        points = [
            ScatterPoint(
                model_label="model-a",
                model_pretty="Model A",
                condition="control",
                x=MetricValue(mean=-0.2, sem=0.03),
                y=MetricValue(mean=1.0, sem=0.10),
            )
        ]

        oriented = _orient_points(points, judge_x_axis=True)

        self.assertEqual(oriented[0].x, points[0].y)
        self.assertEqual(oriented[0].y, points[0].x)
        self.assertEqual(oriented[0].condition, "control")

    def test_scatter_leader_lines_are_added_for_nearby_labels(self):
        fig, ax = plt.subplots()
        try:
            text = ax.text(0.52, 0.53, "GPT-5.4", fontsize=9)
            labels = [PlacedLabel(text=text, anchor=(0.5, 0.5))]
            before = len(ax.texts)

            _add_leader_lines(fig=fig, ax=ax, labels=labels)

            self.assertGreater(len(ax.texts), before)
        finally:
            plt.close(fig)


if __name__ == "__main__":
    unittest.main()
