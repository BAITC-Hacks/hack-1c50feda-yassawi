"""Post-act mock diagnostics and paired comparison; never sends scores to Agent.

The two fixed cohorts (0..99 and 100..149) vary pilot samples and noise in ONE
mock effect model. Final forecasts describe full campaigns before pilot overlap;
only combined score minus pilot score is the final plan's incremental effect.
"""
import argparse
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import time

import pandas as pd

from environment import MAX_PILOTS, MAX_PILOT_CUSTOMERS, MIN_PILOT_CUSTOMERS
from mock_environment import make_mock_env, _mock_impact_model, _mock_fallback
from scoring_core import (
    MAX_CAMPAIGNS, MAX_CUSTOMERS_PER_CAMPAIGN, MAX_TOTAL_CONTACTS, TOTAL_BUDGET,
    apply_filters, sanitize_campaigns, score_campaigns, validate_strategy,
)


ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "snapshots" / "before_stage1"
FILTER_COLUMNS = ["filter_arpu_segment", "filter_data_segment",
                  "filter_call_segment", "filter_current_tariff"]
COHORTS = {"primary": list(range(100)), "additional": list(range(100, 150))}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_snapshot(snapshot=SNAPSHOT):
    """Read-only integrity check including unexpected files such as __pycache__."""
    snapshot = Path(snapshot).resolve()
    manifest = snapshot / "manifest.json"
    entries = json.loads(manifest.read_text(encoding="utf-8-sig"))
    expected = {"manifest.json"}
    for entry in entries:
        relative = entry["path"]
        path = (snapshot / relative).resolve()
        if not path.is_relative_to(snapshot):
            raise ValueError(f"Snapshot manifest path escapes its directory: {relative}")
        if sha256(path).lower() != entry["sha256"].lower():
            raise ValueError(f"Snapshot changed: {relative}")
        expected.add(path.relative_to(snapshot).as_posix())
    actual = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError(f"Snapshot file set changed: {sorted(actual ^ expected)}")
    return {"files_verified": len(entries), "manifest_sha256": sha256(manifest)}


def load_agent(path):
    """Load source without import caches or any writes beside the baseline file."""
    path = Path(path).resolve()
    namespace = {"__file__": str(path), "__name__": "_split_eval_agent"}
    exec(compile(path.read_bytes(), str(path), "exec"), namespace)
    return namespace["Agent"]


def campaign_frame(campaigns):
    frame = pd.DataFrame(campaigns)
    for col in FILTER_COLUMNS + ["explicit_ids", "target_tariff", "channel"]:
        if col not in frame:
            frame[col] = None
    return frame


def score_completed_run(pilots, finals, profile, tariffs, impact_model, fallback):
    """Score three scenarios using the SAME executed pilot IDs, after act ends."""
    baseline = profile["predicted_arpu"].sum()
    scores = {}
    for key, campaigns in (("pilots_only", pilots), ("combined", pilots + finals),
                           ("final_standalone", finals)):
        scores[key] = score_campaigns(campaign_frame(campaigns), profile, impact_model,
                                     tariffs, baseline, fallback, team_id="split_eval")
    return scores


def _hypothesis(campaign, channel=True):
    keys = FILTER_COLUMNS + ["target_tariff"] + (["channel"] if channel else [])
    return tuple(campaign.get(key) for key in keys)


def _validate(env, initial, raw_final, sanitized, finals, pilots, scores, agent_error):
    errors = []
    if agent_error:
        errors.append("agent_exception")
    if not isinstance(raw_final, list):
        errors.append("final_plan_not_list")
    if not 1 <= len(raw_final or []) <= MAX_CAMPAIGNS:
        errors.append("final_campaign_count")
    dropped = len(raw_final or []) - len(sanitized)
    if dropped:
        errors.append("sanitizer_dropped_campaigns")
    if len(sanitized) != len(finals):
        errors.append("campaign_count_truncated")
    try:
        validate_strategy(campaign_frame(finals), env.tariffs)
    except ValueError as exc:
        errors.append(f"invalid_strategy: {exc}")
    final_sizes, final_cost = [], 0.0
    for campaign in finals:
        if "explicit_ids" in campaign:
            errors.append("final_explicit_ids")
        size = len(apply_filters(env.customer_profile, pd.Series(campaign)))
        final_sizes.append(size)
        final_cost += size * env.channels[campaign["channel"]]["cost_per_contact"]
        if not 1 <= size <= MAX_CUSTOMERS_PER_CAMPAIGN:
            errors.append("final_audience_size")
    pilot_contacts = sum(p["n_customers"] for p in env.pilot_history)
    pilot_cost = sum(p["cost"] for p in env.pilot_history)
    if not (len(pilots) == len(env.pilot_history) == initial["pilots"] - env.pilots_left
            and len(pilots) <= MAX_PILOTS):
        errors.append("pilot_count")
    if any(not MIN_PILOT_CUSTOMERS <= p["n_customers"] <= MAX_PILOT_CUSTOMERS
           for p in env.pilot_history):
        errors.append("pilot_size")
    if not math.isclose(initial["budget"] - env.remaining_budget, pilot_cost):
        errors.append("pilot_budget_accounting")
    if initial["contacts"] - env.remaining_contacts != pilot_contacts:
        errors.append("pilot_contact_accounting")
    total_contacts, total_cost = pilot_contacts + sum(final_sizes), pilot_cost + final_cost
    if total_contacts > min(initial["contacts"], MAX_TOTAL_CONTACTS):
        errors.append("total_contact_limit")
    if total_cost > min(initial["budget"], TOTAL_BUDGET) + 1e-8:
        errors.append("total_budget_limit")
    combined = scores["combined"]
    cap_keys = ["capped_at_campaign_limit", "capped_at_reach_budget", "capped_at_money_budget"]
    trimmed = sum(any(detail[key] for key in cap_keys) for detail in combined["campaigns_detail"])
    if trimmed:
        errors.append("scorer_trimmed_campaigns")
    if combined["total_contacts"] != total_contacts or not math.isclose(combined["total_cost"], total_cost):
        errors.append("scorer_resource_accounting")
    return {"all_limits_ok": not errors, "errors": sorted(set(errors)),
            "sanitizer_dropped": dropped, "campaign_count_truncated": len(sanitized) - len(finals),
            "scorer_trimmed": trimmed, "final_audience_sizes": final_sizes,
            "total_contacts": total_contacts, "total_cost": total_cost,
            "pilot_contacts": pilot_contacts, "pilot_cost": pilot_cost,
            "final_contacts": sum(final_sizes), "final_cost": final_cost,
            "budget_left_after_plan": initial["budget"] - total_cost,
            "contacts_left_after_plan": initial["contacts"] - total_contacts}


def evaluate_agent(agent, seed):
    """One fresh environment, exactly one act, then all external evaluation."""
    started = time.perf_counter()
    env, internals = make_mock_env(seed=seed, data_dir=str(ROOT / "data"),
                                   profile_path=str(ROOT / "customer_profile.csv"))
    initial = {"budget": env.remaining_budget, "contacts": env.remaining_contacts,
               "pilots": env.pilots_left}
    act_started = time.perf_counter()
    error = None
    try:
        raw_final = agent.act(env)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raw_final = []
    act_seconds = time.perf_counter() - act_started

    # The scoring model and concrete pilot IDs are fetched only AFTER act.
    evaluation_started = time.perf_counter()
    pilots = internals.executed_pilot_campaigns()
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        sanitized = sanitize_campaigns(raw_final, env.tariffs)
        finals = sanitized[:MAX_CAMPAIGNS]
        model = _mock_impact_model(pd.read_csv(ROOT / "data" / "change_tariff.csv"))
        scores = score_completed_run(pilots, finals, env.customer_profile, env.tariffs,
                                     model, _mock_fallback)
        validation = _validate(env, initial, raw_final, sanitized, finals, pilots, scores, error)
    selected = [e for e in getattr(agent, "log", []) if e["event"] == "selected"]
    forecasts_available = len(selected) == len(finals) and bool(finals)
    row = {"seed": seed, "agent_error": error,
           "net_pilots_only": scores["pilots_only"]["net_arpu_gain"],
           "net_combined": scores["combined"]["net_arpu_gain"],
           "final_incremental_net": scores["combined"]["net_arpu_gain"] - scores["pilots_only"]["net_arpu_gain"],
           "net_final_standalone": scores["final_standalone"]["net_arpu_gain"],
           "forecast_final_mean_net": sum(e["mean_net"] for e in selected) if forecasts_available else None,
           "forecast_final_risk_adjusted_net": sum(e["risk_adjusted_net"] for e in selected) if forecasts_available else None,
           "n_pilots": len(pilots), "n_final_campaigns": len(finals),
           "unique_hypotheses_with_channel": len({_hypothesis(p) for p in pilots}),
           "unique_hypotheses_without_channel": len({_hypothesis(p, False) for p in pilots}),
           "candidate_count": sum(e["event"] == "candidate" for e in getattr(agent, "log", [])),
           "pilot_stops": [e for e in getattr(agent, "log", []) if e["event"] == "pilot_stop"],
           "final_campaigns": finals, "validation": validation,
           "campaigns_detail": scores["combined"]["campaigns_detail"],
           "evaluation_messages": captured.getvalue(), "act_seconds": act_seconds}
    row["evaluation_seconds"] = time.perf_counter() - evaluation_started
    row["total_seconds"] = time.perf_counter() - started
    return row


def describe(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def summarize(rows):
    result = {"n_runs": len(rows)}
    for key in ("net_pilots_only", "net_combined", "final_incremental_net", "net_final_standalone",
                "n_pilots", "n_final_campaigns", "unique_hypotheses_with_channel",
                "unique_hypotheses_without_channel", "act_seconds", "evaluation_seconds", "total_seconds"):
        result[key] = describe([row[key] for row in rows])
    for key in ("total_cost", "total_contacts", "pilot_cost", "pilot_contacts"):
        result[key] = describe([row["validation"][key] for row in rows])
    for key, predicate in (("positive_combined", lambda r: r["net_combined"] > 0),
                           ("negative_final_incremental", lambda r: r["final_incremental_net"] < 0),
                           ("limits_ok", lambda r: r["validation"]["all_limits_ok"])):
        count = sum(predicate(row) for row in rows)
        result[key] = {"count": count, "fraction": count / len(rows)}
    result["sum_act_seconds"] = sum(row["act_seconds"] for row in rows)
    result["sum_total_seconds"] = sum(row["total_seconds"] for row in rows)
    return result


def _json_default(value):
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(type(value).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=[*COHORTS, "both"], default="both")
    parser.add_argument("--seeds", type=int, nargs="+", help="Explicit smoke-test seeds; supersedes --cohort")
    parser.add_argument("--baseline", type=Path, default=SNAPSHOT / "agent.py")
    parser.add_argument("--current", type=Path, default=ROOT / "agent.py")
    parser.add_argument("--output", type=Path, default=ROOT / "diagnostics" / "stage1" / "comparison.json")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "diagnostics" / "stage1") or output.suffix != ".json":
        parser.error("Output must be a .json file inside diagnostics/stage1")
    if output.exists():
        parser.error(f"Refusing to overwrite existing diagnostic: {output}")
    initial_integrity = verify_snapshot()
    paths = {"before": args.baseline.resolve(), "after": args.current.resolve()}
    hashes = {name: sha256(path) for name, path in paths.items()}
    classes = {name: load_agent(path) for name, path in paths.items()}
    groups = {"smoke": args.seeds} if args.seeds else {
        name: seeds for name, seeds in COHORTS.items() if args.cohort in (name, "both")}
    report = {"predeclared_cohorts": COHORTS,
              "source_sha256": hashes, "snapshot_before": initial_integrity,
              "method": "One fresh environment and one act per version/seed; same executed pilots in both scores; evaluation only after act; independent full-campaign forecasts.",
              "limitations": "Seeds change samples/noise, not the mock effect model. Timings are wall-clock measurements.",
              "cohorts": {}}
    started = time.perf_counter()
    for name, seeds in groups.items():
        rows = {"before": [], "after": []}
        for index, seed in enumerate(seeds):
            # Alternate order to reduce a systematic first-run timing advantage.
            order = ("before", "after") if index % 2 == 0 else ("after", "before")
            for version in order:
                rows[version].append(evaluate_agent(classes[version](), seed))
            if (index + 1) % 10 == 0 or index + 1 == len(seeds):
                print(f"{name}: {index + 1}/{len(seeds)} paired seeds complete", flush=True)
        report["cohorts"][name] = {
            version: {"summary": summarize(values), "runs": values} for version, values in rows.items()}
        deltas = [a["net_combined"] - b["net_combined"] for a, b in zip(rows["after"], rows["before"])]
        incremental_deltas = [a["final_incremental_net"] - b["final_incremental_net"]
                              for a, b in zip(rows["after"], rows["before"])]
        report["cohorts"][name]["paired_difference_after_minus_before"] = {
            "net_combined": describe(deltas), "final_incremental_net": describe(incremental_deltas),
            "combined_better_count": sum(delta > 0 for delta in deltas),
            "combined_worse_count": sum(delta < 0 for delta in deltas)}
    report["snapshot_after"] = verify_snapshot()
    if report["snapshot_after"] != initial_integrity:
        raise RuntimeError("Snapshot manifest changed during evaluation")
    if hashes != {name: sha256(path) for name, path in paths.items()}:
        raise RuntimeError("Agent source changed during evaluation; report not written")
    report["wall_seconds"] = time.perf_counter() - started
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, default=_json_default, allow_nan=False)
        stream.write("\n")
    print(f"Saved {output}", flush=True)


if __name__ == "__main__":
    main()
