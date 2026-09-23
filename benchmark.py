"""Evaluation-only comparison; no evaluator information returns to the agent."""
import contextlib
import io
import json
from pathlib import Path
import statistics
import time
from agent import Agent
from agent_template import Agent as Baseline
from local_eval import evaluate_agent


def main():
    report = {}
    for name, cls in (("BeeGrowth", Agent), ("template", Baseline)):
        rows = []
        for seed in range(10):
            captured = io.StringIO()
            started = time.perf_counter()
            with contextlib.redirect_stdout(captured):
                result = evaluate_agent(cls(), seed=seed, verbose=False)
            output = captured.getvalue()
            if result is None or "Агент упал" in output or (name == "BeeGrowth" and "отброшена" in output):
                raise RuntimeError(output or "empty evaluation")
            rows.append({"seed": seed, "net": result["net_arpu_gain"], "pilots": result["n_pilots"],
                         "seconds": time.perf_counter() - started, "output": output})
        values = [r["net"] for r in rows]
        report[name] = {"median": statistics.median(values), "min": min(values), "max": max(values),
                        "positive": sum(v > 0 for v in values), "total_seconds": sum(r["seconds"] for r in rows), "runs": rows}
        print(name, {k: v for k, v in report[name].items() if k != "runs"})
    Path("diagnostics").mkdir(exist_ok=True)
    Path("diagnostics/benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
