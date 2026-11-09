"""Parallel work: which session, repository and client each minute goes to when several agents run at once.

Made-up sessions in made-up repositories, made-up days and made-up clients; no network, no trackers.

    python3 -m unittest tests.test_attribution
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import attribution, dayplan, sessions  # noqa: E402

T0 = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()


def at(minute: float) -> str:
    return datetime.fromtimestamp(T0 + minute * 60, timezone.utc).isoformat().replace("+00:00", "Z")


def repo(root: str, name: str) -> str:
    path = os.path.join(root, "code", name)
    os.makedirs(os.path.join(path, ".git"))
    os.makedirs(os.path.join(path, "src"))
    with open(os.path.join(path, ".git", "config"), "w") as fh:
        fh.write(f'[remote "origin"]\n\turl = git@github.com:example/{name}.git\n')
    return path


def typed(minute, text, cwd):
    return {"type": "user", "timestamp": at(minute), "cwd": cwd, "origin": {"kind": "human"}, "message": {"content": text}}


def call(minute, name, **inp):
    return {"type": "assistant", "timestamp": at(minute),
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def said(minute, text="ok"):
    return {"type": "assistant", "timestamp": at(minute), "message": {"content": [{"type": "text", "text": text}]}}


def result(minute):
    return {"type": "user", "timestamp": at(minute), "toolUseResult": {},
            "message": {"content": [{"type": "tool_result", "content": "done"}]}}


def note(minute):
    return {"type": "user", "timestamp": at(minute), "origin": {"kind": "task-notification"},
            "message": {"content": "<task-notification>finished</task-notification>"}}


class Fixture(unittest.TestCase):
    def setUp(self):
        sessions._repo_of.clear()
        sessions._exists.clear()
        self.root = tempfile.mkdtemp()
        self.acme = repo(self.root, "acme-site")      # a client's site
        self.tool = repo(self.root, "house-tool")     # the agency's own tool
        self.projects = os.path.join(self.root, "projects")
        os.makedirs(os.path.join(self.projects, "-work"))

    def write(self, sid: str, lines: list, title="Work", subagents: dict | None = None):
        path = os.path.join(self.projects, "-work", f"{sid}.jsonl")
        with open(path, "w") as fh:
            fh.write("\n".join(json.dumps(x) for x in [{"type": "custom-title", "customTitle": title}] + lines) + "\n")
        for name, sub in (subagents or {}).items():
            folder = os.path.join(self.projects, "-work", sid, "subagents")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{name}.jsonl"), "w") as fh:
                fh.write("\n".join(json.dumps(x) for x in sub) + "\n")
        return path

    def load(self):
        return sessions.load(T0, T0 + 86400, self.projects)

    def editor(self, s, e, title):
        return {"s": s, "e": e, "app": "Cursor", "title": title, "host": "", "url": "", "cat": "work"}


class TurnRepositoryTest(Fixture):
    """A turn's repository is where its calls did the work, weighted: Edit/Write 3, a command in a folder 2, a read
    or a search 1, a typed path 1, a word 0."""

    def test_an_edit_outweighs_reads_and_a_word_counts_nothing(self):
        acme = os.path.join(self.acme, "src", "a.js")
        tool = os.path.join(self.tool, "src", "t.py")
        self.write("aaaa0001", [
            typed(10, "why does acme-site's form break?", self.tool),
            call(11, "Read", file_path=acme), call(12, "Grep", pattern="acme-site", path=acme),
            call(13, "Edit", file_path=tool),
            call(14, "Bash", command="echo acme-site acme-site acme-site"),  # words, no path
        ])
        [s] = self.load()
        self.assertEqual(s["turns"][0]["project"], "house-tool")  # 3 against 1 + 1
        self.assertEqual(s["turns"][0]["hard"], {"house-tool": 3})

    def test_a_command_in_a_folder_is_hard_and_a_path_that_does_not_exist_counts_nothing(self):
        self.write("aaaa0002", [
            typed(10, "run the tests", self.acme),
            call(11, "Bash", command=f"cd {self.acme} && npm test"),
            call(12, "Bash", command=f"cat {os.path.join(self.root, 'nowhere', 'x', 'y.txt')}"),
        ])
        [s] = self.load()
        self.assertEqual((s["turns"][0]["project"], s["turns"][0]["hard"]), ("acme-site", {"acme-site": 2}))

    def test_paths_written_with_a_tilde_or_home_count_as_the_full_path(self):
        """Commands often say cd ~/Sites/x or git -C $HOME/Sites/x: that is the repository in the home folder. (A turn
        that only did this was named after the session's title, and offered the tasks logged under that title.)"""
        site = repo(sessions.HOME, "tilde-site")
        self.addCleanup(lambda: __import__("shutil").rmtree(os.path.join(sessions.HOME, "code"), ignore_errors=True))
        rel = os.path.relpath(site, sessions.HOME)
        self.write("aaaa0006", [typed(10, "prepare the db upgrade", self.root),
                                call(11, "Bash", command=f"cd ~/{rel} && terminus env:list site"),
                                call(12, "Bash", command=f"git -C $HOME/{rel} status"),
                                call(13, "Read", file_path=f"~/{rel}/src/a.js")])
        [s] = self.load()
        self.assertEqual((s["turns"][0]["project"], s["turns"][0]["hard"]), ("tilde-site", {"tilde-site": 4}))
        self.assertEqual(sessions.find_paths("x~/a b/~/c ~/d"), [os.path.join(sessions.HOME, "d")])

    def test_lines_outside_the_day_are_skipped_before_they_are_parsed_and_the_read_is_the_same(self):
        lo_s, hi_s = sessions._bounds(T0 - 6 * 3600, T0 + 86400 + 3600)
        self.assertTrue(sessions._outside('{"type":"user","timestamp":"2020-01-01T00:00:00.000Z"}', lo_s, hi_s))
        inside = at(10)
        # a time of its own inside the day, after one inside the content that is not: parsed
        self.assertFalse(sessions._outside('{"x":{"timestamp":"2020-01-01T00:00:00Z"},"timestamp":"%s"}' % inside, lo_s, hi_s))
        self.assertFalse(sessions._outside('{"timestamp":"2026-11-04T10:00:00+02:00"}', lo_s, hi_s))  # not UTC: parsed
        self.assertFalse(sessions._outside('{"type":"summary"}', lo_s, hi_s))
        old = {"type": "user", "timestamp": "2020-01-01T00:00:00.000Z", "cwd": "/elsewhere", "origin": {"kind": "human"},
               "message": {"content": "long ago"}}
        self.write("aaaa0007", [old, typed(10, "today", self.acme), call(11, "Edit", file_path=os.path.join(self.acme, "a.js"))])
        [s] = self.load()
        self.assertEqual(([t for _, t in s["prompts"]], s["cwd"]), (["today"], self.acme))

    def test_a_subagents_calls_count_for_the_turn_that_started_it(self):
        self.write("aaaa0003", [typed(10, "look into it", self.root), call(11, "Task", prompt=f"edit {self.tool}"),
                                said(30)],
                   subagents={"agent-1": [call(12, "Edit", file_path=os.path.join(self.acme, "src", "a.js")),
                                          call(20, "Edit", file_path=os.path.join(self.acme, "src", "b.js"))]})
        [s] = self.load()
        self.assertEqual(s["turns"][0]["project"], "acme-site")  # the Task prompt's path counts nothing
        self.assertEqual(s["turns"][0]["hard"], {"acme-site": 6})

    def test_a_call_on_time_entries_is_named_only_by_a_namer_that_knows_it(self):
        """The fi-time-from-activity script names a Productive call on time entries "time logging"; Pulsar's own
        names never see it as a project."""
        self.write("aaaa0005", [typed(10, "log my time", self.acme),
                                call(11, "mcp__productive__create_resource", resource="time_entries", attributes={}),
                                call(12, "mcp__productive__query_resources", resource="time_entries"),
                                call(13, "Read", file_path=os.path.join(self.acme, "src", "a.js"))])
        [s] = self.load()
        self.assertEqual(s["turns"][0]["project"], "acme-site")
        namer = lambda p: "time logging" if p == "tool:mcp__productive__time_entries" else sessions.project_of_path(p)  # noqa: E731
        [s] = sessions.load(T0, T0 + 86400, self.projects, namer)
        self.assertEqual(s["turns"][0]["project"], "time logging")

    def test_with_the_skills_naming_a_turn_is_named_as_the_skill_counts_it(self):
        """With the fi-time-from-activity script, each turn is named as it names it: every string of the turn's calls
        and what was typed counted, words too (one person's 52 days: the right task 33.9% of minutes, 30.4% with the
        weights). Its hard calls count for that project. Without the script, the weights; a naming that fails on a
        session leaves that session to the weights."""
        self.write("aaaa0006", [typed(10, "the house-tool hub: rerun house-tool checks", self.acme),
                                call(11, "Bash", command=f"cd {self.acme} && git status"),
                                call(12, "Bash", command="echo house-tool house-tool")])

        def count(strings, counts):
            for s in strings:
                n = s.count("house-tool")
                if n:
                    counts["house-tool"] = counts.get("house-tool", 0) + n

        def strings_of(r):
            return [str(x.get("input")) for x in r["message"]["content"] if x.get("type") == "tool_use"]
        [s] = self.load()
        self.assertEqual((s["turns"][0]["project"], s["turns"][0]["hard"]), ("acme-site", {"acme-site": 2}))
        [s] = sessions.load(T0, T0 + 86400, self.projects, turns={"strings": strings_of, "count": count})
        self.assertEqual((s["turns"][0]["project"], s["turns"][0]["hard"]), ("house-tool", {"house-tool": 2}))

        def broken(strings, counts):
            raise ValueError("another shape")
        [s] = sessions.load(T0, T0 + 86400, self.projects, turns={"strings": strings_of, "count": broken})
        self.assertEqual(s["turns"][0]["project"], "acme-site")
        # a turn nothing names takes the session's title without a leading IMPORTANT!, as the script does
        self.write("aaaa0006", [typed(10, "how is it going?", "/nowhere")], title="IMPORTANT! Weekly plan")
        [s] = sessions.load(T0, T0 + 86400, self.projects, turns={"strings": strings_of, "count": count})
        self.assertEqual(s["turns"][0]["project"], "Weekly plan")

    def test_one_session_splits_by_the_repository_of_each_turn(self):
        self.write("aaaa0004", [
            typed(10, "fix the client form", self.tool), call(11, "Edit", file_path=os.path.join(self.acme, "src", "a.js")),
            typed(40, "now the tool", self.tool), call(41, "Edit", file_path=os.path.join(self.tool, "src", "t.py")),
        ], title="Mixed work")
        got = sessions.propose(T0, T0 + 86400, [self.editor(9, 70, "Mixed work")], "ActivityWatch", self.projects)
        self.assertEqual([(r["project"], r["s"], r["e"]) for r in got["entries"]], [("acme-site", 9, 40), ("house-tool", 40, 70)])
        self.assertFalse(any(r["single"] for r in got["entries"]))  # the session was in two repositories


class ChainTest(Fixture):
    """An agent's stretch counts as attended while it runs on a chain started by a typed message; a silence of more
    than 10 minutes ends it, and only the next typed message starts another."""

    def test_up_to_ten_minutes_of_silence_counts_and_more_does_not(self):
        self.write("bbbb0001", [typed(0, "go", self.acme), call(1, "Edit", file_path=os.path.join(self.acme, "a")),
                                result(2), said(12),           # 10 minutes of silence: still the stretch
                                said(23),                       # 11 minutes: the chain ends at 12
                                note(24), said(26)])           # a notification does not start it again
        [s] = self.load()
        self.assertEqual([(round((a - T0) / 60), round((b - T0) / 60)) for a, b in s["chains"]], [(0, 12)])

    def test_a_notification_carries_a_live_chain_on(self):
        self.write("bbbb0002", [typed(0, "go", self.acme), call(1, "Edit", file_path=os.path.join(self.acme, "a")),
                                said(5), note(9), said(14)])
        [s] = self.load()
        self.assertEqual([(round((a - T0) / 60), round((b - T0) / 60)) for a, b in s["chains"]], [(0, 14)])

    def test_a_chain_with_no_typed_message_never_counts(self):
        self.write("bbbb0003", [typed(0, "go", self.acme), said(1), note(200), said(205), said(210)])
        [s] = self.load()
        self.assertEqual([(round((a - T0) / 60), round((b - T0) / 60)) for a, b in s["chains"]], [(0, 1)])


class BackgroundTest(Fixture):
    """An agent working while another session is on screen: the background, at its real time, never a row of the
    Likely column's own."""

    def setUp(self):
        super().setUp()
        self.write("cccc0001", [typed(0, "build it", self.tool)] + [call(m, "Edit", file_path=os.path.join(self.tool, "x.py"))
                                                                 for m in range(1, 60, 4)], title="Tool work")
        # a client's agent, started by a typed message, working on its own from 5 to 40, then silent, then again
        self.write("cccc0002", [typed(5, "migrate the client's content", self.acme)]
                   + [call(m, "Edit", file_path=os.path.join(self.acme, "src", "m.js")) for m in range(6, 41, 3)]
                   + [said(55), said(58)], title="Client migration")

    def test_the_background_is_at_its_real_time_split_where_it_stops_counting(self):
        got = sessions.propose(T0, T0 + 86400, [self.editor(0, 60, "Tool work")], "ActivityWatch", self.projects)
        self.assertEqual([(r["project"], r["s"], r["e"]) for r in got["entries"]], [("house-tool", 0, 60)])
        bg = [(r["project"], r["s"], r["e"], r["qualifies"]) for r in got["background"]]
        self.assertEqual(bg, [("acme-site", 5, 40, True), ("acme-site", 40, 59, False)])
        self.assertEqual(got["background"][0]["sessions"], ["cccc0002"])
        self.assertEqual(got["proposed"], 60)  # the background adds nothing to the column's own minutes

    def test_a_turn_with_only_reads_in_the_repository_never_qualifies(self):
        self.write("cccc0003", [typed(5, "read the client's code", self.acme)]
                   + [call(m, "Read", file_path=os.path.join(self.acme, "src", "m.js")) for m in range(6, 30, 3)],
                   title="Client reading")
        got = sessions.propose(T0, T0 + 86400, [self.editor(0, 60, "Tool work")], "ActivityWatch", self.projects)
        reading = [r for r in got["background"] if r["sessions"] == ["cccc0003"]]
        self.assertTrue(reading and not any(r["qualifies"] for r in reading))

    def test_a_link_counts_only_in_the_repository_it_was_typed_for(self):
        self.write("cccc0004", [
            typed(100, "https://app.productive.io/1-x/tasks/777 fix this", self.tool),
            call(101, "Edit", file_path=os.path.join(self.acme, "src", "a.js")),
            typed(130, "and the tool", self.tool), call(131, "Edit", file_path=os.path.join(self.tool, "t.py")),
        ], title="Linked work")
        got = sessions.propose(T0, T0 + 86400, [self.editor(100, 160, "Linked work")], "ActivityWatch", self.projects)
        rows = {r["project"]: r for r in got["entries"] if r["sessions"] == ["cccc0004"]}
        self.assertEqual(rows["acme-site"]["repoLinks"], ["777"])
        self.assertEqual(rows["house-tool"]["repoLinks"], [])
        sessions.attach_tasks(list(rows.values()), T0, sessions.task_links(T0, self.projects))
        self.assertEqual(rows["acme-site"].get("tasks"), ["777"])
        self.assertNotIn("tasks", rows["house-tool"])  # the session's link is not the tool's

    def test_a_naming_function_and_time_spent_logging(self):
        """likely.py names projects with the fi-time-from-activity script's rules when it is installed; its name for
        time spent logging time takes the minutes from every session."""
        namer = lambda p: "logging" if "house-tool" in p else ("Client" if "acme-site" in p else None)  # noqa: E731
        got = sessions.propose(T0, T0 + 86400, [self.editor(0, 60, "Tool work")], "ActivityWatch", self.projects,
                               namer=namer, logging_name="logging")
        self.assertEqual(got["entries"], [])
        self.assertEqual(got["logging"], 60)
        self.assertTrue(got["background"] and all(r["project"] == "Client" for r in got["background"]))


# ---- the planner ---------------------------------------------------------------------------------------------------

DATE = "2026-11-04"
KEY = dayplan.title_key("Mixed session")
TASKS = {  # made-up tasks: a client's, the agency's own tool, the agency's QA hub
    "C1": ("Clientco support", "Clientco - Ongoing Support", True),
    "C2": ("Clientco QA fixes", "Clientco - Ongoing Support", True),
    "T1": ("Build the tool", "FI-AI Tools", False),
    "Q1": ("QA hub upkeep", "FI-AI QA", False),
    "M1": ("Mixed retainer", "Mixedco - Retainer", None),
}


def t0_of(d):
    return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def entry(d, start, minutes, task):
    title, project, billable = TASKS[task]
    return {"date": d, "start": t0_of(d) + start * 60, "minutes": minutes, "taskId": task, "serviceId": "s" + task,
            "service": "Dev", "task": title, "project": project, "billable": billable}


def history(days=8, extra=None):
    """Past days of one session (KEY) in three repositories, each logged to its own task; extra: {date: (rows, entries)}."""
    stored, hist = {}, []
    for i in range(1, days + 1):
        d = f"2026-10-{28 - i:02d}"
        stored[d] = [{"k": [KEY], "p": "client-web", "s": 540, "e": 600}, {"k": [KEY], "p": "house-tool", "s": 600, "e": 720},
                     {"k": [KEY], "p": "qa-hub", "s": 720, "e": 780}]
        hist += [entry(d, 540, 60, "C1"), entry(d, 600, 120, "T1"), entry(d, 720, 60, "Q1")]
    hist.append(entry("2026-10-10", 900, 30, "C2"))
    for d, (rows, entries) in (extra or {}).items():
        stored[d] = rows
        hist += entries
    return dayplan.learn(DATE, hist, stored, t0_of, person="Nick Example"), hist, stored


def work(s, e, app="Cursor", title="Mixed session", cat="work"):
    return {"s": s, "e": e, "app": app, "title": title, "host": "", "url": "", "cat": cat}


def front(s, e, project, **kw):
    return {"s": s, "e": e, "minutes": e - s, "project": project, "kind": "focused", "titles": ["Mixed session"],
            "first_message": "", "tasks": [], "sessions": ["aaaa0001"], **kw}


def back(s, e, project, qualifies=True, **kw):
    return {"s": s, "e": e, "minutes": e - s, "project": project, "kind": "background", "titles": ["Client agent"],
            "first_message": "", "tasks": [], "sessions": ["bbbb0002"], "qualifies": qualifies, **kw}


def plan(items, rows, background=(), ctx=None, **kw):
    base = {"items": items, "away": [], "rows": rows, "background": list(background), "logged": [], "meetings": [],
            "repeats": [], "calls": [], "rules": [], "assigned": [], "picks": {}}
    return dayplan.plan({**base, **kw}, ctx or history()[0])


def client_minutes(p):
    return sum(e["minutes"] for e in p["entries"] if attribution.class_of_task(e["taskId"], history()[0]) == "client")


class LearningCreditTest(unittest.TestCase):
    """A logged minute teaches the unit whose repository is logged to the task's project, not the one in front."""

    def test_a_client_logged_while_internal_work_was_in_front_teaches_the_clients_repository(self):
        d = "2026-10-29"
        rows = [{"k": [KEY], "p": "house-tool", "s": 600, "e": 720},
                {"k": [dayplan.title_key("Client agent")], "p": "client-web", "s": 600, "e": 720, "bg": 1, "q": 1}]
        ctx, _, _ = history(extra={d: (rows, [entry(d, 600, 120, "C1")])})
        self.assertNotIn("C1", ctx["repo"]["house-tool"])            # the tool never learns the client's task
        self.assertIn("C1", ctx["sr"][(dayplan.title_key("Client agent"), "client-web")])
        self.assertEqual(ctx["classes"], {"client-web": "client", "house-tool": "internal", "qa-hub": "internal"})

    def test_with_no_history_to_tell_them_apart_the_row_in_front_learns_as_before(self):
        d = "2026-10-29"
        rows = [{"k": [KEY], "p": "brand-new", "s": 600, "e": 660},
                {"k": [KEY], "p": "other-new", "s": 600, "e": 660, "bg": 1}]
        ctx, _, _ = history(extra={d: (rows, [entry(d, 600, 60, "C1")])})
        self.assertIn("C1", ctx["repo"]["brand-new"])

    def test_pulsars_first_name_is_pulsar(self):
        self.assertEqual(attribution.repo_key("chronodeck"), "pulsar")
        self.assertEqual(attribution.repo_key("pulsar-preview"), "pulsar")
        self.assertEqual(attribution.repo_key("client-web"), "client-web")


class BillingClassTest(unittest.TestCase):
    def test_classes_from_history_name_and_what_the_work_names(self):
        ctx = history()[0]
        self.assertEqual(attribution.segment_class("client-web", ctx), "client")
        self.assertEqual(attribution.segment_class("house-tool", ctx), "internal")
        self.assertEqual(attribution.segment_class("clientco-theme", ctx), "client")    # its name points to Clientco
        self.assertEqual(attribution.segment_class("somewhere", ctx), "unknown")
        self.assertEqual(attribution.segment_class("qa-hub", ctx, hint_task="C2"), "client")  # a link to a client's task
        self.assertEqual(attribution.repo_class({"client": 50, "internal": 0}), "mixed")    # under an hour: not yet
        self.assertEqual(attribution.repo_class({"client": 70, "internal": 30}), "mixed")
        self.assertEqual(attribution.entry_class("FI-AI Tools", True), "internal")          # the agency's own project
        self.assertEqual(attribution.entry_class("Clientco - Ongoing Support", True), "client")  # fixed price counts


class FailureCaseTest(unittest.TestCase):
    """One session works in a client's repository, then the agency's own tool, then its QA hub with a client's task
    link pasted: each stretch gets its own repository's task, and the tool's hours never go to the client."""

    def test_each_segment_gets_its_own_repositorys_task(self):
        d = "2026-10-29"  # the day the tool learned the client's task under the old rule
        rows = [{"k": [KEY], "p": "house-tool", "s": 600, "e": 720},
                {"k": [dayplan.title_key("Client agent")], "p": "client-web", "s": 600, "e": 720, "bg": 1, "q": 1}]
        ctx, _, _ = history(extra={d: (rows, [entry(d, 600, 120, "C1")])})
        today = [front(540, 600, "client-web", single=False), front(600, 790, "house-tool", single=False),
                 front(790, 820, "qa-hub", single=False, tasks=["C2"])]
        p = plan([work(540, 820)], today, ctx=ctx)
        by = [(e["s"], e["e"], e["taskId"]) for e in p["entries"]]
        self.assertEqual(by, [(540, 600, "C1"), (600, 790, "T1"), (790, 820, "C2")])
        qa = p["entries"][-1]
        self.assertEqual((qa["source"], qa["class"]), ("link", "client"))  # the link decides the task and the class

    def test_on_internal_work_a_client_task_only_from_what_the_work_names(self):
        ctx = history()[0]
        # the session's vote for the client passes its own test (the client has 31% of the tool's history) ...
        ctx = {**ctx, "sr": {}, "sess": {KEY: {"C1": 500.0}}, "repo": {**ctx["repo"], "house-tool": {"C1": 90.0, "T1": 200.0}}}
        [e] = plan([work(600, 660)], [front(600, 660, "house-tool", single=True)], ctx=ctx)["entries"]
        self.assertEqual(e["taskId"], "T1")                          # ... and is still never first on internal work
        self.assertIn("C1", [a["taskId"] for a in e["alts"]])         # it stays an alternative
        # a link in that work to a client's task decides it
        [e] = plan([work(600, 660)], [front(600, 660, "house-tool", single=True, tasks=["C2"])], ctx=ctx)["entries"]
        self.assertEqual((e["taskId"], e["source"]), ("C2", "link"))

    def test_no_task_rather_than_a_wrong_billable_one(self):
        ctx = history()[0]
        ctx = {**ctx, "sr": {}, "sess": {KEY: {"C1": 500.0}}, "repo": {}, "classes": {"internal-only": "internal"},
               "recent": []}
        [e] = plan([work(600, 660)], [front(600, 660, "internal-only", single=True)], ctx=ctx)["entries"]
        self.assertIsNone(e["taskId"])
        self.assertFalse(e["checked"])


class SharedTest(unittest.TestCase):
    """A client's agent, attended, while internal work is in front: the client goes first, as a shared row."""

    def setUp(self):
        self.ctx = history()[0]

    def test_the_client_gets_the_minutes_as_a_shared_row_ticked_with_its_choice(self):
        p = plan([work(600, 700)], [front(600, 700, "house-tool")], [back(620, 660, "client-web")], ctx=self.ctx)
        by = [(e["s"], e["e"], e["taskId"], e.get("shared", False)) for e in p["entries"]]
        self.assertEqual(by, [(600, 620, "T1", False), (620, 660, "C1", True), (660, 700, "T1", False)])
        shared = p["entries"][1]
        self.assertTrue(shared["checked"])                          # ticked, with a note to check it
        self.assertIn("shared", shared["flags"])
        self.assertEqual((shared["choice"], shared["sharedWith"]["taskId"]), ("client", "T1"))
        self.assertEqual(p["totals"]["shared"], 40)
        self.assertEqual(p["totals"]["parallel"], 40)

    def test_a_shared_stretch_with_a_gap_in_it_is_one_entry(self):
        # 640-642: a glance at Pulsar itself (logging time) inside the agent's stretch: still one shared entry
        items = [work(600, 640), work(640, 642, app="Pulsar", title="Pulsar"), work(642, 700)]
        p = plan(items, [front(600, 700, "house-tool")], [back(620, 660, "client-web")], ctx=self.ctx)
        shared = [(e["s"], e["e"]) for e in p["entries"] if e.get("shared")]
        self.assertEqual(shared, [(620, 655)])  # one entry; the glance is not the client's (38 minutes seen: 35 on the grid)

    def test_the_clients_own_session_flipping_into_the_front_does_not_cut_a_shared_stretch(self):
        rows = [front(600, 625, "house-tool"), front(625, 627, "client-web"), front(627, 640, "house-tool"),
                front(640, 641, "client-web"), front(641, 700, "house-tool")]
        p = plan([work(600, 700)], rows, [back(610, 650, "client-web")], ctx=self.ctx)
        shared = [(e["s"], e["e"]) for e in p["entries"] if e.get("shared")]
        self.assertEqual(len(shared), 1, p["entries"])  # one stretch, not one entry per piece between the flips
        self.check_no_overlap(p)

    def check_no_overlap(self, p):
        spans = sorted((e["s"], e["e"]) for e in p["entries"])
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0, spans)

    def test_half_each_and_internal_keep_the_real_time(self):
        p = plan([work(600, 700)], [front(600, 700, "house-tool")], [back(620, 660, "client-web")], ctx=self.ctx)
        shared = {**p["entries"][1], "sharedWith": {**p["entries"][1]["sharedWith"], "serviceId": "sT1"}}
        a, b = dayplan.apply_choice(shared, "half")
        self.assertEqual([(a["s"], a["e"], a["taskId"]), (b["s"], b["e"], b["taskId"])], [(620, 640, "C1"), (640, 660, "T1")])
        [i] = dayplan.apply_choice(shared, "internal")
        self.assertEqual((i["s"], i["e"], i["taskId"], i["serviceId"], i["choice"]), (620, 660, "T1", "sT1", "internal"))
        self.assertEqual(dayplan.apply_choice(shared, "client")[0]["taskId"], "C1")

    def test_a_contested_stretch_under_ten_minutes_stays_with_the_work_in_front(self):
        p = plan([work(600, 700)], [front(600, 700, "house-tool")], [back(620, 628, "client-web")], ctx=self.ctx)
        self.assertFalse(any(e.get("shared") for e in p["entries"]))
        self.assertEqual(client_minutes(p), 0)

    def test_soft_evidence_mixed_and_unknown_get_no_priority(self):
        for bg, why in ((back(620, 660, "client-web", qualifies=False), "not attended: silent, not typed, no hard calls"),
                        (back(620, 660, "somewhere-new"), "unknown class"),
                        (back(620, 660, "mixed-repo"), "mixed class")):
            ctx = {**self.ctx, "classes": {**self.ctx["classes"], "mixed-repo": "mixed"}}
            p = plan([work(600, 700)], [front(600, 700, "house-tool")], [bg], ctx=ctx)
            self.assertFalse(any(e.get("shared") for e in p["entries"]), why)
            self.assertEqual(client_minutes(p), 0, why)
            self.assertEqual([(n["project"], n["why"]) for n in p["skipped"]], [(bg["project"], "ran in parallel")], why)

    def test_a_clients_work_in_front_is_never_given_to_another_clients_agent(self):
        p = plan([work(540, 600)], [front(540, 600, "client-web")], [back(550, 590, "clientco-theme")], ctx=self.ctx)
        self.assertEqual([(e["s"], e["e"], e.get("shared", False)) for e in p["entries"]], [(540, 600, False)])

    def test_away_personal_and_idle_minutes_are_never_promoted(self):
        items = [work(600, 630), work(630, 640, app="Telegram", cat="personal"), work(650, 700)]  # 640-650: nothing
        p = plan(items, [front(600, 700, "house-tool")], [back(600, 700, "client-web")], ctx=self.ctx,
                 away=[{"s": 675, "e": 690}])
        shared = [x for e in p["entries"] if e.get("shared") for x in range(e["s"], e["e"])]
        self.assertTrue(shared)
        for x in shared:
            self.assertFalse(630 <= x < 650 or 675 <= x < 690, x)

    def test_the_internal_background_is_noted_never_placed(self):
        p = plan([work(540, 600)], [front(540, 600, "client-web")], [back(540, 590, "house-tool")], ctx=self.ctx)
        self.assertEqual(sum(e["minutes"] for e in p["entries"]), 60)
        self.assertEqual([(n["project"], n["minutes"], n["why"]) for n in p["skipped"]], [("house-tool", 50, "ran in parallel")])


class InvariantTest(unittest.TestCase):
    """On every made-up day: billable proposed never more than the minutes seen, nothing overlaps, the cap holds and
    cuts internal work first."""

    def setUp(self):
        self.ctx = history()[0]

    def check(self, p, seen):
        spans = sorted((e["s"], e["e"]) for e in p["entries"])
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0, spans)
        self.assertLessEqual(p["totals"]["proposed"] + p["totals"]["logged"], p["totals"]["cap"])
        for task, most in seen.items():
            self.assertLessEqual(sum(e["minutes"] for e in p["entries"] if e["taskId"] == task), most, task)

    def test_billable_never_more_than_the_minutes_seen(self):
        # 48 minutes of client work under a 60-minute row: the grid would make 50
        p = plan([work(540, 588)], [front(540, 600, "client-web")], ctx=self.ctx)
        self.check(p, {"C1": 48})
        self.assertEqual(client_minutes(p), 45)
        # shared: at most the attended minutes in front of internal work
        p = plan([work(600, 700)], [front(600, 700, "house-tool")], [back(621, 658, "client-web")], ctx=self.ctx)
        self.check(p, {"C1": 37})

    def test_the_cap_cuts_internal_work_first(self):
        logged = [{"s": 900, "e": 960, "minutes": 60, "taskId": "X"}]  # an hour logged outside the active time
        p = plan([work(600, 700)], [front(600, 700, "house-tool")], [back(620, 660, "client-web")], ctx=self.ctx,
                 logged=logged)
        self.check(p, {"C1": 40})
        self.assertEqual(client_minutes(p), 40)                      # the client's 40 minutes stay
        self.assertTrue(all(u["task"] == "T1" for u in p["unplaced"] if u["why"].startswith("more than your")))


if __name__ == "__main__":
    unittest.main()
