"""
Try the tools by themselves - no Claude, no API key, no cost.

Run it with:   .venv\\Scripts\\python test_tools.py "Your Town"

This is a good habit: make sure each tool works on its own before
handing it to an agent. If something breaks later, you'll know whether
the problem is the tool or the agent.
"""

import json
import sys
from datetime import date

from tools import run_tool

place = sys.argv[1] if len(sys.argv) > 1 else "Boulder"


def show(title, name, tool_input):
    print(f"\n=== {title} ===")
    text, is_error = run_tool(name, tool_input)
    result = json.loads(text) if not text.startswith(("Network", "Unknown")) else text
    print(json.dumps(result, indent=2)[:1500])  # first part only, to keep it readable
    return result, is_error


geo, err = show(f"1. Where is {place}?", "geocode_location", {"place_name": place})
if err:
    sys.exit("Stopping: couldn't find that place.")

first = geo["matches"][0]
lat, lon = first["latitude"], first["longitude"]
print(f"\nUsing: {first['name']}, {first['state_or_region']} ({lat}, {lon})")

show("2. Today's weather", "get_weather_forecast",
     {"latitude": lat, "longitude": lon, "date": date.today().isoformat()})

show("3. Dog-friendly places within 10 miles", "find_dog_friendly_places",
     {"latitude": lat, "longitude": lon, "radius_miles": 10})
