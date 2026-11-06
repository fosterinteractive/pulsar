"""Write the learning cache (likely-days/<date>.json, learn.FORMAT) in the replay's copy of the cache from the
collected days (data/day-<date>.json), and carry older days over from an earlier format. Only the copy is touched.

    cd tools/replay && D=<a private folder>; PULSAR_REPLAY_DIR=$D XDG_CACHE_HOME=$D/xdg/cache XDG_CONFIG_HOME=$D/xdg/config \
        PULSAR_READ_ONLY=1 python3 -E -s -B learning_days.py <pulsar worktree>
"""
import glob
import json
import os
import sys

import replay_dir

HERE = replay_dir.folder()
assert os.environ.get("PULSAR_READ_ONLY") == "1"
for v in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
    assert os.environ.get(v) and not os.path.realpath(os.environ[v]).startswith(os.path.expanduser("~/.")), v
sys.path.insert(0, sys.argv[1])
from pulsar import config, learn  # noqa: E402

assert learn.DIR.startswith(os.environ["XDG_CACHE_HOME"])
made = carried = 0
collected = set()
for path in sorted(glob.glob(os.path.join(HERE, "data", "day-*.json"))):
    doc = json.load(open(path))
    rows = [{**r, "s": r.get("pick_s", r["s"]), "e": r["e"] - (r["s"] - r.get("pick_s", r["s"]))} for r in doc["rows"]]
    config.write_atomic(os.path.join(learn.DIR, f"{doc['date']}.json"),
                        json.dumps({"format": learn.FORMAT, "rows": learn.rows_of({"entries": rows})}))
    collected.add(doc["date"])
    made += 1
for path in sorted(glob.glob(os.path.join(learn.DIR, "*.json"))):
    date = os.path.basename(path)[:-5]
    doc = json.load(open(path))
    if date not in collected and doc.get("format") != learn.FORMAT and isinstance(doc.get("rows"), list):
        config.write_atomic(path, json.dumps({"format": learn.FORMAT, "rows": doc["rows"]}))
        carried += 1
print(f"written {made} days from the collected rows, carried {carried} older days to format {learn.FORMAT}")
