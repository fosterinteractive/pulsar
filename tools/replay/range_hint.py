"""The log dialog's stretch (pulsar/ranges.py) on the replayed days: how often "This range mixes ..." would show for
entries logged as one (one task) and for runs of two or three entries back to back with different tasks, for a few
thresholds; whether the split has the logged task more often than one entry; and whether the main work's task is a
better first pick than the dialog's own (docs 5.1). Each day is planned as if nothing were logged, from the days
before it only (replay.py).

    cd tools/replay && D=<a private folder>; PULSAR_REPLAY_DIR=$D XDG_CACHE_HOME=$D/xdg/cache XDG_CONFIG_HOME=$D/xdg/config \\
        PULSAR_READ_ONLY=1 python3 -E -s -B range_hint.py <pulsar worktree>

Writes range-hint.json (times, task ids, minutes) into PULSAR_REPLAY_DIR, never into the checkout.
"""
from __future__ import annotations

import json
import os

import replay as R  # noqa: E402  (reads sys.argv[1], the worktree)
from pulsar import dayplan, ranges  # noqa: E402

OPTS = {"grid": 5, "min_entry": 10}
KEEP: dict = {}
_plan = dayplan.plan


def _plan_keep(day, ctx, opts=None, keep=None):
    KEEP.clear()
    return _plan(day, ctx, opts, KEEP)


dayplan.plan = _plan_keep


def dates(first="2026-09-21", last="2026-11-05"):
    d = first
    while d <= last:
        yield d
        d = R.ds(d, 1)


def measure() -> list:
    rec = []
    for date in dates():
        out = R.run_day(date)
        if not out:
            continue
        keep = dict(KEEP)
        timed = sorted([r for r in out["truth"] if r["s"] is not None and r["minutes"] >= 10], key=lambda r: r["s"])
        at = [None] * R.N
        for r in out["truth"]:
            if r["s"] is not None and r.get("taskId"):
                for x in R.span(r["s"], r["e"]):
                    at[x] = r["taskId"]
        seq = R.dialog_sequential(out)  # the dialog with the day's earlier entries logged
        index = {id(r): i for i, r in enumerate(out["truth"])}
        blocks = R.likely_blocks(out["day"]["rows"])

        def one(a, b, kind, first):
            res = ranges.analyse(keep, a, b, OPTS)
            old, _ = seq.get(index.get(id(first)), (None, None))
            if old is None:
                old, _ = R.dialog(a, b, out["day"], out["ex"], blocks)
            saved = ranges.MIXED_SHARE, ranges.MIXED_MIN
            ranges.MIXED_SHARE, ranges.MIXED_MIN = 2.0, 0  # always split, to score the split itself
            full = ranges.analyse(keep, a, b, OPTS)
            ranges.MIXED_SHARE, ranges.MIXED_MIN = saved
            dom = (res["dominant"] or {}).get("taskId")
            split_at = {}
            for e in full["split"] or [{"s": a, "minutes": b - a, "taskId": dom}]:
                for x in range(e["s"], e["s"] + e["minutes"]):
                    split_at[x] = e["taskId"]
            logged = {}
            for x in R.span(a, b):
                if at[x]:
                    logged[at[x]] = logged.get(at[x], 0) + 1
            rec.append({"date": date, "kind": kind, "a": a, "b": b, "minutes": b - a, "mixed": res["mixed"],
                        "kept": [g["splitWork"] for g in full["groups"] if g["splitWork"]],
                        "dom": dom, "domKind": (res["dominant"] or {}).get("kind"), "old": old[:5],
                        "main": max(logged, key=logged.get) if logged else None,
                        "loggedMin": sum(logged.values()),
                        "rightSplit": sum(1 for x in R.span(a, b) if at[x] and split_at.get(x) == at[x]),
                        "rightOne": sum(1 for x in R.span(a, b) if at[x] and dom == at[x])})

        for r in timed:
            one(int(r["s"]), int(round(r["e"])), "one", r)
        for i in range(len(timed)):  # runs of two or three entries back to back (5 minutes apart or less)
            for j in (i + 1, i + 2):
                if j >= len(timed):
                    break
                run = timed[i:j + 1]
                if any(q["s"] - p["e"] > 5 or q["s"] < p["e"] - 1 for p, q in zip(run, run[1:])):
                    break
                if len({x["taskId"] for x in run}) >= 2:
                    one(int(run[0]["s"]), int(round(run[-1]["e"])), "several", run[0])
    return rec


def mixed(r, share, least=ranges.MIXED_MIN) -> bool:
    k = r["kept"]
    return len(k) >= 2 and (max(k) / sum(k) < share or sum(1 for w in k if w >= least) >= 2)


def report(rec: list) -> None:
    one = [r for r in rec if r["kind"] == "one"]
    several = [r for r in rec if r["kind"] == "several"]
    apart = [r for r in several if len(r["kept"]) >= 2]
    pct = lambda n, d: f"{100 * n / max(1, d):.1f}%"  # noqa: E731
    print(f"{len(one)} entries logged as one; {len(several)} runs of several, {len(apart)} of them told apart")
    for share in (0.5, 0.6, 0.65, 0.7, 0.75, 0.8):
        print(f"  share < {share} or two of {ranges.MIXED_MIN}+ min: one {pct(sum(mixed(r, share) for r in one), len(one))}, "
              f"several {pct(sum(mixed(r, share) for r in several), len(several))}, "
              f"told apart {pct(sum(mixed(r, share) for r in apart), len(apart))}")
    shown = [r for r in several if r["mixed"]]
    minutes = sum(r["loggedMin"] for r in shown)
    print(f"runs the line shows for: the split has the logged task for {pct(sum(r['rightSplit'] for r in shown), minutes)}"
          f" of the logged minutes, one entry for {pct(sum(r['rightOne'] for r in shown), minutes)}")
    for name, rs in (("one", one), ("several", several)):
        total = sum(r["minutes"] for r in rs)
        old = sum(r["minutes"] for r in rs if r["main"] and r["old"][:1] == [r["main"]])
        lead = lambda r: [r["dom"]] if r["dom"] and r["domKind"] != "outside" else r["old"][:1]  # noqa: E731
        new = sum(r["minutes"] for r in rs if r["main"] and lead(r) == [r["main"]])
        print(f"first task right ({name}): the dialog's own {pct(old, total)}, the main work's {pct(new, total)}")


if __name__ == "__main__":
    rec = measure()
    with open(os.path.join(R.HERE, "range-hint.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    report(rec)
