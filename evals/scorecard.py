"""Write SCORECARD.md: a publishable summary of the eval results.

It contains scores, costs and timings only. The agent's actual answers and
conversations stay in .claude/hillclimb/ on this computer (kept off GitHub).

Run it with:   C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python evals\\scorecard.py
"""

import json
import sys
from datetime import date
from pathlib import Path

EVALS_DIR = Path(__file__).parent
FLOW_DIR = EVALS_DIR.parent / ".claude" / "hillclimb" / "pack-planner"
sys.path.insert(0, str(EVALS_DIR))

from run_evals import row_cost, wilson  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

VARIANTS = [("baseline", "Haiku 5.5"), ("v1", "Sonnet 5.5"), ("v2", "Haiku 5.5 + fixes")]


def load(variant: str) -> list:
    path = FLOW_DIR / variant / "results.jsonl"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in map(json.loads, f) if r["status"] == "ok"]


def rate(rows, metric):
    hits = sum(r["grade"][metric] for r in rows)
    low, high = wilson(hits, len(rows))
    return f"{hits:.0f}/{len(rows)} ({hits / len(rows):.0%}, range {low:.0%}-{high:.0%})"


def median(values):
    values = sorted(values)
    return values[len(values) // 2]


def main():
    results = {v: load(v) for v, _ in VARIANTS}
    present = [(v, label) for v, label in VARIANTS if results[v]]

    lines = [
        "# Eval scorecard",
        "",
        f"Last run: {date.today():%B %d, %Y}. 12 scenarios (see [SCENARIOS.md](SCENARIOS.md)), "
        "each run 3 times in a fixed, fictional test world. Graded by code checks plus an AI "
        "judge (Claude Sonnet 5.5). Ranges are 95% confidence intervals.",
        "",
        "## Overall",
        "",
        "| Agent model | Pass (all checks) | Safety (must be 100%) | Quality (1-5) | Cost per run | Time per run |",
        "|---|---|---|---|---|---|",
    ]
    for v, label in present:
        rows = results[v]
        quality = sum(r["grade"]["quality"] for r in rows) / len(rows)
        lines.append(
            f"| {label} | {rate(rows, 'pass')} | {rate(rows, 'safety')} | {quality:.2f} | "
            f"${median(row_cost(r) for r in rows):.3f} | {median(r['latency_s'] for r in rows):.0f}s |")

    lines += ["", "Cost per run includes the judge. Median values.", "",
              "## By scenario (pass rate out of 3 runs)", "",
              "| Scenario | " + " | ".join(label for _, label in present) + " |",
              "|---|" + "---|" * len(present)]
    for s in SCENARIOS:
        cells = []
        for v, _ in present:
            rows = [r for r in results[v] if r["prompt_id"] == s["id"]]
            if not rows:
                cells.append("-")
                continue
            passed = sum(r["grade"]["pass"] for r in rows)
            unsafe = sum(1 for r in rows if not r["grade"]["safety"])
            cells.append(f"{passed:.0f}/{len(rows)}" + (f" ⚠️ {unsafe} unsafe" if unsafe else ""))
        lines.append(f"| `{s['id']}` | " + " | ".join(cells) + " |")

    notes = EVALS_DIR / "scorecard_notes.md"
    if notes.exists():
        lines += ["", notes.read_text(encoding="utf-8").strip()]

    (EVALS_DIR / "SCORECARD.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Wrote SCORECARD.md for: " + ", ".join(label for _, label in present))


if __name__ == "__main__":
    main()
