"""
Pack Planner - an agent that plans dog-friendly outings.

Stage 1: the agent loop.
  1. Send the conversation + the list of tools to Claude.
  2. If Claude answers with a tool request, run that tool and send the result back.
  3. Repeat until Claude gives a final answer instead of asking for a tool.

Stage 2: guardrails.
  Claude must submit each plan with the `submit_plan` tool. Code in
  guardrails.py checks it against the limits in dogs.json, and unsafe plans
  are sent back for revision. Required safety warnings are added by code,
  so they always appear no matter how Claude words its answer.

Run it with:   C:\\Users\\ryanb\\.venvs\\pack-planner\\Scripts\\python agent.py
"""

import json
from datetime import date

import anthropic

import guardrails
from tools import TOOL_DEFINITIONS, run_tool

MODEL = "claude-haiku-5-5"
MAX_TOOL_ROUNDS = 15  # safety limit so a confused agent can't loop forever

PROFILE = guardrails.load_profile()
ALL_TOOLS = TOOL_DEFINITIONS + [guardrails.SUBMIT_PLAN_TOOL]


def build_system_prompt(profile: dict) -> str:
    """The agent's job description, filled in from dogs.json."""
    dogs = "\n".join(
        f"- {d['name']} ({d['sex']}, {d['breed']}). {d['notes']}".strip()
        for d in profile["dogs"]
    )
    dist = profile["distance_miles"]
    return f"""You are Pack Planner. You plan outings for a person and their two dogs. \
Today is {date.today().strftime('%A, %Y-%m-%d')}.

The dogs:
{dogs}
Both are double-coated breeds that overheat easily. Comfortable hike distance: \
{dist['comfortable_min']}-{dist['comfortable_max']} miles.

How to work:
- Use your tools to look things up. Don't guess about places or weather.
- Typical flow: find the location's coordinates, search for dog-friendly places, \
check the weather, then submit each option with submit_plan before recommending it.
- Never recommend a plan that submit_plan rejected. Revise it and resubmit.
- For hikes, prefer quieter trails and quieter times if a dog above is reactive. \
For dog parks, walks in town and events, crowds are fine.
- Map data is often incomplete. Don't claim a place allows dogs unless the data says so.

Answer with: a top pick, one or two backups, and the best time to go. Keep it short. \
Don't list the safety warnings yourself: the app adds them automatically."""


SYSTEM_PROMPT = build_system_prompt(PROFILE)
client = anthropic.Anthropic()  # reads your ANTHROPIC_API_KEY automatically


def ask_claude(messages: list):
    """Send one request to Claude."""
    extra = {}
    if "haiku" not in MODEL:
        # If Claude's safety filter declines a request, automatically retry on
        # a recommended backup model. (Haiku models don't support this.)
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    return client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=ALL_TOOLS,
        messages=messages,
        output_config={"effort": "medium"},
        **extra,
    )


def run_one_tool(name: str, tool_input: dict, approved: dict) -> tuple[str, bool]:
    """Run a tool. Safety checks go to guardrails.py; everything else to tools.py."""
    if name != "submit_plan":
        return run_tool(name, tool_input)

    result = guardrails.check_plan(**tool_input)
    print(f"  [guardrail] {tool_input['place_name']}: {result['verdict']}")
    if result["verdict"] != "UNSAFE":
        # Remember approved plans so we can attach their warnings at the end.
        approved[tool_input["place_name"]] = result["warnings"]
    return json.dumps(result), False


def add_safety_notes(answer: str, approved: dict) -> str:
    """Attach the required warnings. Done in code so they can't be skipped."""
    if not approved:
        return answer + "\n\n(Note: no plan in this answer was safety-checked.)"

    lines = ["", "", "Safety notes (checked automatically):"]
    shown = set()
    for place, warnings in approved.items():
        for warning in warnings:
            if warning not in shown:  # skip repeats across places
                lines.append(f"  - {warning}")
                shown.add(warning)
    return answer + "\n".join(lines)


def run_agent_turn(messages: list) -> str:
    """Run the agent loop for one user question. Returns the final answer."""
    approved = {}  # place name -> warnings, for plans that passed this turn

    for _ in range(MAX_TOOL_ROUNDS):
        response = ask_claude(messages)

        # Always keep Claude's full reply in the history, including its tool
        # requests - Claude needs to see what it asked for last time.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            return "Sorry, I can't help with that request."

        if response.stop_reason != "tool_use":
            # No more tools needed - this is the final answer.
            answer = "".join(b.text for b in response.content if b.type == "text")
            return add_safety_notes(answer, approved)

        # Claude asked for one or more tools. Run each one.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"  [tool] {block.name}({block.input})")
            result, is_error = run_one_tool(block.name, block.input, approved)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,  # links this result to Claude's request
                "content": result,
                "is_error": is_error,
            })

        # Send all results back together, in one message.
        messages.append({"role": "user", "content": tool_results})

    return "I hit my limit of tool calls for one question. Try asking more specifically."


def main():
    names = " and ".join(d["name"] for d in PROFILE["dogs"])
    print(f"Pack Planner - where should we take {names}? (type 'quit' to exit)\n")
    messages = []  # the whole conversation, so follow-up questions work
    while True:
        question = input("You: ").strip()
        if question.lower() in ("quit", "exit", "q"):
            break
        if not question:
            continue
        turn_start = len(messages)  # so we can undo a failed turn
        messages.append({"role": "user", "content": question})
        try:
            answer = run_agent_turn(messages)
        except anthropic.AuthenticationError:
            print("Your API key is missing or invalid. See README.md, step 4.")
            break
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            print(f"Couldn't get an answer from Claude: {e}")
            del messages[turn_start:]  # forget the half-finished turn
            continue
        print(f"\nPack Planner: {answer}\n")


if __name__ == "__main__":
    main()
