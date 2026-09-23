"""BeeGrowth: public-data priors, sequential experiments, constrained planning."""
from pathlib import Path
import json
import math
import time

import numpy as np
import pandas as pd


FILTERS = {"current_tariff": "filter_current_tariff", "arpu_segment": "filter_arpu_segment",
           "data_segment": "filter_data_segment", "call_segment": "filter_call_segment"}
ALLOWED = {"arpu_segment": {"LOW", "MID", "HIGH"},
           "data_segment": {"NON_USER", "LITE", "HEAVY"},
           "call_segment": {"LOW", "MEDIUM", "HIGH"}}


def audience(profile, campaign):
    """Match the CSV filter contract; never silently trim a final audience."""
    mask = pd.Series(True, index=profile.index)
    for col, key in FILTERS.items():
        value = campaign.get(key)
        if value is None or pd.isna(value):
            continue
        if col == "current_tariff":
            mask &= profile[col].isin([x.strip() for x in str(value).split(";") if x.strip()])
        else:
            mask &= profile[col].eq(value)
    return profile.loc[mask]


class Agent:
    def __init__(self, log_path=None):
        self.log_path = log_path
        self.log = []

    def _event(self, event, **values):
        self.log.append({"event": event, **values})

    def _history(self):
        path = Path(__file__).parent / "data" / "change_tariff.csv"
        try:
            frame = pd.read_csv(path).drop_duplicates()
            before = pd.to_numeric(frame.AVG_ARPU_PREV_3M, errors="coerce")
            after = pd.to_numeric(frame.AVG_ARPU_NEXT_3M, errors="coerce")
            valid = np.isfinite(before) & np.isfinite(after) & (before > 0) & (after >= 0)
            frame = frame.loc[valid].copy()
            # Winsorization limits influence of tiny denominators; never response rates.
            frame["change"] = ((after[valid] - before[valid]) / before[valid]).clip(-1, 2)
            frame["segment"] = np.where(before[valid] < 1000, "LOW",
                                        np.where(before[valid] <= 5000, "MID", "HIGH"))
            stats = frame.groupby(["tariff_plan_code_from", "segment", "tariff_plan_code_to"])["change"].agg(["sum", "count"])
            self._event("history", valid_rows=len(frame), excluded_rows=int((~valid).sum()))
            return stats
        except (OSError, ValueError, AttributeError, KeyError) as exc:
            self._event("history_unavailable", reason=str(exc))
            return pd.DataFrame(columns=["sum", "count"])

    def _cells(self, profile, known):
        cells = []

        def split(frame, filters, depth=0):
            if len(frame) <= 5000:
                values = pd.to_numeric(frame.predicted_arpu, errors="coerce")
                # Invalid rows cannot be removed by arbitrary IDs: skip their entire cell.
                if len(frame) and values.notna().all() and np.isfinite(values).all() and (values >= 0).all() and frame.ID_NUMBER.notna().all():
                    cells.append({"filters": filters, "size": len(frame), "arpu": float(values.sum()),
                                  "ids": frozenset(frame.ID_NUMBER), "current": str(frame.current_tariff.iloc[0]),
                                  "segment": str(frame.arpu_segment.iloc[0])})
                else:
                    self._event("cell_rejected", filters=filters, reason="empty or invalid ARPU/ID")
                return
            if depth == 2:
                self._event("cell_rejected", filters=filters, reason="cannot express audience <= 5000")
                return
            col = ["data_segment", "call_segment"][depth]
            for value, part in frame.groupby(col, sort=True, observed=True):
                if value in ALLOWED[col]:
                    split(part, {**filters, FILTERS[col]: str(value)}, depth + 1)

        for (current, segment), frame in profile.groupby(["current_tariff", "arpu_segment"], sort=True, observed=True):
            if current in known and segment in ALLOWED["arpu_segment"]:
                split(frame, {"filter_current_tariff": str(current), "filter_arpu_segment": str(segment)})
        return cells

    def _candidates(self, env):
        history = self._history()
        tariffs = env.tariffs.set_index("tariff_plan_code")
        cells = self._cells(env.customer_profile, set(tariffs.index))
        candidates = []
        for cell_id, cell in enumerate(cells):
            targets = []
            for target in sorted(tariffs.index):
                if target == cell["current"]:
                    continue
                key = (cell["current"], cell["segment"], target)
                # Price difference is only a weak hypothesis, capped and shrunk.
                price = float(tariffs.loc[target, "price_tariff"])
                base = max(cell["arpu"] / cell["size"], 100)
                weak = float(np.clip((price - base) / base, -0.5, 0.5)) * 0.1
                row = history.loc[key] if key in history.index else {"sum": 0, "count": 0}
                conditional = (float(row["sum"]) + 30 * weak) / (float(row["count"]) + 30)
                targets.append((conditional, str(target)))
            for conditional, target in sorted(targets, key=lambda x: (-x[0], x[1]))[:3]:
                for channel in sorted(env.channels):
                    cost = float(env.channels[channel]["cost_per_contact"])
                    if not math.isfinite(cost) or cost < 0:
                        continue
                    multiplier = float(env.channels[channel].get("conversion_multiplier", 1))
                    # 0.15 is a weak response assumption, NOT estimated from transition counts.
                    mean = conditional * min(0.15 * multiplier, 1)
                    candidate = {**cell, "cell": cell_id, "target": target, "channel": channel,
                                 "cost": cost, "mean": mean, "variance": 0.2 ** 2,
                                 "n": 0, "pilots": [], "failed": False}
                    candidates.append(candidate)
                    self._event("candidate", index=len(candidates)-1, filters=cell["filters"],
                                target=target, channel=channel, size=cell["size"], arpu_sum=cell["arpu"],
                                mean=mean, std=0.2, reason="smoothed history and weak response prior")
        return candidates

    @staticmethod
    def _net(c, risk=1):
        return (c["mean"] - risk * math.sqrt(c["variance"])) * c["arpu"] - c["size"] * c["cost"]

    @staticmethod
    def _campaign(c, number):
        return {"campaign_name": f"BeeGrowth_{number:02d}", **c["filters"],
                "target_tariff": c["target"], "channel": c["channel"]}

    def act(self, env) -> list[dict]:
        start = time.monotonic()
        self.log = []  # No posterior or observation survives an independent call.
        self.rng = np.random.default_rng(42)  # deterministic ties; no sampling currently needed
        candidates = self._candidates(env)
        if not candidates:
            self._event("infeasible", reason="no representable valid audience")
            self._save()
            return []
        # Reserve a whole expressible final campaign, including when push is unavailable.
        affordable = [c for c in candidates if c["size"] <= env.remaining_contacts and
                      c["size"] * c["cost"] <= env.remaining_budget]
        if not affordable:
            self._event("infeasible", reason="no final campaign fits resources")
            self._save()
            return []
        reserve = min(affordable, key=lambda c: (c["size"] * c["cost"], c["size"]))
        reserve_n, reserve_cost = reserve["size"], reserve["size"] * reserve["cost"]
        tested_cells = set()
        successful = 0
        for step in range(min(16, int(env.pilots_left))):
            if time.monotonic() - start > 480:
                break
            choices = []
            leaders = sorted([c for c in candidates if c["n"] > 0],
                             key=lambda c: -self._net(c, 0))[:2]
            for i, c in enumerate(candidates):
                if c["failed"] or len(c["pilots"]) >= 3 or c["size"] < 10:
                    continue
                if c["size"] > env.remaining_contacts or c["size"] * c["cost"] > env.remaining_budget:
                    continue
                # After broad exploration, resolve observed uncertainty rather than
                # endlessly selecting untested arms for their large prior variance.
                if successful >= 6:
                    if c["n"] == 0:
                        alternate = successful in (6, 7) and any(
                            c["cell"] == leader["cell"] and c["target"] == leader["target"]
                            and c["channel"] != leader["channel"] and leader["mean"] > 0
                            for leader in leaders)
                        if not alternate:
                            continue
                    elif abs(c["mean"] - c["cost"] * c["size"] / max(c["arpu"], 1)) > 2.5 * math.sqrt(c["variance"]):
                        continue
                # Aim for posterior SD 0.05 on an ambiguous repeat; use actual
                # accumulated precision, including partially fulfilled pilots.
                needed = math.ceil(0.804**2 * max(0, 1 / 0.05**2 - 1 / c["variance"]))
                n = 100 if c["n"] == 0 else min(200, max(100, needed))
                n = min(n, c["size"], int(env.remaining_contacts - reserve_n))
                if c["cost"] > 0:
                    n = min(n, int((env.remaining_budget - reserve_cost) // c["cost"]))
                if n < 10:
                    continue
                sd = math.sqrt(c["variance"])
                optimistic = self._net(c, -1)
                if optimistic <= 0:
                    continue
                new_var = 1 / (1 / c["variance"] + n / 0.804**2)
                value = (sd - math.sqrt(new_var)) * c["arpu"]
                # Charge both communication cost and opportunity cost of finite contacts.
                value -= n * (c["cost"] + max(c["mean"], 0) * c["arpu"] / c["size"])
                if successful >= 6 and value <= 0:
                    continue
                diversity = int(successful < 6 and c["cell"] not in tested_cells)
                priority = (max(optimistic, 0) + max(value, 0)) / (1 + c["size"] * c["cost"] / max(env.remaining_budget, 1))
                choices.append((diversity, priority, -i, n))
            if not choices:
                self._event("pilot_stop", reason="no affordable experiment with positive value")
                break
            _, _, minus_i, n = max(choices)
            i = -minus_i
            c = candidates[i]
            try:
                result = env.run_pilot(target_tariff=c["target"], channel=c["channel"],
                                       n_customers=n, **c["filters"])
            except (RuntimeError, ValueError) as exc:
                c["failed"] = True
                self._event("pilot_error", index=i, reason=str(exc))
                continue
            observed, actual = float(result["observed_lift_ratio"]), int(result["n_customers"])
            if not math.isfinite(observed) or actual <= 0:
                c["failed"] = True
                self._event("pilot_invalid", index=i, reason="nonfinite observation or empty sample")
                continue
            precision = 1 / c["variance"]
            weight = actual / 0.804**2
            c["mean"] = (precision * c["mean"] + weight * observed) / (precision + weight)
            c["variance"] = 1 / (precision + weight)
            c["n"] += actual
            c["pilots"].append(dict(result))
            tested_cells.add(c["cell"])
            successful += 1
            self._event("pilot", index=i, requested=n, observation=dict(result), mean=c["mean"],
                        std=math.sqrt(c["variance"]), n=c["n"], reason="optimistic value and uncertainty reduction")
        budget, contacts = float(env.remaining_budget), int(env.remaining_contacts)
        selected, used = [], set()
        # Recompute scarcity-aware incremental utility after each allocation.
        pool = list(enumerate(candidates))
        while len(selected) < 10:
            feasible = [(i, c) for i, c in pool if not (used & c["ids"]) and c["size"] <= contacts
                        and c["size"] * c["cost"] <= budget]
            positive = [(i, c) for i, c in feasible if c["n"] > 0 and self._net(c) > 0]
            if not positive:
                if selected or not feasible:
                    break
                i, chosen = max(feasible, key=lambda pair: (self._net(pair[1]), -pair[0]))
                reason = "fallback: least estimated risk-adjusted loss; profit not established"
            else:
                i, chosen = max(positive, key=lambda pair: (
                    self._net(pair[1]) / (1 + pair[1]["size"] / max(contacts, 1) +
                                         pair[1]["size"] * pair[1]["cost"] / max(budget, 1)), -pair[0]))
                reason = "positive risk-adjusted incremental net; disjoint audience; fits resources"
            selected.append(self._campaign(chosen, len(selected) + 1))
            used.update(chosen["ids"])
            budget -= chosen["size"] * chosen["cost"]
            contacts -= chosen["size"]
            self._event("selected", index=i, campaign=selected[-1], size=chosen["size"],
                        mean_net=self._net(chosen, 0), risk_adjusted_net=self._net(chosen),
                        reason=reason)
            if reason.startswith("fallback"):
                break
        picked = {event["index"] for event in self.log if event["event"] == "selected"}
        for i, c in enumerate(candidates):
            if i not in picked:
                reason = ("overlapping audience" if used & c["ids"] else
                          "resource limit" if c["size"] > contacts or c["size"] * c["cost"] > budget else
                          "unverified or nonpositive conservative net / campaign limit")
                self._event("rejected", index=i, reason=reason)
        self._event("plan", campaigns=len(selected), pilots=successful, remaining_budget_after_plan=budget,
                    remaining_contacts_after_plan=contacts,
                    reason="pilot costs already deducted by environment; final audiences counted in full")
        self._save()
        return selected

    def _save(self):
        if self.log_path is not None:
            try:
                path = Path(self.log_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(self.log, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError, TypeError):
                pass
