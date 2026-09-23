"""Verify restored source and historical-data provenance; never runs new pilots."""
import hashlib
import json
from pathlib import Path, PureWindowsPath

import pandas as pd

from split_eval import load_agent, verify_snapshot


ROOT = Path(__file__).resolve().parent
BASELINE = ROOT / "snapshots/before_stage1"
EXPERIMENT = ROOT / "experiments/stage1_expanded_search"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    versions = json.loads((EXPERIMENT / "versions.json").read_text(encoding="utf-8-sig"))
    assert (ROOT / "agent.py").read_bytes() == (BASELINE / "agent.py").read_bytes(), "Main source differs from baseline"
    assert digest(ROOT / "agent.py") == versions["main_sha256"].lower()
    assert digest(EXPERIMENT / "agent.py") == versions["experiment_sha256"].lower()
    archives = {"before_stage1": verify_snapshot(BASELINE), "stage1_experiment": verify_snapshot(EXPERIMENT)}
    histories = []
    for version, source in (("restored_main", ROOT / "agent.py"),
                            ("before_stage1", BASELINE / "agent.py"),
                            ("stage1_experiment", EXPERIMENT / "agent.py")):
        cls = load_agent(source)
        # This is OUR agent's file path, not an environment internal or closure.
        actual_file = Path(cls._history.__globals__["__file__"]).resolve()
        history = actual_file.parent / "data/change_tariff.csv"
        frame = pd.read_csv(history)
        agent = cls()
        grouped = agent._history()
        unavailable = any(event["event"] == "history_unavailable" for event in agent.log)
        history_event = next((event for event in agent.log if event["event"] == "history"), {})
        histories.append({
            "version": version, "agent_path": str(actual_file), "agent_sha256": digest(source),
            "history_path": str(history), "history_relative_path": history.relative_to(ROOT).as_posix(),
            "stage1_comparison_history_path": str(BASELINE / "data/change_tariff.csv")
                if version == "before_stage1" else str(ROOT / "data/change_tariff.csv"),
            "history_sha256": digest(history), "raw_rows": len(frame),
            "rows_after_deduplication": len(frame.drop_duplicates()),
            "processed_valid_rows": history_event.get("valid_rows"),
            "excluded_invalid_rows": history_event.get("excluded_rows"),
            "grouped_observations": int(grouped["count"].sum()),
            "history_unavailable": unavailable,
        })
        assert not unavailable, f"History fallback used for {version}"
        assert history_event["valid_rows"] == int(grouped["count"].sum())
    assert len({row["history_sha256"] for row in histories}) == 1, "History files differ"
    assert histories[0]["history_sha256"] == versions["history_sha256"].lower()
    assert len({row["processed_valid_rows"] for row in histories}) == 1

    comparisons = {}
    for cohort in ("primary", "additional"):
        path = ROOT / f"diagnostics/stage1/comparison_{cohort}.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        expected = {"before": versions["main_sha256"].lower(), "after": versions["experiment_sha256"].lower()}
        assert report["source_sha256"] == expected, "Comparison source hashes differ"
        assert path.read_bytes() == (EXPERIMENT / "diagnostics_stage1" / path.name).read_bytes()
        comparisons[cohort] = {"source_sha256": report["source_sha256"], "result_sha256": digest(path)}
    # All stage1 artifacts, not just the two large comparison reports, stay intact.
    for path in (EXPERIMENT / "diagnostics_stage1").iterdir():
        assert path.read_bytes() == (ROOT / "diagnostics/stage1" / path.name).read_bytes(), path.name

    organizers = []
    for entry in json.loads((ROOT / "organizer_hashes.json").read_text(encoding="utf-8-sig")):
        original = PureWindowsPath(entry["Path"])
        relative = Path("data") / original.name if original.parent.name == "data" else Path(original.name)
        assert digest(ROOT / relative) == entry["Hash"].lower(), f"Organizer file changed: {relative}"
        organizers.append(relative.as_posix())
    return {"main_restored_byte_for_byte": True, "versions": versions, "archives": archives,
            "histories": histories, "previous_comparison_comparable": True,
            "comparison_checks": comparisons, "unchanged_organizer_files": organizers,
            "scope": "History loader only; no new experiments or optimization; historical comparisons retained."}


def main():
    output = ROOT / "diagnostics/stabilization/history_and_integrity.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    result = audit()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
