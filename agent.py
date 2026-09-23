"""QADAM: public-API-only, risk-aware sequential campaign planner.

History ranks hypotheses; only pilots estimate target-population outcomes.
No mock internals, hidden model, environment inspection, or network access.
"""
from pathlib import Path
import math
import time

import numpy as np
import pandas as pd


class Agent:
    def __init__(self, risk=1.28, max_pilots=20, progress=None):
        self.risk = float(risk)
        self.max_pilots = min(20, max(1, int(max_pilots)))
        self.progress = progress
        self.report = {}

    def _emit(self, message):
        if self.progress:
            self.progress(message)

    def _history(self):
        """The provided, observable history is a ranking signal, not ground truth."""
        path = Path(__file__).parent / 'data' / 'change_tariff.csv'
        if not path.exists():
            return {}
        h = pd.read_csv(path)
        h = h[(h.AVG_ARPU_PREV_3M > 100) & (h.AVG_ARPU_NEXT_3M >= 0)].copy()
        h['segment'] = np.where(h.AVG_ARPU_PREV_3M < 1000, 'LOW',
                               np.where(h.AVG_ARPU_PREV_3M > 5000, 'HIGH', 'MID'))
        h['ratio'] = ((h.AVG_ARPU_NEXT_3M - h.AVG_ARPU_PREV_3M) /
                      h.AVG_ARPU_PREV_3M).clip(-1, 2)
        result = {}
        for key, g in h.groupby(['tariff_plan_code_from', 'segment', 'tariff_plan_code_to']):
            n = len(g)
            # Regularization and a sample-size penalty suppress tiny lucky groups.
            result[key] = float(g.ratio.mean() * n / (n + 25) - .3 / math.sqrt(n))
        return result

    def _cells(self, profile):
        cells = []
        for (source, segment), g in profile.groupby(['current_tariff', 'arpu_segment'], sort=True, observed=True):
            groups = [(None, g)] if len(g) <= 5000 else list(g.groupby('data_segment', sort=True, observed=True))
            for data_segment, sub in groups:
                # Never silently accept an unrepresentable >5000 audience.
                parts = [(None, sub)] if len(sub) <= 5000 else list(sub.groupby('call_segment', sort=True, observed=True))
                for call_segment, part in parts:
                    if not 10 <= len(part) <= 5000:
                        continue
                    filters = {'filter_current_tariff': str(source), 'filter_arpu_segment': str(segment)}
                    if data_segment is not None:
                        filters['filter_data_segment'] = str(data_segment)
                    if call_segment is not None:
                        filters['filter_call_segment'] = str(call_segment)
                    cells.append({'id': len(cells), 'source': str(source), 'segment': str(segment),
                                  'filters': filters, 'n': len(part),
                                  'arpu': float(part.predicted_arpu.sum()),
                                  'mean_arpu': float(part.predicted_arpu.mean())})
        return cells

    @staticmethod
    def _posterior(candidate):
        # Weak zero-centred prior; historic effects do not leak into this posterior.
        precision, weighted = 1 / .5 ** 2, 0.0
        for ob in candidate['obs']:
            # Public template says SD ~= .07 at n=150. Conservative SD floor.
            variance = (.9 ** 2 / ob['n']) / ob['multiplier'] ** 2
            precision += 1 / variance
            weighted += ob['ratio'] / ob['multiplier'] / variance
        return weighted / precision, math.sqrt(1 / precision)

    def act(self, env):
        started = time.monotonic()
        self.report = {'pilots': [], 'campaigns': [], 'warnings': [], 'method': 'QADAM sequential evidence portfolio'}
        profile = env.customer_profile
        if profile.empty or not np.isfinite(profile.predicted_arpu).all() or (profile.predicted_arpu < 0).any():
            raise ValueError('Audience is empty or predicted_arpu contains invalid values')
        self._emit('Аудитория мен тарихи ауысуларды талдау')
        cells = self._cells(profile)
        excluded = len(profile) - sum(c['n'] for c in cells)
        if excluded:
            self.report['warnings'].append(f'{excluded} профиль жоспардан шығарылды: сегмент/тариф бос немесе топ көлемі 10–5000 шегіне сай емес. Бастапқы CSV өзгертілмеді.')
        tariffs = sorted(map(str, env.tariffs['tariff_plan_code']))
        channels = env.channels
        if not cells or not channels:
            raise ValueError('No eligible audience or communication channels')
        try:
            history = self._history()
        except (OSError, ValueError, KeyError, AttributeError) as exc:
            history = {}
            self.report['warnings'].append('History unavailable; neutral exploration: ' + str(exc))
        candidates = []
        prices = dict(zip(env.tariffs.tariff_plan_code, env.tariffs.price_tariff))
        for cell in cells:
            targets = [t for t in tariffs if t != cell['source']]
            targets.sort(key=lambda t: (-history.get((cell['source'], cell['segment'], t), 0),
                                        abs(float(prices[t]) - cell['mean_arpu']), t))
            for rank, target in enumerate(targets[:3]):
                prior_rank = history.get((cell['source'], cell['segment'], target), 0)
                candidates.append({'cell': cell, 'target': target, 'rank': rank, 'obs': [],
                                   'priority': cell['arpu'] * (.1 + max(0, prior_rank)) / (rank + 1)})
        candidates.sort(key=lambda c: (-c['priority'], c['cell']['id'], c['target']))
        initial_budget, initial_contacts = float(env.remaining_budget), int(env.remaining_contacts)
        cheap = min(channels, key=lambda k: (channels[k]['cost_per_contact'], k))
        exploration_limit = max(1, int(self.max_pilots * .6))
        tested_cells = set()
        for step in range(min(self.max_pilots, int(env.pilots_left))):
            if time.monotonic() - started > 240 or env.pilots_left <= 0 or env.remaining_contacts < 20:
                break
            untested = [c for c in candidates if not c['obs']]
            tested = [c for c in candidates if c['obs']]
            # Early coverage avoids spending the whole exploration budget on one cell.
            fresh = [c for c in untested if c['cell']['id'] not in tested_cells]
            if step < exploration_limit and (fresh or untested):
                candidate = (fresh or untested)[0]
                reason, channel, sample = 'explore', cheap, 80
            else:
                options = []
                for c in tested:
                    mu, se = self._posterior(c)
                    # Upper confidence opportunity: uncertain high-value cells are worth revisiting.
                    if len(c['obs']) < 3 and mu + 1.5 * se > 0:
                        options.append(((mu + 1.5 * se) * c['cell']['arpu'] / math.sqrt(len(c['obs'])), c))
                if options:
                    candidate = max(options, key=lambda x: x[0])[1]
                    mu, se = self._posterior(candidate)
                    reason, channel = 'confirm', cheap
                    # Test a commercially attractive channel; avoid costly exploratory calls.
                    affordable = [ch for ch, cfg in channels.items()
                                  if cfg['cost_per_contact'] * 120 <= min(env.remaining_budget * .08, 2500)]
                    if affordable and mu - self.risk * se > 0:
                        channel = max(affordable, key=lambda ch: (mu - self.risk * se) *
                                      channels[ch]['conversion_multiplier'] * candidate['cell']['mean_arpu'] -
                                      channels[ch]['cost_per_contact'])
                    sample = min(200, max(100, int(80 + 600 * se)))
                elif untested:
                    candidate = untested[0]
                    reason, channel, sample = 'explore', cheap, 80
                else:
                    break
            cell = candidate['cell']
            cost = float(channels[channel]['cost_per_contact'])
            sample = min(sample, cell['n'], int(env.remaining_contacts) - 10)
            if cost:
                sample = min(sample, int(env.remaining_budget / cost))
            if sample < 10:
                break
            self._emit(f"Пилот {step + 1}: {cell['source']} → {candidate['target']} · {channel} · {sample}")
            try:
                result = env.run_pilot(target_tariff=candidate['target'], channel=channel,
                                       n_customers=sample, **cell['filters'])
                ratio = float(result['observed_lift_ratio'])
                n = int(result['n_customers'])
                if not math.isfinite(ratio) or n < 1:
                    raise ValueError('Pilot returned non-finite effect or invalid sample size')
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                self.report['warnings'].append('Pilot stopped: ' + str(exc))
                break  # A failed call may already have consumed budget: never blindly retry.
            ob = {'n': n, 'ratio': ratio, 'multiplier': float(channels[channel]['conversion_multiplier'])}
            candidate['obs'].append(ob)
            tested_cells.add(cell['id'])
            mu, se = self._posterior(candidate)
            self.report['pilots'].append({'step': step + 1, 'source': cell['source'], 'segment': cell['segment'],
                'target': candidate['target'], 'channel': channel, 'n': n, 'ratio': ratio,
                'cost': float(result['cost']), 'reason': reason, 'mean_base': mu, 'se_base': se,
                'lower_base': mu - self.risk * se, 'upper_base': mu + self.risk * se})

        self._emit('Бюджет пен қамту шегіне сай портфель құру')
        by_cell = {}
        for c in candidates:
            if not c['obs']:
                continue
            mu, se = self._posterior(c)
            cell = c['cell']
            for channel, cfg in channels.items():
                cost = cell['n'] * float(cfg['cost_per_contact'])
                multiplier = float(cfg['conversion_multiplier'])
                lower = (mu - self.risk * se) * multiplier * cell['arpu'] - cost
                mean = mu * multiplier * cell['arpu'] - cost
                item = {'cell_id': cell['id'], 'n': cell['n'], 'cost': cost, 'lower': lower, 'expected': mean,
                        'ratio': mu * multiplier, 'se': se * multiplier, 'source': cell['source'],
                        'segment': cell['segment'], 'target': c['target'], 'channel': channel,
                        'filters': cell['filters'], 'pilot_n': sum(o['n'] for o in c['obs']),
                        'measured_channel': any(p['source'] == cell['source'] and p['segment'] == cell['segment']
                            and p['target'] == c['target'] and p['channel'] == channel for p in self.report['pilots'])}
                if lower > 0:
                    by_cell.setdefault(cell['id'], []).append(item)
        # Approximate multiple-choice knapsack: exact resources, bounded beam search.
        # Each group is a disjoint audience. No duplicate final contacts across campaigns.
        states = [(0.0, 0.0, 0, [])]
        for _, choices in sorted(by_cell.items()):
            expanded = list(states)
            for value, spent, contacts, items in states:
                for item in choices:
                    if (len(items) < 10 and spent + item['cost'] <= env.remaining_budget and
                            contacts + item['n'] <= env.remaining_contacts):
                        expanded.append((value + item['lower'], spent + item['cost'], contacts + item['n'], items + [item]))
            # Keep variety across resource buckets; all feasibility checks use exact values.
            best = {}
            for s in expanded:
                key = (len(s[3]), int(s[1] // 1000), int(s[2] // 250))
                if key not in best or s[0] > best[key][0]:
                    best[key] = s
            states = sorted(best.values(), key=lambda s: (-s[0], s[1], s[2]))[:800]
        selected = max(states, key=lambda s: s[0])[3]
        if not selected:
            # Case requires >=1 campaign even if all observations are negative.
            # Minimize conservative total harm among tested reachable cells; warn explicitly.
            eligible = [c for c in candidates if c['obs'] and c['cell']['n'] <= env.remaining_contacts and
                        c['cell']['n'] * channels[cheap]['cost_per_contact'] <= env.remaining_budget]
            if eligible:
                c = max(eligible, key=lambda c: (self._posterior(c)[0] - self.risk * self._posterior(c)[1]) * c['cell']['arpu'])
                cell = c['cell']; mu, se = self._posterior(c); m = channels[cheap]['conversion_multiplier']
                cost = cell['n'] * channels[cheap]['cost_per_contact']
                selected = [{'cell_id': cell['id'], 'n': cell['n'], 'cost': cost,
                    'lower': (mu - self.risk * se) * m * cell['arpu'] - cost,
                    'expected': mu * m * cell['arpu'] - cost, 'ratio': mu * m, 'se': se * m,
                    'source': cell['source'], 'segment': cell['segment'], 'target': c['target'],
                    'channel': cheap, 'filters': cell['filters'], 'pilot_n': sum(o['n'] for o in c['obs']),
                    'measured_channel': True}]
                self.report['warnings'].append('No positive conservative plan. Required minimum campaign uses the least harmful tested fallback; loss is possible.')
            else:
                self.report['warnings'].append('No feasible campaign remains. Returning empty plan rather than exceeding limits.')
        selected.sort(key=lambda x: (-x['lower'], x['cell_id']))
        campaigns = []
        for i, item in enumerate(selected, 1):
            campaign = {'campaign_name': f'QADAM_{i:02d}_{item["source"]}_{item["target"]}',
                        **item['filters'], 'target_tariff': item['target'], 'channel': item['channel']}
            campaigns.append(campaign)
            self.report['campaigns'].append({**item, 'campaign_name': campaign['campaign_name'],
                'explanation': 'Pilot evidence + conservative effect + exact budget/contact limits. '
                    + ('Channel observed in a pilot.' if item['measured_channel'] else
                       'Channel extrapolated using the public conversion multiplier; not directly piloted.')})
        self.report.update({'plan': campaigns, 'pilot_cost': initial_budget - float(env.remaining_budget),
            'pilot_contacts': initial_contacts - int(env.remaining_contacts),
            'final_cost': sum(c['cost'] for c in selected), 'final_contacts': sum(c['n'] for c in selected),
            'expected_final_gain': sum(c['expected'] for c in selected),
            'conservative_final_gain': sum(c['lower'] for c in selected),
            'seconds': time.monotonic() - started, 'risk': self.risk,
            'audience': len(profile), 'baseline': float(profile.predicted_arpu.sum()),
            'history_pairs': len(history), 'candidate_count': len(candidates)})
        return campaigns
