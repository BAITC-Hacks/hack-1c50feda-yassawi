"""External score separation, ordering and frozen-baseline reproducibility."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import pandas as pd

import split_eval


class SplitEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot_before = split_eval.verify_snapshot()
        baseline = split_eval.load_agent(split_eval.SNAPSHOT / "agent.py")
        cls.events = []
        original_factory = split_eval.make_mock_env
        original_scorer = split_eval.score_campaigns

        class TrackedAgent(baseline):
            def act(self, env):
                cls.events.append("act_start")
                result = super().act(env)
                cls.events.append("act_end")
                return result

        def tracked_factory(**kwargs):
            env, internals = original_factory(**kwargs)

            def pilots():
                cls.events.append("pilot_ids")
                return internals.executed_pilot_campaigns()

            return env, types.SimpleNamespace(executed_pilot_campaigns=pilots)

        def tracked_scorer(*args, **kwargs):
            cls.events.append("score")
            return original_scorer(*args, **kwargs)

        with patch.object(split_eval, "make_mock_env", side_effect=tracked_factory), \
             patch.object(split_eval, "score_campaigns", side_effect=tracked_scorer):
            cls.row = split_eval.evaluate_agent(TrackedAgent(), 42)
        cls.snapshot_after = split_eval.verify_snapshot()

    def test_baseline_seed42_reproduces_independent_audit(self):
        expected = {"net_pilots_only": 18123.313454,
                    "net_combined": 13741.730319,
                    "final_incremental_net": -4381.583135,
                    "net_final_standalone": -7081.568045,
                    "forecast_final_mean_net": 1328482.795148}
        for key, value in expected.items():
            with self.subTest(metric=key):
                self.assertAlmostEqual(self.row[key], value, places=5)
        self.assertTrue(self.row["validation"]["all_limits_ok"], self.row["validation"])
        self.assertEqual(self.row["validation"]["sanitizer_dropped"], 0)
        self.assertEqual(self.row["validation"]["scorer_trimmed"], 0)
        self.assertEqual(self.row["unique_hypotheses_with_channel"], 8)
        self.assertEqual(self.row["unique_hypotheses_without_channel"], 6)

    def test_exactly_one_act_precedes_internal_ids_and_all_scores(self):
        self.assertEqual(self.events, ["act_start", "act_end", "pilot_ids", "score", "score", "score"])
        self.assertIsNone(self.row["agent_error"])

    def test_snapshot_unchanged_and_no_import_cache_written(self):
        self.assertEqual(self.snapshot_before, self.snapshot_after)
        self.assertFalse(any(split_eval.SNAPSHOT.rglob("*.pyc")))
        self.assertFalse(any(split_eval.SNAPSHOT.rglob("__pycache__")))

    def test_overlapping_final_increment_is_not_standalone_score(self):
        profile = pd.DataFrame({"ID_NUMBER": [0, 1], "current_tariff": ["a", "a"],
                                "arpu_segment": ["MID", "MID"], "predicted_arpu": [100., 100.]})
        tariffs = pd.DataFrame({"tariff_plan_code": ["a", "b"], "price_tariff": [100., 100.]})
        model = pd.DataFrame({"tariff_plan_code_from": ["a"], "tariff_plan_code_to": ["b"],
                              "arpu_segment": ["MID"], "arpu_change_pct": [.4], "conversion_rate": [1.]})
        pilot = {"target_tariff": "b", "channel": "push", "explicit_ids": [0]}
        final = {"target_tariff": "b", "channel": "push", "filter_current_tariff": "a"}
        scores = split_eval.score_completed_run([pilot], [final], profile, tariffs, model,
                                                lambda *args: self.fail("Unexpected fallback"))
        pilots = scores["pilots_only"]["net_arpu_gain"]
        combined = scores["combined"]["net_arpu_gain"]
        standalone = scores["final_standalone"]["net_arpu_gain"]
        self.assertEqual((pilots, combined, standalone), (20., 40., 40.))
        self.assertEqual(combined - pilots, 20.)
        self.assertNotEqual(combined - pilots, standalone)
        self.assertNotEqual(combined, pilots + standalone)

    def test_snapshot_verifier_detects_file_changes_and_additions(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            source = snapshot / "agent.py"
            source.write_text("class Agent: pass\n", encoding="utf-8")
            (snapshot / "manifest.json").write_text(json.dumps([
                {"path": "agent.py", "sha256": split_eval.sha256(source)}]), encoding="utf-8")
            split_eval.verify_snapshot(snapshot)
            source.write_text("class Agent: changed = True\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Snapshot changed"):
                split_eval.verify_snapshot(snapshot)
            source.write_text("class Agent: pass\n", encoding="utf-8")
            (snapshot / "unexpected.pyc").write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "file set changed"):
                split_eval.verify_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
