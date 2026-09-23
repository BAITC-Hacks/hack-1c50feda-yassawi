import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest


class DemoTests(unittest.TestCase):
    def test_run_rerun_and_submission(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "demo_app.py"), default_timeout=60).run()
        self.assertFalse(app.exception)
        self.assertNotIn("report", app.session_state)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        before = app.session_state["report"]
        self.assertGreater(len(before["pilots"]), 0)
        app.number_input[0].set_value(19).run()
        self.assertEqual(before, app.session_state["report"])
        app.button[1].click().run()
        self.assertFalse(app.exception)
        self.assertIn(b"campaign_name", app.session_state["submission"])
        self.assertEqual(before, app.session_state["report"])
        app.button[0].click().run()
        self.assertEqual(19, app.session_state["report"]["seed"])


if __name__ == "__main__":
    unittest.main()
