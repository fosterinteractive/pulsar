"""The one Claude Code switch (claudelink.py) and notes from a session (askclaude.steps, askclaude.note), on made-up
files in a temporary folder: Claude Code itself is never run and its real settings are never touched.

    python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import askclaude, claudelink  # noqa: E402

T0 = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()


def iso(minute):
    return datetime.fromtimestamp(T0 + minute * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Link(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pulsar-link-")
        self.settings = os.path.join(self.dir, "settings.json")
        self.claude_json = os.path.join(self.dir, "claude.json")
        self.calls = []
        servers = {}

        def run(args):
            self.calls.append(args)
            if args[:2] == ["mcp", "add"]:
                cmd = args[args.index("--") + 1:]
                servers[args[4]] = {"type": "stdio", "command": cmd[0], "args": cmd[1:]}
            if args[:2] == ["mcp", "remove"]:
                servers.pop(args[2], None)
            with open(self.claude_json, "w") as fh:
                json.dump({"mcpServers": servers}, fh)
            return True, ""
        self.patches = [mock.patch.object(claudelink, "SETTINGS", self.settings),
                        mock.patch.object(claudelink, "CLAUDE_JSON", self.claude_json),
                        mock.patch.object(claudelink, "_run", run),
                        mock.patch.object(claudelink, "_claude", lambda: "/usr/local/bin/claude"),
                        mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": ""})]
        for p in self.patches:
            p.start()
        os.environ.pop("PULSAR_READ_ONLY", None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def write(self, doc):
        with open(self.settings, "w") as fh:
            json.dump(doc, fh)

    def read(self):
        with open(self.settings) as fh:
            return json.load(fh)

    def test_on_adds_pulsar_and_rules_off_takes_back_only_those(self):
        self.write({"permissions": {"allow": ["Bash(ls:*)"], "ask": ["mcp__productive__delete_resource"]}, "env": {"A": "1"}})
        got = claudelink.connect(5610, {})
        self.assertTrue(got["ok"])
        self.assertEqual(sorted(got["rulesAdded"]), sorted(r for r in claudelink.rules() if "delete" not in r))
        doc = self.read()
        self.assertEqual(doc["env"], {"A": "1"})
        self.assertTrue(all(r in doc["permissions"]["ask"] for r in claudelink.rules()))
        st = claudelink.status(5610)
        self.assertTrue(st["registered"] and st["current"] and st["rules"])
        self.assertIn("--mcp", claudelink.registered()["args"])
        self.assertTrue(os.path.exists(self.settings + ".pulsar-backup"))
        off = claudelink.disconnect({"claude_rules_added": got["rulesAdded"]})
        self.assertTrue(off["ok"])
        self.assertEqual(self.read()["permissions"], {"allow": ["Bash(ls:*)"], "ask": ["mcp__productive__delete_resource"]})
        self.assertIsNone(claudelink.registered())

    def test_a_broken_settings_file_is_left_alone(self):
        with open(self.settings, "w") as fh:
            fh.write("{not json")
        got = claudelink.connect(5610, {})
        self.assertTrue(got["ok"])  # Pulsar is still added to Claude Code
        self.assertIn("not valid JSON", got["message"])
        with open(self.settings) as fh:
            self.assertEqual(fh.read(), "{not json")

    def test_no_settings_file_yet(self):
        got = claudelink.connect(5620, {})
        self.assertEqual(sorted(self.read()["permissions"]["ask"]), sorted(claudelink.rules()))
        self.assertEqual(claudelink.disconnect({"claude_rules_added": got["rulesAdded"]})["rulesAdded"], [])
        self.assertEqual(self.read(), {})

    def test_a_moved_copy_is_not_current(self):
        claudelink.connect(5610, {})
        self.assertFalse(claudelink.status(5620)["current"])  # another port: Settings offers to add it again

    def test_read_only_runs_change_nothing(self):
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):
            self.assertTrue(claudelink.connect(5610, {})["ok"])
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.settings))

    def test_preview_starts_its_mcp_server_with_its_own_folders(self):
        with mock.patch.object(claudelink.config, "INSTANCE", "preview"), \
                mock.patch.dict(os.environ, {"PULSAR_PREVIEW": "1"}):
            self.assertEqual(claudelink.name(), "pulsar-preview")
            self.assertEqual(claudelink.command(5620)[-5:], ["--mcp", "--port", "5620", "--instance", "preview"])
        self.assertNotIn("--instance", claudelink.command(5610))  # Pulsar, and an older launcher's Preview

    def test_the_ask_rules_stay_while_the_other_pulsar_uses_them(self):
        with mock.patch.dict(os.environ, {"PULSAR_PREVIEW": "1"}):  # Preview adds the rules first
            first = claudelink.connect(5620, {})
        self.assertEqual(sorted(first["rulesAdded"]), sorted(claudelink.rules()))
        mine = claudelink.connect(5610, {})  # then Pulsar: the rules are already there, so it adds none
        self.assertEqual(mine["rulesAdded"], [])
        with mock.patch.dict(os.environ, {"PULSAR_PREVIEW": "1"}):  # Preview off while Pulsar is still in Claude Code
            off = claudelink.disconnect({"claude_rules_added": first["rulesAdded"]})
        self.assertTrue(off["ok"])
        self.assertIn("Pulsar still uses them", off["message"])
        self.assertEqual(off["rulesAdded"], first["rulesAdded"])  # still its own to take back later
        self.assertTrue(claudelink.status(5610)["rules"])
        self.assertIsNone(claudelink.registered("pulsar-preview"))
        claudelink.disconnect({"claude_rules_added": []})  # Pulsar off: Preview's rules are not Pulsar's to remove
        self.assertTrue(claudelink.status(5610)["rules"])
        with mock.patch.dict(os.environ, {"PULSAR_PREVIEW": "1"}):  # Preview on and off again, alone now
            again = claudelink.connect(5620, {"claude_rules_added": off["rulesAdded"]})
            claudelink.disconnect({"claude_rules_added": again["rulesAdded"]})
        self.assertEqual(self.read(), {})

    def test_a_preview_launchers_read_only_value_still_links(self):
        # PULSAR_READ_ONLY=preview (launcher.sh) is a preview, not a test run: the switch works
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "preview", "PULSAR_PREVIEW": "1"}):
            self.assertTrue(claudelink.connect(5620, {})["ok"])
        self.assertTrue(self.calls)

    def test_pulsar_log_gets_an_ask_rule_too(self):
        # a fallback to the mark on pulsar_log (mcp.py), which is not a documented part of Claude Code
        got = claudelink.connect(5610, {})
        self.assertTrue({"mcp__pulsar__pulsar_log", "mcp__pulsar-preview__pulsar_log"} <= set(got["rulesAdded"]))
        self.assertTrue({"mcp__pulsar__pulsar_log", "mcp__pulsar-preview__pulsar_log"} <= set(self.read()["permissions"]["ask"]))

    def test_a_pulsar_entry_the_person_made_is_left_alone(self):
        theirs = {"type": "stdio", "command": "python3", "args": ["/src/pulsar.py", "--mcp"], "env": {"PULSAR_READ_ONLY": "1"}}
        with open(self.claude_json, "w") as fh:
            json.dump({"mcpServers": {"pulsar": theirs}}, fh)
        got = claudelink.connect(5610, {})
        self.assertFalse(got["ok"])
        self.assertIn("did not add", got["message"])
        self.assertEqual(self.calls, [])  # neither removed nor replaced
        self.assertEqual(claudelink.registered(), theirs)
        claudelink.disconnect({"claude_rules_added": got["rulesAdded"]})
        self.assertEqual(self.calls, [])  # and not removed when switched off either

    def test_an_unreadable_server_list_keeps_the_rules(self):
        with mock.patch.dict(os.environ, {"PULSAR_PREVIEW": "1"}):
            claudelink.connect(5620, {})  # Preview is in Claude Code and uses the rules
        got = claudelink.connect(5610, {"claude_rules_added": claudelink.rules()})
        with open(self.claude_json, "w") as fh:
            fh.write("{broken")
        with mock.patch.object(claudelink, "_run", lambda args: (self.calls.append(args), (True, ""))[1]):
            off = claudelink.disconnect({"claude_rules_added": got["rulesAdded"]})
        self.assertFalse(off["ok"])
        self.assertIn("could not be read", off["message"])
        self.assertEqual(off["rulesAdded"], got["rulesAdded"])
        self.assertIn(["mcp", "remove", "pulsar", "-s", "user"], self.calls)  # Claude Code's own command still asked
        self.assertTrue(all(r in self.read()["permissions"]["ask"] for r in claudelink.rules()))

    def test_the_settings_file_keeps_its_permissions(self):
        self.write({"env": {"A": "1"}})
        os.chmod(self.settings, 0o644)
        claudelink.connect(5610, {})
        self.assertEqual(os.stat(self.settings).st_mode & 0o777, 0o644)

    def test_installed_copy_is_started_through_current(self):
        fake = os.path.join(claudelink.INSTALL, "versions", "9.9.9", "pulsar")
        with mock.patch.object(claudelink.os.path, "abspath", lambda p: os.path.join(fake, "claudelink.py")), \
                mock.patch.object(claudelink.os.path, "realpath", lambda p: p):
            cmd = claudelink.command(5610)
        self.assertEqual(cmd[4], os.path.join(claudelink.INSTALL, "current", "pulsar.py"))
        self.assertEqual(cmd[-3:], ["--mcp", "--port", "5610"])


class SessionNote(unittest.TestCase):
    def setUp(self):
        self.projects = tempfile.mkdtemp(prefix="pulsar-proj-")
        os.makedirs(os.path.join(self.projects, "p"))
        lines = [{"type": "ai-title", "aiTitle": "Fix the event time zone"}]
        for minute, kind, text in [(600, "user", "fix the event pages"), (610, "assistant", "Looking at it."),
                                   (630, "assistant", "Done: event pages show the right time zone again."),
                                   (700, "user", "and the quiz"), (720, "assistant", "Quiz results save again."),
                                   (900, "user", "much later, other work")]:
            lines.append({"type": kind, "timestamp": iso(minute), "message": {"role": kind, "content": text}})
        lines.append({"type": "assistant", "isSidechain": True, "timestamp": iso(640),
                      "message": {"role": "assistant", "content": "an agent's own words"}})
        with open(os.path.join(self.projects, "p", "s.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(x) for x in lines) + "\n")

    def test_steps_carry_claudes_last_words(self):
        got = askclaude.steps(T0, ["Fix the event time zone"], 595, 730, projects_dir=self.projects)
        self.assertEqual(got, [{"typed": "fix the event pages", "reply": "Done: event pages show the right time zone again."},
                               {"typed": "and the quiz", "reply": "Quiz results save again."}])
        self.assertEqual(askclaude.steps(T0, ["Another session"], 595, 730, projects_dir=self.projects), [])

    def test_note_runs_haiku_with_nothing_else_and_guards_the_draft(self):
        seen = {}

        def run(cmd, **kw):
            seen["cmd"], seen["input"], seen["env"] = cmd, kw["input"], kw.get("env") or {}
            out = {"structured_output": {"note": "Event pages show the right time zone again."}, "total_cost_usd": 0.002}
            return mock.Mock(stdout=json.dumps(out), stderr="", returncode=0)
        row = {"s": 595, "e": 730, "project": "trent", "titles": ["Fix the event time zone"], "commits": ["Quiz saves"]}
        with mock.patch.object(askclaude.productive, "_claude_binary", lambda: "/x/claude"), \
                mock.patch.object(askclaude, "steps", lambda *a, **k: [{"typed": "fix", "reply": "Done."}]):
            got = askclaude.note("2026-11-04", row, T0, [], run=run)
        self.assertEqual(got["note"], "Event pages show the right time zone again.")
        for flag in ("--strict-mcp-config", "--no-session-persistence", "dontAsk"):
            self.assertIn(flag, seen["cmd"])
        self.assertEqual(seen["cmd"][seen["cmd"].index("--model") + 1], "haiku")
        self.assertEqual(seen["cmd"][seen["cmd"].index("--setting-sources") + 1], "")  # no CLAUDE.md
        self.assertEqual(seen["env"].get("CLAUDE_CODE_DISABLE_AUTO_MEMORY"), "1")  # and no MEMORY.md
        self.assertIn("Quiz saves", seen["input"])

        def leaky(cmd, **kw):
            return mock.Mock(stdout=json.dumps({"structured_output": {"note": "Fixed web/themes/x/x.theme"}}), stderr="")
        row2 = {**row, "s": 596}
        with mock.patch.object(askclaude.productive, "_claude_binary", lambda: "/x/claude"), \
                mock.patch.object(askclaude, "steps", lambda *a, **k: [{"typed": "fix", "reply": "Done."}]):
            got = askclaude.note("2026-11-04", row2, T0, [], run=leaky)
        self.assertEqual(got["note"], "")
        self.assertIn("file path", got["why"])

    def test_nothing_to_go_on_asks_nobody(self):
        row = {"s": 1000, "e": 1010, "project": "trent", "titles": ["Fix the event time zone"]}
        got = askclaude.note("2026-11-04", row, T0, [], run=lambda *a, **k: self.fail("Claude was asked"))
        self.assertEqual(got["note"], "")


if __name__ == "__main__":
    unittest.main()
