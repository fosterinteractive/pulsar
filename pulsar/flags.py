"""Ticket flags: what Productive says about a task that someone should fix there, shown while time is reviewed (the
log dialog, and each row of Log the day). Read only: Pulsar never fixes them; the page links each one to the task in
Productive.

* closed: the task is closed;
* nobudget: no service of the task's project takes time on that date, so there is no budget to log it to;
* overestimate: with this entry (and the plan's earlier ones on the task), the time worked on the task, by everyone,
  goes over its estimate;
* noestimate: the task has no estimate, while at least a quarter (and at least two) of the project's 100 most recently
  active tasks have one. Estimates are rare in most projects (on one person's 14 projects, 0 to 35% of recent tasks
  had one), so a flag for every task without one would be on nearly every row. Settings > Advanced can switch it off.

No flag for a budget nearly used up: budgets cannot be read without "View budgets where a user is a member"
(budgets_view_restricted), and a service's budget fields (budget_total, budget_used, budgeted_time, worked_time) come
back empty for a role without it, such as Staff (checked against Productive, November 2026).

How many of a project's recent tasks have an estimate, and those estimates (for a new task's, newtask.propose), is kept
in names.json like the names (`estimates:projects:<id>`) and read again after a day; a failed read keeps the old answer
and asks again an hour later.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from . import config, logbook, productive

ESTIMATE_SHARE = 0.25  # this share of the project's recent tasks have an estimate...
ESTIMATE_MIN = 2       # ...and at least this many: then a task without one is flagged
RETRY = 3600           # a failed read: asked again after an hour
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_failed: dict = {}  # project id -> when a read with nothing kept failed, so it is not asked on every row


def _day(date: str) -> str:
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return "that date"
    return f"{d.day} {MONTHS[d.month - 1]}"


def project_share(project_id, settings: dict, names: Optional[productive.Names] = None,
                  sizes: bool = False) -> Optional[dict]:
    """{"n", "with", "sizes"} for the project (productive.project_estimates), from names.json while a day old or less;
    None when it is not known and cannot be read now. Never raises. sizes=True (a new task's estimate, newtask.propose)
    reads again an answer kept before Pulsar kept the estimates themselves."""
    pid = str(project_id or "")
    if not pid.isdigit():
        return None
    names = names or productive.shared_names()
    key = f"estimates:projects:{pid}"
    kept = names.get(key)
    kept = kept if isinstance(kept, dict) and isinstance(kept.get("n"), int) and isinstance(kept.get("with"), list) else None
    if kept and names.fresh(key) and (not sizes or isinstance(kept.get("sizes"), list)):
        return kept
    if not kept and time.time() - _failed.get(pid, 0) < RETRY:
        return None
    try:
        got = productive.project_estimates(pid, settings)
    except Exception as exc:
        if kept:  # keep the old answer; ask again in an hour, not on every row
            names.set_many({key: kept}, at=time.time() - productive.ESTIMATES_AGE + RETRY)
        else:
            _failed[pid] = time.time()
        logbook.record("productive", f"Could not tell whether a project's tasks have estimates: {exc}",
                       kind="warning", once_per=3600)
        return kept
    _failed.pop(pid, None)
    names.set_many({key: got})
    return got


def over_estimate(task: Optional[dict], minutes: float) -> Optional[dict]:
    """The overestimate flag when `minutes` more on the task take its time worked over its estimate, else None."""
    est, worked = (task or {}).get("estimate"), (task or {}).get("worked")
    if not minutes or not est or worked is None:
        return None
    total = worked + minutes
    if total <= est:
        return None
    return {"kind": "overestimate", "text": f"+{dur(total - est)} over the estimate ({dur(total)} of {dur(est)})"}


def dur(minutes) -> str:
    """90 -> "1 h 30 min", as the page writes a duration (core.js dur)."""
    m = int(round(float(minutes or 0)))
    return f"{m} min" if m < 60 else f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def for_task(task: Optional[dict], services: Optional[list], date: str, settings: dict,
             names: Optional[productive.Names] = None, minutes: float = 0) -> list:
    """[{kind, text}] for the task on that date. task: as productive.task_details gives it; services: the services
    that take time on that date (bookable_services), or None when they could not be read (then no "nobudget");
    minutes: the time about to be logged to it (0: no "overestimate"). Never raises: a flag that cannot be worked out
    is left out."""
    out: list = []
    if not task:
        return out
    if task.get("closed"):
        out.append({"kind": "closed", "text": "Closed in Productive"})
    if services is not None and not services:
        out.append({"kind": "nobudget", "text": f"No service takes time on {_day(date)}: no budget for it"})
    over = over_estimate(task, minutes)
    if over:
        out.append(over)
    if not task.get("estimate") and config.advanced(settings or {})["no_estimate"]:  # Advanced can switch it off
        share = project_share(task.get("projectId"), settings, names)
        if share and share["n"]:
            others = [i for i in share["with"] if str(i) != str(task.get("taskId"))]
            if len(others) >= ESTIMATE_MIN and len(others) >= ESTIMATE_SHARE * share["n"]:
                out.append({"kind": "noestimate",
                            "text": f"No estimate, while {len(others)} of the project's {share['n']} recent tasks have one"})
    return out
