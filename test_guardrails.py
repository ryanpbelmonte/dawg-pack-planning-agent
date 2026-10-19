"""
Tests for the safety rules in guardrails.py.

Free and offline: no Claude, no internet. Each test feeds in a made-up plan
and made-up weather, then checks that the verdict is what it should be.

Run it with:   C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python test_guardrails.py

If you change a rule or a limit in dogs.json, run this again to make sure
nothing broke. (This is a first step toward the "evals" in Stage 6.)
"""

from datetime import date, datetime

from guardrails import evaluate_plan, load_profile

PROFILE = load_profile()
WEDNESDAY, SATURDAY = "2026-10-14", "2026-10-17"
# Fixed date and time, so the tests give the same result whenever they run.
TODAY = date(2026, 10, 13)
NOW = datetime(2026, 10, 13, 8, 0)  # 8:00 AM on TODAY


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
        "source": "map", "source_url": "",
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


def outing(place, rating, note="", day="2026-10-10"):
    """A made-up saved outing, for the memory rule (Stage 3)."""
    return {"place_name": place, "date": day, "rating": rating, "note": note}


HISTORY = [
    outing("Test Trail", 2, "Thea was reactive, too crowded"),
    outing("Happy Park", 5, "Perfect morning"),
]

# Same shape as CASES, plus the outing history to check against.
MEMORY_CASES = [
    ("Bad past visit: warned",           plan(),                              fake_weather(62),  "CAUTION", "Thea was reactive", HISTORY),
    ("Name match is loose",              plan(place_name="test trail loop"),  fake_weather(62),  "CAUTION", "rated 2/5",         HISTORY),
    ("Good past visit: no warning",      plan(place_name="Happy Park"),       fake_weather(62),  "SAFE",    None,                HISTORY),
    ("No history: no warning",           plan(),                              fake_weather(62),  "SAFE",    None,                []),
    # The "memory" source: a place from past outings, not from the trusted list.
    ("Place from memory",                plan(place_name="Happy Park", source="memory"),
                                                                              fake_weather(62),  "SAFE",    None,                HISTORY),
    ("Memory source, never visited",     plan(place_name="Never Been Park", source="memory"),
                                                                              fake_weather(62),  "UNSAFE",  "isn't in the saved outings", HISTORY),
    ("Memory place mislabeled my_list",  plan(place_name="Happy Park", source="my_list"),
                                                                              fake_weather(62),  "UNSAFE",  "use source 'memory'", HISTORY),
]


TRUSTED = [  # a made-up trusted list, as events.list_my_events() returns it
    {"name": "Brewmasters Taproom", "latitude": 47.5, "longitude": -122.2},
    {"name": "Typo Cafe", "latitude": 60.48, "longitude": -149.2,
     "data_problem": "its location is about 1,400 miles from Renton, WA"},
]
FOUND_LINK = "https://example.com/events/yappy-hour"
VERIFIED = {FOUND_LINK}  # pretend search_events returned this link


def event(**changes):
    """A Saturday afternoon event found by web search. Each test changes one thing."""
    return plan(place_name="Yappy Hour", place_type="event", source="web_search",
                source_url=FOUND_LINK, dog_rule="yes", date=SATURDAY,
                start_time="14:00", distance_miles=0) | changes


# Stage 4 cases: (description, plan, weather, expected, must mention)
EVENT_CASES = [
    ("Web event with a verified link",   event(),                             fake_weather(62),  "SAFE",    None),
    ("Link differs only by slash/case",  event(source_url="HTTPS://Example.com/events/yappy-hour/"),
                                                                              fake_weather(62),  "SAFE",    None),
    ("Web event, made-up link",          event(source_url="https://fake.example/win"),
                                                                              fake_weather(62),  "UNSAFE",  "didn't come from a web search"),
    ("Web event, no link",               event(source_url=""),                fake_weather(62),  "UNSAFE",  "need the link"),
    ("Web event, dogs not confirmed",    event(dog_rule="unknown"),           fake_weather(62),  "CAUTION", "aren't confirmed"),
    ("Trusted-list venue",               plan(place_name="Brewmasters Taproom", place_type="venue",
                                              source="my_list", dog_rule="yes", distance_miles=0),
                                                                              fake_weather(62),  "SAFE",    None),
    ("Trusted list: name not on the list", plan(place_name="Made Up Pub", place_type="venue",
                                              source="my_list", dog_rule="yes", distance_miles=0),
                                                                              fake_weather(62),  "UNSAFE",  "isn't on the owner's trusted list"),
    ("Trusted list: entry has a typo",   plan(place_name="Typo Cafe", place_type="venue",
                                              source="my_list", dog_rule="yes", distance_miles=0),
                                                                              fake_weather(62),  "UNSAFE",  "data problem"),
    ("Trusted list: Claude moved it",    plan(place_name="Brewmasters Taproom", place_type="venue",
                                              source="my_list", dog_rule="yes", distance_miles=0,
                                              latitude=47.7),
                                                                              fake_weather(62),  "UNSAFE",  "doesn't match"),
    ("Today, start time already passed", plan(date="2026-10-13", start_time="07:00"),
                                                                              fake_weather(62),  "UNSAFE",  "already passed (it's now 08:00)"),
    ("Today, start time still ahead",    plan(date="2026-10-13", start_time="09:00"),
                                                                              fake_weather(62),  "SAFE",    None),
    ("Date already passed",              plan(date="2026-10-01"),             fake_weather(62),  "UNSAFE",  "already passed"),
    ("Too far from home (Portland)",     plan(latitude=45.52, longitude=-122.68),
                                                                              fake_weather(62),  "UNSAFE",  "drive limit"),
    ("Within range (Mount Rainier)",     plan(latitude=46.79, longitude=-121.74),
                                                                              fake_weather(62),  "SAFE",    None),
]


def main():
    passed = 0
    all_cases = ([case + (None,) for case in CASES] + MEMORY_CASES
                 + [case + (None,) for case in EVENT_CASES])
    for description, test_plan, weather, expected, must_mention, history in all_cases:
        result = evaluate_plan(test_plan, weather, PROFILE, history, VERIFIED, TODAY,
                               trusted=TRUSTED, now=NOW)
        text = " ".join(result["problems"] + result["warnings"])
        ok = result["verdict"] == expected and (must_mention is None or must_mention in text)
        passed += ok
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}  {description:<36} expected {expected:<8} got {result['verdict']}")
        if not ok:
            print(f"      problems: {result['problems']}\n      warnings: {result['warnings']}")
    print(f"\n{passed}/{len(all_cases)} passed")


if __name__ == "__main__":
    main()
