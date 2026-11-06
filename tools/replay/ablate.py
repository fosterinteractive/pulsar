"""Ablations: the same replay with one change to the planner or the dialog at a time.

    PULSAR_REPLAY_DIR=... XDG_CACHE_HOME=... XDG_CONFIG_HOME=... PULSAR_READ_ONLY=1 python3 -E -s -B ablate.py <worktree> name [name ...]
        [--first D] [--last D] [--exclude D,D]
"""
from __future__ import annotations

import os, sys, types
from collections import defaultdict

import analyze as A
import replay as R

# the planner to vary: the working copy, or ABLATE_SRC (e.g. `git show HEAD:pulsar/dayplan.py > before.py`)
SRC = open(os.environ.get("ABLATE_SRC") or R.dayplan.__file__, encoding="utf-8").read()

PATCHES = {
    # a piece too short for an entry, with no entry near it, is left uncovered instead of becoming a tiny entry
    "drop-lone-short": [(
        """    skipped += [{"s": m["s"], "e": m["last"] + 1, "minutes": m["minutes"], "project": m.get("project") or "",""",
        """    merged = [m for m in merged if m["minutes"] >= o["min_entry"] or m["kind"] == "skip" or not o["min_entry"]
              or (m.get("attended") and m["minutes"] >= 5)]
    skipped += [{"s": m["s"], "e": m["last"] + 1, "minutes": m["minutes"], "project": m.get("project") or "",""")],
    # a calendar meeting is "the same call" as a repeat only when the repeat covers half of the meeting itself (today:
    # half of the shorter, so a repeat stretched over the next meeting swallowed it and left its call with no task)
    "meeting-own-half": [(
        "        same = next((x for x in attended if _overlap(x[0], x[1], s, e) >= 0.5 * min(x[1] - x[0], e - s)), None)",
        "        same = next((x for x in attended if _overlap(x[0], x[1], s, e) >= 0.5 * (e - s)), None)")],
    # an editor or terminal minute with no session row is work outside Claude (the neighbour's task, unticked)
    # instead of being set aside
    "editor-outside": [(
        """        if work[x] and owner[x] is None and (editor[x] or logging_[x]):""",
        """        if work[x] and owner[x] is None and logging_[x]:""")],
    # a session vote of 40-60% is "low", not "medium" (so it starts unticked)
    "session-medium-low": [(
        '                conf = "high" if share >= 0.6 and m >= 30 else "medium" if share >= 0.4 else "low"',
        '                conf = "high" if share >= 0.6 and m >= 30 else "low"')],
    # a short piece joins a neighbour only in the same sitting and within MERGE_GAP of it (today: any neighbour,
    # however far: a 3-minute row after midnight pulled a 200-minute afternoon entry to 00:40)
    "join-near": [(
        '''    if o["min_entry"]:
        changed = True''',
        '''    def near(p, q) -> bool:
        x, y = sorted((p, q), key=lambda r: r["s"])
        sx, sy = sitting[x["last"]], sitting[y["s"]]
        return y["s"] - x["last"] <= o["merge_gap"] and (sx is None or sy is None or sx == sy)

    if o["min_entry"]:
        changed = True'''), (
        '''                          if q is not None and q["kind"] != "skip"]''',
        '''                          if q is not None and q["kind"] != "skip" and near(p, q)]''')],
    # "you never log this repository" only from days whose entries all have a start time (an entry logged by length
    # only cannot be matched to the work, so it made the person's main repositories look never logged)
    "never-timed": [(
        '''    for d, rows in days.items():
        if not lo <= d < date or not rows:
            continue
        t0 = t0_of(d)''',
        '''    untimed = {d for d, rs in by_day.items() if any(r.get("start") is None for r in rs)}
    for d, rows in days.items():
        if not lo <= d < date or not rows:
            continue
        t0 = t0_of(d)'''), (
        '''            seen[row.get("p") or ""] += m
            logged[row.get("p") or ""] += min(cov, m)''',
        '''            if d not in untimed:
                seen[row.get("p") or ""] += m
                logged[row.get("p") or ""] += min(cov, m)''')],
    # entries laid out where their work was: a merged entry keeps its first part's start and is split where its
    # parts are more than 5 minutes apart (instead of running on from its first minute over the gaps)
    "split-gaps": [(
        '''        if m and m.get("task") and m["task"] == p.get("task") and "skip" not in (m["kind"], p["kind"]) \\
                and p["s"] - m["last"] <= o["merge_gap"] and sitting[m["last"]] == sitting[p["s"]]:''',
        '''        if m and m.get("task") and m["task"] == p.get("task") and "skip" not in (m["kind"], p["kind"]) \\
                and p["s"] - m["last"] <= o.get("split_gap", o["merge_gap"]) and sitting[m["last"]] == sitting[p["s"]]:''')],
}


def variant(names: list):
    src = SRC
    for n in names:
        for a, b in PATCHES.get(n, []):
            assert a in src, (n, a[:60])
            src = src.replace(a, b)
    mod = types.ModuleType("dayplan_variant")
    mod.__dict__["__package__"] = "pulsar"
    mod.__dict__["__name__"] = "pulsar.dayplan_variant"
    exec(compile(src, "dayplan_variant.py", "exec"), mod.__dict__)
    orig = mod._entry

    def with_span(m, s, e, names_):
        out = orig(m, s, e, names_)
        out["span_s"], out["span_e"] = m["s"], m["last"] + 1
        out["part_spans"] = [(q["s"], q["last"] + 1) for q in m["parts"]]
        return out
    mod._entry = with_span
    return mod


def run(names: list):
    mod = variant([n for n in names if n in PATCHES])
    saved = R.dayplan
    R.dayplan = mod
    opts = {}
    if "split-gaps" in names:
        opts["plan_opts"] = {"split_gap": 5}
    if "no-assigned" in names:
        opts["no_assigned"] = True
    if "no-meetings" in names:
        opts["no_meetings"] = True
    try:
        days, crashes, _ = A.run_all(opts)
    finally:
        R.dayplan = saved
    sm = A.summary(days)
    checks = defaultdict(int)
    for d in days:
        for k, v in d["checks"].items():
            checks[k] += v
    E = [e for d in days for e in d["entries"]]
    hi = [e for e in E if e["conf"] == "high"]
    tick = [e for e in E if e["checked"]]
    M = sum(d["M"] for d in days)
    cal = " | ".join(f"{c} {sum(e['minutes'] for e in E if e['conf'] == c)} min "
                     f"{A.pct(sum(e['right_time'] for e in E if e['conf'] == c), sum(e['minutes'] for e in E if e['conf'] == c))}/"
                     f"{A.pct(sum(e['right_day'] for e in E if e['conf'] == c), sum(e['minutes'] for e in E if e['conf'] == c))}"
                     for c in ("high", "medium", "low"))
    tm = sum(e["minutes"] for e in tick)
    print(f"{'+'.join(names) or 'baseline':28s} " + A.short(sm))
    print(f"{'':28s} right amount {M} min ({A.pct(M, sm['Q'])} of logged) | calibration (right time/right day) {cal}")
    print(f"{'':28s} ticked {tm} min, right day {sum(e['right_day'] for e in tick)}, wrong day {tm - sum(e['right_day'] for e in tick)}"
          f" | nights {checks['proposed minutes in Off stretches (nights)']}")
    print(f"{'':28s} entries {len(E)} | high right time {A.pct(sum(e['right_time'] for e in hi), sum(e['minutes'] for e in hi))}"
          f" | ticked right time {A.pct(sum(e['right_time'] for e in tick), sum(e['minutes'] for e in tick))}"
          f" right day {A.pct(sum(e['right_day'] for e in tick), sum(e['minutes'] for e in tick))}"
          f" | on away {checks['proposed work minutes on away time']} | off its work {checks['minutes laid out off the work they stand for']}"
          f" | crashes {len(crashes)}", flush=True)
    return sm, days


if __name__ == "__main__":
    names = [a for a in sys.argv[2:] if not a.startswith("--") and not a[:4].isdigit()]
    for spec in names or ["baseline"]:
        run([] if spec == "baseline" else spec.split("+"))
