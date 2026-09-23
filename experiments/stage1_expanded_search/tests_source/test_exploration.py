"""Controlled policy scenarios: no organizer effect model and no seed tuning."""
import unittest
from unittest.mock import patch

import pandas as pd

from agent import Agent, audience


class ExplorationEnv:
    def __init__(self, names=None, positive_cells=(), positive_target=None):
        names = names or [f"x{i}" for i in range(8)]
        self.customer_profile = pd.DataFrame([
            {"ID_NUMBER": i * 300 + j, "current_tariff": name, "arpu_segment": "HIGH",
             "data_segment": "HEAVY", "call_segment": "LOW", "predicted_arpu": 6000.}
            for i, name in enumerate(names) for j in range(300)])
        self.tariffs = pd.DataFrame({"tariff_plan_code": names, "price_tariff": [6000.] * len(names)})
        self.channels = {"push": {"cost_per_contact": 0., "conversion_multiplier": .5}}
        self.remaining_budget, self.remaining_contacts, self.pilots_left = 100000, 15000, 20
        self.pilot_history, self.calls = [], []
        self.positive_cells = set(positive_cells)
        self.positive_target = positive_target

    def run_pilot(self, n_customers, **campaign):
        assert 10 <= n_customers <= min(200, len(audience(self.customer_profile, campaign)))
        assert n_customers <= self.remaining_contacts
        self.remaining_contacts -= n_customers
        self.pilots_left -= 1
        self.calls.append(campaign)
        positive = campaign["filter_current_tariff"] in self.positive_cells or campaign["target_tariff"] == self.positive_target
        result = {"n_customers": n_customers, "cost": 0., "observed_lift_ratio": .4 if positive else -.4}
        self.pilot_history.append(result)
        return result


class ExplorationTests(unittest.TestCase):
    def test_six_negative_segments_do_not_end_search(self):
        env = ExplorationEnv(positive_cells=("x6", "x7"))
        agent = Agent()
        plan = agent.act(env)
        self.assertEqual([-.4] * 6, [p["observed_lift_ratio"] for p in env.pilot_history[:6]])
        self.assertGreater(len(env.calls), 6)
        self.assertTrue({"x6", "x7"} <= {c["filter_current_tariff"] for c in env.calls[6:]})
        self.assertTrue(1 <= len(plan) <= 10)
        self.assertTrue(any(e["event"] == "pilot_choice" and e["successful_before"] >= 6
                            and e["new_hypothesis"] for e in agent.log))

    def test_new_target_after_six_negative_observations(self):
        env = ExplorationEnv()
        agent = Agent()
        agent.act(env)
        first_pairs = {(c["filter_current_tariff"], c["target_tariff"]) for c in env.calls[:6]}
        first_cells = {x[0] for x in first_pairs}
        self.assertTrue(any(c["filter_current_tariff"] in first_cells and
                            (c["filter_current_tariff"], c["target_tariff"]) not in first_pairs
                            for c in env.calls[6:]))

    def test_tariff_names_are_not_special(self):
        names = [f"plan_{i:02d}" for i in range(8)]
        env = ExplorationEnv(names, positive_cells=names[6:])
        Agent().act(env)
        self.assertTrue(set(names[6:]) <= {c["filter_current_tariff"] for c in env.calls[6:]})

    def test_strong_evidence_stops_with_unexplored_affordable_hypotheses(self):
        env, agent = ExplorationEnv(), Agent()
        run_pilot = env.run_pilot

        def strong_observation(**kwargs):
            result = run_pilot(**kwargs)
            result["observed_lift_ratio"] = 1.0
            return result

        with patch.object(env, "run_pilot", side_effect=strong_observation):
            agent.act(env)
        stop = [e for e in agent.log if e["event"] == "pilot_stop"][-1]
        self.assertEqual("no_estimated_value", stop["reason"])
        self.assertEqual(0, stop["exclusions"]["resources"])
        self.assertTrue(6 < len(env.pilot_history) < 16)
        self.assertGreater(env.pilots_left, 0)
        self.assertGreater(env.remaining_contacts, 300)
        self.assertLess(len(env.pilot_history), sum(e["event"] == "candidate" for e in agent.log))

    def test_stop_reasons_and_no_obligatory_full_budget(self):
        for case, expected in (("contacts", "insufficient_resources"), ("environment", "environment_pilot_limit"),
                               ("internal", "internal_pilot_limit"), ("value", "no_estimated_value")):
            env, agent = ExplorationEnv(), Agent()
            if case == "contacts":
                env.remaining_contacts = 300
            elif case == "environment":
                env.pilots_left = 2
            elif case == "value":
                env.customer_profile["predicted_arpu"] = 0.
            agent.act(env)
            self.assertEqual(expected, [e["reason"] for e in agent.log if e["event"] == "pilot_stop"][-1])
            self.assertLess(len(env.pilot_history), 20)

    def test_search_exhaustion_and_timeout(self):
        env = ExplorationEnv(["a", "b"])
        agent = Agent()
        with patch.object(env, "run_pilot", side_effect=RuntimeError("pilot unavailable")):
            agent.act(env)
        self.assertEqual("search_space_exhausted", [e["reason"] for e in agent.log if e["event"] == "pilot_stop"][-1])
        with patch("agent.time.monotonic", side_effect=[0., 481.]):
            agent.act(ExplorationEnv())
        self.assertEqual("timeout", [e["reason"] for e in agent.log if e["event"] == "pilot_stop"][-1])


if __name__ == "__main__":
    unittest.main()
