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
        mine = claudelink.deny_rules()[0]  # one the person had already: never Pulsar's to take back
        self.write({"permissions": {"allow": ["Bash(ls:*)"], "ask": ["mcp__productive__delete_resource"],
                                    "deny": [mine]}, "env": {"A": "1"}})
        got = claudelink.connect(5610, {})
        self.assertTrue(got["ok"])
        self.assertEqual(sorted(got["rulesAdded"]), sorted(claudelink.LOG_RULES))  # never Productive's by default
        self.assertEqual(sorted(got["deniesAdded"]), sorted(r for r in claudelink.deny_rules() if r != mine))
        doc = self.read()
        self.assertEqual(doc["env"], {"A": "1"})
        self.assertEqual(sorted(doc["permissions"]["ask"]),
                         sorted(["mcp__productive__delete_resource", *claudelink.LOG_RULES]))
        self.assertEqual(sorted(doc["permissions"]["deny"]), sorted(claudelink.deny_rules()))
        st = claudelink.status(5610, {})
        self.assertTrue(st["registered"] and st["current"] and st["rules"])
        self.assertFalse(st["askProductive"])
        self.assertIn("--mcp", claudelink.registered()["args"])
        self.assertTrue(os.path.exists(self.settings + ".pulsar-backup"))
        off = claudelink.disconnect({"claude_rules_added": got["rulesAdded"], "claude_denies_added": got["deniesAdded"]})
        self.assertTrue(off["ok"])
        self.assertEqual(self.read()["permissions"], {"allow": ["Bash(ls:*)"], "ask": ["mcp__productive__delete_resource"],
                                                      "deny": [mine]})
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
        self.assertEqual(sorted(self.read()["permissions"]["deny"]), sorted(claudelink.deny_rules()))
        off = claudelink.disconnect({"claude_rules_added": got["rulesAdded"], "claude_denies_added": got["deniesAdded"]})
        self.assertEqual((off["rulesAdded"], off["deniesAdded"]), ([], []))
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
            off = claudelink.disconnect({"claude_rules_added": first["rulesAdded"], "claude_denies_added": first["deniesAdded"]})
        self.assertTrue(off["ok"])
        self.assertIn("Pulsar still uses them", off["message"])
        self.assertEqual(off["rulesAdded"], first["rulesAdded"])  # still its own to take back later
        self.assertEqual(off["deniesAdded"], first["deniesAdded"])
        self.assertTrue(claudelink.status(5610)["rules"])
        self.assertIsNone(claudelink.registered("pulsar-preview"))
        claudelink.disconnect({"claude_rules_added": []})  # Pulsar off: Preview's rules are not Pulsar's to remove
        self.assertTrue(claudelink.status(5610)["rules"])
        with mock.patch.dict(os.environ, {"PULSAR_PREVIEW": "1"}):  # Preview on and off again, alone now
            again = claudelink.connect(5620, {"claude_rules_added": off["rulesAdded"], "claude_denies_added": off["deniesAdded"]})
            claudelink.disconnect({"claude_rules_added": again["rulesAdded"], "claude_denies_added": again["deniesAdded"]})
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

    def test_productive_ask_rules_only_when_chosen_and_taken_back_otherwise(self):
        """Ask rules for Productive's own write tools make every Claude Code session ask before each change: only
        "Ask before any Claude Code session changes Productive" adds them. Those Pulsar 1.2 added with the switch go
        (tidy, on start and on Save), and a later Save does not put them back; a rule the person had stays."""
        theirs = "mcp__productive__delete_resource"
        self.write({"permissions": {"ask": [theirs]}})
        chosen = claudelink.connect(5610, {"claude_ask_productive": True})
        self.assertEqual(sorted(chosen["rulesAdded"]),
                         sorted([*claudelink.LOG_RULES, *(r for r in claudelink.productive_rules() if r != theirs)]))
        s = {"ask_claude": True, "claude_ask_productive": True, "claude_rules_added": chosen["rulesAdded"]}
        self.assertTrue(claudelink.status(5610, s)["rules"] and claudelink.status(5610, s)["askProductive"])
        self.assertIsNone(claudelink.tidy(s))  # chosen: they stay
        s["claude_ask_productive"] = False  # unchosen (or Pulsar 1.2's switch): tidy takes back Pulsar's own
        got = claudelink.tidy(s)
        self.assertEqual(sorted(got["rulesAdded"]), sorted(claudelink.LOG_RULES))
        self.assertEqual(sorted(self.read()["permissions"]["ask"]), sorted([theirs, *claudelink.LOG_RULES]))
        s["claude_rules_added"] = got["rulesAdded"]
        self.assertIsNone(claudelink.tidy(s))  # once only
        again = claudelink.connect(5610, s)  # a later Save with the switch on: not put back
        self.assertEqual(sorted(self.read()["permissions"]["ask"]), sorted([theirs, *claudelink.LOG_RULES]))
        self.assertEqual(sorted(again["rulesAdded"]), sorted(claudelink.LOG_RULES))
        self.assertTrue(claudelink.status(5610, s)["rules"])

    def test_settings_save_adds_productive_rules_only_when_chosen_and_never_puts_them_back(self):
        from pulsar import config, server
        saved = {**config.DEFAULTS, "ask_claude": True, "person_id": "42",
                 "claude_rules_added": [*claudelink.LOG_RULES, "mcp__productive__update_resource"]}
        self.write({"permissions": {"ask": [*claudelink.LOG_RULES, "mcp__productive__update_resource"]}})

        def save(body):
            with mock.patch.object(server.config, "load", lambda: dict(saved)), \
                    mock.patch.object(server.config, "update", lambda ch: saved.update(ch) or dict(saved)), \
                    mock.patch.object(server, "current_settings", lambda: dict(saved)), \
                    mock.patch.object(server, "public_config", lambda s: {}):
                return server.api_settings(body)
        save({"likely": "auto"})  # a Save of something else: Pulsar 1.2's Productive rule goes
        self.assertEqual(sorted(self.read()["permissions"]["ask"]), sorted(claudelink.LOG_RULES))
        self.assertEqual(sorted(saved["claude_rules_added"]), sorted(claudelink.LOG_RULES))
        save({"claude_ask_productive": True})  # chosen: all four, kept on the list to take back
        self.assertTrue(set(claudelink.productive_rules()) <= set(self.read()["permissions"]["ask"]))
        self.assertTrue(set(claudelink.productive_rules()) <= set(saved["claude_rules_added"]))
        self.assertTrue(set(claudelink.deny_rules()) <= set(self.read()["permissions"]["deny"]))
        save({"claude_ask_productive": False})
        save({"relink": True})  # and a later Save with the switch on does not put them back
        self.assertEqual(sorted(self.read()["permissions"]["ask"]), sorted(claudelink.LOG_RULES))
        with self.assertRaises(ValueError):
            save({"claude_ask_productive": "yes"})

    def test_tidy_when_pulsar_starts_updates_its_own_list(self):
        from pulsar import config
        old = ["mcp__productive__create_resource", "mcp__productive__perform_action", *claudelink.LOG_RULES]
        self.write({"permissions": {"ask": old + ["Bash(rm *)"]}})
        config.update({"ask_claude": True, "claude_rules_added": old})
        try:
            claudelink.tidy_saved()
            self.assertEqual(sorted(config.load()["claude_rules_added"]), sorted(claudelink.LOG_RULES))
            self.assertEqual(sorted(self.read()["permissions"]["ask"]), sorted([*claudelink.LOG_RULES, "Bash(rm *)"]))
            with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):  # a read-only run changes nothing
                self.write({"permissions": {"ask": old}})
                config.update({"claude_rules_added": old})
                claudelink.tidy_saved()
                self.assertEqual(self.read()["permissions"]["ask"], old)
        finally:
            config.update({"ask_claude": False, "claude_rules_added": []})

    def test_deny_rules_cover_pulsars_folders_and_the_release_key_only_where_it_is(self):
        home = os.path.expanduser("~")
        release = os.path.join(self.dir, "pulsar-release")
        with mock.patch.object(claudelink.config, "MAIN_CONFIG_DIR", os.path.join(home, ".config", "pulsar")), \
                mock.patch.object(claudelink, "RELEASE_DIR", release):
            self.assertEqual(claudelink.deny_rules(), ["Read(~/.config/pulsar/**)", "Edit(~/.config/pulsar/**)",
                                                       "Read(~/.config/pulsar-preview/**)",
                                                       "Edit(~/.config/pulsar-preview/**)"])
            os.makedirs(release)
            self.assertEqual(claudelink.deny_rules()[-2:], [f"Read(/{release}/**)", f"Edit(/{release}/**)"])

    def test_claude_config_dir_moves_the_files_pulsar_reads_and_writes(self):
        import importlib
        moved = os.path.join(self.dir, "claude-work")
        for p in reversed(self.patches[:2]):
            p.stop()
        try:
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": moved}):
                importlib.reload(claudelink)
                self.assertEqual((claudelink.SETTINGS, claudelink.CLAUDE_JSON, claudelink.status(5610)["configDir"]),
                                 (os.path.join(moved, "settings.json"), os.path.join(moved, ".claude.json"), moved))
        finally:
            importlib.reload(claudelink)
            for p in self.patches[:2]:
                p.start()
        self.assertEqual(claudelink.SETTINGS, self.settings)

    def test_status_says_what_keeps_the_rules_from_applying_and_which_account(self):
        managed = os.path.join(self.dir, "managed")
        os.makedirs(os.path.join(managed, "managed-settings.d"))
        with open(os.path.join(managed, "managed-settings.d", "10-policy.json"), "w") as fh:
            json.dump({"allowManagedPermissionRulesOnly": True}, fh)
        plist = os.path.join(self.dir, "claudecode.plist")
        with open(plist, "wb") as fh:
            import plistlib
            plistlib.dump({"allowManagedMcpServersOnly": True}, fh)
        with open(self.claude_json, "w") as fh:
            json.dump({"oauthAccount": {"emailAddress": "me@example.com", "organizationType": "claude_max"}}, fh)
        with mock.patch.object(claudelink, "MANAGED_DIR", managed), mock.patch.object(claudelink, "MANAGED_PLISTS", (plist,)):
            st = claudelink.status(5610, {})
        self.assertEqual((st["managed"]["permissionRulesOnly"], st["managed"]["mcpServersOnly"]), (True, True))
        self.assertEqual(st["account"], {"email": "me@example.com", "plan": "Max", "consumer": True})
        self.assertTrue(st["dataUsage"].startswith("https://code.claude.com/"))
        with open(self.claude_json, "w") as fh:
            json.dump({"oauthAccount": {"emailAddress": "me@corp.example", "organizationType": "claude_team"}}, fh)
        with mock.patch.object(claudelink, "MANAGED_DIR", os.path.join(self.dir, "none")), \
                mock.patch.object(claudelink, "MANAGED_PLISTS", ()):
            st = claudelink.status(5610, {})
        self.assertEqual((st["managed"]["permissionRulesOnly"], st["account"]["consumer"]), (False, False))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-x"}):
            self.assertEqual(claudelink.account()["plan"], "an API key")

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
        row = {"s": 595, "e": 730, "project": "eventsite", "titles": ["Fix the event time zone"], "commits": ["Quiz saves"]}
        with mock.patch.object(askclaude.productive, "_claude_binary", lambda: "/x/claude"), \
                mock.patch.object(askclaude, "steps", lambda *a, **k: [{"typed": "fix", "reply": "Done."}]):
            got = askclaude.note("2026-11-04", row, T0, [], run=run)
        self.assertEqual(got["note"], "Event pages show the right time zone again.")
        for flag in ("--strict-mcp-config", "--no-session-persistence", "dontAsk"):
            self.assertIn(flag, seen["cmd"])
        self.assertEqual(seen["cmd"][seen["cmd"].index("--model") + 1], "haiku")
        self.assertEqual(seen["cmd"][seen["cmd"].index("--setting-sources") + 1], "")  # no CLAUDE.md
        self.assertEqual(seen["env"].get("CLAUDE_CODE_DISABLE_AUTO_MEMORY"), "1")  # and no MEMORY.md
        self.assertEqual(seen["cmd"][seen["cmd"].index("--disallowedTools") + 1], "mcp__*")  # no MCP tool at all
        self.assertEqual(seen["env"].get("ENABLE_CLAUDEAI_MCP_SERVERS"), "false")  # nor a claude.ai connector
        self.assertIn("Quiz saves", seen["input"])

        def leaky(cmd, **kw):
            return mock.Mock(stdout=json.dumps({"structured_output": {"note": "Fixed web/themes/x/x.theme"}}), stderr="")
        row2 = {**row, "s": 596}
        with mock.patch.object(askclaude.productive, "_claude_binary", lambda: "/x/claude"), \
                mock.patch.object(askclaude, "steps", lambda *a, **k: [{"typed": "fix", "reply": "Done."}]):
            got = askclaude.note("2026-11-04", row2, T0, [], run=leaky)
        self.assertEqual(got["note"], "")
        self.assertIn("file path", got["why"])

    def test_a_session_note_naming_another_client_is_blanked(self):
        """The log dialog's note has no task yet: the projects of the block's likely tasks, and those named like its
        repository, are its own; any other project of the person's history is another client's."""
        history = [{"taskId": "7", "task": "Events", "project": "EV-OS Eventsite Ongoing Support", "date": "2026-11-02"},
                   {"taskId": "8", "task": "Sitemap", "project": "FG-OS Fengate Ongoing Support", "date": "2026-11-01"}]
        drafts = iter(["Eventsite pages show the right time zone again.", "Same fix as on Fengate."])

        def run(cmd, **kw):
            return mock.Mock(stdout=json.dumps({"structured_output": {"note": next(drafts)}}), stderr="")
        got = []
        for s in (581, 583):  # starts no other test uses: the drafts are kept by their block
            row = {"s": s, "e": 730, "project": "eventsite", "titles": ["Fix the event time zone"], "commits": ["Quiz"]}
            with mock.patch.object(askclaude.productive, "_claude_binary", lambda: "/x/claude"), \
                    mock.patch.object(askclaude.config, "load", lambda: {}), \
                    mock.patch.object(askclaude, "steps", lambda *a, **k: [{"typed": "fix", "reply": "Done."}]):
                got.append(askclaude.note("2026-11-04", row, T0, history, run=run))
        self.assertEqual(got[0]["note"], "Eventsite pages show the right time zone again.")  # its own project
        self.assertEqual((got[1]["note"], got[1]["why"]), ("", "the draft had a name from another project, so it was left out"))

    def test_nothing_to_go_on_asks_nobody(self):
        row = {"s": 1000, "e": 1010, "project": "eventsite", "titles": ["Fix the event time zone"]}
        got = askclaude.note("2026-11-04", row, T0, [], run=lambda *a, **k: self.fail("Claude was asked"))
        self.assertEqual(got["note"], "")


if __name__ == "__main__":
    unittest.main()
