"""Variants of the log dialog's task order, scored on every logged entry (top-1/3/5 by entries and by minutes).
Cold (nothing logged that day) and in time order (Log and next: the day's earlier entries logged).

    PULSAR_REPLAY_DIR=... XDG_CACHE_HOME=... XDG_CONFIG_HOME=... PULSAR_READ_ONLY=1 python3 -E -s -B dialog_ablate.py <worktree> [--first D] [--exclude ...]
"""
from __future__ import annotations

from collections import Counter, defaultdict

import analyze as A
import replay as R


def ov(r, a, b):
    return max(0.0, min(b, r["e"]) - max(a, r["s"]))


def lead_overlap(a, b, rows, near=30):
    """The rows under [a, b) vote with their overlap: links first (strong), then each row's learned tasks by rank.
    No row under it: the nearest row within `near` minutes, the one before first."""
    under = [r for r in rows if ov(r, a, b) > 0]
    if not under:
        cand = [(min(abs(r["e"] - a), abs(r["s"] - b)), r) for r in rows if r["e"] <= a + near and r["s"] >= a - near - 600]
        cand = [(d, r) for d, r in cand if d <= near]
        under = [min(cand, key=lambda x: x[0])[1]] if cand else []
    score = Counter()
    for r in under:
        w = ov(r, a, b) or 1.0
        for t in r.get("tasks") or []:
            score[str(t)] += 3 * w
        for i, t in enumerate(r.get("learned") or []):
            score[str(t["taskId"])] += w / (i + 1)
    return [t for t, _ in score.most_common()]


def recent_weighted(date, today_before):
    from datetime import datetime
    d0 = R.ds(date, -30)
    rows = [r for r in R.ROWS if d0 <= r["date"] < date] + today_before
    sc = Counter()
    for r in rows:
        if r.get("taskId"):
            age = (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(r["date"], "%Y-%m-%d")).days
            sc[r["taskId"]] += r["minutes"] * 0.5 ** (age / 7)
    return [t for t, _ in sc.most_common()]


def orders(out, r, before, mode):
    """{variant: ordered ids} for one logged entry."""
    date, day, ex = out["date"], out["day"], out["ex"]
    if mode == "seq":
        recent, assigned = R.suggest_lists_with(date, before, R.ASSIGNED)
        rows = R.with_commits(date, [dict(x) for x in out["doc"]["rows"]])
        R.learning.annotate({"entries": rows}, date, R.S, ex["hist60"], before, R.TZ, pool=R.POOL)
        done = {x["taskId"] for x in before}
        ex = {**ex, "recent": recent, "assigned": assigned, "reps": [p for p in ex["reps"] if str(p["taskId"]) not in done]}
    else:
        rows = day["rows"]
    blocks = R.likely_blocks(rows)
    base, kind = R.dialog(r["s"], r["e"], {**day, "rows": rows}, ex, blocks)
    lead, _ = R.dialog_lead(r["s"], r["e"], {**day, "rows": rows}, ex, blocks)
    tail = [t["taskId"] for t in ex["recent"][:10]] + [t["taskId"] for t in ex["assigned"][:10]]
    rw = recent_weighted(date, before if mode == "seq" else [])
    rep = lead[:1] if kind == "repeat" else []
    ovl = lead_overlap(r["s"], r["e"], rows)
    # the planner's entry over most of [a, b) and its alternatives
    cov = Counter()
    for i, e in enumerate(out["plan"]["entries"]):
        o = ov(e, r["s"], r["e"])
        if o > 0 and e["taskId"]:
            cov[i] += o
    plan = []
    if cov:
        e = out["plan"]["entries"][cov.most_common(1)[0][0]]
        plan = [e["taskId"]] + [x["taskId"] for x in e["alts"]]
    u = lambda *ls: list(dict.fromkeys(t for l in ls for t in l))  # noqa: E731
    return {
        "today": base,
        "overlap-lead": u(rep, ovl, lead if kind == "meeting" else [], tail),
        "recency-weighted lately": u(lead, rw[:10], [t["taskId"] for t in ex["assigned"][:10]]),
        "overlap+weighted": u(rep, ovl, lead if kind == "meeting" else [], rw[:10], [t["taskId"] for t in ex["assigned"][:10]]),
        "planner first": u(plan, base),
        "overlap+planner": u(rep, ovl[:1], plan, ovl, lead if kind == "meeting" else [], rw[:10], [t["taskId"] for t in ex["assigned"][:10]]),
    }


def main():
    res = {m: defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0, 0]) for m in ("cold", "seq")}  # n, top1, top3, top5, minutes...
    for d in A.dates():
        out = R.run_day(d)
        if not out:
            continue
        timed = sorted([r for r in out["truth"] if r["s"] is not None], key=lambda r: r["s"])
        for k, r in enumerate(timed):
            for mode in ("cold", "seq"):
                for name, order in orders(out, r, timed[:k], mode).items():
                    rank = order.index(r["taskId"]) + 1 if r["taskId"] in order else None
                    x = res[mode][name]
                    x[0] += 1; x[4] += r["minutes"]
                    for j, top in ((1, 1), (2, 3), (3, 5)):
                        if rank and rank <= top:
                            x[j] += 1; x[4 + j] += r["minutes"]
    for mode in ("cold", "seq"):
        print(f"## {mode}")
        for name, x in res[mode].items():
            print(f"{name:26s} top1/3/5 {x[1] / x[0]:.1%}/{x[2] / x[0]:.1%}/{x[3] / x[0]:.1%} | by minutes "
                  f"{x[5] / x[4]:.1%}/{x[6] / x[4]:.1%}/{x[7] / x[4]:.1%} | n {x[0]}")


if __name__ == "__main__":
    main()
