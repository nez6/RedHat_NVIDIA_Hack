"""Summarize NeMo Gym rollouts per agent -> results/gym/summary.json (read by the dashboard)."""
import json, statistics, sys
from collections import defaultdict
from pathlib import Path


def summarize(out_dir):
    out_dir = Path(out_dir)
    per = defaultdict(list)
    files = {"eval": out_dir / "eval_rollouts.jsonl", "rl": out_dir / "rl_rollouts.jsonl"}
    for kind, path in files.items():
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            task = r.get("task") or {}
            per[(task.get("metric", "?"), kind)].append(float(r.get("reward", 0.0)))
    agents = {}
    for (metric, kind), rewards in sorted(per.items()):
        agents[metric] = {"mode": "eval+rl" if kind == "rl" else "eval", "n": len(rewards),
                          "mean_reward": round(statistics.mean(rewards), 3),
                          "pass_rate": round(sum(r >= 0.999 for r in rewards) / len(rewards), 3)}
    summary = {"agents": agents, "files": {k: str(v) for k, v in files.items() if v.exists()}}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    summarize(sys.argv[1] if len(sys.argv) > 1 else "results/gym")
