"""Ask Claude which Productive task each Likely row of a day belongs to: the button in the Likely column, off until
switched on in Settings.

It runs the person's own Claude Code (`claude -p`), with their own subscription, and gives it, for each row of at
least ten minutes, the time, the repository, the session titles and what they typed then; and the tasks they logged
to in the last 30 days. Claude may search Productive through the person's own Productive connection, read only: the
tools that change anything are refused, and no other tool is allowed. Nothing is written anywhere; the session is not
kept (so it never shows up in the Likely column itself).

On one person's days the picks read right (an estimate to the estimations task, a database upgrade to the client's
"Database Upgrade" task, a personal project to none), for about $0.13 and 15 seconds a day.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import threading
from datetime import datetime, timedelta

from . import config, productive, sessions

MODELS = ("sonnet", "haiku", "opus")  # Claude Code's aliases: always the latest model of each
BUDGET = {"haiku": 0.25, "sonnet": 0.75, "opus": 2.0}  # dollars one day's question may cost at most (--max-budget-usd)
# The answer's shape, checked by Claude Code itself (--json-schema): it comes back in "structured_output"
SCHEMA = {"type": "object", "required": ["picks"], "properties": {"picks": {"type": "array", "items": {
    "type": "object", "required": ["id", "tasks", "why"],
    "properties": {"id": {"type": "integer"}, "tasks": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                   "why": {"type": "string"}}}}}}
TIMEOUT = 240
READ_TOOLS = ["mcp__productive__search_records", "mcp__productive__query_resources", "mcp__productive__load_resource",
              "mcp__productive__describe_resource"]
WRITE_TOOLS = ["mcp__productive__create_resource", "mcp__productive__update_resource",
               "mcp__productive__delete_resource", "mcp__productive__perform_action"]
PROMPT = """You help a person at their company log time in Productive. Below are blocks of their work on {date}, from \
their Claude Code sessions: the time, the git repository, the session titles and what they typed.
For each block, find the Productive task the work belongs to. First look in YOUR TASKS: the tasks this person logged \
time to in the last 30 days, which are the ones they use now (estimates and scoping go to their estimations task, \
meetings to their meeting task, and so on). Only when none of them fits, use the Productive tools to search tasks by \
the words of the work (client, site, feature, ticket number), preferring open tasks in active projects. A personal \
project with no client gets no task. Read only: never create or change anything.
Answer with JSON only, as the last thing in your reply: \
{{"picks": [{{"id": <block id>, "tasks": ["<best task id>", "<second>", "<third>"], "why": "<at most 8 words>"}}]}}

BLOCKS:
{blocks}

YOUR TASKS (id, task, project, minutes in the last 30 days, last day):
{tasks}"""

_cache: dict = {}  # (date, rows signature) -> answer
_lock = threading.Lock()


class AskError(Exception):
    pass


def available() -> bool:
    return productive._claude_binary() is not None


def typed(t0: float, titles: list, a: float, b: float, projects_dir: str = sessions.PROJECTS_DIR) -> list:
    """What the person typed, between minutes a and b of the day, in the sessions with these titles (at most 15
    messages of 400 characters)."""
    keys = {k for k in (sessions.norm_title(t) for t in titles) if len(k) >= 5}
    if not keys:
        return []
    out = []
    for path in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < t0 - 3600:
                continue
            s = sessions.read_session(path, t0, t0 + 86400)
        except OSError:
            continue
        if not s:
            continue
        names = s["titles"] | {sessions.norm_title(s["title"])}
        if any(t.startswith(k) or k.startswith(t) for k in keys for t in names if t):
            out += [(t, x) for t, x in s["prompts"] if t0 + a * 60 - 300 <= t <= t0 + b * 60]
    return [x[:400] for _, x in sorted(out)][:15]


def recent_tasks(entries: list) -> list:
    mine: dict = {}
    for x in entries:
        if x.get("taskId"):
            t = mine.setdefault(x["taskId"], [x["taskId"], x.get("task") or "", x.get("project") or "", 0, ""])
            t[3] += x.get("minutes") or 0
            t[4] = max(t[4], x.get("date") or "")
    return sorted(mine.values(), key=lambda t: t[4], reverse=True)[:150]


def ask(date: str, rows: list, t0: float, entries_30: list, run=subprocess.run, model: str = "sonnet") -> dict:
    """{"picks": {"<s>|<project>": {"tasks": [{taskId, title, project}], "why"}}, "cost": dollars}. Raises AskError
    with a sentence for the page when Claude Code is missing or its answer cannot be read."""
    blocks = [{"id": i, "time": "%02d:%02d-%02d:%02d" % (r["s"] // 60, r["s"] % 60, (r["e"] // 60) % 24, r["e"] % 60),
               "repository": r.get("project") or "", "session_titles": (r.get("titles") or [])[:3],
               "typed": typed(t0, r.get("titles") or [], r["s"], r["e"])}
              for i, r in enumerate(rows) if r["e"] - r["s"] >= 10]
    if not blocks:
        return {"picks": {}, "cost": 0}
    model = model if model in MODELS else "sonnet"
    sig = (date, model, tuple((r["s"], r["e"], r.get("project")) for r in rows))
    with _lock:
        if sig in _cache:
            return _cache[sig]
    mine = recent_tasks(entries_30)
    prompt = PROMPT.format(date=date, blocks=json.dumps(blocks, ensure_ascii=False, indent=1),
                           tasks="\n".join(json.dumps(t, ensure_ascii=False) for t in mine))
    binary = productive._claude_binary()
    if not binary:
        raise AskError("Claude Code was not found on this Mac.")
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    try:
        done = run([binary, "-p", "--model", model, "--no-session-persistence", "--output-format", "json",
                    "--json-schema", json.dumps(SCHEMA), "--max-budget-usd", str(BUDGET[model]),
                    "--tools", "", "--allowedTools", ",".join(READ_TOOLS), "--disallowedTools", ",".join(WRITE_TOOLS)],
                   input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd=config.CACHE_DIR)
    except subprocess.TimeoutExpired:
        raise AskError("Claude took too long to answer. Try again later.") from None
    except OSError as exc:
        raise AskError(f"Claude Code could not be started ({exc}).") from None
    try:
        res = json.loads(done.stdout)
        if isinstance(res.get("structured_output"), dict):  # checked against SCHEMA by Claude Code
            picks = res["structured_output"]["picks"]
        else:  # an older Claude Code: the JSON at the end of its text
            text = str(res.get("result") or "")
            picks = json.loads(text[text.rindex('{"picks"'):text.rindex("}") + 1])["picks"]
    except (ValueError, KeyError, TypeError):
        tail = (done.stderr or done.stdout or "").strip()[-200:]
        raise AskError("Claude's answer could not be read." + (f" {tail}" if tail else "")) from None
    names = {t[0]: {"taskId": t[0], "title": t[1], "project": t[2]} for t in mine}
    unknown = [str(x) for p in picks for x in (p.get("tasks") or []) if x and str(x) not in names and str(x).isdigit()]
    if unknown:  # found by searching Productive: their names
        try:
            for tid, d in productive.task_details(list(dict.fromkeys(unknown))[:20], config.load()).items():
                names[tid] = {"taskId": tid, "title": d.get("title") or "", "project": d.get("project") or ""}
        except Exception:
            pass
    out: dict = {}
    by_id = {b["id"]: b for b in blocks}
    for p in picks if isinstance(picks, list) else []:
        try:
            r = rows[int(p.get("id"))]
        except (TypeError, ValueError, IndexError):
            continue
        if int(p.get("id")) not in by_id:
            continue
        tasks = [names[str(x)] for x in (p.get("tasks") or []) if x and str(x) in names][:3]
        out[f"{r['s']}|{r.get('project') or ''}"] = {"tasks": tasks, "why": str(p.get("why") or "")[:120]}
    answer = {"picks": out, "cost": res.get("total_cost_usd")}
    with _lock:
        _cache[sig] = answer
        for old in list(_cache)[:-30]:
            del _cache[old]
    return answer


def history_span(date: str) -> tuple:
    """(first, last) day of the 30 days of history before `date`."""
    d = datetime.strptime(date, "%Y-%m-%d")
    return (d - timedelta(days=30)).strftime("%Y-%m-%d"), (d - timedelta(days=1)).strftime("%Y-%m-%d")

