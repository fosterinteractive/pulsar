"""Run the replay with some of the planner's constants set one way, and keep the result for compare.py.

    ... python3 -E -s -B variants.py <worktree> name key=value [key=value ...] [--first D] [--exclude ...] [--no-seq]

keys: pool (0/1: offer tasks you never logged to, from data/tasks.json), strict (1: only tasks created before the
day), place (their place in the list), replies (0/1), newmin, pickmin (floats). Writes <PULSAR_REPLAY_DIR>/var-<name>.json.
"""
import json
import os
import sys

import analyze as A
import replay as R

name = sys.argv[2]
sets = dict(a.split("=", 1) for a in sys.argv[3:] if "=" in a and not a.startswith("--"))
dp, lr = R.dayplan, R.learning
if sets.get("pool", "0") == "1":
    tasks = json.load(open(os.path.join(R.DATA, "tasks.json")))["tasks"]
    if sets.get("strict") == "1":  # created before the day began: as if the person never made a task that day
        _pool = lr.new_task_pool
        lr.new_task_pool = lambda t, h, date, p, days=lr.NEW_DAYS: [x for x in _pool(t, h, date, p, days)
                                                                     if str(x.get("created"))[:10] < date]
    R.POOL = tasks
lr.NEW_PLACE = int(sets.get("place", lr.NEW_PLACE))
lr.NEW_REPLIES = sets.get("replies", "1" if lr.NEW_REPLIES else "0") == "1"
lr.NEW_MIN = float(sets.get("newmin", lr.NEW_MIN))
dp.NEW_PICK_MIN = float(sets.get("pickmin", dp.NEW_PICK_MIN))
days, crashes, _ = A.run_all()
sm = A.summary(days)
print(f"{name:14s} {A.short(sm)} | crashes {len(crashes)}", flush=True)
for c in crashes:
    print(c[0], c[1], c[2][-400:])
json.dump({"summary": sm, "days": days}, open(os.path.join(R.HERE, f"var-{name}.json"), "w"), default=str)
