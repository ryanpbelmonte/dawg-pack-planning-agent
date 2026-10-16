"""
Tests for the safety rules in guardrails.py.

Free and offline: no Claude, no internet. Each test feeds in a made-up plan
and made-up weather, then checks that the verdict is what it should be.

Run it with:   C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python test_guardrails.py

If you change a rule or a limit in dogs.json, run this again to make sure
nothing broke. (This is a first step toward the "evals" in Stage 6.)
"""

from guardrails import evaluate_plan, load_profile

PROFILE = load_profile()
WEDNESDAY, SATURDAY = "2026-10-14", "2026-10-17"


def fake_weather(feels_like, cloud=80, rain=0, uv=3):
    """A day where every hour from 6am to 9pm has the same weather."""
    return [
        {"time": f"{h:02d}:00", "temp_f": feels_like, "feels_like_f": feels_like,
         "rain_chance_pct": rain, "uv_index": uv, "cloud_cover_pct": cloud}
        for h in range(6, 22)
    ]


def plan(**changes):
    """A normal, safe weekday-morning hike. Each test changes one thing."""
    base = {
        "place_name": "Test Trail", "place_type": "hike", "dog_rule": "leashed",
        "latitude": 47.5, "longitude": -122.2, "date": WEDNESDAY,
        "start_time": "07:30", "duration_hours": 2, "distance_miles": 4,
    }
    return base | changes


def hot_afternoon():
    """Cool morning, 90°F from noon on."""
    day = fake_weather(65)
    for hour in day:
        if hour["time"] >= "12:00":
            hour["feels_like_f"] = hour["temp_f"] = 90
    return day


# (description, plan, weather, expected verdict, text that must appear or None)
CASES = [
    ("Cool weekday morning hike",       plan(),                              fake_weather(62),  "SAFE",    None),
    ("Too hot",                          plan(),                              fake_weather(88),  "UNSAFE",  "Too hot"),
    ("Warm, cloudy",                     plan(),                              fake_weather(78),  "CAUTION", "Warm"),
    ("Warm and sunny: pavement warning", plan(),                              fake_weather(78, cloud=10), "CAUTION", "pavement"),
    ("Cool morning, hot afternoon",      plan(start_time="13:00"),            hot_afternoon(),   "UNSAFE",  "Too hot"),
    ("Same day, but early start",        plan(start_time="07:00"),            hot_afternoon(),   "SAFE",    None),
    ("Hike too long",                    plan(distance_miles=8),              fake_weather(62),  "UNSAFE",  "maximum"),
    ("Short hike",                       plan(distance_miles=2),              fake_weather(62),  "CAUTION", "Shorter"),
    ("Dogs not allowed",                 plan(dog_rule="no"),                 fake_weather(62),  "UNSAFE",  "not allowed"),
    ("Dog rule unknown",                 plan(dog_rule="unknown"),            fake_weather(62),  "CAUTION", "aren't confirmed"),
    ("Saturday hike: busy for Thea",     plan(date=SATURDAY),                 fake_weather(62),  "CAUTION", "Thea"),
    ("Saturday dog park: crowds fine",   plan(date=SATURDAY, place_type="dog_park", distance_miles=0),
                                                                              fake_weather(62),  "SAFE",    None),
    ("Rainy",                            plan(),                              fake_weather(62, rain=80), "CAUTION", "rain"),
    ("High UV",                          plan(),                              fake_weather(62, uv=8),    "CAUTION", "UV index"),
    ("UV exactly 7: no warning",         plan(),                              fake_weather(62, uv=7),    "SAFE",    None),
    ("No forecast for that date",        plan(),                              None,              "UNSAFE",  "couldn't be checked"),
    ("Badly formatted time",             plan(start_time="7am"),              fake_weather(62),  "UNSAFE",  "HH:MM"),
]


def main():
    passed = 0
    for description, test_plan, weather, expected, must_mention in CASES:
        result = evaluate_plan(test_plan, weather, PROFILE)
        text = " ".join(result["problems"] + result["warnings"])
        ok = result["verdict"] == expected and (must_mention is None or must_mention in text)
        passed += ok
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}  {description:<36} expected {expected:<8} got {result['verdict']}")
        if not ok:
            print(f"      problems: {result['problems']}\n      warnings: {result['warnings']}")
    print(f"\n{passed}/{len(CASES)} passed")


if __name__ == "__main__":
    main()
