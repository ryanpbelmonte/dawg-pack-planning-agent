"""
Stage 3: long-term memory.

The agent's short-term memory is the `messages` list in agent.py, which is
gone when you quit. Long-term memory is this: outings saved to memory.json,
which is still there next time.

Claude doesn't read the whole file. It asks for what it needs through the
`recall_outings` tool, the same way it asks for the weather. That keeps
requests small and cheap even after hundreds of outings.

Writing to memory goes through `save_outing`, and agent.py asks you to
confirm before anything is saved.
"""

import json
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
MEMORY_FILE = PROJECT_DIR / "memory.json"


def load_outings(path: Path = MEMORY_FILE) -> list:
    """Read all saved outings. Returns an empty list if there are none yet."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)["outings"]


def save_outing(outing: dict, path: Path = MEMORY_FILE) -> None:
    """Add one outing to the memory file."""
    outings = load_outings(path)
    outings.append(outing | {"saved_at": datetime.now().isoformat(timespec="seconds")})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"outings": outings}, f, indent=2)


def date_key(outing: dict) -> str:
    """Sort key for dates. "unknown" counts as the oldest possible date."""
    return "0000" if outing["date"] == "unknown" else outing["date"]


def same_place(a: str, b: str) -> bool:
    """Loose name match, so "Echo Peak" finds "Echo Peak Trail" and vice versa."""
    a, b = a.lower().strip(), b.lower().strip()
    return a in b or b in a


def recall_outings(place_name: str = "", outings: list | None = None) -> dict:
    """Look up past outings.

    With a place name: every visit to that place, newest first.
    Without one: a short summary of every place, best-rated first.
    """
    if outings is None:
        outings = load_outings()
    if not outings:
        return {"message": "No outings saved yet."}

    if place_name:
        visits = [o for o in outings if same_place(o["place_name"], place_name)]
        if not visits:
            return {"message": f"No saved outings at '{place_name}'. This would be a first visit."}
        visits.sort(key=date_key, reverse=True)
        return {"place": place_name, "visits": visits}

    # No place given: summarize by place instead of returning every entry.
    places = {}
    for o in outings:
        places.setdefault(o["place_name"], []).append(o)
    summary = []
    for name, visits in places.items():
        latest = max(visits, key=date_key)
        summary.append({
            "place_name": name,
            "visits": len(visits),
            "average_rating": round(sum(o["rating"] for o in visits) / len(visits), 1),
            "last_visit": latest["date"],
            "last_note": latest["note"],
        })
    summary.sort(key=lambda p: p["average_rating"], reverse=True)
    return {"places": summary}


def format_for_confirmation(outing: dict) -> str:
    """How a pending save is shown to you before you approve it."""
    stars = "★" * outing["rating"] + "☆" * (5 - outing["rating"])
    when = outing["date"] if outing["time"] == "unknown" else f"{outing['date']} {outing['time']}"
    return (
        f"  {outing['place_name']} · {when} · {stars} · crowd: {outing['crowd_level']}\n"
        f"  Thea: {outing['thea']}\n"
        f"  Nico: {outing['nico']}\n"
        f"  Note: {outing['note']}"
    )


# ---------------------------------------------------------------------------
# Tool definitions: what Claude sees
# ---------------------------------------------------------------------------

RECALL_OUTINGS_TOOL = {
    "name": "recall_outings",
    "description": (
        "Look up the owner's past outings with Thea and Nico. Call it with no "
        "place_name to get a summary of every place they've been (rating, last "
        "visit, last note), which is useful before suggesting places. Call it "
        "with a place_name to see every visit to that place. Use this before "
        "recommending, and prefer well-rated places. If a place went badly, "
        "say why and suggest a fix or an alternative."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "place_name": {
                "type": "string",
                "description": "Place to look up. Leave out to summarize all places.",
            },
        },
    },
}

SAVE_OUTING_TOOL = {
    "name": "save_outing",
    "description": (
        "Save a finished outing to long-term memory. Use it when the owner "
        "tells you how an outing went. The owner will be asked to confirm "
        "before it is saved. If they haven't given a star rating, ask for one "
        "first. Use 'unknown' for anything they didn't mention, rather than "
        "guessing."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "place_name": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "time": {"type": "string", "description": "24-hour HH:MM, or 'unknown'"},
            "rating": {"type": "integer", "enum": [1, 2, 3, 4, 5],
                       "description": "Owner's rating, 1-5 stars"},
            "crowd_level": {"type": "string",
                            "enum": ["quiet", "moderate", "busy", "unknown"]},
            "thea": {"type": "string", "description": "How Thea did, in a few words"},
            "nico": {"type": "string", "description": "How Nico did, in a few words"},
            "note": {"type": "string", "description": "Anything else worth remembering"},
        },
        "required": ["place_name", "date", "time", "rating", "crowd_level",
                     "thea", "nico", "note"],
        "additionalProperties": False,
    },
}
