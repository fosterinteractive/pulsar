"""Ticket flags: what Productive says about a task that someone should fix there, shown while time is reviewed (the
log dialog, and each row of Log the day). Read only: Pulsar never fixes them; the page links each one to the task in
Productive.

* closed: the task is closed;
* nobudget: no service of the task's project takes time on that date, so there is no budget to log it to;
* overestimate: with this entry (and the plan's earlier ones on the task), the time worked on the task, by everyone,
  goes over its estimate;
* nearestimate: with this entry (and the plan's earlier ones on the task) the task reaches NEAR (80%) of its estimate
  but not over it: how much is left, before it is too late to tell the PM (Nick, 2026-11-10, as Timely shows it);
* overbudget, nearbudget: the same for the row's service's time budget, only where Productive lets the person read it;
* noestimate: the task has no estimate, while at least a quarter (and at least two) of the project's 100 most recently
  active tasks have one. Estimates are rare in most projects (on one person's 14 projects, 0 to 35% of recent tasks
  had one), so a flag for every task without one would be on nearly every row. Settings > Advanced can switch it off.

A service's budget can be read only with "View budgets where a user is a member" (budgets_view_restricted): its fields
(budget_total, budget_used, budgeted_time, worked_time) come back empty for a role without it, such as Staff (checked
against Productive, November 2026), and then no budget flag is shown.

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


NEAR = 0.8  # from this share of an estimate or a time budget, with this entry, a row says how much is left


def near_estimate(task: Optional[dict], minutes: float) -> Optional[dict]:
    """The nearestimate flag: with `minutes` more, the task's time worked reaches NEAR of its estimate, not over it."""
    est, worked = (task or {}).get("estimate"), (task or {}).get("worked")
    if not est or worked is None:
        return None
    total = worked + (minutes or 0)
    if total > est or total < NEAR * est:
        return None
    return {"kind": "nearestimate", "text": f"{dur(total)} of the {dur(est)} estimate with this entry, {dur(est - total)} left"}


def service_budget(service: Optional[dict], minutes: float) -> Optional[dict]:
    """The service's time budget with `minutes` more: overbudget past it, nearbudget from NEAR of it; None when it has
    none, or Productive does not let this person read it (productive.budget_time)."""
    b = (service or {}).get("budgetTime") or {}
    budgeted, worked = b.get("budgeted"), b.get("worked")
    if not budgeted or worked is None:
        return None
    total = worked + (minutes or 0)
    if total > budgeted:
        return {"kind": "overbudget",
                "text": f"+{dur(total - budgeted)} over the service's budget ({dur(total)} of {dur(budgeted)})"}
    if total >= NEAR * budgeted:
        return {"kind": "nearbudget",
                "text": f"{dur(total)} of the service's {dur(budgeted)} budget with this entry, {dur(budgeted - total)} left"}
    return None


def dur(minutes) -> str:
    """90 -> "1 h 30 min", as the page writes a duration (core.js dur)."""
    m = int(round(float(minutes or 0)))
    return f"{m} min" if m < 60 else f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def for_task(task: Optional[dict], services: Optional[list], date: str, settings: dict,
             names: Optional[productive.Names] = None, minutes: float = 0, service_id=None,
             service_minutes: float = 0) -> list:
    """[{kind, text}] for the task on that date. task: as productive.task_details gives it; services: the services
    that take time on that date (bookable_services), or None when they could not be read (then no "nobudget");
    minutes: the time about to be logged to it (0: no "overestimate" and no "nearestimate"); service_id and
    service_minutes: the service it goes to and the time about to be logged to that service (its budget). Never raises:
    a flag that cannot be worked out is left out."""
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
    near = near_estimate(task, minutes) if minutes else None
    if near:
        out.append(near)
    if service_id and services:
        svc = next((x for x in services if str(x.get("id")) == str(service_id)), None)
        bud = service_budget(svc, service_minutes or minutes)
        if bud:
            out.append(bud)
    if not task.get("estimate") and config.advanced(settings or {})["no_estimate"]:  # Advanced can switch it off
        share = project_share(task.get("projectId"), settings, names)
        if share and share["n"]:
            others = [i for i in share["with"] if str(i) != str(task.get("taskId"))]
            if len(others) >= ESTIMATE_MIN and len(others) >= ESTIMATE_SHARE * share["n"]:
                out.append({"kind": "noestimate",
                            "text": f"No estimate, while {len(others)} of the project's {share['n']} recent tasks have one"})
    return out
