"""Everything Pulsar asks Claude (pulsar/claudeuses.py): the list Settings shows, the model chosen per use and how it
is kept, and that each `claude -p` call runs the chosen model with its own cap. Claude Code is faked (`run=`).

    python3 -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import askclaude, claudeuses, config, dayreview, newtask, productive, server  # noqa: E402

ROWS = [{"s": 600, "e": 660, "project": "acme", "titles": ["Fix the menu"], "commits": ["Fix the menu"], "minutes": 60}]


def flag(argv, name):
    return argv[argv.index(name) + 1]


class RegistryTest(unittest.TestCase):

    def test_every_use_says_what_it_does_and_sends(self):
        for use, u in claudeuses.USES.items():
            for key in ("name", "does", "sends"):
                self.assertTrue(u[key].strip(), (use, key))
            self.assertIn(u["model"], claudeuses.MODELS + (None,), use)
        self.assertEqual(claudeuses.MODELS, ("haiku", "sonnet", "opus"))  # aliases only: never a pinned model id
        self.assertFalse(claudeuses.runs_claude("session"))  # Pulsar in Claude Code: the session's own model

    def test_the_defaults_are_what_pulsar_ran_before(self):
        self.assertEqual({u: (claudeuses.USES[u]["model"], claudeuses.cap(u)) for u in ("ask", "note", "task")},
                         {"ask": ("sonnet", 0.75), "note": ("haiku", 0.1), "task": ("haiku", 0.1)})
        self.assertEqual({m: claudeuses.cap("ask", m) for m in claudeuses.MODELS},
                         {"haiku": 0.25, "sonnet": 0.75, "opus": 2.0})

    def test_the_cap_grows_with_the_model(self):
        for use in ("ask", "note", "task"):
            caps = [claudeuses.cap(use, m) for m in claudeuses.MODELS]
            self.assertEqual(caps, sorted(set(caps)), use)
        self.assertEqual(claudeuses.cap("note", "unknown"), claudeuses.cap("note", "haiku"))  # unknown: the default

    def test_the_chosen_model_else_the_old_ask_setting_else_the_default(self):
        model = claudeuses.model
        self.assertEqual(model("note", {"claude_models": {"note": "opus"}}), "opus")
        self.assertEqual(model("note", {"claude_models": {"note": "claude-opus-4-1"}}), "haiku")  # not an alias
        self.assertEqual(model("note", {"claude_models": "opus"}), "haiku")
        self.assertEqual(model("ask", {"ask_claude_model": "haiku"}), "haiku")  # chosen before 1.5
        self.assertEqual(model("ask", {"ask_claude_model": "haiku", "claude_models": {"ask": "opus"}}), "opus")
        self.assertEqual(model("note", {"ask_claude_model": "opus"}), "haiku")  # Ask Claude's only
        self.assertEqual(model("ask", config.DEFAULTS), "sonnet")

    def test_choices_are_kept_only_when_they_are_real_and_not_the_default(self):
        got = claudeuses.clean({"ask": "haiku", "note": "haiku", "task": "fable", "session": "opus", "other": "opus"})
        self.assertEqual(got, {"ask": "haiku"})
        with self.assertRaises(ValueError):
            claudeuses.clean(["ask", "haiku"])

    def test_the_list_for_settings(self):
        rows = claudeuses.public({"claude_models": {"task": "sonnet"}})
        self.assertEqual([r["id"] for r in rows], list(claudeuses.USES))
        by = {r["id"]: r for r in rows}
        self.assertEqual((by["task"]["model"], by["task"]["default"], by["task"]["caps"]["sonnet"]), ("sonnet", "haiku", 0.3))
        self.assertEqual((by["session"]["model"], by["session"]["default"]), (None, None))
        self.assertNotIn("caps", by["session"])

    def test_what_runs_cost_is_counted_per_use(self):
        with mock.patch.dict(claudeuses._spent, clear=True):
            for cost in (0.002, 0.003, None, -1, True):
                claudeuses.spent("note", cost)
            by = {r["id"]: r for r in claudeuses.public({})}
            self.assertEqual((by["note"]["runs"], by["note"]["spent"]), (2, 0.005))
            self.assertEqual(by["ask"]["runs"], 0)


class SettingsTest(unittest.TestCase):

    def test_the_server_keeps_one_key_and_ask_claudes_old_one_in_step(self):
        got = server.validate({"claude_models": {"ask": "haiku", "note": "opus", "task": "haiku", "nope": "opus"}})
        self.assertEqual(got, {"claude_models": {"ask": "haiku", "note": "opus"}, "ask_claude_model": "haiku"})
        got = server.validate({"claude_models": {"ask": "sonnet", "note": "gpt"}})
        self.assertEqual(got, {"claude_models": {}, "ask_claude_model": "sonnet"})  # back to the defaults
        with self.assertRaises(ValueError):
            server.validate({"claude_models": "opus"})

    def test_a_saved_file_with_an_unknown_name_loads_and_keeps_the_default(self):
        path = os.path.join(_SCRATCH, "claudeuses-settings.json")
        with open(path, "w") as fh:
            json.dump({"claude_models": {"note": "fable", "ask": "opus"}}, fh)
        with mock.patch.object(config, "SETTINGS_FILE", path):
            s = config.load()
        self.assertEqual((claudeuses.model("note", s), claudeuses.model("ask", s)), ("haiku", "opus"))
        self.assertIn("claude_models", config.SEED_SKIP)  # Preview starts with its own choices, as with the switch

    def test_the_page_gets_every_use_with_its_model(self):
        s = {**config.DEFAULTS, "claude_models": {"note": "sonnet"}, "ask_claude_model": "haiku"}
        with mock.patch.object(server.productive, "connection", lambda s: {"kind": None}):
            c = server.public_config(s)
        by = {r["id"]: r for r in c["claude"]["uses"]}
        self.assertEqual((by["ask"]["model"], by["note"]["model"], by["task"]["model"]), ("haiku", "sonnet", "haiku"))
        self.assertEqual(c["likely"]["askClaudeModel"], "haiku")  # Log the day names it


class CallSiteTest(unittest.TestCase):
    """Each call runs the model it is given, with that model's cap, and keeps its isolation."""

    def setUp(self):
        from pulsar import askclaude
        askclaude._cache.clear()  # each test asks afresh: no answer kept by another (askclaude.kept)
        __import__("shutil").rmtree(askclaude.PICKS_DIR, ignore_errors=True)
        self.runs = []
        patches = [mock.patch.object(productive, "_claude_binary", lambda: "/bin/claude-fake"),
                   mock.patch.object(askclaude, "steps", lambda *a, **k: [{"typed": "fix", "reply": "Menu fixed."}]),
                   mock.patch.object(askclaude.config, "load", lambda: {}),
                   mock.patch.dict(claudeuses._spent, clear=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_claude(self, out):
        def run(argv, **kw):
            self.runs.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, json.dumps({**out, "total_cost_usd": 0.01}), "")
        return run

    def isolated(self, argv, kw):
        for name, value in (("--tools", ""), ("--permission-mode", "dontAsk"), ("--setting-sources", "")):
            self.assertEqual(flag(argv, name), value)
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(kw["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")

    def test_ask_claude(self):
        run = self.run_claude({"structured_output": {"picks": [{"id": 0, "tasks": ["7"], "why": "menu", "note": ""}]}})
        entries = [{"taskId": "7", "task": "Menu", "project": "P", "minutes": 30, "date": "2026-10-02"}]
        for date, model, cap in (("2026-10-03", None, "0.75"), ("2026-10-04", "haiku", "0.25"), ("2026-10-05", "opus", "2.0")):
            askclaude.ask(date, ROWS, 0, entries, run=run, model=model)
            argv, kw = self.runs[-1]
            self.assertEqual((flag(argv, "--model"), flag(argv, "--max-budget-usd")), (model or "sonnet", cap))
            self.isolated(argv, kw)
            self.assertEqual(flag(argv, "--allowedTools"), "mcp__productive__search_records")
        self.assertEqual(claudeuses._spent["ask"][0], 3)

    def test_the_note(self):
        run = self.run_claude({"structured_output": {"note": "The menu works on phones again."}})
        for s, model, cap in ((600, None, "0.1"), (601, "sonnet", "0.3"), (602, "opus", "0.8")):
            got = askclaude.note("2026-10-03", dict(ROWS[0], s=s), 0, [], run=run, model=model)
            self.assertEqual(got["note"], "The menu works on phones again.")
            argv, kw = self.runs[-1]
            self.assertEqual((flag(argv, "--model"), flag(argv, "--max-budget-usd")), (model or "haiku", cap))
            self.isolated(argv, kw)
            self.assertEqual(flag(argv, "--disallowedTools"), "mcp__*")
        self.assertEqual(claudeuses._spent["note"][0], 3)

    def test_the_new_task(self):
        run = self.run_claude({"structured_output": {"title": "Menu: phones", "description": ""}})
        newtask._cache.clear()
        for model, cap in ((None, "0.1"), ("sonnet", "0.3"), ("opus", "0.8")):
            newtask.claude_draft("2026-10-03", ROWS, 0, [], "P", run=run, model=model)
            argv, kw = self.runs[-1]
            self.assertEqual((flag(argv, "--model"), flag(argv, "--max-budget-usd")), (model or "haiku", cap))
            self.isolated(argv, kw)
            self.assertNotIn("--mcp-config", argv)
        self.assertEqual(len(self.runs), 3)  # a model of its own is a draft of its own, not the cached one
        self.assertEqual(claudeuses._spent["task"][0], 3)

    def test_the_day_review(self):
        run = self.run_claude({"structured_output": {"suggestions": []}})
        for model, cap in ((None, "0.25"), ("sonnet", "0.75"), ("opus", "2.0")):
            dayreview.review("2026-10-03", {}, [], run=run, model=model)
            argv, kw = self.runs[-1]
            self.assertEqual((flag(argv, "--model"), flag(argv, "--max-budget-usd")), (model or "haiku", cap))
            self.isolated(argv, kw)
        self.assertEqual(claudeuses._spent["review"][0], 3)

    def test_the_server_passes_each_use_its_own_choice(self):
        s = {**config.DEFAULTS, "timezone": "UTC", "likely": "on", "ask_claude": True,
             "claude_models": {"ask": "haiku", "note": "opus", "task": "sonnet"}}
        seen = {}
        with mock.patch.object(server, "current_settings", lambda: dict(s)), \
                mock.patch.object(server.likely, "enabled", lambda s: True), \
                mock.patch.object(server.likely, "proposal", lambda *a, **k: {"entries": [dict(r) for r in ROWS]}), \
                mock.patch.object(server, "history_rows", lambda *a, **k: ([], None)), \
                mock.patch.object(server.learn, "annotate", lambda *a, **k: None), \
                mock.patch.object(askclaude, "ask", lambda *a, model=None, **k: seen.update(ask=model) or {"picks": {}}), \
                mock.patch.object(askclaude, "note", lambda *a, model=None, **k: seen.update(note=model) or {"note": ""}), \
                mock.patch.object(newtask, "claude_draft", lambda *a, model=None, **k: seen.update(task=model) or {}):
            server.api_ask_claude({"date": "2026-10-03"})
            server.api_likely_note({"date": "2026-10-03", "s": 600, "e": 660, "project": "acme"})
            server.api_task_draft({"date": "2026-10-03", "s": 600, "e": 660, "claude": True})
        self.assertEqual(seen, {"ask": "haiku", "note": "opus", "task": "sonnet"})


if __name__ == "__main__":
    unittest.main()
