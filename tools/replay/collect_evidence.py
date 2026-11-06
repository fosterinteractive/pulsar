"""Collect, read only, each Likely row's same-day evidence for the replay: the person's commit subjects in the row
(commits.attach: `git config` and `git log` only) and its steps (askclaude.steps: what was typed and Claude's last
words). Also reads Productive's tasks with any activity since a date (productive.recent_tasks), which the replay
filters day by day: never a task created after the day.

    cd tools/replay && D=<a private folder>; PULSAR_REPLAY_DIR=$D XDG_CACHE_HOME=$D/xdg/cache XDG_CONFIG_HOME=$D/xdg/config \
        PULSAR_READ_ONLY=1 python3 -E -s -B collect_evidence.py <pulsar worktree> <first day> <last day> <tasks since>

Writes data/evidence-<date>.json ([{s, e, project, commits, steps}]) and data/tasks.json. All of it is private.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import replay_dir

HERE = replay_dir.folder()
assert os.environ.get("PULSAR_READ_ONLY") == "1"
for v in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
    assert os.environ.get(v) and not os.path.realpath(os.environ[v]).startswith(os.path.expanduser("~/.")), v
sys.path.insert(0, sys.argv[1])
from pulsar import askclaude, commits, config, productive  # noqa: E402

OUT = os.path.join(HERE, "data")
first, last, since = sys.argv[2], sys.argv[3], sys.argv[4]
s = config.load()
tasks = productive.recent_tasks(since, s, limit=10000)
json.dump({"since": since, "read": datetime.now().isoformat(timespec="minutes"), "tasks": tasks},
          open(os.path.join(OUT, "tasks.json"), "w"), ensure_ascii=False)
print("tasks", len(tasks), file=sys.stderr, flush=True)
d = datetime.strptime(first, "%Y-%m-%d")
while d <= datetime.strptime(last, "%Y-%m-%d"):
    date = d.strftime("%Y-%m-%d")
    d += timedelta(days=1)
    src = os.path.join(OUT, f"day-{date}.json")
    dst = os.path.join(OUT, f"evidence-{date}.json")
    if not os.path.exists(src) or os.path.exists(dst):
        continue
    t = time.time()
    doc = json.load(open(src))
    rows = [dict(r, s=r.get("pick_s", r["s"])) for r in doc["rows"]]  # elapsed minutes, as the session files count
    commits.attach(rows, doc["t0"])
    out = []
    for r in rows:
        steps = askclaude.steps(doc["t0"], r.get("titles") or [], r["s"], r["e"]) if r.get("titles") else []
        out.append({"s": r["s"], "e": r["e"], "project": r.get("project"), "commits": r.get("commits") or [],
                    "steps": steps})
    json.dump(out, open(dst, "w"), ensure_ascii=False)
    print(date, len(out), "rows", sum(1 for x in out if x["commits"]), "with commits", f"{time.time() - t:.1f}s",
          file=sys.stderr, flush=True)
print("done", file=sys.stderr)
