"""
The agent's tools.

Each tool has two parts:
  1. A plain Python function that does the real work (calls a free web API).
  2. A "tool definition" (name, description, input schema) that tells Claude
     what the tool does and what inputs it takes.

Claude never runs these functions itself. It *asks* to use a tool, and our
code in agent.py runs the function and sends the result back.

All three APIs used here are free and need no API key:
  - Open-Meteo geocoding  (place name -> latitude/longitude)
  - Open-Meteo forecast   (weather)
  - OpenStreetMap Overpass (parks, trails, dog parks)
"""

import json
import math
import time

import requests

# Identify ourselves politely to the free APIs.
HEADERS = {"User-Agent": "DawgPackPlanner/0.1 (learning project)"}

# Public OpenStreetMap search servers, tried in order.
OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


# ---------------------------------------------------------------------------
# Tool 1: find a place's coordinates
# ---------------------------------------------------------------------------

def geocode_location(place_name: str) -> dict:
    """Turn a place name like 'Boulder' into latitude/longitude.

    Returns up to 5 matches so Claude can pick the right one
    (there are a lot of towns named Springfield).
    """
    # Open-Meteo matches on the town name only, so drop anything after a comma
    # ("Austin, TX" -> "Austin"). The state is still shown in each match.
    name = place_name.split(",")[0].strip()
    response = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": name, "count": 5, "language": "en", "format": "json"},
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        return {"error": f"No places found matching '{place_name}'."}

    return {
        "matches": [
            {
                "name": r.get("name"),
                "state_or_region": r.get("admin1"),
                "country": r.get("country"),
                "latitude": r.get("latitude"),
                "longitude": r.get("longitude"),
            }
            for r in results
        ]
    }


# ---------------------------------------------------------------------------
# Tool 2: weather forecast
# ---------------------------------------------------------------------------

def get_weather_forecast(latitude: float, longitude: float, date: str) -> dict:
    """Get an hourly forecast (6am-9pm) for one day, in Fahrenheit.

    `date` is YYYY-MM-DD and must be within the next 7 days.
    """
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,apparent_temperature,"
                      "precipitation_probability,uv_index,cloud_cover",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "start_date": date,
            "end_date": date,
        },
        headers=HEADERS,
        timeout=15,
    )
    if response.status_code == 400:
        # Usually means the date is too far out or badly formatted.
        return {"error": response.json().get("reason", "Invalid request.")}
    response.raise_for_status()
    hourly = response.json()["hourly"]

    hours = []
    for i, timestamp in enumerate(hourly["time"]):
        hour = int(timestamp[11:13])
        if 6 <= hour <= 21:  # daytime only - keeps the result short
            hours.append({
                "time": timestamp[11:16],
                "temp_f": hourly["temperature_2m"][i],
                "feels_like_f": hourly["apparent_temperature"][i],
                "rain_chance_pct": hourly["precipitation_probability"][i],
                "uv_index": hourly["uv_index"][i],
                "cloud_cover_pct": hourly["cloud_cover"][i],
            })
    return {"date": date, "hourly": hours}


# ---------------------------------------------------------------------------
# Tool 3: dog-friendly places from OpenStreetMap
# ---------------------------------------------------------------------------

def miles_between(lat1, lon1, lat2, lon2) -> float:
    """Straight-line distance between two points (haversine formula)."""
    r = 3958.8  # Earth's radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _run_overpass(query: str) -> list | None:
    """Send a query to the OpenStreetMap servers. Returns the map elements,
    or None if every server failed.

    The free Overpass servers are often busy, so try each one in turn.
    If they all fail, wait a few seconds and go around once more.
    """
    for attempt in range(2):
        if attempt > 0:
            time.sleep(5)
        for server in OVERPASS_SERVERS:
            try:
                response = requests.post(server, data={"data": query},
                                         headers=HEADERS, timeout=60)
            except requests.RequestException:
                continue
            if response.status_code == 200:
                return response.json().get("elements", [])
    return None


def find_dog_friendly_places(latitude: float, longitude: float,
                             radius_miles: float = 15) -> dict:
    """Search OpenStreetMap for dog parks and for parks/trails tagged as
    allowing dogs, within `radius_miles` of a point.

    OpenStreetMap is volunteer-mapped, so tags can be missing or out of date.
    """
    radius_m = int(min(radius_miles, 30) * 1609)  # cap the search size
    around = f"(around:{radius_m},{latitude},{longitude})"
    # Overpass query language: find named dog parks, plus named parks,
    # nature reserves and paths whose "dog" tag is yes or leashed.
    query = f"""
    [out:json][timeout:50];
    (
      nwr["leisure"="dog_park"]{around};
      nwr["leisure"~"^(park|nature_reserve)$"]["dog"~"^(yes|leashed)$"]["name"]{around};
      way["highway"~"^(path|footway|track)$"]["dog"~"^(yes|leashed)$"]["name"]{around};
      relation["route"="hiking"]["dog"~"^(yes|leashed)$"]{around};
    );
    out center tags 60;
    """
    elements = _run_overpass(query)
    if elements is None:
        return {"error": "The map servers are busy. Try again in a few minutes."}

    places = {}
    for element in elements:
        tags = element.get("tags", {})
        # Nodes have lat/lon directly; ways/relations have a "center".
        lat = element.get("lat") or element.get("center", {}).get("lat")
        lon = element.get("lon") or element.get("center", {}).get("lon")
        if lat is None:
            continue

        kind = tags.get("leisure") or tags.get("highway") or tags.get("route")
        name = tags.get("name") or ("Unnamed dog park" if kind == "dog_park" else None)
        if not name:
            continue

        place = {
            "name": name,
            "type": kind,
            "dog_rule": tags.get("dog", "dog park" if kind == "dog_park" else "unknown"),
            "distance_miles": round(miles_between(latitude, longitude, lat, lon), 1),
            "has_drinking_water": tags.get("drinking_water", "unknown"),
            "surface": tags.get("surface", "unknown"),
            "opening_hours": tags.get("opening_hours", "unknown"),
            "latitude": round(lat, 5),
            "longitude": round(lon, 5),
        }
        # Long trails are split into many map pieces with the same name.
        # Keep only the closest piece of each one.
        key = (name, kind)
        if key not in places or place["distance_miles"] < places[key]["distance_miles"]:
            places[key] = place

    results = sorted(places.values(), key=lambda p: p["distance_miles"])[:25]
    if not results:
        return {"error": "No dog-friendly places found. Try a larger radius."}
    return {"count": len(results), "places": results}


# ---------------------------------------------------------------------------
# Tool 4 (Stage 4): dog-friendly venues from OpenStreetMap
# ---------------------------------------------------------------------------

def find_dog_friendly_venues(latitude: float, longitude: float,
                             radius_miles: float = 15) -> dict:
    """Search OpenStreetMap for breweries, pubs, cafes and restaurants tagged
    as allowing dogs (inside, or outside on a patio), plus pet stores.

    These are venues, not events: they don't say what's happening when.
    """
    radius_m = int(min(radius_miles, 30) * 1609)
    around = f"(around:{radius_m},{latitude},{longitude})"
    query = f"""
    [out:json][timeout:50];
    (
      nwr["amenity"~"^(pub|bar|biergarten|cafe|restaurant)$"]["dog"~"^(yes|leashed|outside)$"]["name"]{around};
      nwr["craft"="brewery"]["dog"~"^(yes|leashed|outside)$"]["name"]{around};
      nwr["shop"="pet"]["name"]{around};
    );
    out center tags 60;
    """
    elements = _run_overpass(query)
    if elements is None:
        return {"error": "The map servers are busy. Try again in a few minutes."}

    venues = []
    for element in elements:
        tags = element.get("tags", {})
        lat = element.get("lat") or element.get("center", {}).get("lat")
        lon = element.get("lon") or element.get("center", {}).get("lon")
        if lat is None:
            continue
        dog = tags.get("dog", "unknown")
        venues.append({
            "name": tags["name"],
            "type": tags.get("craft") or tags.get("amenity") or tags.get("shop"),
            # "outside" means dogs on the patio only, not indoors.
            "dog_rule": "patio only" if dog == "outside" else dog,
            "outdoor_seating": tags.get("outdoor_seating", "unknown"),
            "opening_hours": tags.get("opening_hours", "unknown"),
            "distance_miles": round(miles_between(latitude, longitude, lat, lon), 1),
            "latitude": round(lat, 5),
            "longitude": round(lon, 5),
        })

    # Pet stores are common and would crowd out the patios and breweries,
    # so list food and drink first, then the 5 closest pet stores.
    venues.sort(key=lambda v: v["distance_miles"])
    food_and_drink = [v for v in venues if v["type"] != "pet"][:20]
    pet_stores = [v for v in venues if v["type"] == "pet"][:5]
    results = food_and_drink + pet_stores
    if not results:
        return {"error": "No dog-friendly venues found. Try a larger radius."}
    return {"count": len(results), "venues": results}


# ---------------------------------------------------------------------------
# Tool definitions: what Claude sees
# ---------------------------------------------------------------------------
# The descriptions matter a lot - they are how Claude decides which tool
# to use and when. Treat them like instructions to a new coworker.

TOOL_DEFINITIONS = [
    {
        "name": "geocode_location",
        "description": (
            "Look up the latitude and longitude of a town, city or landmark. "
            "Use this first whenever the user names a place, because the other "
            "tools need coordinates. Returns several matches; pick the one in "
            "the right state or region, or ask the user if it's unclear."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "place_name": {
                    "type": "string",
                    "description": "Place to look up, e.g. 'Asheville' or 'Boulder, CO'.",
                },
            },
            "required": ["place_name"],
        },
    },
    {
        "name": "get_weather_forecast",
        "description": (
            "Get the hourly weather forecast (6am-9pm, Fahrenheit) for one day "
            "at a location, including feels-like temperature, rain chance and "
            "UV index. Only works for today through the next 6 days. Use it to "
            "decide whether it's safe for the dogs and what time of day is best."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "date": {"type": "string", "description": "Date as YYYY-MM-DD."},
            },
            "required": ["latitude", "longitude", "date"],
        },
    },
    {
        "name": "find_dog_friendly_places",
        "description": (
            "Search OpenStreetMap for dog parks, and for parks, nature reserves "
            "and trails tagged as allowing dogs ('yes' or 'leashed'), near a "
            "point. Returns names, distance, dog rule, water and surface info "
            "where known. Map data is volunteer-made and often incomplete, so "
            "treat 'unknown' as unknown, not as 'no'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "radius_miles": {
                    "type": "number",
                    "description": "Search radius in miles (default 15, max 30).",
                },
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "find_dog_friendly_venues",
        "description": (
            "Search OpenStreetMap for breweries, pubs, cafes and restaurants "
            "tagged as allowing dogs, plus pet stores, near a point. dog_rule "
            "'patio only' means dogs are allowed outside but not indoors. These "
            "are places, not events: they don't tell you what's happening on a "
            "given day. Map data is often incomplete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "radius_miles": {
                    "type": "number",
                    "description": "Search radius in miles (default 15, max 30).",
                },
            },
            "required": ["latitude", "longitude"],
        },
    },
]

# Maps each tool name to the Python function that runs it.
TOOL_FUNCTIONS = {
    "geocode_location": geocode_location,
    "get_weather_forecast": get_weather_forecast,
    "find_dog_friendly_places": find_dog_friendly_places,
    "find_dog_friendly_venues": find_dog_friendly_venues,
}


def run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """Run the tool Claude asked for. Returns (result_as_text, is_error)."""
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return f"Unknown tool: {name}", True
    try:
        result = function(**tool_input)
    except requests.RequestException as e:
        # Network problems shouldn't crash the agent - tell Claude instead,
        # so it can retry or explain the problem to the user.
        return f"Network error calling {name}: {e}", True
    return json.dumps(result), "error" in result
