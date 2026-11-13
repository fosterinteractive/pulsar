"""Log the day's new rules, on made-up days: filling the holes the evidence cap leaves (dayplan._refill), a client's
agent that ran beside another client's work in front (3d in dayplan._rows), and the invariants every plan keeps, on
random interleaved days with fixed seeds. No network, no trackers.

    python3 -m unittest discover -s tests
"""

import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH, refused  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import dayplan  # noqa: E402
from test_attribution import KEY, TASKS, back, entry, front, history, plan, work  # noqa: E402

# Globex: a second client with its own repository, next to test_attribution's Clientco (C1, C2), tool (T1) and QA hub (Q1)
GLOBEX = {f"2026-10-{d:02d}": ([{"k": [KEY], "p": "globex-site", "s": 800, "e": 860}], [entry(f"2026-10-{d:02d}", 800, 60, "G1")])
          for d in (14, 15, 16)}
REPO = {"C1": "client-web", "C2": "client-web", "T1": "house-tool", "Q1": "qa-hub", "G1": "globex-site"}
NEVER = "private-repo"  # a repository the person never logs (ctx never_logged)


def rows(*spans):
    """Rows in front, each with a link to its task (so its class and task are the task's): (s, e, task or repository)."""
    return [front(s, e, REPO.get(t, t), tasks=[t] if t in TASKS else []) for s, e, t in spans]


def by_task(p) -> dict:
    out: dict = {}
    for e in p["entries"]:
        out[e["taskId"]] = out.get(e["taskId"], 0) + e["minutes"]
    return out


def of_project(p, project) -> int:
    return sum(e["minutes"] for e in p["entries"] if e["taskId"] in TASKS and TASKS[e["taskId"]][1] == project)


def spans(p):
    return [(e["s"], e["e"], e["taskId"]) for e in p["entries"]]


CLIENTCO, GLOBEX_P = "Clientco - Ongoing Support", "Globex - Ongoing Support"


class Ctx(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = history(extra=GLOBEX)[0]


class RefillTest(Ctx):
    """_refill: the minutes the evidence cap took back go to the work seen in front then, by the rules in its docstring."""

    def test_a_projects_short_owed_pieces_together_fill_the_hole_rounded_down(self):
        # 6 minutes of C1 and 6 of C2 (one client) after 40 of Globex: each joins Globex's entry, the evidence cap cuts
        # it back to 40, and neither task alone is owed an entry; together the client is owed 12, so it gets 10
        p = plan([work(540, 592)], rows((540, 580, "G1"), (580, 586, "C1"), (586, 592, "C2")), ctx=self.ctx)
        self.assertEqual(spans(p), [(540, 580, "G1"), (580, 590, "C1")])
        self.assertEqual(p["uncovered"], [])
        self.assertLessEqual(of_project(p, CLIENTCO), 12)

    def test_never_past_the_active_time_cap(self):
        # the same day with 8 minutes logged without a start: 52 active, 44 left, 40 proposed: no room for a step more,
        # so the hole stays a hole
        p = plan([work(540, 592)], rows((540, 580, "G1"), (580, 586, "C1"), (586, 592, "C2")), ctx=self.ctx,
                 logged=[{"s": None, "minutes": 8, "taskId": "X9"}])
        self.assertEqual(spans(p), [(540, 580, "G1")])
        self.assertEqual([(u["s"], u["e"]) for u in p["uncovered"]], [(580, 592)])
        self.assertLessEqual(p["totals"]["proposed"], max(0, p["totals"]["cap"] - p["totals"]["logged"]) + p["totals"]["overLeft"])

    @mock.patch("pulsar.sessions.LOGGING_IS_WORK", False)  # time spent logging: never proposed, behind its switch
    def test_time_spent_logging_is_never_a_hole_to_fill(self):
        # as the test below, then 553-560 in Pulsar's own window: blocked. The tool's work takes the hole up to the
        # logging time, never into it
        items = [work(540, 553), work(553, 560, app="Pulsar", title="Pulsar")]
        p = plan(items, rows((540, 547, "C1"), (547, 553, "T1")), ctx=self.ctx)
        self.assertEqual(spans(p), [(540, 550, "T1")])
        self.assertEqual([(u["s"], u["e"]) for u in p["uncovered"]], [(550, 560)])

    def test_a_hole_where_the_clients_entry_was_dropped_goes_to_internal_work_seen_there(self):
        # 7 minutes of a client, then 6 of the tool: one entry of the client's, cut by the evidence cap to under an
        # entry and dropped. No client entry is against the hole, so the tool's work in front takes it to the step
        p = plan([work(540, 553)], rows((540, 547, "C1"), (547, 553, "T1")), ctx=self.ctx)
        self.assertEqual(spans(p), [(540, 550, "T1")])
        self.assertEqual(of_project(p, CLIENTCO), 0)

    def test_internal_work_after_a_clients_work_is_its_own(self):
        # 2026-11-10: internal work goes to a client only inside that client's work (between two of its turns);
        # after a client's work, or between two clients, internal work in front is its own (switching): here T1 and Q1,
        # 14 minutes together after Globex and Clientco
        p = plan([work(540, 590)], rows((540, 570, "G1"), (570, 576, "C2"), (576, 582, "T1"), (582, 590, "Q1")), ctx=self.ctx)
        self.assertLessEqual(of_project(p, CLIENTCO), 6, spans(p))     # Clientco: never the internal minutes after it

    def test_a_hole_between_two_turns_of_one_client_gets_no_internal_work(self):
        p = plan([work(540, 610)], rows((540, 565, "C1"), (565, 571, "T1"), (571, 575, "Q1"), (575, 610, "C2")), ctx=self.ctx)
        self.assertNotIn("T1", by_task(p), spans(p))
        self.assertNotIn("Q1", by_task(p), spans(p))

    def test_a_never_logged_repositorys_stretch_after_a_clients_entry_stays_uncovered(self):
        # the hole after the client's entry is mostly a repository the person never logs, not work seen in front: the
        # client's entry does not grow over it
        ctx = {**self.ctx, "never_logged": (NEVER,)}
        p = plan([work(540, 600)], rows((540, 570, "C1"), (570, 576, "C2"), (576, 600, NEVER)), ctx=ctx)
        self.assertTrue(all(e["e"] <= 576 for e in p["entries"]), spans(p))
        self.assertIn((NEVER, "you never log this repository"),
                      [(s.get("project"), s["why"].split(": ")[-1]) for s in p["skipped"]])
        self.assertLessEqual(of_project(p, CLIENTCO), 36)

    # ---- what the refill gets wrong (2026-11-10 review) -----------------------------------------------------------
    # The last branch of _refill ("what is left of a hole in a client's work ... that client's entry grows") gives the
    # rest of the hole to the client entry against it with no look at its project's pool: it undoes the evidence cap.
    # On random interleaved days (tests/test_cov_dayplan.py's generator) a client got more than it was seen in front on
    # about a third of them; with that branch off, none.

    # fixed 2026-11-10: _refill's leftover branch bills Globex for Clientco's minutes (pool not checked)
    def test_a_short_budget_never_gives_one_clients_minutes_to_another(self):
        # as test_a_projects_short_owed_pieces_together_fill_the_hole_rounded_down, with 3 minutes logged without a start:
        # room for 5 minutes only, too short for Clientco's own entry, so the 5 go to Globex's entry: 45 for 40 seen
        p = plan([work(540, 592)], rows((540, 580, "G1"), (580, 586, "C1"), (586, 592, "C2")), ctx=self.ctx,
                 logged=[{"s": None, "minutes": 3, "taskId": "X9"}])
        self.assertLessEqual(of_project(p, GLOBEX_P), 40, spans(p))

    # fixed 2026-11-10: _refill's leftover branch bills a client for a long stretch of internal work
    def test_a_client_never_gets_the_internal_work_after_it(self):
        # 33 minutes of the tool, 15 of Clientco, 6 of the QA hub: Clientco was seen 15 minutes and gets 20, the extra
        # 5 being the tool's time in front (not a short turn between two turns of the client, 3b')
        p = plan([work(540, 594)], rows((540, 573, "T1"), (573, 588, "C2"), (588, 594, "Q1")), ctx=self.ctx)
        self.assertLessEqual(of_project(p, CLIENTCO), 15, spans(p))

    # fixed 2026-11-10: _refill's leftover branch bills a client for a never-logged repository's minutes
    def test_a_never_logged_repository_between_a_clients_turns_is_never_billed(self):
        # Clientco seen 72 minutes (66 of C1, 6 of C2); 8 minutes in a repository never logged between its turns. Without
        # the refill: 540-605 for C1 and 605-620 not covered. With it: 540-620, all 80 minutes
        ctx = {**self.ctx, "never_logged": (NEVER,)}
        p = plan([work(540, 620)], rows((540, 570, "C1"), (570, 576, "C2"), (576, 584, NEVER), (584, 620, "C1")), ctx=ctx)
        self.assertLessEqual(of_project(p, CLIENTCO), 72, spans(p))

    # fixed 2026-11-10: _refill places a "touches its own entry" piece at the hole's start, away from it
    def test_a_refilled_entry_is_never_a_crumb_away_from_its_own_entry(self):
        # takes() lets a task with its own entry right after the hole take 5 minutes (under an entry) to grow it; with
        # room for less than the hole (5 minutes logged without a start) the piece is laid from the hole's start: C2 gets
        # 565-570 on its own, its entry stays at 575, and 570-575 shows as not covered
        p = plan([work(540, 594)], rows((540, 573, "T1"), (573, 588, "C2"), (588, 594, "Q1")), ctx=self.ctx,
                 logged=[{"s": None, "minutes": 5, "taskId": "X9"}])
        for e in p["entries"]:
            own = any(x is not e and x["taskId"] == e["taskId"] and (x["e"] == e["s"] or x["s"] == e["e"]) for x in p["entries"])
            self.assertTrue(e["minutes"] >= 10 or own, spans(p))


class BesideTest(Ctx):
    """3d: a client's agent that ran attended beside ANOTHER client's work in front: ignored under PARALLEL_MIN minutes
    a (task, sitting); from that long, every other overlapping minute is its."""

    def test_short_runs_in_two_sittings_are_each_ignored(self):
        # 14 minutes in each of two sittings (20 minutes with nothing between them): 28 in all, but never 15 in one
        items = [work(600, 630), work(650, 690)]
        p = plan(items, [front(600, 630, "client-web"), front(650, 690, "client-web")],
                 [back(600, 614, "globex-site", tasks=["G1"]), back(650, 664, "globex-site", tasks=["G1"])], ctx=self.ctx)
        self.assertEqual(by_task(p), {"C1": 70})
        self.assertEqual([(s["project"], s["minutes"]) for s in p["skipped"]], [("globex-site", 28)])

    def test_its_own_task_or_repository_in_front_is_never_halved_or_noted(self):
        for fr, bg in ((front(600, 660, "globex-site", tasks=["G1"]), back(600, 640, "globex-site", tasks=["G1"])),
                       (front(600, 660, "client-web", tasks=["C1"]), back(600, 640, "client-web", tasks=["G1"]))):
            p = plan([work(600, 660)], [fr], [bg], ctx=self.ctx)
            self.assertEqual(spans(p), [(600, 660, fr["tasks"][0])])
            self.assertEqual(p["skipped"], [], fr)  # its own repository's minutes are not "ran in parallel" (bg_left)
            self.assertEqual(p["totals"]["parallel"], 40)

    def test_a_meeting_in_front_is_never_halved(self):
        meet = {"s": 600, "e": 640, "title": "Planning", "uid": "m1", "declined": False, "all_day": False,
                "tasks": [{"taskId": "C1", "title": "x", "project": CLIENTCO, "n": 4, "of": 5}]}
        p = plan([work(600, 640, app="zoom.us", title="Zoom Meeting"), work(640, 660)], [front(640, 660, "client-web")],
                 [back(600, 640, "globex-site", tasks=["G1"])], ctx=self.ctx, meetings=[meet], calls=[{"s": 600, "e": 640}])
        self.assertNotIn("G1", by_task(p))
        self.assertEqual(by_task(p), {"C1": 60})

    def test_away_minutes_are_not_counted_and_the_half_keeps_to_its_time(self):
        # 40 minutes of the agent, 20 of them away: 20 beside the work in front, so 10 are its, before the break
        p = plan([work(600, 700)], [front(600, 700, "client-web")], [back(600, 640, "globex-site", tasks=["G1"])],
                 ctx=self.ctx, away=[{"s": 610, "e": 630}])
        self.assertEqual(spans(p), [(600, 610, "G1"), (630, 700, "C1")])

    def test_a_mixed_repositorys_client_task_in_front_is_a_clients_work(self):
        # a repository logged to a client's task and to internal ones ("mixed"): its row, picked to the client's task,
        # is a client's work in front, so another client's agent beside it gets half (not the shared stretch of 3b)
        extra = {**GLOBEX, **{f"2026-10-{d:02d}": ([{"k": [KEY], "p": "both-repo", "s": 800, "e": 860}],
                                                   [entry(f"2026-10-{d:02d}", 800, 60, t)])
                              for d, t in ((11, "C1"), (12, "C1"), (13, "C1"), (17, "T1"), (18, "T1"))}}
        ctx = history(extra=extra)[0]
        self.assertEqual(ctx["classes"].get("both-repo"), "mixed")
        picks = {"600|both-repo": {"tasks": [{"taskId": "C1"}], "why": "Claude", "note": ""}}
        p = plan([work(600, 660)], [front(600, 660, "both-repo")], [back(600, 640, "globex-site", tasks=["G1"])],
                 ctx=ctx, picks=picks)
        self.assertEqual(by_task(p), {"C1": 40, "G1": 20})
        self.assertFalse(any(e.get("shared") for e in p["entries"]))

    def test_each_attended_minute_of_the_agent_is_its_half_or_noted(self):
        p = plan([work(600, 660)], [front(600, 660, "client-web")], [back(600, 640, "globex-site", tasks=["G1"])], ctx=self.ctx)
        noted = sum(s["minutes"] for s in p["skipped"] if s["project"] == "globex-site")
        self.assertEqual(by_task(p).get("G1", 0) + noted, 40)

    # ---- what 3d gets wrong (2026-11-10 review) --------------------------------------------------------------------
    # The agent's half is laid out after the work in front's entry (_layout, "one after the other"): past its own last
    # minute, so it is listed as not placed ("no room before an entry already logged", with nothing logged), and it comes
    # back only when _refill finds it within MERGE_GAP of the hole the evidence cap left at the end of the front's entry.

    # fixed 2026-11-10: 3d's half is lost when the work in front goes on more than MERGE_GAP after the run
    def test_a_run_beside_long_work_in_front_gets_its_half(self):
        # 2 hours of Clientco in front, Globex's agent for its first 40 minutes: Globex should get 20 and Clientco 100;
        # Globex gets nothing and Clientco 120 (the refill's leftover gives the hole back to it)
        p = plan([work(600, 720)], [front(600, 720, "client-web")], [back(600, 640, "globex-site", tasks=["G1"])], ctx=self.ctx)
        self.assertEqual(by_task(p), {"C1": 100, "G1": 20})

    # fixed 2026-11-10: from 15 to 21 minutes beside, the agent gets nothing and the front all of it
    def test_the_work_in_front_never_gets_the_agents_half_back(self):
        # 16 minutes beside: 8 are the agent's, under an entry, so they join the front's entry; the evidence cap cuts
        # them, the refill's leftover gives them back. 20 minutes: 10, an entry, but laid past its time (above).
        for n, half in ((16, 8), (20, 10)):
            p = plan([work(600, 660)], [front(600, 660, "client-web")], [back(600, 600 + n, "globex-site", tasks=["G1"])],
                     ctx=self.ctx)
            self.assertLessEqual(by_task(p)["C1"], 60 - half, (n, spans(p)))

    # fixed 2026-11-10: minutes the refill places stay listed as not placed ("unplaced")
    def test_minutes_proposed_are_never_also_listed_as_not_placed(self):
        # test_attribution.ParallelClientsTest's day: Globex gets its 20 minutes as an entry, and the same 20 minutes are
        # still in "unplaced" (Claude Code reads them as notPlaced; the reason names an entry already logged, and there
        # is none)
        p = plan([work(600, 660)], [front(600, 660, "client-web")], [back(600, 640, "globex-site", tasks=["G1"])], ctx=self.ctx)
        self.assertEqual(by_task(p).get("G1"), 20)
        self.assertEqual([u for u in p["unplaced"] if u["task"] == "G1"], [])


# ---- random days ---------------------------------------------------------------------------------------------------

def random_day(rng):
    """A made-up day: turns of 1 to 40 minutes of five tasks in four repositories, with now and then a gap, a new sitting,
    a client's agent, time away, a meeting with or without a call, a Pulsar window, and entries already logged."""
    t, spans_ = 540, []
    for _ in range(rng.randint(2, 10)):
        task = rng.choice(sorted(REPO))
        n = rng.choice([1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 15, 20, 25, 33, 40])
        spans_.append((t, t + n, task))
        t += n + (rng.choice([2, 5, 20]) if rng.random() < 0.12 else 0)
    end = max(t, 560)
    day = {"items": [work(540, end)], "rows": rows(*spans_), "background": [], "logged": [], "away": [], "meetings": [],
           "calls": []}
    if rng.random() < 0.3:
        a = rng.randint(540, end - 10)
        task = rng.choice(["C1", "G1"])
        day["background"].append(back(a, min(end, a + rng.choice([10, 16, 25, 40])), REPO[rng.choice(["C1", "G1"])], tasks=[task]))
    if rng.random() < 0.25:
        a = rng.randint(540, end - 10)
        day["logged"].append({"s": a, "e": a + 10, "minutes": 10, "taskId": rng.choice(["C1", "T1", "X9"])})
    if rng.random() < 0.15:
        day["logged"].append({"s": None, "minutes": rng.choice([5, 10, 30]), "taskId": "X9"})
    if rng.random() < 0.3:
        a = rng.randint(545, end - 10)
        day["away"] = [{"s": a, "e": a + rng.choice([3, 8, 16])}]
    if rng.random() < 0.2:
        a = rng.randint(540, end - 10) // 5 * 5
        day["meetings"] = [{"s": a, "e": a + 30, "title": "Standup", "uid": "m", "declined": False, "all_day": False,
                            "tasks": [{"taskId": "Q1", "title": "QA", "project": "FI-AI QA", "n": 4, "of": 5}]}]
        if rng.random() < 0.7:
            day["calls"] = [{"s": a, "e": a + rng.choice([10, 25, 40])}]
            day["items"].append(work(a, a + 25, app="zoom.us", title="Zoom Meeting"))
    if rng.random() < 0.3:
        a = rng.randint(540, end - 10)
        day["items"].append(work(a, a + rng.choice([4, 9, 15]), app="Pulsar", title="Pulsar"))
    return day


class RandomDaysTest(Ctx):
    """On 150 random days (fixed seeds), each step of 5, 10 or 15 minutes, logging time as work or not: entries never
    overlap each other or what is logged, proposed plus logged never past the active time and meetings, every boundary
    on the grid (or against an entry already logged), and the totals add up."""

    def test_every_plan_keeps_its_invariants(self):
        for seed in range(150):
            rng = random.Random(seed)
            d = random_day(rng)
            step = rng.choice([5, 5, 10, 15])
            with mock.patch("pulsar.sessions.LOGGING_IS_WORK", rng.random() < 0.5):
                p = dayplan.plan({**d, "repeats": [], "rules": [], "assigned": [], "picks": {}}, self.ctx,
                                 dayplan.opts_for({"round_to": step}))
            why = (seed, step, spans(p))
            busy = [(r["s"], r["s"] + r["minutes"]) for r in d["logged"] if r.get("s") is not None]
            ends = {x for b in busy for x in b}
            placed = sorted([(e["s"], e["e"]) for e in p["entries"]] + busy)
            for (_, a1), (b0, _) in zip(placed, placed[1:]):
                self.assertLessEqual(a1, b0, why)                                        # never two at once
            t = p["totals"]
            self.assertLessEqual(t["proposed"], max(0, t["cap"] - t["logged"]) + t["overLeft"], why)  # the cap, past it only by work seen
            self.assertEqual(t["proposed"], sum(e["minutes"] for e in p["entries"]), why)
            self.assertEqual([e["s"] for e in p["entries"]], sorted(e["s"] for e in p["entries"]), why)
            for e in p["entries"]:
                self.assertEqual(e["e"] - e["s"], e["minutes"], why)
                self.assertGreater(e["minutes"], 0, why)
                for x in (e["s"], e["e"]):
                    self.assertTrue(x % step == 0 or x in ends, why)                   # on the grid
            for u in p["uncovered"]:                                                  # not covered: by no entry
                self.assertFalse(any(e["s"] < u["e"] and u["s"] < e["e"] for e in p["entries"]), (why, u))


class NewTaskAndWordsTest(unittest.TestCase):
    """2026-11-10: a session titled "Оновлення бази даних Drupal 11" in a checkout on the branch
    event-listings-cta went to Event Listings (the branch named it, the Ukrainian words named nothing), and Pulsar turns
    between turns of a client's new "Drupal 11 – DB Upgrade" task stayed Pulsar's (a task never logged had no class)."""

    def test_ukrainian_database_words_name_the_db_task_before_the_branch(self):
        from pulsar import learn
        tasks = [{"taskId": "1", "title": "Drupal 11 – DB Upgrade", "project": "P", "mine": True},
                 {"taskId": "2", "title": "Enhancement to Event Listings (set different labels per listing)", "project": "P", "mine": True}]
        said = learn.english("Оновлення бази даних Drupal 11 закінчуй тренд, апдейт бази даних на тест")
        self.assertEqual(learn.work_match(said, {"P"}, tasks)[0]["taskId"], "1")
        self.assertIn("database db", said)
        entry = {"titles": ["Оновлення бази даних Drupal 11"], "branches": ["event-listings-cta"], "commits": [], "github": []}
        self.assertNotIn("campaign", learn.work_text(entry, 0, steps=[], branch=False))
        self.assertIn("event listings cta", learn.work_text(entry, 0, steps=[]))  # the branch, kept for a fallback
        self.assertEqual(learn.english("no Ukrainian here"), "no Ukrainian here")

    def test_a_task_never_logged_takes_its_projects_class(self):
        # N1 is Claude's pick, a Clientco task with no entry yet, in a repository logged both ways ("mixed"): a
        # short tool turn between two of its turns is Clientco's
        extra = {f"2026-10-{d:02d}": ([{"k": [KEY], "p": "both-repo", "s": 800, "e": 860}], [entry(f"2026-10-{d:02d}", 800, 60, t)])
                 for d, t in ((11, "C1"), (12, "C1"), (13, "C1"), (14, "T1"), (15, "T1"))}
        ctx = history(extra=extra)[0]
        self.assertEqual(ctx["classes"].get("both-repo"), "mixed")
        pick = {"tasks": [{"taskId": "N1", "title": "Clientco DB upgrade", "project": "Clientco - Ongoing Support"}],
                "why": "Claude", "note": ""}
        rows = [front(600, 625, "both-repo"), front(625, 631, "house-tool"), front(631, 660, "both-repo")]
        p = plan([work(600, 660)], rows, ctx=ctx, picks={"600|both-repo": pick, "631|both-repo": pick})
        self.assertNotIn("T1", [e["taskId"] for e in p["entries"]], [(e["s"], e["e"], e["taskId"]) for e in p["entries"]])

if __name__ == "__main__":
    unittest.main()
