"""Read the calendar for the replay, read only, through the real config dir (the Google client stays where it is,
never copied or opened here; Pulsar's calendar.client() reads it). Every writer in config is replaced by one that
raises, so nothing in ~/.config/pulsar or the keychain can change.

    cd tools/replay && D=<a private folder>; PULSAR_REPLAY_DIR=$D XDG_CACHE_HOME=$D/xdg/cache PULSAR_READ_ONLY=1 \
        python3 -E -s -B cal_collect.py <pulsar worktree> <first day> <last day>

Writes data/calendar.json: {"days": {date: events_on(...)}, "moments": [{s, e, uid, title, declined}]}.
"""
import json, os, sys
from datetime import datetime, timedelta

assert os.environ.get("PULSAR_READ_ONLY") == "1"
assert os.environ.get("XDG_CACHE_HOME") and not os.path.realpath(os.environ["XDG_CACHE_HOME"]).startswith(os.path.expanduser("~/."))
assert not os.environ.get("XDG_CONFIG_HOME")
sys.path.insert(0, sys.argv[1])
from pulsar import config, calendar  # noqa: E402


def refuse(*a, **k):
    raise RuntimeError("write refused in the replay")


for name in ("write_atomic", "save", "update", "set_google_token", "delete_google_token", "_write_secret",
             "_delete_secret", "set_token", "delete_token", "_keep_copy", "ensure_dirs", "_save_sessions",
             "new_session", "end_session", "new_signin_code", "take_signin_code"):
    if hasattr(config, name):
        setattr(config, name, refuse)

s = config.load()
tz = config.tz(s)
first, last = sys.argv[2], sys.argv[3]
d0 = datetime.strptime(first, "%Y-%m-%d")
n = (datetime.strptime(last, "%Y-%m-%d") - d0).days + 1
items = []
token = calendar._access_token()
for k in range(0, n, 14):
    day = (d0 + timedelta(days=k)).strftime("%Y-%m-%d")
    try:
        doc = calendar._list(day, tz, token, min(14, n - k))
    except calendar._Expired:
        token = calendar._access_token(renew=True)
        doc = calendar._list(day, tz, token, min(14, n - k))
    items += doc["items"]
    print(day, len(doc["items"]), file=sys.stderr)
days = {}
for k in range(n):
    day = (d0 + timedelta(days=k)).strftime("%Y-%m-%d")
    days[day] = calendar.events_on(items, day, tz)
moments = []
for x in items:  # as calendar.history()
    a, b = calendar._moment((x.get("start") or {}).get("dateTime")), calendar._moment((x.get("end") or {}).get("dateTime"))
    if x.get("status") == "cancelled" or x.get("eventType") in calendar.NOT_MEETINGS or not a or not b:
        continue
    moments.append({"s": a.timestamp(), "e": b.timestamp(), "uid": str(x.get("recurringEventId") or x.get("id") or ""),
                    "title": str(x.get("summary") or "")[:300],
                    "declined": any(isinstance(g, dict) and g.get("self") and g.get("responseStatus") == "declined"
                                    for g in x.get("attendees") or [])})
out = os.path.join(os.environ.get("PULSAR_REPLAY_DIR") or os.path.dirname(os.path.abspath(__file__)), "data", "calendar.json")
json.dump({"days": days, "moments": moments}, open(out, "w"), ensure_ascii=False)
print("events", len(items), "moments", len(moments), "days", len(days), file=sys.stderr)
