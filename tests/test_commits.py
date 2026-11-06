"""Notes from your commits (commits.py): which commits, which row, the draft. A real git repository in a temporary
folder and a made-up Claude session; no network.

    python3 -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import commits  # noqa: E402

T0 = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Note(unittest.TestCase):
    def test_joins_and_says_a_shared_area_once(self):
        self.assertEqual(commits.note(["Reports: one scale", "Reports: readable counts", "QA page: no empty links"]),
                         "Reports: one scale; readable counts; QA page: no empty links")

    def test_stops_short(self):
        long = ["A" * 100, "B" * 70, "C" * 10]
        self.assertEqual(commits.note(long), "A" * 100)
        self.assertEqual(commits.note(["probe: mirror hint"]), "Probe: mirror hint")
        self.assertEqual(commits.note(["qa:full: release sweep"]), "qa:full: release sweep")
        self.assertEqual(commits.note([]), "")

    def test_clean_drops_what_a_note_cannot_carry(self):
        found = [(3, "Merge branch 'qa' into dev"), (1, "Fix the event time zone."), (2, "Pulsar 1.2.1"),
                 (4, "Edit web/themes/x/x.theme"), (5, "Ask Claude: draft the note"), (6, "fix the event time zone"),
                 (7, "WIP"), (8, "fixup! Fix the event time zone"), (9, "Search keeps every letter")]
        self.assertEqual(commits.clean(found), ["Fix the event time zone", "Search keeps every letter"])

    def test_matches_names_either_way(self):
        self.assertTrue(commits.matches("Acme", "/x/acme-web"))
        self.assertTrue(commits.matches("claude-rules / skills", "/x/claude-rules"))
        self.assertFalse(commits.matches("client-site", "/x/pulsar"))
        self.assertFalse(commits.matches("", "/x/pulsar"))


@unittest.skipUnless(commits.git_binary(), "git is not installed")
class Attach(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pulsar-commits-")
        self.repo = os.path.join(self.dir, "Sites", "event-site")
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "me@example.com")
        self.git("config", "user.name", "Me")
        self.projects = os.path.join(self.dir, "projects")
        os.makedirs(os.path.join(self.projects, "p"))
        commits._root_of.clear()

    def git(self, *args, when=None, email=None):
        env = {**os.environ}
        if when is not None:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(when)} +0000"
        if email:
            env["GIT_AUTHOR_EMAIL"] = email
        subprocess.run(["git", "-C", self.repo, *args], check=True, capture_output=True, env=env)

    def commit(self, subject, minute, email=None):
        self.git("commit", "-q", "--allow-empty", "-m", subject, when=T0 + minute * 60, email=email)

    def session(self, title, messages):
        lines = [{"type": "ai-title", "aiTitle": title}]
        for minute, text in messages:
            lines.append({"type": "user", "timestamp": iso(T0 + minute * 60), "cwd": self.repo,
                          "message": {"role": "user", "content": text}})
        with open(os.path.join(self.projects, "p", "s1.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(x) for x in lines) + "\n")

    def test_each_commit_goes_to_the_row_it_was_made_in(self):
        self.commit("Event pages show the right time zone", 9 * 60 + 40)
        self.commit("Quiz results save again", 11 * 60 + 2)  # just after the second row: still that row's
        self.commit("Someone else's work", 9 * 60 + 45, email="other@example.com")
        self.commit("Too early", 7 * 60)
        self.session("Fix the event time zone", [(9 * 60, "fix it"), (10 * 60 + 30, "and the quiz")])
        rows = [{"s": 9 * 60, "e": 10 * 60, "project": "event-site", "titles": ["Fix the event time zone"]},
                {"s": 10 * 60 + 30, "e": 11 * 60, "project": "event-site", "titles": ["Fix the event time zone"]},
                {"s": 12 * 60, "e": 12 * 60 + 3, "project": "event-site", "titles": ["Fix the event time zone"]}]
        commits.attach(rows, T0, projects_dir=self.projects)
        self.assertEqual(rows[0]["commits"], ["Event pages show the right time zone"])
        self.assertEqual(rows[1]["commits"], ["Quiz results save again"])
        self.assertNotIn("commits", rows[2])  # under five minutes

    def test_another_projects_repository_gives_nothing(self):
        self.commit("Event pages show the right time zone", 9 * 60 + 40)
        self.session("Fix the event time zone", [(9 * 60, "fix it")])
        rows = [{"s": 9 * 60, "e": 10 * 60, "project": "pulsar", "titles": ["Fix the event time zone"]}]
        commits.attach(rows, T0, projects_dir=self.projects)
        self.assertEqual(rows[0]["commits"], [])

    def test_a_repositorys_own_settings_start_no_program(self):
        # A folder from elsewhere (an unpacked archive) whose .git/config asks git to check signatures with a program
        # of its choosing, and a commit carrying a made-up signature: plain `git log` would run that program.
        marker = os.path.join(self.dir, "ran")
        program = os.path.join(self.dir, "fake-gpg")
        with open(program, "w") as fh:
            fh.write(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
        os.chmod(program, 0o755)
        for k, v in (("log.showSignature", "true"), ("gpg.program", program), ("core.hooksPath", self.dir),
                     ("core.fsmonitor", program)):
            self.git("config", k, v)
        tree = subprocess.run(["git", "-C", self.repo, "mktree"], input=b"", capture_output=True, check=True).stdout.strip()
        stamp = int(T0 + 9 * 3600)
        raw = (f"tree {tree.decode()}\nauthor Me <me@example.com> {stamp} +0000\ncommitter Me <me@example.com> {stamp} "
               "+0000\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEEforgedforgedforged\n -----END PGP "
               "SIGNATURE-----\n\nSigned by nobody\n").encode()
        sha = subprocess.run(["git", "-C", self.repo, "hash-object", "-t", "commit", "-w", "--stdin"], input=raw,
                             capture_output=True, check=True).stdout.strip().decode()
        self.git("update-ref", "refs/heads/main", sha)
        subprocess.run(["git", "-C", self.repo, "log", "-1", "--format=%s"], capture_output=True)  # the trap, armed
        if not os.path.exists(marker):
            self.skipTest("this git does not run gpg.program for log.showSignature")
        os.remove(marker)
        self.assertEqual(commits._log(commits.git_binary(), self.repo, T0, subprocess.run),
                         [(stamp, "Signed by nobody")])
        self.assertFalse(os.path.exists(marker), "git ran the repository's program")

    def test_git_runs_with_its_settings_overridden_and_no_transport(self):
        seen = []

        def run(cmd, **kw):
            seen.append((cmd, kw["env"]))
            return subprocess.CompletedProcess(cmd, 0, "me@example.com\n" if "config" in cmd else "", "")
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere/.git", "GIT_SSH_COMMAND": "evil"}):
            commits._log("git", self.repo, T0, run)
        self.assertEqual(len(seen), 2)
        for cmd, env in seen:
            self.assertEqual(cmd[1:3], ["-C", self.repo])
            self.assertTrue(all(x in cmd for x in ("log.showSignature=false", "core.fsmonitor=false",
                                                   "core.hooksPath=/dev/null", "--no-pager")))
            self.assertEqual((env["GIT_ALLOW_PROTOCOL"], env["GIT_NO_LAZY_FETCH"]), ("", "1"))
            self.assertNotIn("GIT_DIR", env)
            self.assertNotIn("GIT_SSH_COMMAND", env)
        self.assertIn("--no-show-signature", seen[1][0])

    def test_no_git_no_commits(self):
        self.session("Fix the event time zone", [(9 * 60, "fix it")])
        rows = [{"s": 9 * 60, "e": 10 * 60, "project": "event-site", "titles": ["Fix the event time zone"]}]
        saved = commits._git.get("path")
        commits._git["path"] = None
        try:
            commits.attach(rows, T0, projects_dir=self.projects)
        finally:
            commits._git["path"] = saved
        self.assertNotIn("commits", rows[0])


if __name__ == "__main__":
    unittest.main()
