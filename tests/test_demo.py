import contextlib
import hashlib
import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

from agent import Agent
from local_eval import evaluate_agent


ROOT = Path(__file__).resolve().parents[1]


class DemoTests(unittest.TestCase):
    def test_run_rerun_and_submission(self):
        app = AppTest.from_file(str(ROOT / "demo_app.py"), default_timeout=60).run()
        self.assertFalse(app.exception)
        self.assertNotIn("report", app.session_state)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        before = app.session_state["report"]
        self.assertGreater(len(before["pilots"]), 0)
        with patch("local_eval.evaluate_agent", side_effect=AssertionError("A rerun must not conduct pilots")):
            app.number_input[0].set_value(19).run()
        self.assertFalse(app.exception)
        self.assertEqual(before, app.session_state["report"])
        app.button[1].click().run()
        self.assertFalse(app.exception)
        self.assertIn(b"campaign_name", app.session_state["submission"])
        self.assertEqual(hashlib.sha256((ROOT / "agent.py").read_bytes()).hexdigest(),
                         app.session_state["submission_agent_sha256"])
        self.assertEqual(before, app.session_state["report"])
        app.button[0].click().run()
        self.assertEqual(19, app.session_state["report"]["seed"])

    def test_stale_report_is_discarded(self):
        for stale_report in ({"seed": 42}, {"seed": 42, "agent_sha256": "stage1-experimental-source"}):
            with self.subTest(report=stale_report):
                app = AppTest.from_file(str(ROOT / "demo_app.py"), default_timeout=60)
                app.session_state["report"] = stale_report
                app.run()
                self.assertFalse(app.exception)
                self.assertNotIn("report", app.session_state)
                self.assertEqual(2, len(app.metric))

    def test_stale_submission_is_discarded(self):
        for old_hash in (None, "stage1-experimental-source"):
            with self.subTest(hash=old_hash):
                app = AppTest.from_file(str(ROOT / "demo_app.py"), default_timeout=60)
                app.session_state["submission"] = b"experimental CSV"
                if old_hash is not None:
                    app.session_state["submission_agent_sha256"] = old_hash
                app.run()
                self.assertFalse(app.exception)
                self.assertNotIn("submission", app.session_state)
                self.assertNotIn("submission_agent_sha256", app.session_state)
                self.assertEqual(0, len(app.get("download_button")))

    def test_fresh_report_uses_current_root_source_despite_cached_import(self):
        with contextlib.redirect_stdout(io.StringIO()):
            expected = evaluate_agent(Agent(), seed=42, verbose=False)
        stale_module = types.ModuleType("agent")

        class StaleAgent:
            def __init__(self):
                raise AssertionError("The demo imported a cached experimental agent")

        stale_module.Agent = StaleAgent
        with patch.dict(sys.modules, {"agent": stale_module}):
            app = AppTest.from_file(str(ROOT / "demo_app.py"), default_timeout=60).run()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        report = app.session_state["report"]
        self.assertEqual(str(ROOT / "agent.py"), report["agent_path"])
        self.assertEqual(hashlib.sha256((ROOT / "agent.py").read_bytes()).hexdigest(), report["agent_sha256"])
        self.assertEqual(42, report["seed"])
        self.assertAlmostEqual(expected["net_arpu_gain"], report["evaluation"]["net_arpu_gain"])
        self.assertEqual(expected["n_pilots"], len(report["pilots"]))
        self.assertFalse(any(event["event"] == "history_unavailable" for event in report["log"]))


if __name__ == "__main__":
    unittest.main()
