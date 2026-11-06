"""Compare two analyze.py results (before, after): the headline, calibration, and every slice of the logged entries
(kind of work, weekday, repositories the session touched, the dialog's first group, new or known task), with the
largest drop. A slice of fewer than 10 entries is shown but not counted as the worst.

    python3 compare.py before.json after.json
"""
import json
import sys
from collections import defaultdict


def load(p):
    return json.load(open(p))


def metrics(recs):
    m = sum(r["minutes"] for r in recs) or 1
    n = len(recs) or 1
    return {"right time": 100 * sum(r["right_time"] for r in recs) / m,
            "dialog top1": 100 * sum(1 for r in recs if r.get("dialog_rank") == 1) / n,
            "dialog top5": 100 * sum(1 for r in recs if r.get("dialog_rank") and r["dialog_rank"] <= 5) / n,
            "in order top1": 100 * sum(1 for r in recs if r.get("seq_rank") == 1) / n,
            "in order top5": 100 * sum(1 for r in recs if r.get("seq_rank") and r["seq_rank"] <= 5) / n}


def slices(res):
    T = [t for d in res["days"] for t in d["truth"] if t["s"] is not None]
    out = {"all": T}
    keys = {"work": lambda t: t["cat"], "weekday": lambda t: t["weekday"], "repos": lambda t: min(t.get("nrep", 0), 3),
            "dialog group": lambda t: t.get("dialog_kind"), "task": lambda t: "known" if t["known"] else "new"}
    for name, k in keys.items():
        g = defaultdict(list)
        for t in T:
            g[k(t)].append(t)
        for v, recs in g.items():
            out[f"{name}={v}"] = recs
    return out


def calib(res):
    E = [e for d in res["days"] for e in d["entries"]]
    out = {}
    for c in ("high", "medium", "low"):
        es = [e for e in E if e["conf"] == c]
        m = sum(e["minutes"] for e in es) or 1
        out[c] = (sum(e["minutes"] for e in es), 100 * sum(e["right_time"] for e in es) / m,
                  100 * sum(e["right_day"] for e in es) / m)
    t = [e for e in E if e["checked"]]
    m = sum(e["minutes"] for e in t) or 1
    out["ticked"] = (sum(e["minutes"] for e in t), 100 * sum(e["right_time"] for e in t) / m,
                     100 * sum(e["right_day"] for e in t) / m)
    return out


def main(a, b):
    A, B = load(a), load(b)
    sa, sb = A["summary"], B["summary"]
    for k in ("no_change", "rec_time", "prec_time", "top1", "top5", "seq_top1", "seq_top5", "plan_top1m", "plan_top4m"):
        print(f"{k:10s} {100 * sa[k]:5.1f} -> {100 * sb[k]:5.1f}")
    Ma, Mb = sum(d["M"] for d in A["days"]), sum(d["M"] for d in B["days"])
    print(f"right amount {Ma} -> {Mb} min ({100 * Ma / sa['Q']:.1f}% -> {100 * Mb / sb['Q']:.1f}%)")
    night = "proposed minutes in Off stretches (nights)"
    na, nb = (sum(d["checks"].get(night, 0) for d in X["days"]) for X in (A, B))
    print(f"right time {sum(d['right_time'] for d in A['days'])} -> {sum(d['right_time'] for d in B['days'])} min | nights {na} -> {nb}")
    def new(res):
        T = [t for d in res["days"] for t in d["truth"] if t["s"] is not None and not t["known"]]
        m = sum(t["minutes"] for t in T) or 1
        top = lambda k, key: sum(t["minutes"] for t in T if t.get(key) and t[key] <= k)  # noqa: E731
        return (sum(t["right_time"] for t in T), m, top(1, "dialog_rank"), top(5, "dialog_rank"), top(1, "seq_rank"),
                top(5, "seq_rank"))
    xa, xb = new(A), new(B)
    print(f"new tasks ({xa[1]} min): Log the day right time {xa[0]} -> {xb[0]} min | dialog top1/top5 by minutes "
          f"{xa[2]}/{xa[3]} -> {xb[2]}/{xb[3]} | in time order {xa[4]}/{xa[5]} -> {xb[4]}/{xb[5]}")
    ca, cb = calib(A), calib(B)
    for c in ca:
        print(f"{c:7s} {ca[c][0]:5d} min {ca[c][1]:4.1f}%/{ca[c][2]:4.1f}% -> {cb[c][0]:5d} min {cb[c][1]:4.1f}%/{cb[c][2]:4.1f}%")
    la, lb = slices(A), slices(B)
    worst = (0, "", "")
    for name in sorted(la):
        ma, mb = metrics(la[name]), metrics(lb.get(name, []))
        diffs = {k: mb[k] - ma[k] for k in ma}
        line = " ".join(f"{k} {ma[k]:4.1f}->{mb[k]:4.1f}" for k in ma)
        print(f"  {name:22s} n={len(la[name]):3d} {line}")
        if len(la[name]) >= 10:
            for k, v in diffs.items():
                if v < worst[0]:
                    worst = (v, name, k)
    print(f"worst slice drop: {worst[0]:.1f} points ({worst[1]}, {worst[2]})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
