"""Run the replay with the planner's switches set one way, and keep the result for compare.py.

    ... python3 -E -s -B variants.py <worktree> name key=value [key=value ...] [--first D] [--exclude ...] [--no-seq]

keys: pick, carry, swap, stop (0/1), lcarry (0/filter/demote: the dialog's learned list), share (float),
wordmin (int). Writes <PULSAR_REPLAY_DIR>/var-<name>.json.
"""
import json
import os
import sys

import analyze as A
import replay as R

name = sys.argv[2]
sets = dict(a.split("=", 1) for a in sys.argv[3:] if "=" in a and not a.startswith("--"))
dp, lr = R.dayplan, R.learning
dp.FIT_PICK = sets.get("pick", "0") == "1"
dp.FIT_CARRY = sets.get("carry", "0") == "1"
lr.FIT_CARRY = {"0": False, "filter": "filter", "demote": "demote", "demote1": "demote1"}[sets.get("lcarry", "0")]
dp.FIT_SWAP = sets.get("swap", "1") == "1"
dp.STOPWORDS = sets.get("stop", "0") == "1"
dp.FIT_SHARE = lr.FIT_SHARE = float(sets.get("share", "0.25"))
dp.WORD_MIN = int(sets.get("wordmin", "3"))
days, crashes, _ = A.run_all()
sm = A.summary(days)
print(f"{name:14s} {A.short(sm)} | crashes {len(crashes)}", flush=True)
json.dump({"summary": sm, "days": days}, open(os.path.join(R.HERE, f"var-{name}.json"), "w"), default=str)
