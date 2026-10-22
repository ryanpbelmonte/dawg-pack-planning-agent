"""
Tests for memory.py (Stage 3).

Free and offline. Uses a temporary file, so your real memory.json is never
touched.

Run it with:   C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python test_memory.py
"""

import sys
import tempfile
from pathlib import Path

from memory import load_outings, recall_outings, save_outing


def outing(place, rating, day, note=""):
    return {"place_name": place, "date": day, "time": "09:00", "rating": rating,
            "crowd_level": "quiet", "thea": "fine", "nico": "fine", "note": note}


def main():
    results = []

    def check(description, ok):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {description}")

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "memory.json"

        check("Empty memory loads as an empty list", load_outings(path) == [])

        save_outing(outing("Cedar River Dog Park", 5, "2026-10-11"), path)
        save_outing(outing("Douglas Fir Trail", 2, "2026-10-17", "Too crowded"), path)
        save_outing(outing("Cedar River Dog Park", 3, "2026-10-18"), path)
        saved = load_outings(path)
        check("Saved outings survive a reload", len(saved) == 3)
        check("Each save is time-stamped", all("saved_at" in o for o in saved))

        visits = recall_outings("cedar river", saved)["visits"]
        check("Recall by place finds both visits (case-insensitive)", len(visits) == 2)
        check("Visits are newest first", visits[0]["date"] == "2026-10-18")

        places = recall_outings("", saved)["places"]
        check("Summary has one row per place", len(places) == 2)
        check("Summary averages ratings", places[0]["average_rating"] == 4.0)
        check("Summary lists best-rated first", places[0]["place_name"] == "Cedar River Dog Park")

        check("Unknown place says it's a first visit",
              "first visit" in recall_outings("Nowhere Park", saved)["message"])

        undated = saved + [outing("Echo Peak Trail", 5, "unknown")]
        echo = recall_outings("Echo Peak", undated)["visits"]
        check("Undated outings still work", echo[0]["date"] == "unknown")
        mixed = recall_outings("", undated + [outing("Echo Peak Trail", 4, "2026-10-01")])
        echo_row = next(p for p in mixed["places"] if p["place_name"] == "Echo Peak Trail")
        check("A dated visit counts as newer than an undated one",
              echo_row["last_visit"] == "2026-10-01")

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)  # a non-zero exit tells GitHub Actions a test failed


if __name__ == "__main__":
    main()
