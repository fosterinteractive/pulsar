"""A range of days in numbers: logged against active, by day, by hour of the week, by app. No I/O here."""

from __future__ import annotations

import bisect
from datetime import datetime


def split_by_day(items: list, offsets: list, lengths: list) -> list:
    """Items (minutes from the range start) cut at midnights: one list of (s, e, item) per day, day-relative."""
    days: list = [[] for _ in offsets]
    for it in items:
        i = max(bisect.bisect_right(offsets, it["s"]) - 1, 0)
        s = it["s"]
        while i < len(offsets) and s < it["e"]:
            stop = min(it["e"], offsets[i] + lengths[i])
            if stop > s:
                days[i].append((s - offsets[i], stop - offsets[i], it))
            s, i = stop, i + 1
    return days


def build(dates: list, bounds: list, sources: dict, entries: list, today: str, source: str = "auto", tz=None) -> dict:
    """dates: YYYY-MM-DD; bounds: (start, end) epoch per date; sources: tracker name -> items, minutes from
    bounds[0][0]; entries: Productive rows (date, start epoch, minutes, approved, ...)."""
    t0 = bounds[0][0]
    offsets = [(a - t0) / 60 for a, _ in bounds]
    lengths = [(b - a) / 60 for a, b in bounds]
    per_source = {name: split_by_day(items, offsets, lengths) for name, items in sources.items()}

    by_date: dict = {}
    for x in entries:
        by_date.setdefault(x["date"], []).append(x)
    hours = [[0.0] * 24 for _ in range(7)]
    apps: dict = {}
    days = []
    for i, date in enumerate(dates):
        a = bounds[i][0]
        length = int(lengths[i]) + 1
        pieces = {k: v[i] for k, v in per_source.items()}
        # each day uses the tracker asked for, or the one that saw more of it
        src = source if source in pieces else max(pieces, key=lambda k: sum(e - s for s, e, _ in pieces[k])) if pieces else None
        weekday = datetime.strptime(date, "%Y-%m-%d").weekday()
        active = [0.0] * length  # active share of each minute
        work = personal = 0.0
        for s, e, it in pieces.get(src, []):
            if it["cat"] == "personal":
                personal += e - s
                continue
            work += e - s
            label = it["host"] or it["app"] or "?"
            entry = apps.setdefault(label, {"name": label, "minutes": 0.0, "icon": it.get("icon")})
            entry["minutes"] += e - s
            m = s
            while m < e:
                stop = min(e, int(m) + 1)
                if int(m) < length:
                    active[int(m)] += stop - m
                m = stop
            h = s
            while h < e:
                stop = min(e, (int(h // 60) + 1) * 60)
                # the hour on the clock, which differs from elapsed hours after a clock change
                hour = datetime.fromtimestamp(a + h * 60, tz).hour if tz else min(23, int(h // 60))
                hours[weekday][hour] += stop - h
                h = stop
        covered = bytearray(length)
        day_entries = by_date.get(date, [])
        for x in day_entries:
            if x.get("start") is not None:
                cs = int((x["start"] - a) / 60)
                for m in range(max(0, cs), min(length, cs + int(x["minutes"]))):
                    covered[m] = 1
        days.append({
            "date": date, "weekday": weekday, "source": src if (work or personal) else None,
            "active": round(work), "personal": round(personal),
            "notLogged": round(sum(min(1.0, active[m]) for m in range(length) if not covered[m])),
            "logged": sum(x["minutes"] for x in day_entries),
            "approved": sum(x["minutes"] for x in day_entries if x.get("approved")),
            "billable": sum(x["minutes"] for x in day_entries if x.get("billable")),
            "nonBillable": sum(x["minutes"] for x in day_entries if x.get("billable") is False),
            "entries": len(day_entries)})

    top_apps = sorted(apps.values(), key=lambda x: -x["minutes"])[:12]
    for x in top_apps:
        x["minutes"] = round(x["minutes"])
    keep = ("date", "minutes", "project", "budget", "service", "task", "taskId", "approved", "billable", "billing")
    return {"from": dates[0], "to": dates[-1], "today": today, "days": days,
            "entries": [{k: x.get(k) for k in keep} for x in entries if dates[0] <= x["date"] <= dates[-1]],
            "hours": [[round(v) for v in row] for row in hours], "apps": top_apps,
            "activeTotal": round(sum(x["minutes"] for x in apps.values()))}
