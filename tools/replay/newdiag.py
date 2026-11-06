"""How often the first new task found on a row (learn.new_tasks) is the task logged over most of that row, by how it
was found (a ticket key, or the cosine of the words): what NEW_MIN and NEW_PICK_MIN were set from.

    cd tools/replay && D=<a private folder>; PULSAR_REPLAY_DIR=$D XDG_CACHE_HOME=$D/xdg/cache XDG_CONFIG_HOME=$D/xdg/config \
        PULSAR_READ_ONLY=1 python3 -E -s -B newdiag.py <pulsar worktree> [new_min] --first D --exclude D,D
"""
import json, os, sys
from collections import Counter
import analyze as A, replay as R
R.POOL = json.load(open(os.path.join(R.DATA, "tasks.json")))["tasks"]
R.learning.NEW_MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
st = Counter(); bysrc = Counter(); okscore = []
for d in A.dates():
    out = R.run_day(d)
    if not out: continue
    for r in out["day"]["rows"]:
        fresh = [x for x in r.get("learned") or [] if x.get("new")]
        if not fresh: continue
        cov = Counter()
        for t in out["truth"]:
            if t["s"] is not None:
                o = R.dayplan._overlap(t["s"], t["e"], r["s"], r["e"])
                if o > 0: cov[t["taskId"]] += o
        top = cov.most_common(1)[0][0] if cov else None
        m = r["e"] - r["s"]
        f = fresh[0]
        ok = f["taskId"] == top
        any_ok = f["taskId"] in cov
        b = "key" if f.get("key") else ("s>=0.5" if f["score"] >= 0.5 else "s>=0.4" if f["score"] >= 0.4 else "s>=0.3" if f["score"] >= 0.3 else "s<0.3")
        st[(b, "main" if ok else "some" if any_ok else "wrong")] += m
        okscore.append((round(f["score"], 2), ok))
for k in sorted(st): print(k, st[k])
