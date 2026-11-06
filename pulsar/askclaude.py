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
import re
import subprocess
import threading
from datetime import datetime, timedelta

from . import config, productive, sessions

MODELS = ("sonnet", "haiku", "opus")  # Claude Code's aliases: always the latest model of each
BUDGET = {"haiku": 0.25, "sonnet": 0.75, "opus": 2.0}  # dollars one day's question may cost at most (--max-budget-usd)
NOTE_MAX = 200  # characters in a drafted note
# The answer's shape, checked by Claude Code itself (--json-schema): it comes back in "structured_output"
SCHEMA = {"type": "object", "required": ["picks"], "properties": {"picks": {"type": "array", "items": {
    "type": "object", "required": ["id", "tasks", "why", "note"],
    "properties": {"id": {"type": "integer"}, "tasks": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                   "why": {"type": "string"}, "note": {"type": "string", "maxLength": NOTE_MAX}}}}}}
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

Then draft the NOTE for the best task: the description of the time entry. The company may copy notes into the \
client's invoice and time reports, so the client may read it.
- Say what was done or decided, the outcome, in plain words the client understands: one or two short sentences, at \
most {note_max} characters. "Event pages show the right time zone again", not "Looked into the time zone code".
- Write it the way this person writes, in the language of their notes: "their_notes" in each block are their own \
recent notes on the likely tasks, with the task each was for. Match their length, tone and form, above all those for \
the task you pick, but take every fact from the block itself, never from those notes.
- Never put in a note: file names or paths, web addresses, code, error messages or anything pasted from a log, \
passwords, keys or tokens, the tools that helped (no Claude, Pulsar, agents or AI), anyone's private matters, or the \
name of a client other than the one the task is for.
- Never say how the time was spent or counted: no durations, "in parallel", "overlap", who did the work, or whether \
the person was there, waiting or reviewing.
- The note is empty when no task fits, or when the block does not show what was done.
Answer with JSON only, as the last thing in your reply: \
{{"picks": [{{"id": <block id>, "tasks": ["<best task id>", "<second>", "<third>"], "why": "<at most 8 words>", \
"note": "<the note for the best task>"}}]}}

BLOCKS:
{blocks}

YOUR TASKS (id, task, project, minutes in the last 30 days, last day):
{tasks}"""
NOTE_EXAMPLES = 5  # of the person's own notes per block

# A drafted note is checked here before the page sees it: one that fails any check is blanked, never repaired
_TLD = r"com|org|net|io|ca|dev|app|co|ai|vc|edu|gov|uk|us|info|site|cloud"
_FILE_EXT = (r"php|py|js|ts|tsx|jsx|twig|ya?ml|json|md|scss|css|html?|sh|module|theme|install|inc|txt|log|sql|xml|lock"
             r"|env|conf|ini|csv")
_NOTE_CHECKS = [
    ("a web address", re.compile(rf"https?://|\bwww\.|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:{_TLD})\b", re.I)),
    ("a file path", re.compile(r"(?:^|[\s(\[\"'])(?:~|\.\.?)?/[\w.\-]+/"     # /web/themes/, ~/Sites/
                               r"|\b[a-z0-9_.\-]+/[a-z0-9_.\-]+/|\b[A-Za-z]:\\"  # web/modules/, C:\
                               rf"|\b(?!(?:Node|Next|Nuxt|Vue)\.js\b)[\w\-]+\.(?:{_FILE_EXT})\b")),  # settings.php
    ("something like a token", re.compile(r"\b(?=[\w\-]*\d)(?=[\w\-]*[A-Za-z])[\w\-]{24,}\b|\b[0-9a-f]{16,}\b"
                                          r"|\b(?:sk|pk|ghp|gho|ghs|github_pat|xox[abpr]|AKIA|eyJ)[\w\-]{8,}", re.I)),
    ("code or a log", re.compile(r"[`{}<>=\\]|\$[A-Za-z_{(]|::|->|\(\)|Traceback|SQLSTATE|Stack trace|Uncaught"
                                 r"|\w+(?:Exception|Error)\b")),
    ("a tool's name", re.compile(r"\b(?:claude|pulsar|agents?|anthropic|chatgpt|gpt|openai|codex|copilot|llm)\b", re.I)),
    ("how the time was spent", re.compile(r"\b(?:in parallel|overlap\w*|run time|time entr\w*|unattended|while away"
                                          r"|moved down|time (?:logged|tracked|counted))\b", re.I)),
]
_SENTENCE_END = re.compile(r"[.!?]+(?=\s+[A-Z0-9\u00c0-\u024f]|\s*$)")


def guard_note(note) -> tuple:
    """(the note, None) when a drafted note may be offered, else ("", why). Productive can copy a note into a client's
    invoice, so a draft with a path, a web address, something like a token, code, a tool's name, a remark on how the
    time was spent, or more than two sentences is dropped whole."""
    text = re.sub(r"\s+", " ", str(note or "")).strip().strip('"\u201c\u201d').strip()
    if not text:
        return "", None
    if len(text) > NOTE_MAX + 20:
        return "", "too long"
    for why, rx in _NOTE_CHECKS:
        if rx.search(text):
            return "", why
    if len(_SENTENCE_END.findall(text)) > 2:
        return "", "more than two sentences"
    return text, None


def _plain(note: str) -> str:
    """A note as read from Productive, without the Markdown escapes it comes back with (TASK\\_ID, \\[ABC-1\\])."""
    return re.sub(r"\\([\\_\[\]*#`~>|-])", r"\1", re.sub(r"\s+", " ", note or "")).strip()


def their_notes(row: dict, history: list, limit: int = NOTE_EXAMPLES, per_task: int = 2) -> list:
    """Up to `limit` of the person's own recent notes on a row's likely tasks, [{"task": id, "note"}]: how they write
    notes for this work. The likeliest task first (learned, then linked in its sessions), `per_task` notes each, then
    more of theirs, then other tasks of the same projects. Only notes that pass guard_note, so the examples never teach
    what a draft may not contain."""
    likely_ids = [t.get("taskId") for t in row.get("learned") or []] + [str(x) for x in row.get("tasks") or []]
    likely_ids = [x for x in dict.fromkeys(likely_ids) if x]
    if not likely_ids:
        return []
    newest = sorted((r for r in history if r.get("note") and r.get("taskId")),
                    key=lambda r: (r.get("date") or "", r.get("start") or 0), reverse=True)
    out: list = []
    seen: set = set()

    def take(rows, most):
        n = 0
        for r in rows:
            if n >= most or len(out) >= limit:
                return
            note, bad = guard_note(_plain(r["note"]))
            if note and not bad and note not in seen:
                seen.add(note)
                out.append({"task": r["taskId"], "note": note})
                n += 1
    for most in (per_task, limit):  # a few of each likely task, then more of them while there is room
        for tid in likely_ids:
            take([r for r in newest if r["taskId"] == tid], most)
    projects = [p for p in dict.fromkeys(r.get("project") for tid in likely_ids for r in newest if r["taskId"] == tid)
                if p not in (None, "", "(no project)")]
    for project in projects:
        take([r for r in newest if r.get("project") == project and r["taskId"] not in likely_ids], limit)
    return out


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


def ask(date: str, rows: list, t0: float, entries_30: list, run=subprocess.run, model: str = "sonnet",
        history: list = ()) -> dict:
    """{"picks": {"<s>|<project>": {"tasks": [{taskId, title, project}], "why", "note"}}, "cost": dollars,
    "notesBlocked": [why, ...]}. The note is a draft for the first task, "" when there is none or guard_note refused
    it. `history` is the person's earlier entries with their notes (the examples). Raises AskError with a sentence for
    the page when Claude Code is missing or its answer cannot be read."""
    blocks = [{"id": i, "time": "%02d:%02d-%02d:%02d" % (r["s"] // 60, r["s"] % 60, (r["e"] // 60) % 24, r["e"] % 60),
               "repository": r.get("project") or "", "session_titles": (r.get("titles") or [])[:3],
               "typed": typed(t0, r.get("titles") or [], r["s"], r["e"]), "their_notes": their_notes(r, history)}
              for i, r in enumerate(rows) if r["e"] - r["s"] >= 10]
    if not blocks:
        return {"picks": {}, "cost": 0}
    model = model if model in MODELS else "sonnet"
    sig = (date, model, tuple((r["s"], r["e"], r.get("project")) for r in rows))
    with _lock:
        if sig in _cache:
            return _cache[sig]
    mine = recent_tasks(entries_30)
    prompt = PROMPT.format(date=date, note_max=NOTE_MAX, blocks=json.dumps(blocks, ensure_ascii=False, indent=1),
                           tasks="\n".join(json.dumps(t, ensure_ascii=False) for t in mine))
    binary = productive._claude_binary()
    if not binary:
        raise AskError("Claude Code was not found on this Mac.")
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    try:
        done = run([binary, "-p", "--model", model, "--no-session-persistence", "--output-format", "json",
                    "--json-schema", json.dumps(SCHEMA), "--max-budget-usd", str(BUDGET[model]),
                    "--tools", "", "--permission-mode", "dontAsk",  # nothing outside --allowedTools can run
                    "--allowedTools", ",".join(READ_TOOLS), "--disallowedTools", ",".join(WRITE_TOOLS)],
                   input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd=config.CACHE_DIR)
    except subprocess.TimeoutExpired:
        raise AskError("Claude took too long to answer. Try again later.") from None
    except OSError as exc:
        raise AskError(f"Claude Code could not be started ({exc}).") from None
    try:
        res = json.loads(done.stdout)
        if isinstance(res.get("structured_output"), dict):  # checked against SCHEMA by Claude Code
            picks = res["structured_output"]["picks"]
        else:  # an older Claude Code: the JSON at the end of its text, unchecked, so no notes from it
            text = str(res.get("result") or "")
            picks = json.loads(text[text.rindex('{"picks"'):text.rindex("}") + 1])["picks"]
            picks = [{k: v for k, v in p.items() if k != "note"} if isinstance(p, dict) else p for p in picks]
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
    blocked: list = []
    by_id = {b["id"]: b for b in blocks}
    for p in picks if isinstance(picks, list) else []:
        try:
            r = rows[int(p.get("id"))]
        except (TypeError, ValueError, IndexError):
            continue
        if int(p.get("id")) not in by_id:
            continue
        tasks = [names[str(x)] for x in (p.get("tasks") or []) if x and str(x) in names][:3]
        note, bad = guard_note(p.get("note"))
        first = str((p.get("tasks") or [""])[0])
        if note and not (tasks and tasks[0]["taskId"] == first):  # written for a task that could not be named
            note, bad = "", "no task"
        if bad:
            blocked.append(bad)
        out[f"{r['s']}|{r.get('project') or ''}"] = {"tasks": tasks, "why": str(p.get("why") or "")[:120], "note": note}
    answer = {"picks": out, "cost": res.get("total_cost_usd"), "notesBlocked": blocked}
    with _lock:
        _cache[sig] = answer
        for old in list(_cache)[:-30]:
            del _cache[old]
    return answer


def history_span(date: str) -> tuple:
    """(first, last) day of the 30 days of history before `date`."""
    d = datetime.strptime(date, "%Y-%m-%d")
    return (d - timedelta(days=30)).strftime("%Y-%m-%d"), (d - timedelta(days=1)).strftime("%Y-%m-%d")

