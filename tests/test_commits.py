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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRATCH = tempfile.mkdtemp(prefix="pulsar-tests-")
os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(_SCRATCH, "config"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_SCRATCH, "cache"))

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
        self.assertTrue(commits.matches("Inovia", "/x/inovia-web-ast"))
        self.assertTrue(commits.matches("claude-rules / skills", "/x/claude-rules"))
        self.assertFalse(commits.matches("tua-trent", "/x/pulsar"))
        self.assertFalse(commits.matches("", "/x/pulsar"))


@unittest.skipUnless(commits.git_binary(), "git is not installed")
class Attach(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pulsar-commits-")
        self.repo = os.path.join(self.dir, "Sites", "trent-site")
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
        rows = [{"s": 9 * 60, "e": 10 * 60, "project": "trent-site", "titles": ["Fix the event time zone"]},
                {"s": 10 * 60 + 30, "e": 11 * 60, "project": "trent-site", "titles": ["Fix the event time zone"]},
                {"s": 12 * 60, "e": 12 * 60 + 3, "project": "trent-site", "titles": ["Fix the event time zone"]}]
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

    def test_no_git_no_commits(self):
        self.session("Fix the event time zone", [(9 * 60, "fix it")])
        rows = [{"s": 9 * 60, "e": 10 * 60, "project": "trent-site", "titles": ["Fix the event time zone"]}]
        saved = commits._git.get("path")
        commits._git["path"] = None
        try:
            commits.attach(rows, T0, projects_dir=self.projects)
        finally:
            commits._git["path"] = saved
        self.assertNotIn("commits", rows[0])


if __name__ == "__main__":
    unittest.main()
