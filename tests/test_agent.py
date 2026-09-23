import unittest
from unittest.mock import patch
import pandas as pd
from agent import Agent


class PublicEnvironment:
    """Independent test double, not organizer implementation or hidden effects."""
    def __init__(self, effect=.2, budget=100000, contacts=15000, size=400):
        self.customer_profile = pd.DataFrame([{'ID_NUMBER': i, 'current_tariff': 'a' if i < size//2 else 'b',
            'arpu_segment': 'HIGH', 'data_segment': 'HEAVY', 'call_segment': 'MEDIUM',
            'predicted_arpu': 6000.0} for i in range(size)])
        self.tariffs = pd.DataFrame({'tariff_plan_code':['a','b','c'], 'price_tariff':[1000,2000,3000]})
        self.channels = {'push':{'cost_per_contact':0,'conversion_multiplier':.5},
                         'sms':{'cost_per_contact':4,'conversion_multiplier':.65},
                         'call':{'cost_per_contact':160,'conversion_multiplier':1.2}}
        self.remaining_budget=budget;self.remaining_contacts=contacts;self.pilots_left=20
        self.effect=effect;self.pilot_history=[];self.calls=[]

    def run_pilot(self, target_tariff, channel, n_customers, **filters):
        assert 10 <= n_customers <= 200
        assert self.pilots_left > 0
        cost = n_customers * self.channels[channel]['cost_per_contact']
        assert cost <= self.remaining_budget and n_customers <= self.remaining_contacts
        self.remaining_budget -= cost;self.remaining_contacts -= n_customers;self.pilots_left -= 1
        self.calls.append((target_tariff,channel,n_customers,filters))
        result={'n_customers':n_customers,'observed_lift_ratio':self.effect * self.channels[channel]['conversion_multiplier'],'cost':cost}
        self.pilot_history.append(result)
        return result


class AgentTests(unittest.TestCase):
    def run_agent(self, env, **options):
        with patch.object(Agent, '_history', return_value={}):
            agent=Agent(**options);plan=agent.act(env)
        return agent,plan

    def assert_limits(self, agent, plan, budget=100000, contacts=15000):
        self.assertTrue(1 <= len(plan) <= 10)
        a=agent.report
        self.assertLessEqual(a['pilot_cost']+a['final_cost'],budget)
        self.assertLessEqual(a['pilot_contacts']+a['final_contacts'],contacts)
        self.assertTrue(all(x['n']<=5000 for x in a['campaigns']))
        self.assertEqual(len(a['campaigns']),len({c['cell_id'] for c in a['campaigns']}))

    def test_limits_and_pilot_use(self):
        env=PublicEnvironment();a,p=self.run_agent(env)
        self.assertTrue(env.calls);self.assert_limits(a,p)

    def test_zero_budget_uses_free_channel(self):
        a,p=self.run_agent(PublicEnvironment(budget=0))
        self.assert_limits(a,p,budget=0);self.assertTrue(all(c['channel']=='push' for c in p))

    def test_negative_feedback_changes_selection_and_is_disclosed(self):
        positive,pp=self.run_agent(PublicEnvironment(effect=.7))
        negative,np=self.run_agent(PublicEnvironment(effect=-.7))
        self.assertNotEqual(pp,np)
        self.assertTrue(negative.report['warnings']);self.assertLess(negative.report['expected_final_gain'],0)
        self.assert_limits(negative,np)

    def test_deterministic(self):
        _,a=self.run_agent(PublicEnvironment());_,b=self.run_agent(PublicEnvironment());self.assertEqual(a,b)

    def test_never_blindly_retries_failed_pilot(self):
        env=PublicEnvironment()
        with patch.object(env,'run_pilot',side_effect=RuntimeError('budget consumed')) as call:
            a,p=self.run_agent(env)
        self.assertEqual(call.call_count,1);self.assertEqual(p,[]);self.assertTrue(a.report['warnings'])

    def test_large_cell_split_preserves_5000_limit(self):
        env=PublicEnvironment(size=12000)
        env.customer_profile.loc[env.customer_profile.ID_NUMBER%2==0,'data_segment']='LITE'
        a,p=self.run_agent(env,max_pilots=8);self.assert_limits(a,p)

    def test_small_remaining_contacts_never_exceeded(self):
        env=PublicEnvironment(size=40,contacts=150)
        a,p=self.run_agent(env,max_pilots=1);self.assert_limits(a,p,contacts=150)

    def test_nonfinite_feedback_stops_without_invalid_json(self):
        env=PublicEnvironment(effect=float('nan'));a,p=self.run_agent(env)
        self.assertEqual(len(env.calls),1);self.assertEqual(p,[]);self.assertTrue(a.report['warnings'])


if __name__=='__main__': unittest.main()
