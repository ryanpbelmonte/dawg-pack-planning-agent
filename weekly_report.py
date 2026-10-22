"""
Stage 5: the weekly report - the agent running on its own.

Every Monday at 7 AM Pacific, GitHub Actions runs this file. It asks the
agent to plan the week, then emails the plan privately.

Running with nobody watching changes the rules:
  - No questions: no one is there to answer them.
  - No memory writes: saving needs a human's yes (Stage 3), so it's off.
  - Nothing private in the logs: this repo is public, and so are its run
    logs. The plan only ever goes into the email, never the log.

Try it on your own computer (prints the plan instead of emailing it):
    C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python weekly_report.py --print

Settings come from environment variables (GitHub Secrets in the cloud),
never from this file:
    ANTHROPIC_API_KEY          your Claude API key
    PACK_PLAN_EMAIL_FROM       the Gmail address that sends the plan
    PACK_PLAN_EMAIL_PASSWORD   a Gmail "app password" for that address
    PACK_PLAN_EMAIL_TO         where to send the plan
"""

import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import agent

PACIFIC = ZoneInfo("America/Los_Angeles")

# GitHub's schedule only understands UTC time, which doesn't follow daylight
# saving. So the workflow runs at both 13:37 and 14:37 UTC on Mondays, and
# this picks whichever one is 6:37 AM in Pacific time this week. (A bit before
# 7 AM, at an off-peak minute, because GitHub often starts top-of-the-hour
# runs late.) These must match the cron lines in the workflow file exactly.
CRON_FOR_PACIFIC_OFFSET = {
    -7: "37 13 * * 1",  # Pacific Daylight Time (March-November)
    -8: "37 14 * * 1",  # Pacific Standard Time (November-March)
}


def is_the_right_scheduled_run() -> bool:
    """For scheduled runs, only continue for the one that's 7 AM Pacific."""
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return True  # started by hand: always run
    offset_hours = datetime.now(PACIFIC).utcoffset().total_seconds() / 3600
    return os.environ.get("SCHEDULE_CRON") == CRON_FOR_PACIFIC_OFFSET[int(offset_hours)]


def build_request(start: date) -> str:
    end = start + timedelta(days=6)
    home = agent.PROFILE["home_base"]["name"]
    return f"""This is the automated weekly plan. It runs unattended: no one can answer \
questions, so don't ask any. Make reasonable choices instead.

Plan the week of {start:%A %B %d} to {end:%A %B %d} for Thea and Nico near {home}:
1. Two or three outings across the week, such as a hike or park walk and a dog park \
visit. Pick the best day and time for each based on the weather.
2. One brewery, patio or event. Prefer the owner's trusted list, and include any \
dog-friendly festival, market or training class you find this week.

Submit every option with submit_plan, and only include options that pass. Then write \
the plan as a short, friendly email organized by day, saying where each suggestion \
came from.

Format: this goes in a plain-text email. Don't write a subject line (the email \
already has one). Don't use markdown such as ** or #; use day names as headings and \
"- " for bullet points."""


def make_plan() -> str:
    """Run the agent in report mode and return the finished plan."""
    agent.SHOW_TOOL_CALLS = False  # keep place names out of the (public) logs
    # No memory writes when no one is present to approve them.
    agent.ALL_TOOLS = [t for t in agent.ALL_TOOLS if t["name"] != "save_outing"]
    agent.confirm_and_save = lambda outing: ("Saving is turned off in the weekly report.", True)

    messages = [{"role": "user", "content": build_request(date.today())}]
    return agent.run_agent_turn(messages)


def send_email(plan: str, start: date) -> None:
    """Email the plan. Every address and password comes from the environment."""
    message = EmailMessage()
    message["Subject"] = f"Pack Plan for Thea and Nico: week of {start:%B %d}"
    message["From"] = os.environ["PACK_PLAN_EMAIL_FROM"]
    message["To"] = os.environ["PACK_PLAN_EMAIL_TO"]
    message.set_content(
        plan + "\n\n--\nSent automatically by Dawg Pack Planner every Monday at 7 AM. "
        "Check each place's hours and dog rules before you go."
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
        server.login(os.environ["PACK_PLAN_EMAIL_FROM"], os.environ["PACK_PLAN_EMAIL_PASSWORD"])
        server.send_message(message)


def main() -> int:
    print_only = "--print" in sys.argv

    if not is_the_right_scheduled_run():
        print("Not 7 AM Pacific for this scheduled run (daylight saving check). Skipping.")
        return 0

    if not print_only:
        missing = [name for name in ("ANTHROPIC_API_KEY", "PACK_PLAN_EMAIL_FROM",
                                     "PACK_PLAN_EMAIL_PASSWORD", "PACK_PLAN_EMAIL_TO")
                   if not os.environ.get(name)]
        if missing:
            # Name the missing settings, never their values.
            print(f"Missing settings: {', '.join(missing)}. Add them as GitHub Secrets.")
            return 1

    start = date.today()
    try:
        plan = make_plan()
        if print_only:
            print(plan)
            return 0
        send_email(plan, start)
    except Exception as e:
        # Only the kind of error goes in the log, not its details, which
        # could include parts of the plan or the email conversation.
        print(f"Weekly plan failed: {type(e).__name__}. Run it locally with --print to debug.")
        return 1

    print(f"Weekly plan for the week of {start:%B %d} was emailed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
