"""Write SCENARIOS.md, a readable list of the eval scenarios, from scenarios.py.

Run it with:   C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python evals\\describe.py
"""

from pathlib import Path

from scenarios import SCENARIOS, UNIVERSAL_SAFETY_RUBRIC

WEATHER = {
    "mild": "Mild: about 58°F all day, cloudy",
    "hot": "Hot: 68°F before 9 AM, 80°F at 9-11, 92°F from 11 AM to 6 PM, cooler in the evening",
}

lines = [
    "# Eval scenarios",
    "",
    "12 scenarios, each run 3 times. The agent's tools return fixed, fictional data "
    "(see `fixtures.py`), so every run faces the same situation.",
    "",
    "| # | Scenario | Tags | Weather |",
    "|---|---|---|---|",
]
for i, s in enumerate(SCENARIOS, 1):
    lines.append(f"| {i} | `{s['id']}` | {', '.join(s['tags'])} | {s['weather']} |")

lines += ["", f"**Every scenario (safety):** {UNIVERSAL_SAFETY_RUBRIC}", ""]

for i, s in enumerate(SCENARIOS, 1):
    lines += [f"## {i}. `{s['id']}`", "", "**The owner asks:**", "", "> " + s["request"], "",
              f"**Setup:** {WEATHER[s['weather']]}."
              + (" Trusted list includes **Typo Cafe**, whose location is mistyped (in Alaska)."
                 if s.get("typo") else ""), "",
              "**Code checks** (from the tool calls):", ""]
    for c in s["checks"]:
        lines.append(f"- {'🛡️ **Safety:** ' if c['safety'] else ''}{c['description']}")
    lines += ["", "**Judge checklist** (from the answer):", ""]
    lines += [f"- {item}" for item in s["rubric"]]
    lines.append("")

out = Path(__file__).parent / "SCENARIOS.md"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote {out.name}: {len(SCENARIOS)} scenarios")
