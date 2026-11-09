"""Everything Pulsar asks Claude, in one list: what each use does, what it sends to Anthropic, the model it runs and
the most one run may cost. Settings > Claude shows the list, one row per use, with a model to choose for each use
that runs Claude itself (the setting `claude_models`, {use: model}). Every `claude -p` call takes its model and its
cap from here; how it is isolated (no tools, no settings, the JSON schema, the guards) stays at the call.

To add a use: one entry in USES; in the server, `claudeuses.model("<id>", settings)` for its model; at the call,
`model = claudeuses.chosen("<id>", model)`, `"--max-budget-usd", str(claudeuses.cap("<id>", model))` and
`askclaude._answer(done, what, "<id>", model)` (which counts what the run cost). The page lists it by itself.

Other runs of the claude command ask no model: `claude mcp add`, `remove` and `get` (claudelink.py: the switch adds
Pulsar to Claude Code) and `claude mcp get productive` (productive.py: Claude Code renews its Productive sign-in).
"""

from __future__ import annotations

import threading

MODELS = ("haiku", "sonnet", "opus")  # Claude Code's own names: always the latest model of each, never a pinned one
# What one run may cost against Haiku. Sonnet and Opus cost more for the same words, so a cap that fits Haiku would
# stop them before they answer: a use's cap is its Haiku cap times this (Ask Claude: $0.25, $0.75, $2).
SCALE = {"haiku": 1, "sonnet": 3, "opus": 8}

# id -> name, what it does and what it sends to Anthropic (a sentence each, shown as written), its default model
# (None: it runs in the person's own Claude Code session, with that session's model) and its cap with Haiku, in
# dollars of the person's Claude usage per run
USES = {
    "ask": {
        "name": "Check my day: task picks",
        "does": "A task and a note for each row Log the day is unsure of, before the review.",
        "sends": "Sends each block's time, repository and session titles, what you typed and Claude's replies, your "
                 "commit subjects, the tasks Pulsar would pick for it and a few of your notes, and your tasks of the "
                 "last 30 days. May search Productive, "
                 "read only. A day's answers are kept on this Mac for 60 days.",
        "model": "sonnet", "cap": 0.25,
    },
    "note": {
        "name": "Note",
        "does": "Drafts the note for time you log, in the log dialog or a row of Log the day.",
        "sends": "Sends that work's time and repository, what you typed and Claude's replies, your commit subjects "
                 "and a few of your notes.",
        "model": "sonnet", "cap": 0.1,  # since 1.6.12: as fast as Haiku on one day's 15 notes, fewer left empty
    },
    "task": {
        "name": "New task",
        "does": "Drafts the title and description of a task you create.",
        "sends": "Sends that work's time, repository and session titles, what you typed and Claude's replies, your "
                 "commit subjects and the names of your recent tasks.",
        "model": "sonnet", "cap": 0.1,  # Sonnet by default since 1.6.12
    },
    "review": {
        "name": "Check my day: review",
        "does": "Checks Log the day's plan with FI's rules; you accept or reject each suggestion.",
        "sends": "Sends a digest of the day: the plan, what you typed and Claude's replies (cut short), session titles, "
                 "commit subjects, meeting titles (not private or declined ones), app names by hour, the notes of entries already logged, and your "
                 "tasks of the last 30 days. Never window titles, sites or addresses. May search Productive, read only.",
        "model": "sonnet", "cap": 0.25,  # since 1.6.12: on the replay a little righter than Haiku, and faster
    },
    "session": {
        "name": "Pulsar in Claude Code",
        "does": "Say “log the time of this session” in Claude Code: it reads your day from Pulsar and asks "
                "you before each write.",
        "sends": "The session sees what it reads from Pulsar: your entries' times, projects and tasks, unlogged time "
                 "and note drafts.",
        "model": None, "cap": None,
    },
}


def runs_claude(use: str) -> bool:
    """Whether Pulsar runs Claude itself for this use, so a model can be chosen for it."""
    return bool(USES.get(use, {}).get("model"))


def chosen(use: str, model=None) -> str:
    """The model a call runs: the one it was given when that is one of MODELS, else the use's default."""
    return model if model in MODELS else USES[use]["model"]


def model(use: str, settings: dict) -> str:
    """The model `use` runs with: the person's choice in Settings, else (Ask Claude only) the choice Pulsar kept before
    there was one per use (`ask_claude_model`), else the default. A value that is not one of MODELS (a file changed by
    hand, a newer Pulsar's name) counts as no choice."""
    picked = settings.get("claude_models")
    picked = picked.get(use) if isinstance(picked, dict) else None
    if picked in MODELS:
        return picked
    if use == "ask" and settings.get("ask_claude_model") in MODELS:
        return settings["ask_claude_model"]
    return USES[use]["model"]


def cap(use: str, model=None) -> float:
    """The most one run of `use` with `model` may cost, in dollars (--max-budget-usd)."""
    return round(USES[use]["cap"] * SCALE[chosen(use, model)], 2)


def clean(choices) -> dict:
    """The models chosen in Settings, as kept: uses that run Claude, names in MODELS, and only those that are not the
    default (so a default changed later applies). Anything else is dropped, so that use keeps its default."""
    if not isinstance(choices, dict):
        raise ValueError("The models are a list of uses and models.")
    return {k: v for k, v in choices.items() if runs_claude(k) and v in MODELS and v != USES[k]["model"]}


_spent: dict = {}  # use -> (runs, dollars) since Pulsar started, as Claude Code said each run cost
_lock = threading.Lock()


def spent(use: str, cost) -> None:
    """Count one run of `use` and what Claude Code said it cost (its total_cost_usd), when it said."""
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
        with _lock:
            runs, dollars = _spent.get(use, (0, 0.0))
            _spent[use] = (runs + 1, dollars + cost)


def public(settings: dict) -> list:
    """The list for Settings, in order: each use with its words, the model it runs now, its default, its cap with each
    model, and its runs and cost since Pulsar started."""
    out = []
    for use, u in USES.items():
        runs, dollars = _spent.get(use, (0, 0.0))
        row = {"id": use, "name": u["name"], "does": u["does"], "sends": u["sends"], "model": None, "default": None}
        if runs_claude(use):
            row.update(model=model(use, settings), default=u["model"], caps={m: cap(use, m) for m in MODELS},
                       runs=runs, spent=round(dollars, 4))
        out.append(row)
    return out
