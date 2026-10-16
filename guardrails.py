"""
Stage 2: guardrails - safety rules enforced by code, not by the prompt.

Claude proposes a plan by calling the `submit_plan` tool. This file checks
that plan against the rules in dogs.json and returns one of three verdicts:

  SAFE     - approved
  CAUTION  - approved, but warnings MUST be shown to the user
  UNSAFE   - rejected; Claude has to revise the plan and submit again

There is no AI in this file. The same plan and weather always give the same
answer, which is exactly what you want for safety rules.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from tools import get_weather_forecast

PROJECT_DIR = Path(__file__).parent
LOG_FILE = PROJECT_DIR / "guardrail_log.jsonl"


def load_profile() -> dict:
    """Read the dogs' profile and the safety limits from dogs.json."""
    with open(PROJECT_DIR / "dogs.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# The rules (pure logic: easy to test, no internet needed)
# ---------------------------------------------------------------------------

def _hours_in_window(hourly: list, start_time: str, duration_hours: float) -> list:
    """Pick out the forecast hours that overlap the outing."""
    start = datetime.strptime(start_time, "%H:%M")
    end = start + timedelta(hours=duration_hours)
    chosen = []
    for hour in hourly:
        t = datetime.strptime(hour["time"], "%H:%M")
        # Include the hour the outing starts in, and every hour until it ends.
        if start - timedelta(minutes=59) <= t < end:
            chosen.append(hour)
    return chosen


def evaluate_plan(plan: dict, hourly: list | None, profile: dict) -> dict:
    """Check one plan against the rules.

    `hourly` is the forecast for the plan's date (from get_weather_forecast),
    or None if the weather couldn't be checked.

    Returns {"verdict": ..., "problems": [...], "warnings": [...]}.
    Problems block the plan. Warnings must be shown to the user.
    """
    # Reject badly formatted input up front, with a message Claude can act on.
    try:
        plan_date = date.fromisoformat(plan["date"])
        start_hour = datetime.strptime(plan["start_time"], "%H:%M").hour
    except ValueError:
        return {
            "verdict": "UNSAFE",
            "problems": ["Use date format YYYY-MM-DD and time format HH:MM (24-hour)."],
            "warnings": [],
        }

    problems = []
    warnings = []
    limits = profile["heat_limits_feels_like_f"]
    names = " and ".join(d["name"] for d in profile["dogs"])
    is_hike = plan["place_type"] == "hike"

    # --- Rule 1: dogs must be allowed ---------------------------------------
    rule = plan["dog_rule"].lower()
    if rule == "no":
        problems.append(f"Dogs are not allowed at {plan['place_name']}.")
    elif rule == "unknown":
        warnings.append(
            f"Dog rules for {plan['place_name']} aren't confirmed. "
            "Check the park's website or signs before you go."
        )

    # --- Rule 2: heat --------------------------------------------------------
    if hourly is None:
        problems.append(
            "The weather couldn't be checked for this date (forecasts only go "
            "about 6 days ahead). Pick a date within that range."
        )
    else:
        window = _hours_in_window(hourly, plan["start_time"], plan["duration_hours"])
        if not window:
            problems.append(
                "The outing falls outside 6am-9pm, so the weather can't be "
                "checked. Pick a time within that range."
            )
        else:
            hottest = max(h["feels_like_f"] for h in window)
            hottest_air = max(h["temp_f"] for h in window)
            least_cloud = min(h["cloud_cover_pct"] for h in window)
            wettest = max(h["rain_chance_pct"] or 0 for h in window)
            highest_uv = max(h["uv_index"] or 0 for h in window)

            if hottest >= limits["unsafe"]:
                problems.append(
                    f"Too hot: it feels like {hottest:.0f}°F during this outing. "
                    f"The limit for {names} is {limits['unsafe']}°F. "
                    "Pick an earlier or later time, or a different day."
                )
            elif hottest >= limits["caution"]:
                warnings.append(
                    f"Warm: up to {hottest:.0f}°F feels-like. Bring plenty of "
                    "water, take shade breaks and watch for heavy panting."
                )

            if hottest_air >= profile["pavement_warning_air_temp_f"] and least_cloud < 50:
                warnings.append(
                    "Sunny and warm: pavement can get hot enough to burn paws. "
                    "Test it with the back of your hand for 7 seconds, and "
                    "stick to grass or dirt where possible."
                )

            if wettest >= profile["rain_warning_chance_pct"]:
                warnings.append(f"Up to a {wettest}% chance of rain. Pack a towel.")

            if highest_uv > profile["uv_warning_index"]:
                warnings.append(
                    f"Strong sun: UV index up to {highest_uv:.0f}. Pick shaded "
                    "trails, take shade breaks and bring extra water."
                )

    # --- Rule 3: distance (hikes only) --------------------------------------
    if is_hike:
        dist = profile["distance_miles"]
        if plan["distance_miles"] > dist["comfortable_max"]:
            problems.append(
                f"{plan['distance_miles']} miles is longer than the "
                f"{dist['comfortable_max']}-mile maximum. Suggest a shorter "
                "route or a turnaround point."
            )
        elif plan["distance_miles"] < dist["comfortable_min"]:
            warnings.append(
                f"Shorter than usual ({plan['distance_miles']} miles), so "
                f"{names} may still have energy to burn afterward."
            )

    # --- Rule 4: Thea and busy trails (hikes only) --------------------------
    if is_hike:
        reactive = [d["name"] for d in profile["dogs"] if "reactive" in d["notes"].lower()]
        is_weekend = plan_date.weekday() >= 5  # Saturday=5, Sunday=6
        if reactive and (is_weekend or 10 <= start_hour < 16):
            warnings.append(
                f"Trails are often busiest on weekends and from 10am-4pm. For "
                f"{' and '.join(reactive)}, go early, give other dogs space and "
                "have a turnaround plan."
            )

    # --- Rule 5: always shown ------------------------------------------------
    warnings.append(
        "Some parks limit how many dogs one person can bring. Confirm that "
        f"two dogs ({names}) are allowed."
    )

    if problems:
        verdict = "UNSAFE"
    elif len(warnings) > 1:  # more than just the always-shown reminder
        verdict = "CAUTION"
    else:
        verdict = "SAFE"
    return {"verdict": verdict, "problems": problems, "warnings": warnings}


# ---------------------------------------------------------------------------
# The tool Claude calls
# ---------------------------------------------------------------------------

def check_plan(**plan) -> dict:
    """Run the guardrails on a plan from Claude, using a fresh weather check.

    The code looks up the weather itself instead of trusting numbers from
    Claude, so a mistake (or a made-up number) can't slip through.
    """
    profile = load_profile()
    try:
        weather = get_weather_forecast(plan["latitude"], plan["longitude"], plan["date"])
        hourly = None if "error" in weather else weather["hourly"]
    except requests.RequestException:
        hourly = None  # no weather means the plan can't pass the heat check

    result = evaluate_plan(plan, hourly, profile)
    _log(plan, result)
    return result


def _log(plan: dict, result: dict) -> None:
    """Append one line per check to guardrail_log.jsonl."""
    entry = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "place": plan["place_name"],
        "date": plan["date"],
        "start_time": plan["start_time"],
        "verdict": result["verdict"],
        "problems": result["problems"],
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


SUBMIT_PLAN_TOOL = {
    "name": "submit_plan",
    "description": (
        "Submit an outing plan for a safety check BEFORE recommending it. "
        "Call this once for each option you plan to recommend (top pick and "
        "backups). The checker looks up the weather itself and applies the "
        "owner's heat, distance and dog-rule limits. If the verdict is UNSAFE, "
        "do not recommend that plan: fix the problems (different time, day, "
        "route or place) and submit again. If it is SAFE or CAUTION, you may "
        "recommend it."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "place_name": {"type": "string"},
            "place_type": {
                "type": "string",
                "enum": ["hike", "dog_park", "park_walk", "event", "other"],
                "description": "'hike' for trail outings; everything else is a non-hike.",
            },
            "dog_rule": {
                "type": "string",
                "enum": ["yes", "leashed", "dog park", "no", "unknown"],
                "description": "What the data says about dogs here. Use 'unknown' if not stated.",
            },
            "latitude": {"type": "number"},
            "longitude": {"type": "number"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "start_time": {"type": "string", "description": "24-hour HH:MM, e.g. 07:30"},
            "duration_hours": {"type": "number"},
            "distance_miles": {
                "type": "number",
                "description": "Walking distance in miles. Use 0 for dog parks and events.",
            },
        },
        "required": [
            "place_name", "place_type", "dog_rule", "latitude", "longitude",
            "date", "start_time", "duration_hours", "distance_miles",
        ],
        "additionalProperties": False,
    },
}
