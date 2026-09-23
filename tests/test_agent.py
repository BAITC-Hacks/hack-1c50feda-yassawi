import os
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from agent import Agent, audience
from mock_environment import make_mock_env
from scoring_core import apply_filters, sanitize_campaigns, validate_strategy


class PublicTestEnv:
    """Controlled observations independent of organizer effect functions."""
    def __init__(self, mode="positive", budget=100000, contacts=15000):
        self.customer_profile = pd.DataFrame({
            "ID_NUMBER": range(600), "current_tariff": ["a"]*300 + ["b"]*300,
            "arpu_segment": ["HIGH"]*600, "data_segment": ["HEAVY"]*600,
            "call_segment": ["LOW"]*600, "predicted_arpu": [6000.]*600})
        self.tariffs = pd.DataFrame({"tariff_plan_code": ["a", "b", "c"], "price_tariff": [1000., 2000., 3000.]})
        self.channels = {"push": {"cost_per_contact": 0, "conversion_multiplier": .5},
                         "sms": {"cost_per_contact": 4, "conversion_multiplier": .65}}
        self.remaining_budget, self.remaining_contacts = budget, contacts
        self.pilots_left, self.pilot_history, self.mode = 20, [], mode

    def run_pilot(self, n_customers, **campaign):
        assert 10 <= n_customers <= 200
        assert n_customers <= len(audience(self.customer_profile, campaign))
        # Deliberately return fewer people than requested.
        actual = max(10, n_customers // 2)
        cost = actual * self.channels[campaign["channel"]]["cost_per_contact"]
        assert cost <= self.remaining_budget and actual <= self.remaining_contacts
        self.remaining_budget -= cost
        self.remaining_contacts -= actual
        self.pilots_left -= 1
        value = .4 if self.mode == "positive" else -.4
        if self.mode == "target":
            value = .6 if campaign["target_tariff"] == "b" else -.5
        if self.mode == "noisy":
            value = [-.05, .45, -.02, .3][len(self.pilot_history) % 4]
        result = {"n_customers": actual, "cost": cost, "observed_lift_ratio": value}
        self.pilot_history.append(result)
        return result


class AgentTests(unittest.TestCase):
    def check_limits(self, env, plan, budget=100000, contacts=15000):
        self.assertTrue(1 <= len(plan) <= 10)
        spent = sum(p["cost"] for p in env.pilot_history)
        used_contacts = sum(p["n_customers"] for p in env.pilot_history)
        seen = set()
        for c in plan:
            self.assertNotIn("explicit_ids", c)
            segment = audience(env.customer_profile, c)
            self.assertTrue(0 < len(segment) <= 5000)
            self.assertFalse(seen & set(segment.ID_NUMBER))
            seen.update(segment.ID_NUMBER)
            spent += len(segment) * env.channels[c["channel"]]["cost_per_contact"]
            used_contacts += len(segment)
        self.assertLessEqual(spent, budget)
        self.assertLessEqual(used_contacts, contacts)
        self.assertLessEqual(len(env.pilot_history), 20)

    def test_official_contract_and_filters(self):
        env, _ = make_mock_env(seed=42)
        plan = Agent().act(env)
        self.assertGreater(len(env.pilot_history), 0)
        self.check_limits(env, plan)
        self.assertEqual(plan, sanitize_campaigns(plan, env.tariffs))
        validate_strategy(pd.DataFrame(plan), env.tariffs)
        for c in plan + [{"filter_current_tariff": "tariff_1; tariff_2", "filter_arpu_segment": "LOW"}]:
            self.assertEqual(list(audience(env.customer_profile, c).ID_NUMBER), list(apply_filters(env.customer_profile, pd.Series(c)).ID_NUMBER))

    def test_observations_change_plan(self):
        first, second = PublicTestEnv(), PublicTestEnv("target")
        a, b = Agent().act(first), Agent().act(second)
        self.assertNotEqual(a, b)
        self.check_limits(first, a)
        self.check_limits(second, b)

    def test_negative_noisy_and_actual_sample(self):
        for mode in ("negative", "noisy"):
            env, agent = PublicTestEnv(mode), Agent()
            plan = agent.act(env)
            self.check_limits(env, plan)
            events = [e for e in agent.log if e["event"] == "pilot"]
            self.assertTrue(events)
            self.assertEqual(sum(e["observation"]["n_customers"] for e in events), sum(p["n_customers"] for p in env.pilot_history))
            self.assertTrue(all(e["observation"]["n_customers"] < e["requested"] for e in events))
            if mode == "negative":
                self.assertTrue(any("fallback" in e.get("reason", "") for e in agent.log))

    def test_small_budget_contacts(self):
        env = PublicTestEnv(budget=40, contacts=400)
        self.check_limits(env, Agent().act(env), budget=40, contacts=400)

    def test_empty_missing_invalid(self):
        env = PublicTestEnv()
        env.customer_profile = env.customer_profile.iloc[:0]
        self.assertEqual([], Agent().act(env))
        env = PublicTestEnv()
        env.customer_profile.loc[0, "predicted_arpu"] = np.nan
        env.customer_profile.loc[300, "arpu_segment"] = None
        self.check_limits(env, Agent().act(env))

    def test_reproducibility_no_key_and_log_error(self):
        with patch.dict(os.environ, {}, clear=True):
            agent = Agent(log_path="agent.py/impossible.json")
            first = agent.act(PublicTestEnv())
            self.assertEqual(first, agent.act(PublicTestEnv()))
        env1, _ = make_mock_env(seed=13)
        env2, _ = make_mock_env(seed=13)
        self.assertEqual(Agent().act(env1), Agent().act(env2))

    def test_pilot_error(self):
        env = PublicTestEnv()
        with patch.object(env, "run_pilot", side_effect=RuntimeError("unavailable")):
            self.check_limits(env, Agent().act(env))

    def test_adaptive_experiment_count_and_size(self):
        strong, ambiguous = PublicTestEnv("positive"), PublicTestEnv("noisy")
        a, b = Agent(), Agent()
        a.act(strong)
        b.act(ambiguous)
        self.assertNotEqual(len(strong.pilot_history), len(ambiguous.pilot_history))
        self.assertGreater(len({e["requested"] for e in b.log if e["event"] == "pilot"}), 1)

    def test_split_large_cells(self):
        env = PublicTestEnv()
        frame = pd.concat([env.customer_profile.iloc[:300]] * 20, ignore_index=True)
        frame.ID_NUMBER = range(len(frame))
        frame.loc[:2999, "data_segment"] = "LITE"
        env.customer_profile = frame
        self.check_limits(env, Agent().act(env))


if __name__ == "__main__":
    unittest.main()
