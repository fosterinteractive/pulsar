"""Collect, read only, everything Log the day and the log dialog read for each day of the replay window.

    cd tools/replay && D=<a private folder>; PULSAR_REPLAY_DIR=$D XDG_CACHE_HOME=$D/xdg/cache XDG_CONFIG_HOME=$D/xdg/config PULSAR_READ_ONLY=1 \
        python3 -E -s -B collect.py <pulsar worktree> <first eval day> <last eval day>

Writes data/entries.json (Productive, 60+28 days before the window to its end), data/assigned.json and
data/day-<date>.json (activity, away, calls, Likely rows with task links). Calendar: cal_collect.py, separately.
"""
import json, os, sys, time
from datetime import datetime, timedelta

assert os.environ.get("PULSAR_READ_ONLY") == "1"
for v in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
    assert os.environ.get(v) and not os.path.realpath(os.environ[v]).startswith(os.path.expanduser("~/.")), v  # copies, never the real dirs
sys.path.insert(0, sys.argv[1])
from pulsar import config, likely, productive, server, sessions  # noqa: E402

assert config.CACHE_DIR.startswith(os.environ["XDG_CACHE_HOME"]) and config.CONFIG_DIR.startswith(os.environ["XDG_CONFIG_HOME"])
HERE = os.environ.get("PULSAR_REPLAY_DIR") or os.path.dirname(os.path.abspath(__file__))  # data/ and results go here
OUT = os.path.join(HERE, "data")
first, last = sys.argv[2], sys.argv[3]
s = server.current_settings()
tz = config.tz(s)
tzname = config.timezone_name(s)
print("tz", tzname, "own script", likely.own_script(), "person", bool(s.get("person_id")), file=sys.stderr)

if "--skip-productive" not in sys.argv:
    h0 = (datetime.strptime(first, "%Y-%m-%d") - timedelta(days=62)).strftime("%Y-%m-%d")
    rows, problem, source = productive.entries(h0, last, s, bulk=True)
    print("entries", len(rows), "problem", problem, "source", source, file=sys.stderr)
    assert not problem, problem
    keep = ("id", "date", "start", "minutes", "taskId", "task", "serviceId", "service", "project", "budget", "billable",
            "billing", "note")
    json.dump({"tz": tzname, "from": h0, "to": last, "rows": [{k: r.get(k) for k in keep} for r in rows]},
              open(os.path.join(OUT, "entries.json"), "w"), ensure_ascii=False)
    try:
        assigned = productive.assigned_tasks(s)
    except Exception as exc:
        print("assigned failed", exc, file=sys.stderr)
        assigned = []
    json.dump(assigned, open(os.path.join(OUT, "assigned.json"), "w"), ensure_ascii=False)
    print("assigned", len(assigned), file=sys.stderr)

d = datetime.strptime(first, "%Y-%m-%d")
end = datetime.strptime(last, "%Y-%m-%d")
while d <= end:
    date = d.strftime("%Y-%m-%d")
    d += timedelta(days=1)
    path = os.path.join(OUT, f"day-{date}.json")
    if os.path.exists(path) and "--again" not in sys.argv:
        continue
    t = time.time()
    t0, t1 = server.day_bounds(date, tz)
    shift = datetime.fromtimestamp(t0, tz).utcoffset() != datetime.fromtimestamp(t1 - 1, tz).utcoffset()
    err = None
    try:
        act = server.api_activity({"date": date, "source": "auto"})
    except Exception as exc:
        act, err = {"source": None, "items": [], "away": [], "minutes": {}}, f"activity: {type(exc).__name__}: {exc}"
    got = likely._run(date, tzname, tz) if likely.own_script() else likely._built_in(date, s)
    if not got.get("error"):
        likely._with_tasks(got, date, s, commits=False)
    rows = []
    for e in got.get("entries") or []:  # as dayplan.gather's proposal(): by the clock on the day the clocks change
        r = {**e, "pick_s": e["s"]}
        if shift:
            r["s"], r["e"] = round(server.clock(t0 + e["s"] * 60, t0, tz), 2), round(server.clock(t0 + e["e"] * 60, t0, tz), 2)
        rows.append(r)
    calls = [{"s": it["s"], "e": it["e"]} for it in act.get("items") or [] if it.get("cat") == "work" and server.is_call(it)]
    doc = {"date": date, "t0": t0, "shift": shift, "error": err, "likelyError": got.get("error"),
           "activity": {"source": act.get("source"), "minutes": act.get("minutes"), "away": act.get("away") or [],
                        "items": [{k: it.get(k) for k in ("s", "e", "app", "title", "host", "url", "cat", "workspace")}
                                  for it in act.get("items") or []]},
           "calls": calls, "rows": rows,
           "likely": {k: got.get(k) for k in ("tracker", "working", "proposed", "logging")}}
    json.dump(doc, open(path, "w"), ensure_ascii=False)
    print(date, "items", len(doc["activity"]["items"]), act.get("source"), "rows", len(rows), "err", err or got.get("error"),
          f"{time.time() - t:.1f}s", file=sys.stderr, flush=True)
print("done", file=sys.stderr)
