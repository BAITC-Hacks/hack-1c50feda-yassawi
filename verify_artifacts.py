"""Check organizer integrity and persisted submission against a fresh official build."""
import hashlib
import json
from pathlib import Path
import pandas as pd
from agent import Agent
from make_submission import build_submission
from mock_environment import make_mock_env
from scoring_core import apply_filters, sanitize_campaigns, validate_strategy


def main():
    root = Path(__file__).resolve().parent
    entries = json.loads((root / "organizer_hashes.json").read_text(encoding="utf-8-sig"))
    checked = []
    for entry in entries:
        # Snapshot originated on this workspace; file basenames in data remain scoped.
        original = Path(entry["Path"])
        relative = Path("data") / original.name if original.parent.name == "data" else Path(original.name)
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest().upper()
        assert actual == entry["Hash"], f"Organizer file changed: {relative}"
        checked.append(str(relative))
    saved = pd.read_csv(root / "submission.csv")
    regenerated = build_submission(Agent())
    pd.testing.assert_frame_equal(saved.fillna("").astype(str), regenerated.fillna("").astype(str))
    validate_strategy(saved, pd.read_csv(root / "data/dict_tariff.csv"))
    checks = []
    for seed in [*range(10), 42]:
        env, _ = make_mock_env(seed=seed)
        agent = Agent()
        plan = agent.act(env)
        assert 1 <= len(plan) <= 10
        assert plan == sanitize_campaigns(plan, env.tariffs)
        validate_strategy(pd.DataFrame(plan), env.tariffs)
        assert 0 < len(env.pilot_history) <= 20
        seen, cost, contacts = set(), 0, 0
        for campaign in plan:
            frame = apply_filters(env.customer_profile, pd.Series(campaign))
            assert 0 < len(frame) <= 5000
            assert not (seen & set(frame.ID_NUMBER))
            seen.update(frame.ID_NUMBER)
            cost += len(frame) * env.channels[campaign["channel"]]["cost_per_contact"]
            contacts += len(frame)
        assert cost <= env.remaining_budget and contacts <= env.remaining_contacts
        assert all(10 <= p["n_customers"] <= 200 for p in env.pilot_history)
        checks.append({"seed": seed, "final_campaigns": len(plan), "pilots": len(env.pilot_history),
                       "total_contacts": contacts + sum(p["n_customers"] for p in env.pilot_history),
                       "total_cost": cost + sum(p["cost"] for p in env.pilot_history)})
    report = {"organizer_files_unchanged": checked, "submission_reproduced": True, "runs": checks}
    (root / "diagnostics/verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
