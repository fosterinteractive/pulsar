"""Made-up days by the hundred, through the logging logic end to end, every result checked against what the docs promise.

A seeded generator builds one day at a time: a time zone and date (the four days the clocks change included), the
activity tracker's windows, Claude Code session files in made-up git repositories (parallel sessions, one session
in several repositories, agent-only time, typed task links, tool output), the calendar, calls with and without an
event, declined events, entries already logged (some without a start), 60 days of history (repeats such as a daily
scrum, meetings of a series, the session and repository votes), rules, Ask Claude's picks, commits, and inputs that
are malformed on purpose. Secret strings are planted where the person's private words live (window titles, typed
messages, meeting titles, other sessions' titles, logged notes, rule words).

Each day goes through sessions.propose, attach_tasks, commits.attach, learn, server.away, server.repeating,
server.meeting_tasks and dayplan.plan, then the overlap check of POST /api/log (dry), and on some days the MCP tools
against a real Pulsar server on a free port with Productive faked: pulsar_day, pulsar_propose, pulsar_session,
pulsar_preview and pulsar_log. The checks are in `check_*`.

    python3 -E -s -B -m unittest tests.test_simulate              # the fixed seeds, quickly
    PULSAR_SIM=3000 python3 -E -s -B -m unittest tests.test_simulate   # 3000 days (PULSAR_SIM_FROM: the first seed)
"""

import contextlib
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _RUN  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

_SCRATCH = tempfile.mkdtemp(prefix="pulsar-sim-")  # inside the run's own scratch folder, removed with it

from pulsar import (askclaude, calendar, commits, config, dayplan, learn, likely, logbook, mcp, mcptools,  # noqa: E402
                    productive, server, sessions, trackers)

QUICK_SEEDS = 200           # days in the default run (well under 20 seconds)
QUICK_MCP_EVERY = 4        # every fourth of them also through the MCP tools
GRID, MIN_ENTRY, SITTING_GAP = dayplan.GRID, dayplan.MIN_ENTRY, dayplan.SITTING_GAP
DST_DAYS = {"Europe/Kiev": ["2026-03-29", "2026-10-25"], "America/Toronto": ["2026-03-08", "2026-11-01"]}
ME, OTHER = "me@example.com", "someone@example.com"
SAFE_NOTES = ["Checkout totals fixed", "Search keeps every letter", "Reports: weekly export added",
              "Hero video encoded for mobile", "Release notes drafted"]

# the tasks a person might log to: id -> (title, project, repository it is usually for)
TASKS = {
    "101": ("Checkout fixes - Dana Example", "Acme", "acme-site"),
    "102": ("Hero video", "Acme", "acme-site"),
    "201": ("API sync [BETA-12]", "Beta", "beta-app"),
    "301": ("Pulsar development", "FI Internal", "pulsar"),
    "401": ("Skills upkeep", "FI Internal", "claude-rules"),
    "501": ("Acme commands", "Acme - Ongoing Support", "acme-web"),
    "701": ("[FIP-72] Scrum - Dana Example", "FI-PM", None),
    "702": ("Client review meetings", "Acme", None),
    "703": ("Internal meetings", "FI-PM", None),
    "999": ("Estimations - Dana Example", "FI-PR", None),
}
# repository -> (folder under the repos root, origin url): "repo" folders are named from their origin
REPOS = {"acme-site": ("checkouts/repo", "git@github.com:acme/acme-site.git"),
         "beta-app": ("beta-app", "https://github.com/beta/beta-app.git"),
         "pulsar": ("pulsar", "git@github.com:fosterinteractive/pulsar.git"),
         "claude-rules": ("claude-rules", "git@github.com:fosterinteractive/claude-rules.git"),
         "bot": ("bot", None),
         "acme-web": ("acme/web", "git@github.com:acme/acme-web.git")}
TOPICS = {"acme-site": ["Fix the checkout form", "Hero video encode", "Acme cart totals"],
          "beta-app": ["Beta API sync BETA-12", "Beta webhook retries"],
          "pulsar": ["Pulsar log the day", "Pulsar simulated days"],
          "claude-rules": ["Skill upkeep for rules", "Rules plugin release"],
          "bot": ["Coins bot userscript"],
          "acme-web": ["Acme content commands", "Acme team page"]}
REPO_ROOT = os.path.join(_SCRATCH, "repos")


def _make_repos() -> None:
    for name, (folder, origin) in REPOS.items():
        path = os.path.join(REPO_ROOT, folder)
        os.makedirs(os.path.join(path, ".git"), exist_ok=True)
        os.makedirs(os.path.join(path, "src"), exist_ok=True)
        with open(os.path.join(path, ".git", "config"), "w") as fh:
            fh.write("[core]\n\tbare = false\n" + (f'[remote "origin"]\n\turl = {origin}\n' if origin else ""))
    os.makedirs(os.path.join(REPO_ROOT, "scratch-notes"), exist_ok=True)  # a folder in no repository


def repo_path(name: str) -> str:
    return os.path.join(REPO_ROOT, REPOS[name][0])


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def hm(m) -> str:
    m = int(m)
    return f"{m // 60:02d}:{m % 60:02d}"


def ov(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


# ---- the generator -------------------------------------------------------------------------------------------------

class Day:
    """One made-up day. Everything the tracker, Claude Code, Productive and Google would say, in elapsed minutes from
    midnight (as the trackers and sessions.py work) and, for the planner, on the wall clock (as gather passes it)."""

    def __init__(self, seed: int):
        self.seed = seed
        rng = self.rng = random.Random(seed)
        self.tag = hashlib.sha1(str(seed).encode()).hexdigest()[:6]
        self.secrets: list = []
        if rng.random() < 0.3:
            self.tzname = rng.choice(list(DST_DAYS))
            self.date = rng.choice(DST_DAYS[self.tzname])
        else:
            self.tzname = rng.choice(["UTC", "Europe/Kiev", "America/Toronto"])
            self.date = (datetime(2026, 1, 5) + timedelta(days=rng.randrange(330))).strftime("%Y-%m-%d")
        self.tz = ZoneInfo(self.tzname)
        self.t0, self.t1 = server.day_bounds(self.date, self.tz)
        self.n = int(round((self.t1 - self.t0) / 60))  # 1380, 1440 or 1500 minutes
        self.shift = datetime.fromtimestamp(self.t0, self.tz).utcoffset() != \
            datetime.fromtimestamp(self.t1 - 1, self.tz).utcoffset()
        self.weekday = datetime.strptime(self.date, "%Y-%m-%d").weekday()
        self.dst = None  # on the clock: the hour that happens twice, or never
        if self.shift:
            x = next(x for x in range(self.n) if datetime.fromtimestamp(self.t0 + x * 60, self.tz).utcoffset() !=
                     datetime.fromtimestamp(self.t0, self.tz).utcoffset())
            self.dst = (x - 60, x) if self.n > 1440 else (x, x + 60)
        self.items: list = []          # activity, elapsed minutes
        self.calls: list = []          # call windows seen, elapsed [s, e]
        self.sessions: list = []       # Claude Code sessions
        self.parallel: list = []       # (session, s, e) an agent ran while the person did something else
        self.meetings: list = []       # calendar, elapsed
        self.logged: list = []         # Productive rows of the day
        self.history: list = []        # Productive rows of the 60 days before
        self.stored: dict = {}         # learn.py's past Likely rows
        self.past_events: list = []    # calendar.history
        self.rules: list = []
        self.picks: dict = {}
        self.commits: dict = {}        # repository folder -> [(epoch, email, subject)]
        self.malformed = rng.random() < 0.15
        self.style = rng.choices(["normal", "long", "empty", "midnight", "tiny", "fragmented", "night"],
                                 weights=[40, 10, 6, 8, 6, 12, 8 if self.shift else 2])[0]
        self.rule_word = self.secret("rule")
        self._sessions()
        self._activity()
        self._calendar()
        self._history()
        self._logged()
        self._rules()

    def secret(self, kind: str) -> str:
        s = f"zq{kind}{self.tag}x{len(self.secrets)}"
        self.secrets.append(s)
        return s

    # -- Claude Code sessions --

    def _sessions(self):
        rng = self.rng
        k = 0 if self.style == "empty" else rng.choice([1, 2, 3, 3, 4, 5, 6, 8])
        for i in range(k):
            repo = rng.choice(list(REPOS))
            repos = [repo] + ([rng.choice([r for r in REPOS if r != repo])] if rng.random() < 0.25 else [])
            if rng.random() < 0.1:
                repos.append(rng.choice(list(REPOS)))
            title = f"{rng.choice(TOPICS[repo])} {self.secret('title')}"
            cwd = rng.choice([repo_path(repo)] * 4 + [os.path.join(REPO_ROOT, "scratch-notes"), REPO_ROOT])
            sid = hashlib.md5(f"{self.seed}-{i}".encode()).hexdigest()
            sid = f"{sid[:8]}-{sid[8:12]}-{sid[12:16]}-{sid[16:20]}-{sid[20:32]}"
            self.sessions.append({"id": sid, "title": title, "custom": rng.random() < 0.5, "repos": repos, "cwd": cwd,
                                  "lines": [], "repo": repo, "links": [], "tool_links": []})

    def _turn(self, s: dict, at: float, until: float, notification: bool = False):
        """A message (or a background task reporting back) at `at`, the agent working until `until`."""
        rng = self.rng
        epoch = self.t0 + at * 60
        if notification:
            s["lines"].append({"type": "user", "timestamp": iso(epoch), "cwd": s["cwd"],
                               "origin": {"kind": "task-notification"},
                               "message": {"content": "<task-notification>done</task-notification>"}})
        else:
            text = f"please look at this {self.secret('typed')}"
            if rng.random() < 0.15:
                tid = rng.choice(list(TASKS))
                text += f" https://app.productive.io/1-acme/tasks/{tid}"
                s["links"].append((epoch, tid))
            if rng.random() < 0.3:
                text += " " + os.path.join(repo_path(rng.choice(s["repos"])), "src", "a.py")
            if rng.random() < 0.05:
                text = "[Request interrupted by user]"
            s["lines"].append({"type": "user", "timestamp": iso(epoch), "cwd": s["cwd"], "origin": {"kind": "human"},
                               "message": {"content": text}})
        t = at + rng.uniform(0.2, 1.5)
        while t < until:
            path = os.path.join(repo_path(rng.choice(s["repos"])), "src", f"f{rng.randrange(9)}.py")
            s["lines"].append({"type": "assistant", "timestamp": iso(self.t0 + t * 60), "cwd": s["cwd"],
                               "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": path}}]}})
            if rng.random() < 0.1:  # what a tool returned: never a typed link
                s["lines"].append({"type": "user", "timestamp": iso(self.t0 + t * 60 + 5), "cwd": s["cwd"],
                                   "toolUseResult": {}, "message": {"content": [
                                       {"type": "tool_result", "content": "see https://app.productive.io/1-acme/tasks/666 "
                                                                          + self.secret("tool")}]}})
                s["tool_links"].append("666")
            t += rng.uniform(1, 6)
        s["lines"].append({"type": "assistant", "timestamp": iso(self.t0 + until * 60), "cwd": s["cwd"],
                           "message": {"content": [{"type": "text", "text": "Done."}]}})

    def write_sessions(self, folder: str) -> None:
        for s in self.sessions:
            d = os.path.join(folder, "-" + re.sub(r"[^A-Za-z0-9]", "-", s["cwd"])[-60:])
            os.makedirs(d, exist_ok=True)
            head = [{"type": "custom-title" if s["custom"] else "ai-title",
                     ("customTitle" if s["custom"] else "aiTitle"): s["title"]}]
            path = os.path.join(d, f"{s['id']}.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                for line in head + sorted(s["lines"], key=lambda x: x.get("timestamp", "")):
                    fh.write(json.dumps(line) + "\n")
                if self.malformed:
                    fh.write("{not json\n")
            os.utime(path, (self.t1, self.t1))

    # -- the activity tracker --

    def item(self, s, e, app, title="", host="", url="", cat="work"):
        self.items.append({"s": round(s, 3), "e": round(e, 3), "app": app, "title": title, "host": host, "url": url,
                           "cat": cat, "icon": None})

    def _activity(self):
        rng, n = self.rng, self.n
        if self.style == "empty":
            return
        start, end = {"normal": (rng.uniform(420, 600), rng.uniform(900, 1140)),
                      "long": (rng.uniform(300, 420), rng.uniform(1320, n - 1)),
                      "midnight": (rng.uniform(1140, 1300), n),
                      "tiny": (rng.uniform(500, 900), 0),
                      "fragmented": (rng.uniform(480, 600), rng.uniform(780, 1000)),
                      "night": (rng.uniform(20, 90), rng.uniform(300, 600))}[self.style]
        if self.style == "tiny":
            for _ in range(rng.randrange(1, 4)):
                a = start + rng.uniform(0, 300)
                self.item(a, a + rng.uniform(0.05, 4), rng.choice(["Cursor", "Google Chrome", "Slack"]), "x")
            return
        if self.style == "midnight":  # the night before carried on past midnight
            self._blocks(0, rng.uniform(30, 120))
        self._blocks(start, end)

    def _blocks(self, t, end):
        rng = self.rng
        kinds = ["editor"] * 6 + ["browser_project", "browser", "productive", "pulsar", "call", "huddle", "personal",
                                  "break", "break", "lunch", "fragments", "parallel", "editor_rule"]
        if self.style == "fragmented":
            kinds += ["fragments"] * 6 + ["break"] * 2
        lunch_done = False
        while t < end:
            k = rng.choice(kinds)
            if not self.sessions and k in ("editor", "parallel", "editor_rule"):
                k = "browser"
            if k == "lunch" and lunch_done:
                k = "break"
            d = {"editor": rng.uniform(8, 90), "editor_rule": rng.uniform(8, 40), "browser_project": rng.uniform(3, 40),
                 "browser": rng.uniform(3, 30), "productive": rng.uniform(1, 12), "pulsar": rng.uniform(1, 15),
                 "call": rng.uniform(10, 65), "huddle": rng.uniform(4, 30), "personal": rng.uniform(1, 20),
                 "break": rng.uniform(5, 28), "lunch": rng.choice([rng.uniform(30, 90), rng.uniform(180, 240)]),
                 "fragments": rng.uniform(3, 20), "parallel": rng.uniform(10, 40)}[k]
            b = min(end, t + d)
            if b - t < 0.05:
                break
            if k in ("editor", "editor_rule"):
                s = rng.choice(self.sessions)
                app = rng.choice(["Cursor", "Code", "Terminal", "iTerm2", "Warp"])
                title = s["title"] if rng.random() < 0.8 else s["title"][:12] + "…"
                if k == "editor_rule":
                    title += f" {self.rule_word}"
                self.item(t, b, app, title)
                at = t + rng.uniform(0, 3)
                while at < b:
                    nxt = at + rng.uniform(4, 30)
                    self._turn(s, at, min(nxt, b + rng.uniform(0, 10)) - rng.uniform(0, 2))
                    at = nxt
            elif k == "browser_project":
                repo = rng.choice(list(REPOS))
                self.item(t, b, "Google Chrome", f"Pull request #4 · {repo}", "github.com", f"https://github.com/x/{repo}/pull/4")
            elif k == "browser":
                self.item(t, b, rng.choice(["Google Chrome", "Safari", "Slack", "Finder"]), self.secret("window"),
                          "docs.google.com", "https://docs.google.com/d/" + self.secret("url"))
            elif k == "productive":
                self.item(t, b, "Google Chrome", "Time · Productive", "app.productive.io", "https://app.productive.io/1-acme/time")
            elif k == "pulsar":
                self.item(t, b, "Pulsar", "Pulsar")
            elif k in ("call", "huddle"):
                if k == "call":
                    self.item(t, b, "zoom.us", "Zoom Meeting")
                else:
                    self.item(t, b, "Slack", "Huddle: design")
                self.calls.append([t, b])
                if rng.random() < 0.3 and b - t > 10:  # notes in the editor during the call
                    a = rng.uniform(t, b - 3)
                    self.item(a, a + rng.uniform(1, 3), "Cursor", "meeting notes")
            elif k == "personal":
                self.item(t, b, "Telegram", self.secret("personal"), cat="personal")
            elif k == "fragments":
                x = t
                while x < b:
                    dx = rng.choice([0.05, 0.2, 0.5, 1, 1.5, 3])
                    app = rng.choice(["Cursor", "Google Chrome", "Slack", "Pulsar", "Telegram", "Finder"])
                    self.item(x, min(b, x + dx), app, self.secret("frag") if app != "Pulsar" else "Pulsar",
                              cat="personal" if app == "Telegram" else "work")
                    x += dx + rng.choice([0, 0, 0, 0.5, 2, 6])
            elif k == "parallel":  # an agent works on its own while the person reads elsewhere
                s = rng.choice(self.sessions)
                self.item(t, b, "Google Chrome", self.secret("window"), "news.example.com")
                self._turn(s, t, b - rng.uniform(0, 3), notification=True)
                self.parallel.append((s, t, b))
            elif k == "lunch":
                lunch_done = True
            t = b + (rng.choice([0, 0, 0, 0.3, 1, 2]) if k not in ("break", "lunch") else 0)
        if self.rng.random() < 0.2 and self.sessions:  # a session from yesterday, answered once after midnight
            s = self.rng.choice(self.sessions)
            self._turn(s, -40, -10)

    # -- the calendar --

    def _calendar(self):
        rng = self.rng
        series = f"series-{self.tag}"
        for a, b in self.calls:
            if rng.random() < 0.6:
                s = round((a + rng.uniform(-8, 8)) / 5) * 5
                self.meetings.append({"s": s, "e": s + max(5, round((b - a) / 15) * 15), "title": self.secret("meeting"),
                                      "uid": rng.choice([series, f"one-{rng.randrange(99)}"]),
                                      "declined": rng.random() < 0.1, "all_day": False, "recurring": False})
        for _ in range(rng.choice([0, 0, 1, 2, 3])):  # meetings with no call seen: on the calendar, maybe not attended
            s = rng.randrange(480, 1080, 15)
            self.meetings.append({"s": s, "e": s + rng.choice([15, 30, 45, 60]), "title": self.secret("meeting"),
                                  "uid": series if rng.random() < 0.3 else f"x{rng.randrange(999)}",
                                  "declined": rng.random() < 0.2, "all_day": False, "recurring": False})
        if rng.random() < 0.15:
            self.meetings.append({"s": 0, "e": 1440, "title": self.secret("meeting"), "uid": "allday", "declined": False,
                                  "all_day": True, "recurring": False})
        if rng.random() < 0.1:
            s = rng.randrange(600, 900)
            self.meetings.append({"s": s, "e": s + 3, "title": self.secret("meeting"), "uid": "tiny", "declined": False,
                                  "all_day": False, "recurring": False})
        self.series = series

    # -- Productive: the 60 days before, the day itself --

    def _history(self):
        rng = self.rng
        day0 = datetime.strptime(self.date, "%Y-%m-%d")
        self.scrum_at = rng.choice([540, 570, 600, 990])  # the daily scrum, on the clock
        scrum = rng.random() < 0.6
        for i in range(1, 61):
            d = (day0 - timedelta(days=i)).strftime("%Y-%m-%d")
            t0 = server.day_bounds(d, self.tz)[0]
            wd = (day0 - timedelta(days=i)).weekday()
            rows = []
            if scrum and i <= 28 and wd < 5 and rng.random() < 0.85:
                at = self.scrum_at + rng.choice([0, 0, 0, 2, -3, 60 if rng.random() < 0.05 else 0])
                start = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=self.tz) + timedelta(minutes=at)
                self.history.append(self.row(d, start.timestamp(), rng.choice([15, 20, 25]), "701",
                                             note=rng.choice(["Daily scrum", "", "Daily scrum"])))
                if rng.random() < 0.5:
                    self.past_events.append({"s": start.timestamp(), "e": start.timestamp() + 1800, "uid": self.series,
                                             "title": "Daily scrum", "declined": rng.random() < 0.1})
            if rng.random() < 0.5 or wd >= 5:
                continue
            for s in self.sessions:  # the same sessions, some of them, on earlier days
                if rng.random() < 0.4:
                    a = rng.randrange(480, 1000)
                    m = rng.randrange(10, 120)
                    rows.append({"k": [learn.key(s["title"])], "p": s["repo"], "s": a, "e": a + m})
                    if s["repo"] != "bot" and rng.random() < 0.8:
                        task = rng.choice([t for t, v in TASKS.items() if v[2] == s["repo"]] + ["999"])
                        self.history.append(self.row(d, t0 + a * 60, m, task, note=rng.choice(SAFE_NOTES)))
            if rows:
                self.stored[d] = rows

    def row(self, date, start, minutes, task, note=""):
        title, project, _ = TASKS[task]
        return {"id": str(self.rng.randrange(10 ** 6, 10 ** 7)), "date": date, "start": start, "minutes": minutes,
                "taskId": task, "task": title, "project": project, "serviceId": f"8{task}", "service": "Dev",
                "budget": "B", "billable": True, "note": note, "approved": False}

    def _logged(self):
        rng = self.rng
        k = rng.choice([0, 0, 0, 1, 2, 3, 5])
        for _ in range(k):
            if self.items and rng.random() < 0.8:
                it = rng.choice(self.items)
                a = it["s"] + rng.uniform(-15, 15)
            else:
                a = rng.uniform(300, 1200)
            a = max(0, round(a) if rng.random() < 0.5 else round(a / 5) * 5)
            m = rng.choice([5, 10, 15, 30, 45, 60, 90, 7, 23])
            start = None if rng.random() < 0.15 else self.t0 + a * 60
            if start is not None and start + m * 60 > self.t1:
                m = max(1, int((self.t1 - start) / 60))
            self.logged.append({**self.row(self.date, start, m, rng.choice(list(TASKS)), note=self.secret("lognote"))})

    def _rules(self):
        if self.rng.random() < 0.5:
            tid = self.rng.choice(list(TASKS))
            self.rules.append({"match": self.rule_word, "taskId": tid, "title": TASKS[tid][0], "project": TASKS[tid][1]})

    # -- on the clock --

    def clock(self, m: float) -> float:
        return round(server.clock(self.t0 + m * 60, self.t0, self.tz), 3) if self.shift else m

    def services(self, task: str) -> dict:
        title, project, _ = TASKS.get(task, ("Unknown", "Nowhere", None))
        h = int(hashlib.md5(f"{self.seed}{task}".encode()).hexdigest(), 16)
        svcs = [{"id": f"8{task}", "name": "Dev", "budget": "B", "billable": True},
                {"id": f"9{task}", "name": "Meetings", "budget": "B", "billable": False}][: 1 + h % 2]
        if h % 11 == 0:
            svcs = []  # the task's project takes no time on that date
        return {"task": {"title": title, "project": project, "projectId": "5", "serviceId": f"8{task}"}, "services": svcs}


# ---- running a day through the pieces ------------------------------------------------------------------------------

class Run:
    """The pieces on one Day, in the order gather calls them, with what each step returned kept for the checks."""

    def __init__(self, day: Day):
        self.day = day
        self.folder = tempfile.mkdtemp(prefix="projects-", dir=_SCRATCH)
        day.write_sessions(self.folder)
        self.problems: list = []
        self.limits: list = []  # documented limits met, counted, not failures

    def close(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def fail(self, what: str, detail="") -> None:
        self.problems.append(f"{what}: {detail}" if detail else what)

    # the Likely column, as likely.proposal makes it (elapsed minutes)
    def likely(self) -> dict:
        d = self.day
        items = [dict(it) for it in d.items]
        got = sessions.propose(d.t0, d.t1, items, "ActivityWatch" if items else "no tracker", self.folder)
        rows = got["entries"]
        found = sessions.task_links(d.t0, self.folder)
        sessions.attach_tasks(rows, d.t0, found)
        with mock.patch.dict(commits._git, {"path": "/sim/git"}):
            commits.attach(rows, d.t0, self.folder, run=self.git)
        for s, a, b in d.parallel:  # agent-only rows, as the fi-time-from-activity script gives them
            if b - a >= 5:
                rows.append({"s": int(a), "e": int(math.ceil(b)), "minutes": int(b - a), "project": s["repo"],
                             "kind": "parallel", "titles": [s["title"]], "messages": 0,
                             "first_message": d.secret("typed"), "sessions": [s["id"][:8]]})
        self.proposal = got
        return got

    def git(self, cmd, **kw):
        """git config user.email / git log, for the made-up repositories."""
        d = self.day
        root = cmd[cmd.index("-C") + 1]
        if "config" in cmd:
            return mock.Mock(stdout=ME + "\n", returncode=0)
        name = next((n for n, (f, _) in REPOS.items() if repo_path(n) == root), None)
        if root not in d.commits:
            rng = random.Random(f"{d.seed}{root}")
            out = []
            for _ in range(rng.randrange(0, 8)):
                at = d.t0 + rng.uniform(400, 1300) * 60
                subject = rng.choice(SAFE_NOTES + ["Merge branch 'qa'", "WIP", "v1.2.3", "Bump version",
                                                   "Update web/themes/acme/style.css", f"fixup! {name}",
                                                   "Fix checkout https://acme.example.com/x"])
                out.append((int(at), ME, subject))
                out.append((int(at) + 30, OTHER, d.secret("commit")))
            d.commits[root] = out
        return mock.Mock(stdout="".join(f"{t}\t{who}\t{s}\n" for t, who, s in d.commits[root]), returncode=0)

    def learned(self, rows: list) -> None:
        """learn.annotate, as api_proposal calls it, on the stored days (nothing worked out in the background)."""
        d = self.day
        with mock.patch.object(learn, "warm", lambda date, s: None), \
                mock.patch.object(learn, "_stored", lambda date: d.stored.get(date)):
            learn.annotate({"entries": rows}, d.date, {}, d.history, [], d.tz)

    def plan_inputs(self) -> tuple:
        d = self.day
        rows = []
        for r in self.proposal["entries"]:
            x = {**r, "pick_s": r["s"]}
            if d.shift:
                x["s"], x["e"] = round(d.clock(r["s"]), 2), round(d.clock(r["e"]), 2)
            rows.append(x)
        items = [{**it, "s": d.clock(it["s"]), "e": d.clock(it["e"])} for it in d.items]
        self.items_clock = [dict(it) for it in items]
        away = server.away(items)
        logged = [{"s": None if x["start"] is None else round(server.clock(x["start"], d.t0, d.tz), 2),
                   "e": None if x["start"] is None else round(server.clock(x["start"] + x["minutes"] * 60, d.t0, d.tz), 2),
                   "minutes": x["minutes"], "taskId": x["taskId"], "id": x["id"]} for x in d.logged]
        meetings = [dict(m) for m in d.meetings if not m.get("all_day")]
        first28 = (datetime.strptime(d.date, "%Y-%m-%d") - timedelta(days=28)).strftime("%Y-%m-%d")
        hist28 = [r for r in d.history if r["date"] >= first28]
        offered = self.recent() + self.assigned()
        for m in meetings:
            m["tasks"] = server.meeting_tasks(m, d.past_events, hist28, offered, {"dana", "example"})
            self.check_meeting_tasks(m)
        self.patterns = server.repeating(d.history, d.tz, d.date)
        repeats = server.repeat_suggestions(self.patterns, items, d.weekday)
        calls = [{"s": it["s"], "e": it["e"]} for it in items if it.get("cat") == "work" and server.is_call(it)]
        for r in rows:
            if r.get("minutes", 0) >= 10 and d.rng.random() < 0.25:
                key = f"{r['pick_s']}|{r.get('project') or ''}"
                if d.rng.random() < 0.2:
                    d.picks[key] = {"tasks": [], "why": ""}
                else:
                    tid = d.rng.choice(list(TASKS))
                    d.picks[key] = {"tasks": [{"taskId": tid, "title": TASKS[tid][0], "project": TASKS[tid][1]}],
                                    "why": "the messages say so", "note": d.rng.choice(SAFE_NOTES + [""])}
        day = {"items": items, "away": away, "rows": rows, "logged": logged, "meetings": meetings, "repeats": repeats,
               "calls": calls, "rules": d.rules, "assigned": self.assigned(), "picks": d.picks,
               "gone": server.missing_hour(d.t0, d.t1, d.tz)}
        if d.malformed:
            day = self.spoil(day)
        t0_of = lambda x: server.day_bounds(x, d.tz)[0]  # noqa: E731
        ctx = dayplan.learn(d.date, d.history, d.stored, t0_of, person="Dana Example")
        return day, ctx

    def spoil(self, day: dict) -> dict:
        """Inputs that are wrong on purpose: missing fields, zero-length and backwards items, out of order, beyond
        the day."""
        rng = self.day.rng
        items = [dict(it) for it in day["items"]]
        for it in items:
            if rng.random() < 0.05:
                it.pop(rng.choice(["app", "title", "host", "url", "cat"]), None)
        items += [{"s": 600, "e": 600, "app": "Cursor", "cat": "work"}, {"s": 700, "e": 690, "cat": "work"},
                  {"s": -30, "e": 20, "app": "Cursor", "title": "x", "cat": "work"},
                  {"s": 1430, "e": 1460, "app": "Cursor", "title": "x", "cat": "work"}]
        rng.shuffle(items)
        rows = [dict(r) for r in day["rows"]] + [{"s": 800, "e": 800, "minutes": 0, "project": "beta-app"},
                                                 {"s": 810, "e": 805, "project": None, "titles": None}]
        logged = [dict(r) for r in day["logged"]] + [{"s": 900, "minutes": 0, "taskId": None},
                                                     {"s": None, "minutes": 5, "taskId": "101"}]
        meetings = [dict(m) for m in day["meetings"]] + [{"s": 700, "e": 650, "title": None, "uid": None},
                                                         {"s": 1000, "e": 1030}]
        rng.shuffle(meetings)
        return {**day, "items": items, "rows": rows, "logged": logged, "meetings": meetings}

    def recent(self) -> list:
        d, out = self.day, {}
        for r in sorted(d.history, key=lambda r: r["date"]):
            out[r["taskId"]] = {"taskId": r["taskId"], "title": r["task"], "project": r["project"],
                                "serviceId": r["serviceId"], "service": r["service"], "last": r["date"], "count": 1,
                                "minutes": r["minutes"], "budget": "B", "billable": True}
        return sorted(out.values(), key=lambda t: t["last"], reverse=True)

    def assigned(self) -> list:
        return [{"taskId": t, "title": TASKS[t][0], "project": TASKS[t][1], "projectId": "5", "serviceId": f"8{t}",
                 "due": None, "status": "", "blocked": False, "assignee": "7"} for t in ("102", "201", "999")]

    # -- small pieces with their own checks --

    def check_meeting_tasks(self, m: dict) -> None:
        got = m["tasks"]
        if len(got) > 5 or len({t["taskId"] for t in got}) != len(got):
            self.fail("meeting_tasks: more than five, or a task twice", got)
        for t in got:
            if t["taskId"] not in TASKS:
                self.fail("meeting_tasks: a task nobody logged or was offered", t)
            if any(s in json.dumps(t) for s in self.day.secrets):
                self.fail("meeting_tasks: a private word in a task's reason", t)

    def check_likely(self) -> None:
        """sessions.propose, attach_tasks and commits.attach."""
        d = self.day
        rows = [r for r in self.proposal["entries"] if r.get("kind") == "focused"]
        ids = {s["id"][:8] for s in d.sessions}
        allowed = set(REPOS) | {"Claude Code", "scratch-notes", "repos"} | {s["title"].strip()[:40] for s in d.sessions}
        # the minutes a session could have: a work window that is not a call or mostly logging; a session in Pulsar's
        # own repository may also have a minute mostly in Pulsar's window (testing it: sessions.OWN_REPO)
        n = d.n
        work_s, log_s, pul_s, open_ = [0.0] * n, [0.0] * n, [0.0] * n, [False] * n
        for it in d.items:
            if it["cat"] != "work":
                continue
            # time in Pulsar or Productive is work like any other since 1.6.9 (sessions.LOGGING_IS_WORK)
            logging_ = not sessions.LOGGING_IS_WORK and bool(
                sessions.LOGGING.search(it["app"]) or sessions.LOGGING.search(it["host"]) or sessions.LOGGING.search(it["url"]))
            pulsar_ = logging_ and any(sessions.PULSAR_WINDOW.search(it[k]) for k in ("app", "host", "url"))
            meeting = not sessions.CLAUDE_APPS.search(it["app"]) and sessions.MEETING.search(
                " ".join((it["app"], it["title"], it["host"])))
            for x in range(max(0, int(it["s"])), min(n, int(math.ceil(it["e"])))):
                sec = (min(it["e"], x + 1) - max(it["s"], x)) * 60
                work_s[x] += sec
                log_s[x] += sec if logging_ else 0
                pul_s[x] += sec if pulsar_ else 0
                open_[x] = open_[x] or not (logging_ or meeting)
        for r in rows:
            if not (0 <= r["s"] < r["e"] <= n) or not 1 <= r["minutes"] <= r["e"] - r["s"]:
                self.fail("likely: a row outside the day or with more minutes than its span", r)
            if r["project"] not in allowed:
                self.fail("likely: a project that is no repository, title or folder", r["project"])
            if not set(r.get("sessions") or []) <= ids:
                self.fail("likely: a session that does not exist", r.get("sessions"))
            if d.items:
                own = bool(sessions.OWN_REPO.match(r["project"] or ""))
                logs = lambda x: log_s[x] and 2 * log_s[x] >= work_s[x]  # noqa: E731
                can = sum(1 for x in range(r["s"], r["e"])
                          if (open_[x] and not logs(x)) or (own and logs(x) and 2 * pul_s[x] >= log_s[x]))
                if r["minutes"] > can:
                    self.fail("likely: minutes on calls, logging or idle time", (r["s"], r["e"], r["minutes"], can))
            if any(s in (r.get("first_message") or "") for s in d.secrets if s.startswith("zqtool")) or \
                    "<task-notification>" in (r.get("first_message") or ""):
                self.fail("likely: the first message is not one the person typed", r.get("first_message"))
            # task links: typed in one of the row's sessions, in the three hours before it or during it
            for tid in r.get("tasks") or []:
                ok = any(sessions.norm_title(s["title"]).startswith(sessions.norm_title(t)[:5])
                         and d.t0 + r["s"] * 60 - 3 * 3600 <= at <= d.t0 + r["e"] * 60
                         for s in d.sessions for at, x in s["links"] if x == tid for t in r.get("titles") or [])
                if not ok:
                    self.fail("attach_tasks: a task that was not typed in the row's session at that time", tid)
            # commits: mine, in a matching repository, from five minutes before the row to ten after it
            for subject in r.get("commits") or []:
                if askclaude.guard_note(subject)[0] != subject or commits.SKIP.match(subject):
                    self.fail("commits: a subject a note may not carry", subject)
                ok = any(who == ME and s.strip().rstrip(".") == subject and
                         d.t0 + r["s"] * 60 - commits.LEAD <= t <= d.t0 + r["e"] * 60 + commits.TAIL
                         and commits.matches(r["project"], root)
                         for root, cs in d.commits.items() for t, who, s in cs)
                if not ok:
                    self.fail("commits: a subject that is not one of mine in the row's time and repository", subject)
        for a, b in zip(rows, rows[1:]):
            if b["s"] < a["e"]:
                self.fail("likely: two rows share minutes", ((a["s"], a["e"]), (b["s"], b["e"])))

    def check_away(self, items: list, away: list) -> None:
        if not items:
            return
        d = self.day
        if d.dst and d.n > 1440 and any(ov(g["s"], g["e"], *d.dst) > 0 for g in away):
            # a window across the hour the clocks go back ends before it starts on the clock: a known limit
            self.limits.append("the hour the clocks go back is one hour on the clock")
            return
        first, last = min(it["s"] for it in items), max(it["e"] for it in items)
        for g in away:
            if g["e"] - g["s"] < config.away_after() or g["s"] < first or g["e"] > last or g["off"] != (g["e"] - g["s"] >= 180):
                self.fail("away: a gap too short, outside the day's activity or wrongly off", g)
            if any(ov(it["s"], it["e"], g["s"], g["e"]) > 0.01 for it in items):  # away rounds to 0.01
                self.fail("away: activity inside a gap", g)
        for a, b in zip(away, away[1:]):
            if b["s"] < a["e"]:
                self.fail("away: gaps overlap", (a, b))

    def check_repeats(self, repeats: list) -> None:
        d = self.day
        for p in self.patterns:
            if p["days"] < server.REPEAT_MIN_DAYS or p["taskId"] not in {r["taskId"] for r in d.history}:
                self.fail("repeating: a pattern of fewer than three days", p)
        calls = [c for c in self.items_clock if c["cat"] == "work" and server.is_call(c)]
        for r in repeats:
            if d.weekday not in r["weekdays"]:
                self.fail("repeat_suggestions: offered on a weekday it never happened", r)
            if not any(abs(c["s"] - r["at"]) <= server.CALL_WINDOW + server.CALL_GAP + 60 for c in calls):
                self.fail("repeat_suggestions: no call near the usual time", r)
            if not 5 <= r["minutes"] <= max(5, int(round(r["minutes"] * 1.5 / 5) * 5)) or r["e"] - r["s"] != r["minutes"]:
                self.fail("repeat_suggestions: a length that is not the call's", r)

    # -- Log the day ------------------------------------------------------------------------------------------------

    def check_plan(self, day: dict, ctx: dict, res: dict, loc: dict) -> None:
        d = self.day
        entries, totals = res["entries"], res["totals"]
        logged = [r for r in day["logged"] if r.get("s") is not None]
        busy = [(r["s"], max(r["s"] + (r.get("minutes") or 0), r.get("e") or 0)) for r in logged]
        zero = any(r.get("minutes") == 0 for r in logged)  # malformed: the server calls it an overlap too
        work = [0] * 1440
        for it in day["items"]:
            if it.get("cat") == "work":
                for x in range(max(0, int(it["s"])), min(1440, int(math.ceil(it["e"])))):
                    work[x] = 1
        personal_only = [0] * 1440
        for it in day["items"]:
            if it.get("cat") == "personal":
                for x in range(max(0, int(it["s"])), min(1440, int(math.ceil(it["e"])))):
                    personal_only[x] = 0 if work[x] else 1
        meetingish = list(work)
        calls = [{"s": a, "e": b} for a, b in dayplan.join_calls(day["calls"])]  # a call goes on through short gaps
        for m in list(day["meetings"]) + list(day["repeats"]) + calls:
            if not m.get("all_day"):
                for x in range(max(0, int(m["s"])), min(1440, int(math.ceil(m["e"])))):
                    meetingish[x] = 1
        # a calendar meeting goes on with its call: up to CALL_GAP from its end to a call window (dayplan._meetings)
        for m in day["meetings"]:
            if m.get("all_day") or m.get("declined"):
                continue
            nxt = [c["s"] for c in calls if int(math.ceil(m["e"])) <= c["s"] <= m["e"] + dayplan.CALL_GAP]
            for x in range(int(math.ceil(m["e"])), min(1440, int(min(nxt)) if nxt else 0)):
                meetingish[x] = 1
        notes = {"meeting": {(m.get("title") or "").strip() for m in day["meetings"]},
                 "repeat": {(r.get("note") or "").strip() for r in day["repeats"]},
                 "commits": {commits.note(r.get("commits") or []) for r in day["rows"]}}
        for e in entries:
            where = f"{hm(e['s'])}-{hm(e['e'])} {e.get('kind')} {e.get('source')}"
            if not (0 <= e["s"] < e["e"] <= 1440) or e["minutes"] != e["e"] - e["s"]:
                self.fail("plan: an entry outside the day, or minutes that are not its span", where)
            # on the grid, except an edge right against an entry already logged (it takes in the minutes up to it)
            if (e["s"] % GRID and not any(int(math.ceil(b)) == e["s"] for _, b in busy)) or \
                    (e["e"] % GRID and not any(int(a) == e["e"] for a, _ in busy)):
                self.fail("plan: an entry off the 5-minute grid", where)
            if not (e.get("why") or "").strip():
                self.fail("plan: an entry with no reason", where)
            if e["checked"] and (e["confidence"] == "low" or not e["taskId"] or e["flags"]):
                self.fail("plan: an unsure entry starts ticked", (where, e["confidence"], e["flags"]))
            if e["kind"] in ("agent", "unattended") and e["checked"]:
                self.fail("plan: agent-only time or a meeting with no call starts ticked", where)
            if e["checked"] and (e["noteFrom"] == "claude" or (e["source"] == "claude" and e["taskId"] not in
                                                                {str(t) for t in ctx.get("recent") or ()})):
                self.fail("plan: Claude's note, or its pick of a task not logged to in 30 days, starts ticked", where)
            if bool(e["note"]) != bool(e["noteFrom"]) or (e["noteFrom"] and e["noteFrom"] not in
                                                          ("meeting", "repeat", "claude", "commits")):
                self.fail("plan: a note from nowhere", (where, e["note"], e["noteFrom"]))
            elif e["noteFrom"] == "claude":
                ok = any(p.get("note") == e["note"] and p.get("tasks") and str(p["tasks"][0]["taskId"]) == e["taskId"]
                         for p in day["picks"].values())
                if not ok:
                    self.fail("plan: Claude's note on another task's entry", (where, e["note"], e["taskId"]))
            elif e["noteFrom"] and e["note"] not in notes[e["noteFrom"]]:
                self.fail("plan: a note that is not its source's", (where, e["noteFrom"], e["note"]))
            if e["noteFrom"] == "commits" and askclaude.guard_note(e["note"])[0] != e["note"]:
                self.fail("plan: a commit note the guard refuses", e["note"])
            if e["noteFrom"] != "meeting" and any(s in e["note"] for s in d.secrets):
                self.fail("plan: a private word in a note", (where, e["note"]))
            for a, b in busy:
                if ov(e["s"], e["e"], a, b) > 0:
                    self.fail("plan: an entry overlaps one already logged", (where, hm(a), b - a))
            for g in day["away"]:
                # a break the planner sees as more than a sitting's gap (whole minutes); an attended meeting keeps its
                # calendar time even when the call window closed earlier; two grid steps of rounding and push
                if int(g["e"]) - math.ceil(g["s"]) > SITTING_GAP and e["kind"] != "meeting" and \
                        ov(e["s"], e["e"], g["s"], g["e"]) > 2 * GRID:
                    self.fail("plan: an entry over a break of more than half an hour",
                              (where, hm(g["s"]), round(g["e"] - g["s"]), round(ov(e["s"], e["e"], g["s"], g["e"]))))
        for u in res["unplaced"]:  # cut at an entry already logged: rounding, or a remainder too short to stand
            if u["why"].startswith("no room") and u["minutes"] > MIN_ENTRY + 2 * GRID and not zero:
                self.fail("plan: work after an entry already logged was not placed", (u["minutes"], u["project"]))
            if u["minutes"] <= 0:
                self.fail("plan: nothing listed as not placed", u)
        spans = sorted((e["s"], e["e"]) for e in entries)
        for a, b in zip(spans, spans[1:]):
            if b[0] < a[1]:
                self.fail("plan: two proposed entries overlap", (a, b))
        if totals["proposed"] != sum(e["minutes"] for e in entries):
            self.fail("plan: the total is not the sum of the entries")
        if totals["active"] != sum(work):
            self.fail("plan: active minutes are not the tracker's work minutes", (totals["active"], sum(work)))
        if totals["cap"] > sum(meetingish):
            self.fail("plan: the cap counts more than work and meetings", (totals["cap"], sum(meetingish)))
        if entries and totals["proposed"] > max(0, totals["cap"] - totals["logged"]) + totals["overLeft"]:
            self.fail("plan: proposed plus logged exceeds active time plus meetings",
                      (totals["proposed"], totals["logged"], totals["cap"]))
        # short entries: a meeting someone attended, or work with nothing to join in its sitting (a meeting someone
        # attended keeps its own time, so work beside it does not join it)
        # the cap may take a neighbour; a zero-minute entry (malformed) cuts a piece the way the server's check would
        order = sorted(entries, key=lambda e: e["s"]) if not totals["over"] and not zero else []
        for k, e in enumerate(order):
            if any(0 <= x[0] - e["e"] < GRID for x in busy):
                continue  # stopped on the grid before an entry already logged: the rest is listed as not placed
            if e["minutes"] < MIN_ENTRY and not (e["kind"] == "meeting" and e["minutes"] >= 5):
                for q in (order[k - 1] if k else None, order[k + 1] if k + 1 < len(order) else None):
                    if q is None or (q["kind"] == "meeting" or q["source"] in ("repeat", "meeting", "call")) and q["s"] > e["s"]:
                        continue  # work before a meeting someone attended does not join it
                    a, b = sorted([(q["s"], q["e"]), (e["s"], e["e"])])
                    if d.dst and ov(e["s"], e["e"], *d.dst) > 0:
                        continue  # the hour the clocks go back is one hour on the clock: a known limit
                    # one sitting (laid out on the grid, so two steps of slack); time skipped between them blocks it
                    # a client's entry is never joined across time away (every made-up service is billable)
                    if b[0] - a[1] <= SITTING_GAP - 2 * GRID and \
                            not any(loc["away"][x] for x in range(max(0, int(a[1]) - GRID), min(1440, int(b[0]) + GRID))) and \
                            not any(x["s"] <= b[0] and x["e"] >= a[0] for x in res["skipped"]) and \
                            not any(x[0] < b[0] + GRID and x[1] >= a[1] - GRID for x in busy):
                        self.fail("plan: a short entry with others beside it", (hm(e["s"]), e["minutes"], e["kind"]))
        # minute by minute, as the planner placed them
        owner, claims = loc["owner"], loc["claims"]
        for x in range(1440):
            i = owner[x]
            if i is None or i == dayplan.BLOCKED:
                continue
            c = claims[i]
            k = c["kind"]
            own = bool(dayplan.OWN_REPO.match(c.get("project") or ""))
            if k in ("logged", "skip", "meeting"):
                continue
            if loc["away"][x] and not loc["work"][x]:  # both: the edge of a window, rounded
                self.fail("plan: a minute of away time placed", (hm(x), k))
            elif loc["logging"][x] and not own:
                self.fail("plan: a minute spent logging placed", (hm(x), k, c.get("project")))
            elif k in ("agent", "outside", "unattended") and not loc["work"][x]:
                self.fail("plan: agent-only or outside work placed on a minute with no activity", (hm(x), k))
            elif personal_only[x]:
                self.fail("plan: a minute in a personal window placed", (hm(x), k))
        # meetings keep their time: what each attended meeting holds is in the entries, at its own time
        cut = any(u["why"].startswith(("more than", "no room")) for u in res["unplaced"])
        for i, c in enumerate(claims):
            if not c.get("attended"):
                continue
            mine = [x for x in range(1440) if owner[x] == i]
            if len(mine) < 5 or cut:  # under 5 minutes is rounding
                continue
            a, b = mine[0], mine[-1] + 1
            got = sum(ov(e["s"], e["e"], a, b) for e in entries)
            if got < len(mine) - 2 * GRID:
                self.fail("plan: a meeting lost its time", (hm(a), hm(b), len(mine), got))
            if c.get("task") and not any(e["taskId"] == c["task"] and ov(e["s"], e["e"], a - GRID, b + GRID) >= min(5, b - a)
                                         for e in entries):
                self.fail("plan: a meeting's time went to another task", (hm(a), hm(b), c.get("task")))

    def check_log_dry(self, res: dict) -> None:
        """POST /api/log with dry for every proposed entry: the server's own overlap check finds nothing."""
        d = self.day
        settings = {"timezone": d.tzname, "person_id": "1"}
        rows = [dict(r) for r in d.logged]
        with mock.patch.object(server, "current_settings", lambda: settings), \
                mock.patch.object(server.productive, "entries", lambda d0, d1, s, **k: (rows, None, "token")):
            for e in res["entries"]:
                try:
                    got = server.api_log({"date": d.date, "start": hm(e["s"]), "minutes": e["minutes"],
                                          "taskId": e["taskId"] or "101", "serviceId": "8101", "dry": True})
                except ValueError as exc:
                    self.fail("api_log dry: refused a proposed entry", (hm(e["s"]), e["minutes"], str(exc)))
                    continue
                if got.get("overlap"):
                    if d.dst and ov(e["s"], e["e"], *d.dst) > 0:
                        self.limits.append("the hour the clocks change is one hour on the clock")
                        continue
                    self.fail("api_log dry: a proposed entry overlaps one already logged",
                              (hm(e["s"]), e["minutes"], got["overlap"]))


    # -- the MCP server ---------------------------------------------------------------------------------------------

    def mcp(self) -> None:
        """pulsar_day, pulsar_propose, pulsar_session, pulsar_suggest, pulsar_preview and pulsar_log against a real
        Pulsar server on a free port, every source faked with this day, Productive's writes faked on top of the
        no-network guard."""
        d = self.day
        base = config.load()
        settings = {**base, "timezone": d.tzname, "person_id": "7", "org_id": "2", "person_name": "Dana Example",
                    "rules": d.rules, "likely": "on"}
        self.created: list = []
        self.records: list = []
        meetings = [dict(m) for m in d.meetings]
        mine = max(d.sessions, key=lambda s: len(s["lines"]), default=None) if d.rng.random() < 0.8 else None
        env = {"PULSAR_MCP_SESSION_ID": mine["id"] if mine else "", "CLAUDE_CODE_SESSION_ID": "",
               "PULSAR_MCP_IGNORE_LOGGED": "",
               "CLAUDE_PROJECT_DIR": repo_path(mine["repo"]) if mine and d.rng.random() < 0.7 else "",
               "PULSAR_PREVIEW": ""}
        allowed = [mine["title"]] if mine else []  # the calling session's own title may come back
        # notes the docs allow back: drafts from repeats, Ask Claude and commits (none of them secret here)

        def entries(d0, d1, s, names=None, bulk=False):
            rows = [r for r in d.history + d.logged + self.created if d0 <= r["date"] <= d1]
            return [dict(r) for r in rows], None, "token"

        def create(entry, s):
            when = datetime.fromisoformat(entry["started_at"]).timestamp()
            row = {**d.row(entry["date"], when, entry["minutes"], entry["task_id"] if entry["task_id"] in TASKS else "999"),
                   "taskId": entry["task_id"], "serviceId": entry["service_id"], "note": entry["note"]}
            self.created.append(row)
            return {"id": row["id"]}

        def record(where, message, kind="error", detail=None, once_per=None, **k):
            self.records.append((where, message, kind, detail))

        def proposal(date, tz_name, today, fresh, settings=None, keep=True, quick=False):
            return {"tracker": "ActivityWatch", "entries": json.loads(json.dumps(
                [r for r in self.proposal["entries"]]))}

        for cache in (server._rows_before, server._reading, server._repeats, server._recent, server._just_logged,
                      likely._cache, dayplan._services, mcptools._pending, mcptools._calls):
            cache.clear()
        if os.path.exists(dayplan.journal_path()):
            os.remove(dayplan.journal_path())
        patches = [
            mock.patch.object(config, "load", lambda: settings),
            mock.patch.object(server, "current_settings", lambda: settings),
            mock.patch.object(server, "public_config", lambda s: {
                "version": server.__version__, "preview": None, "timezone": d.tzname, "targetMinutes": 480,
                "writesBlocked": config.writes_blocked(), "rules": d.rules}),
            mock.patch.object(trackers, "both", lambda t0, t1, url, cls, away=None, fill=None: {"aw": [dict(it) for it in d.items], "memtime": []}),
            mock.patch.object(likely, "available", lambda: True),
            mock.patch.object(likely, "proposal", proposal),
            mock.patch.object(learn, "warm", lambda date, s: None),
            mock.patch.object(learn, "_stored", lambda date: d.stored.get(date)),
            mock.patch.object(productive, "entries", entries),
            mock.patch.object(productive, "create_entry", create),
            mock.patch.object(productive, "assigned_tasks", lambda s: self.assigned()),
            mock.patch.object(productive, "bookable_services", lambda t, date, s, names=None: d.services(t)),
            mock.patch.object(productive, "search_tasks", lambda q, s: []),
            mock.patch.object(productive, "task_details", lambda ids, s, reasons=None: {}),
            mock.patch.object(calendar, "day", lambda date, s, fresh=False: {
                "events": [dict(m) for m in meetings], "connected": True, "problem": ""}),
            mock.patch.object(calendar, "history", lambda first, n, s: list(d.past_events)),
            mock.patch.object(askclaude, "latest", lambda date: {"picks": d.picks} if d.picks else None),
            mock.patch.object(logbook, "record", record),
            mock.patch.object(mcptools, "PROJECTS", self.folder),
            mock.patch.object(mcptools, "_today", lambda tz_name: d.date),  # the made-up day is today (later days are refused)
            mock.patch.dict(os.environ, env),
        ]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            self._mcp_calls(allowed)
        for where, message, kind, detail in self.records:
            if detail or re.search(r"\b\w+(?:Error|Exception)\b", str(message)):
                self.fail("mcp: an exception in Pulsar", f"{where}: {message}")

    def call(self, name: str, args: dict, client: str = "claude-code") -> tuple:
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
                           {"client": client})
        data = json.loads(reply["result"]["content"][0]["text"])
        if reply["result"]["isError"] and re.fullmatch(r"\w+ in pulsar_\w+", str(data.get("error") or "")):
            self.fail("mcp: an exception in a tool", f"{name}: {data['error']}")
        return data, reply["result"]["isError"]

    def leaks(self, name: str, data, allowed: list) -> None:
        text = json.dumps(data, ensure_ascii=False)
        for a in allowed:
            text = text.replace(a, "")
        for s in self.day.secrets:
            if s in text:
                self.fail(f"mcp: {name} returns a private word", re.sub(r"^zq([a-z]+).*", r"\1", s))
                break

    def _mcp_calls(self, allowed: list) -> None:
        d = self.day
        date = d.date
        logged = [(server.clock(r["start"], d.t0, d.tz), max(server.clock(r["start"], d.t0, d.tz) + r["minutes"],
                                                              server.clock(r["start"] + r["minutes"] * 60, d.t0, d.tz)))
                  for r in d.logged if r["start"] is not None]
        day, err = self.call("pulsar_day", {"date": date})
        if err:
            self.fail("mcp: pulsar_day failed", day)
        else:
            self.leaks("pulsar_day", day, allowed)
            if day["totals"]["loggedMinutes"] != sum(r["minutes"] for r in d.logged):
                self.fail("mcp: pulsar_day's logged total is not the entries'", day["totals"]["loggedMinutes"])
        prop, err = self.call("pulsar_propose", {"date": date})
        if err:
            self.fail("mcp: pulsar_propose failed", prop)
            return
        self.leaks("pulsar_propose", prop, allowed)
        spans = []
        for p in prop["proposed"]:
            a = int(p["start"][:2]) * 60 + int(p["start"][3:])
            b = a + p["minutes"]
            spans.append((a, b))
            if p["pulsarWouldTick"] and (p["confidence"] == "low" or not p["taskId"] or p["flags"]):
                self.fail("mcp: pulsar_propose ticks an unsure entry", p)
            for x, y in logged:
                if ov(a, b, x, y) > 0 and not (d.dst and ov(a, b, *d.dst) > 0):
                    self.fail("mcp: pulsar_propose overlaps an entry already logged", (p["start"], p["minutes"]))
            if p.get("noteDraft") and p.get("noteFrom") not in ("repeat", "commits", "claude"):
                self.fail("mcp: a note draft from elsewhere", p)
        for x, y in zip(sorted(spans), sorted(spans)[1:]):
            if y[0] < x[1]:
                self.fail("mcp: pulsar_propose entries overlap", (x, y))
        sess, err = self.call("pulsar_session", {"date": date})
        if not err:
            self.leaks("pulsar_session", sess, allowed)
            for piece in sess["notLoggedYet"] + sess["sharedNotLogged"]:
                a = int(piece["start"][:2]) * 60 + int(piece["start"][3:])
                if any(ov(a, a + piece["minutes"], x, y) > 0 for x, y in logged) and not (d.dst and ov(a, a + piece["minutes"], *d.dst) > 0):
                    self.fail("mcp: pulsar_session offers minutes already logged", piece)
        elif os.environ.get("PULSAR_MCP_SESSION_ID"):
            self.fail("mcp: pulsar_session failed", sess)
        if spans:
            a, b = d.rng.choice(spans)
            sug, err = self.call("pulsar_suggest", {"date": date, "start": hm(a), "minutes": b - a})
            if err:
                self.fail("mcp: pulsar_suggest failed", sug)
            self.leaks("pulsar_suggest", sug, allowed)
        self._writes(prop, logged)

    def _writes(self, prop: dict, logged: list) -> None:
        """pulsar_preview never writes; pulsar_log writes only with the code and Pulsar's own summary, from Claude
        Code, with writes allowed, and only what the preview showed."""
        d = self.day
        pick = [p for p in prop["proposed"] if p["taskId"] and p["minutes"] <= 600][:5]
        if not pick:
            return
        args = {"date": d.date, "entries": [{"start": p["start"], "minutes": p["minutes"], "taskId": p["taskId"],
                                             **({"note": p["noteDraft"]} if p.get("noteDraft") else {})} for p in pick]}
        if d.rng.random() < 0.2:  # a note a client must not read
            args["entries"][0]["note"] = "Updated web/themes/acme/style.css"
        journal = json.dumps(dayplan.journal())
        dry, err = self.call("pulsar_preview", args)
        if self.created or json.dumps(dayplan.journal()) != journal:
            self.fail("mcp: pulsar_preview wrote something")
        if err:
            if "overlap" not in json.dumps(dry):
                self.fail("mcp: pulsar_preview failed", dry)
            return
        self.leaks("pulsar_preview", dry, [])
        if not dry["code"]:
            if not any(e.get("problem") for e in dry["entries"]):
                self.fail("mcp: no code and no problem", dry)
            return
        if any(e.get("problem") for e in dry["entries"]):
            self.fail("mcp: a code despite a problem", dry)
        code, summary = dry["code"], dry["summary"]
        for wrong in ({"code": code, "summary": summary + " "}, {"code": code, "summary": summary.replace("min", "mins")},
                      {"code": "deadbeef", "summary": summary}, {"code": code}):
            got, err = self.call("pulsar_log", wrong)
            if not err or self.created:
                self.fail("mcp: pulsar_log wrote without the code and Pulsar's summary", wrong)
        got, err = self.call("pulsar_log", {"code": code, "summary": summary}, client="cursor")
        if not err or self.created:
            self.fail("mcp: pulsar_log wrote from an app that does not ask the person")
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):
            got, err = self.call("pulsar_log", {"code": code, "summary": summary})
        if not err or self.created:
            self.fail("mcp: pulsar_log wrote while writes are blocked")
        before = list(self.created)
        with mock.patch.object(config, "writes_blocked", lambda: None), \
                mock.patch.object(mcp, "_read_only", lambda: False):  # https and the network stay refused
            got, err = self.call("pulsar_log", {"code": code, "summary": summary})
            again, err2 = self.call("pulsar_log", {"code": code, "summary": summary})
        if err:
            self.fail("mcp: pulsar_log refused a good code", got)
            return
        if not err2 or len(self.created) != got["written"] + len(before):
            self.fail("mcp: pulsar_log wrote twice, or not what it says", (got["written"], len(self.created)))
        held = {(e["start"], e["minutes"], e["taskId"], e["serviceId"], e["note"]) for e in dry["entries"]}
        for c in self.created:
            t = datetime.fromtimestamp(c["start"], d.tz)
            key = (t.strftime("%H:%M"), c["minutes"], c["taskId"], c["serviceId"], c["note"])
            if key not in held:
                self.fail("mcp: pulsar_log wrote an entry the preview did not show", key)
            a = server.clock(c["start"], d.t0, d.tz)
            if any(ov(a, a + c["minutes"], x, y) > 0 for x, y in logged):
                self.fail("mcp: pulsar_log wrote over an entry already logged", key)


class NoNetwork(AssertionError):
    pass


def _refuse(*a, **k):
    raise NoNetwork("the simulation never reaches Productive, Google or the keychain")


@contextlib.contextmanager
def no_network():
    """Every way out of this process refused: HTTPS, Productive's reads and writes, the keychain and `claude mcp get`
    (productive's subprocess). A step that needs Productive gets a fake on top of this."""
    import http.client
    with contextlib.ExitStack() as stack:
        for target, name in ((productive, "https"), (http.client, "HTTPSConnection"), (productive, "create_entry"),
                             (productive, "update_entry"), (productive, "delete_entry"), (productive, "entries"),
                             (productive, "assigned_tasks"), (productive, "bookable_services"),
                             (productive, "search_tasks"), (productive, "task_details"), (calendar, "day"),
                             (calendar, "history")):
            stack.enter_context(mock.patch.object(target, name, _refuse))
        stack.enter_context(mock.patch.object(productive, "subprocess", mock.Mock(run=_refuse)))
        stack.enter_context(mock.patch.object(productive, "connection", lambda s: {"kind": None, "problem": "simulated"}))
        yield


def run_plan(day: dict, ctx: dict) -> tuple:
    """dayplan.plan, with where each minute went (what it keeps for ranges.py: owner, claims, work, away, logging)."""
    kept: dict = {}
    res = dayplan.plan(day, ctx, keep=kept)
    return res, kept


_httpd: list = []
LIMITS: dict = {}  # documented limits the days met: counted, never failures


def start_server() -> None:
    """One Pulsar server for the MCP days, on a free port of 127.0.0.1; stop_server() stops it."""
    if not _httpd:
        from http.server import ThreadingHTTPServer
        config.ensure_dirs()
        config.session_secret()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        mcp._state.update({"port": httpd.server_address[1], "verified": False})
        _httpd.append(httpd)


def stop_server() -> None:
    while _httpd:
        httpd = _httpd.pop()
        httpd.shutdown()
        httpd.server_close()


STEPS_READ = [0]  # steps of typed words askclaude.steps found in the made-up sessions: the words checks had words


def simulate(seed: int, with_mcp: bool = False) -> list:
    """One day through every piece; the problems found. Every reader of Claude Code's sessions reads the day's own
    made-up ones (sessions.PROJECTS_DIR), so the typed words planted there are what the leak checks look for."""
    day = Day(seed)
    run = Run(day)
    if with_mcp:
        start_server()
    real_steps = askclaude.steps

    def steps(*a, **k):
        got = real_steps(*a, **k)
        STEPS_READ[0] += len(got)
        return got
    try:
        with no_network(), mock.patch.object(sessions, "PROJECTS_DIR", run.folder), \
                mock.patch.object(askclaude, "steps", steps):
            _simulate(run, with_mcp)
    except NoNetwork as exc:
        run.fail("network", str(exc))
    finally:
        run.close()
    for k in run.limits:
        LIMITS[k] = LIMITS.get(k, 0) + 1
    return [f"seed {seed} ({day.tzname} {day.date} {day.style}{' malformed' if day.malformed else ''}): {p}"
            for p in run.problems]


def _simulate(run, with_mcp: bool) -> None:
    try:
        run.likely()
        run.check_likely()
        run.learned(run.proposal["entries"])
        plan_day, ctx = run.plan_inputs()
        run.check_away(run.items_clock, server.away(run.items_clock))
        run.check_repeats(plan_day["repeats"])
        res, loc = run_plan(plan_day, ctx)
        run.check_plan(plan_day, ctx, res, loc)
        run.check_log_dry(res)
        if with_mcp:
            run.mcp()
    except NoNetwork:
        raise
    except Exception as exc:  # no exception anywhere
        import traceback
        run.fail("exception", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


_make_repos()


class SimulatedDaysTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        stop_server()

    def test_the_guard_refuses_every_way_out(self):
        with no_network():
            for f, args in ((productive.create_entry, ({}, {})), (productive.https, ("POST", "https://x.example/")),
                            (productive.entries, ("2026-11-04", "2026-11-04", {}))):
                with self.assertRaises(NoNetwork):
                    f(*args)

    def test_many_made_up_days_keep_every_promise(self):
        first = int(os.environ.get("PULSAR_SIM_FROM") or 1)
        count = int(os.environ.get("PULSAR_SIM") or QUICK_SEEDS)
        every = int(os.environ.get("PULSAR_SIM_MCP_EVERY") or QUICK_MCP_EVERY)
        problems = []
        for seed in range(first, first + count):
            problems += simulate(seed, with_mcp=every > 0 and seed % every == 0)
        self.assertGreater(STEPS_READ[0], 0, "no typed words were read: the words checks checked nothing")
        if os.environ.get("PULSAR_SIM"):
            print(f"\n{count} days from seed {first}, every {every}th through the MCP tools; documented limits met: "
                  f"{LIMITS or 'none'}", file=sys.stderr)
        if problems:
            kinds: dict = {}
            for p in problems:
                k = ": ".join(p.split(": ")[1:3])
                kinds.setdefault(k, []).append(p)
            report = "\n".join(f"{len(v)} x {k}\n    e.g. {v[0][:600]}" for k, v in sorted(kinds.items(), key=lambda kv: -len(kv[1])))
            self.fail(f"{len(problems)} problems in {count} days:\n{report}")


if __name__ == "__main__":
    unittest.main()
