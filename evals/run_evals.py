"""
Stage 6: run the eval suite and print the scores.

Each (scenario, repeat) runs as its own process (run_case.py), several at a
time. Results are saved as each one finishes, and re-running the same command
skips anything already done, so a crash or a stop loses nothing.

Examples (from the project folder):
    # a 2-scenario trial run, 1 repeat
    C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python evals\\run_evals.py --variant baseline --cases hot_afternoon,save_outing --reps 1

    # the full baseline: 12 scenarios x 3 repeats with Haiku
    C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python evals\\run_evals.py --variant baseline --model claude-haiku-5-5

    # the comparison: the same eval with Sonnet as the agent's model
    C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python evals\\run_evals.py --variant v1 --model claude-sonnet-5-5

Results go to .claude/hillclimb/pack-planner/<variant>/ (kept off GitHub).
"""

import argparse
import json
import math
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EVALS_DIR = Path(__file__).parent
PROJECT_DIR = EVALS_DIR.parent
FLOW_DIR = PROJECT_DIR / ".claude" / "hillclimb" / "pack-planner"
sys.path.insert(0, str(EVALS_DIR))

from scenarios import SCENARIOS  # noqa: E402

# Price per million tokens (input, output), from Anthropic's price list.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00),
          "claude-opus-5-5": (4.00, 20.00)}

STATE = {  # tells the report builder what the metrics are
    "flow": "pack-planner",
    "metrics": [
        {"id": "pass", "label": "Pass", "kind": "binary"},
        {"id": "safety", "label": "Safety", "kind": "binary"},
        {"id": "quality", "label": "Quality (1-5)", "kind": "number"},
    ],
    "perf_fields": ["latency_s", "tool_calls"],
    "prices": {m: {"in": p[0], "out": p[1]} for m, p in PRICES.items()},
}

write_lock = threading.Lock()


def cost(model: str, usage: dict) -> float:
    price_in, price_out = PRICES[model.split("-2")[0] if model not in PRICES else model]
    tokens_in = (usage.get("input_tokens", 0) + 1.25 * usage.get("cache_creation_input_tokens", 0)
                 + 0.1 * usage.get("cache_read_input_tokens", 0))
    return (tokens_in * price_in + usage.get("output_tokens", 0) * price_out) / 1_000_000


def row_cost(row: dict) -> float:
    return cost(row["model"], row["usage"]) + cost(row["judge_model"], row["judge_usage"])


def done_keys(results_file: Path) -> set:
    if not results_file.exists():
        return set()
    with open(results_file, encoding="utf-8") as f:
        return {(r["prompt_id"], r["rep"]) for r in map(json.loads, f) if r}


def run_one(case: str, rep: int, args, out: Path) -> None:
    """Run one (scenario, repeat) in its own process, with a hard time limit."""
    command = [sys.executable, str(EVALS_DIR / "run_case.py"), "--case", case,
               "--rep", str(rep), "--model", args.model, "--out", str(out)]
    started = time.monotonic()
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              timeout=args.timeout_s, cwd=PROJECT_DIR)
        row_line = next((ln for ln in done.stdout.splitlines() if ln.startswith("ROW ")), None)
        if done.returncode != 0 or row_line is None:
            last_error = (done.stderr.strip().splitlines() or ["no output"])[-1]
            raise RuntimeError(last_error[:300])
        row = json.loads(row_line[4:])
        with write_lock, open(out / "results.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        g = row["grade"]
        print(f"  {case:<20} rep {rep}  pass={g['pass']:.0f} safety={g['safety']:.0f} "
              f"quality={g['quality']:.0f}  {row['latency_s']}s  ${row_cost(row):.4f}", flush=True)
    except subprocess.TimeoutExpired:
        record_error(out, case, rep, "timeout", f"over {args.timeout_s}s", started)
    except Exception as e:  # harness or API error: not a model failure, so not scored
        record_error(out, case, rep, "error", str(e), started)


def record_error(out: Path, case: str, rep: int, kind: str, detail: str, started: float):
    entry = {"prompt_id": case, "rep": rep, "failure_class": kind, "detail": detail,
             "seconds": round(time.monotonic() - started, 1)}
    with write_lock, open(out / "errors.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  {case:<20} rep {rep}  {kind.upper()}: {detail[:120]}", flush=True)


def wilson(successes: float, n: int) -> tuple[float, float]:
    """95% confidence range for a pass rate (Wilson score interval)."""
    if n == 0:
        return 0.0, 0.0
    z, p = 1.96, successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - margin), min(1.0, centre + margin)


def summarize(out: Path) -> None:
    with open(out / "results.jsonl", encoding="utf-8") as f:
        rows = [r for r in map(json.loads, f) if r["status"] == "ok"]
    if not rows:
        print("No completed rows yet.")
        return
    n = len(rows)
    print(f"\n{out.name}: {n} completed runs, model {rows[0]['model']}, judge {rows[0]['judge_model']}")
    for metric in ("pass", "safety"):
        hits = sum(r["grade"][metric] for r in rows)
        low, high = wilson(hits, n)
        print(f"  {metric:<8} {hits:.0f}/{n} = {hits / n:.0%}  (95% range {low:.0%}-{high:.0%})")
    quality = [r["grade"]["quality"] for r in rows]
    print(f"  quality  {sum(quality) / n:.2f} / 5")
    costs = sorted(row_cost(r) for r in rows)
    latencies = sorted(r["latency_s"] for r in rows)
    print(f"  cost per run: median ${costs[n // 2]:.4f} (range ${costs[0]:.4f}-${costs[-1]:.4f}), "
          f"total ${sum(costs):.2f}")
    print(f"  time per run: median {latencies[n // 2]}s (range {latencies[0]}-{latencies[-1]}s)")
    errors = out / "errors.jsonl"
    if errors.exists():
        print(f"  errors (not scored): {sum(1 for _ in open(errors, encoding='utf-8'))} - see errors.jsonl")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="baseline", help="baseline, v1, v2, ...")
    parser.add_argument("--model", default="claude-haiku-5-5")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--cases", default="", help="comma-separated scenario ids (default: all)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout-s", type=int, default=420)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    out = FLOW_DIR / args.variant
    out.mkdir(parents=True, exist_ok=True)
    (FLOW_DIR / "_state.json").write_text(json.dumps(STATE, indent=2), encoding="utf-8")
    if args.summary_only:
        summarize(out)
        return

    wanted = [s["id"] for s in SCENARIOS
              if not args.cases or s["id"] in args.cases.split(",")]
    skip = done_keys(out / "results.jsonl")
    jobs = [(case, rep) for case in wanted for rep in range(args.reps) if (case, rep) not in skip]
    print(f"Running {len(jobs)} of {len(wanted) * args.reps} runs with {args.model} "
          f"({len(skip)} already done)...")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for case, rep in jobs:
            pool.submit(run_one, case, rep, args, out)
    print(f"Finished in {time.monotonic() - started:.0f}s")
    summarize(out)


if __name__ == "__main__":
    main()
