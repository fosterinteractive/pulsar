"""tools/replay keeps a person's real entries, window titles, meetings and typed text: never inside this repository.

    python3 -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPLAY = os.path.join(ROOT, "tools", "replay")
TOOLS = ["collect.py", "cal_collect.py", "replay.py", "analyze.py", "variants.py", "ablate.py", "dialog_ablate.py"]


def run(tool: str, replay_dir):
    # PULSAR_READ_ONLY unset and a worktree that does not exist: if the folder check were gone, the tool would still
    # stop at its next line, before importing Pulsar or reading anything
    env = {k: v for k, v in os.environ.items() if k not in ("PULSAR_REPLAY_DIR", "PULSAR_READ_ONLY")}
    if replay_dir is not None:
        env["PULSAR_REPLAY_DIR"] = replay_dir
    nowhere = os.path.join(tempfile.mkdtemp(), "no-worktree")
    return subprocess.run([sys.executable, "-E", "-s", "-B", tool, nowhere, "x", "2026-11-01", "2026-11-02"],
                          cwd=REPLAY, env=env, capture_output=True, text=True, timeout=60)


class ReplayDataTest(unittest.TestCase):
    def test_every_tool_refuses_to_run_without_a_private_folder(self):
        for tool in TOOLS:
            got = run(tool, None)
            self.assertNotEqual(got.returncode, 0, tool)
            self.assertIn("Set PULSAR_REPLAY_DIR", got.stderr, tool)
        self.assertFalse(os.path.exists(os.path.join(REPLAY, "data")))

    def test_a_folder_inside_a_git_checkout_is_refused(self):
        for folder in (REPLAY, os.path.join(ROOT, "private")):  # this repository, even where nothing exists yet
            got = run("collect.py", folder)
            self.assertNotEqual(got.returncode, 0)
            self.assertIn("inside the git checkout", got.stderr)
        self.assertFalse(os.path.exists(os.path.join(REPLAY, "data")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "private")))

    def test_a_private_folder_is_accepted_and_kept_owner_only(self):
        folder = os.path.join(tempfile.mkdtemp(), "replay")
        got = run("collect.py", folder)
        self.assertNotIn("PULSAR_REPLAY_DIR", got.stderr)  # it got past the folder check (and stopped at read-only)
        self.assertEqual(os.stat(os.path.join(folder, "data")).st_mode & 0o777, 0o700)

    def test_git_ignores_the_replays_data_even_if_written_here(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            ignored = {line.strip() for line in fh}
        self.assertTrue({"tools/replay/data/", "tools/replay/results.json", "tools/replay/var-*.json"} <= ignored)


if __name__ == "__main__":
    unittest.main()
