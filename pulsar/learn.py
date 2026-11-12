"""Tasks for the Likely rows, learned from your own logging: the task you logged while working in the same Claude
Code session in the same repository, else in the same repository, over the last LEARN_DAYS days. A session's votes
from another repository count only for a task this repository is logged to, or when the session has been in this
repository all day (attribution.session_votes). A minute logged while an agent ran in the background teaches the one
whose repository is logged to that task's project (attribution.credits). Older logging counts less: a day's votes
halve every HALF_LIFE days. Then one task found by its words: the task whose name, project and your own notes on it
share most words with the row's work (its session titles, what you typed then, your commits), recent tasks first; it
takes the fifth place, after the first four votes. Local and free; no one is asked anything.

On 30 days of one person's logging (337 rows), each day learned from the days before it only, the right task was
first for 43% of the minutes and among the first five for 57% with the votes alone (39% and 52% when every day counted
the same). With the words in the fifth place, tuned on the first 15 days and checked on the other 15: among the first
five for 59% of those 15 days' minutes instead of 52%, first unchanged (38%); over all 30 days 66% instead of 57%.

A past day's Likely rows take a few seconds each to work out, so they are worked out once, in the background, and kept
in ~/.cache/pulsar/likely-days/<date>.json: the times, the repository and a hash of each session title (never the
titles or anything typed), for the rows in front and the agents' background work. Until they are all there, the suggestions use the days that are. The words are never
kept: the notes come with the Productive history already in memory, the session text is read when a day is shown.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from datetime import datetime, timedelta

from . import attribution, config, logbook

LEARN_DAYS = 60
HALF_LIFE = 7  # days: a day's votes count half as much this many days later (as in dayplan.py)
TEXT_PLACE = 5  # the place of the task found by its words: the votes keep the places before it
FORMAT = 3  # 3: rows at the real time of the work, and the background (bg); 2: a day kept only once it is over
DIR = os.path.join(config.CACHE_DIR, "likely-days")
_warming = threading.Lock()
_state = {"done": 0, "of": 0}


def key(title: str) -> str:
    from .sessions import norm_title
    t = norm_title(title)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16] if len(t) >= 5 else ""


def _path(date: str) -> str:
    return os.path.join(DIR, f"{date}.json")


def _doc(date: str):
    try:
        with open(_path(date), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) and doc.get("format") == FORMAT and isinstance(doc.get("rows"), list) else None
    except (OSError, ValueError):
        return None


def _stored(date: str):
    doc = _doc(date)
    return doc["rows"] if doc else None


def rows_of(proposal: dict) -> list:
    """The rows in front, then the background's (bg; q when it was attended work), of five minutes or more."""
    out = [{"k": [k for k in (key(t) for t in e.get("titles") or []) if k], "p": e.get("project") or "",
            "s": e["s"], "e": e["e"]} for e in proposal.get("entries") or [] if e.get("e", 0) - e.get("s", 0) >= 5]
    return out + [{"k": [k for k in (key(t) for t in e.get("titles") or []) if k], "p": e.get("project") or "",
                   "s": e["s"], "e": e["e"], "bg": 1, **({"q": 1} if e.get("qualifies") else {})}
                  for e in proposal.get("background") or [] if e.get("e", 0) - e.get("s", 0) >= 5]


def _days_before(date: str) -> list:
    d = datetime.strptime(date, "%Y-%m-%d")
    return [(d - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, LEARN_DAYS + 1)]


def warm(date: str, settings: dict) -> None:
    """Work out, in the background, the past days not kept yet. One run at a time; a second call returns at once."""
    if not _warming.acquire(blocking=False):
        return

    def run():
        from . import likely, trackers
        up: list = []

        def answering() -> bool:  # asked once a run, and only when a day without a tracker needs it
            if not up:
                up.append(trackers.answering(settings.get("activitywatch_url") or config.DEFAULTS["activitywatch_url"]))
            return up[0]

        try:
            today = datetime.now(config.tz(settings)).strftime("%Y-%m-%d")  # a day is kept on disk only once it is over
            docs = {d: _doc(d) for d in _days_before(date) if d < today}
            # a day worked out while no tracker answered (ActivityWatch not up yet at login) is worked out again once
            # one does; a day without activity while one answered is kept as it is
            todo = [d for d, doc in docs.items() if doc is None or (doc.get("blind") and answering())]
            _state.update(done=LEARN_DAYS - len(todo), of=LEARN_DAYS)
            os.makedirs(DIR, mode=0o700, exist_ok=True)
            recent = (datetime.now(config.tz(settings)) - timedelta(days=likely.KEEP_DAYS)).strftime("%Y-%m-%d")
            for d in todo:
                got = likely.proposal(d, config.timezone_name(settings), today, False, settings, keep=False)
                if not got.get("error"):
                    blind = got.get("tracker") == "no tracker" and not answering()
                    config.write_atomic(_path(d), json.dumps({"format": FORMAT, "rows": rows_of(got),
                                                              **({"blind": True} if blind else {})}))
                    if d >= recent:  # kept for the day view too, so it opens at once (yesterday, after midnight)
                        likely.keep_day(d, config.timezone_name(settings), got, settings)
                _state["done"] += 1
        except Exception as exc:  # a hint: the suggestions just use fewer days
            logbook.record("learn", f"Pulsar could not learn tasks from past days: {exc}", kind="warning",
                           detail=type(exc).__name__)
        finally:
            _warming.release()

    threading.Thread(target=run, daemon=True, name="pulsar-learn").start()


def main_task(rows: list, a: float, b: float):
    """The task that took most of [a, b) (epoch seconds), when it took at least ten minutes or a third of it."""
    cover: dict = {}
    for r in rows:
        if r.get("start") is None or not r.get("taskId"):
            continue
        o = min(r["start"] + r["minutes"] * 60, b) - max(r["start"], a)
        if o > 0:
            cover[r["taskId"]] = cover.get(r["taskId"], 0) + o
    if not cover:
        return None
    best = max(cover, key=cover.get)
    return best if cover[best] >= min(600, (b - a) / 3) else None


def votes(days: dict, entries: list, t0_of, date: str, half_life: float = HALF_LIFE) -> tuple:
    """({session key: {task: [weight, minutes]}}, {repository: {task: [weight, minutes]}}, {(session key, repository):
    {task: [weight, minutes]}}) from past days' rows and your entries, for the rows of `date`, each logged stretch
    credited as attribution.credits says. The minutes are the real ones; the weight is the minutes halved every
    HALF_LIFE days before `date`, and sets the order. days: {date: rows}; t0_of(date) gives the day's midnight in
    epoch seconds."""
    day0 = datetime.strptime(date, "%Y-%m-%d")
    sess: dict = {}
    repo: dict = {}
    sr: dict = {}

    def add(table: dict, at, task: str, w: float, m: float) -> None:
        got = table.setdefault(at, {}).setdefault(task, [0.0, 0])
        got[0] += w
        got[1] += m

    for d, keys, at, task, m in _credited(days, entries, t0_of)[0]:
        fade = 0.5 ** (max((day0 - datetime.strptime(d, "%Y-%m-%d")).days, 0) / half_life)
        for k in keys:
            add(sess, k, task, m * fade, m)
            add(sr, (k, at), task, m * fade, m)
        add(repo, at, task, m * fade, m)
    return sess, repo, sr


def _credited(days: dict, entries: list, t0_of) -> tuple:
    """(attribution.credits of the days, {task: project}) with your entries, as votes reads them."""
    by_day: dict = {}
    project: dict = {}
    for r in entries:
        by_day.setdefault(r.get("date"), []).append(r)
        if r.get("taskId") and r.get("project"):
            project[r["taskId"]] = r["project"]

    def task_of(d, s, e):
        t0 = t0_of(d)
        return main_task(by_day.get(d, []), t0 + s * 60, t0 + e * 60)
    return attribution.credits(days, task_of, project.get), project


def suggest(row: dict, sess: dict, repo: dict, names: dict, limit: int = 5, text: dict | None = None,
            sr: dict | None = None) -> list:
    """The tasks for one row, the session's first (its votes in this repository, else the ones that may carry over:
    attribution.session_votes), then the repository's, each in the order of its weight (recent logging first); each
    with why, in words, with the real minutes. text ({task: (score, words)}, text_match): the best task by words not
    among the first TEXT_PLACE - 1 takes place TEXT_PLACE; more of them fill a shorter list."""
    out: dict = {}
    at = attribution.repo_key(row["p"])
    here: dict = {}
    for k in row["k"]:
        for t, (w, m) in ((sr or {}).get((k, at)) or {}).items():
            got = here.setdefault(t, [0.0, 0])
            got[0] += w
            got[1] += m
    if not (here and max(w for w, _ in here.values()) >= attribution.SR_MIN):
        wide: dict = {}
        for k in row["k"]:
            for t, (w, m) in sess.get(k, {}).items():
                got = wide.setdefault(t, [0.0, 0])
                got[0] += w
                got[1] += m
        project_of = lambda t: (names.get(t) or {}).get("project")  # noqa: E731
        weights = {t: w for t, (w, _) in (repo.get(at) or {}).items()}
        here = {}
        single = row.get("single") and attribution.one_project({t: w for t, (w, _) in wide.items()}, project_of)
        for t, v in wide.items():
            f = attribution.fits(weights, project_of, t)
            if f is True or (single and f is None):
                here[t] = v
    total = sum(w for w, _ in here.values()) or 1
    share = {t: w / total for t, (w, _) in here.items()}  # how sure the session's vote is (first_offers)
    for t, (w, m) in sorted(here.items(), key=lambda kv: -kv[1][0]):
        out.setdefault(t, f"logged while in this Claude session: {_dur(m)}")
    for t, (w, m) in sorted(repo.get(at, {}).items(), key=lambda kv: -kv[1][0]):
        out.setdefault(t, f"logged while in {row['p']}: {_dur(m)}")
    order = [t for t in out if t in names]
    if text:
        found = [t for t, _ in sorted(text.items(), key=lambda kv: -kv[1][0]) if t in names]
        head = order[:min(limit, TEXT_PLACE) - 1]
        first = [t for t in found if t not in head][:1]
        for t in found:  # the place it took by words says so; a voted task further on keeps its votes' why
            if t in first or t not in out:
                out[t] = f"your notes on it share words with this work: {', '.join(text[t][1])}"
        order = head + first + order[len(head):] + found
    order = list(dict.fromkeys(order))[:limit]
    return [{"taskId": t, "title": names.get(t, {}).get("task") or "", "project": names.get(t, {}).get("project") or "",
             "why": out[t], **({"share": round(share[t], 3)} if t in share and out[t].startswith("logged while in this")
                               else {})} for t in order]


def _dur(m: float) -> str:
    m = int(round(m))
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


# ---- the task found by its words ----------------------------------------------------------------------------------

_WORD = re.compile(r"[^\W\d_][^\W_]+")
_STOP = frozenset("""the and for with to of in on at is it this that be are was as by an or from not but have has can will
so if we you me my our your all any do does did no yes ok now then there here what which when how why also just only into
out up about more one two new use used would should could need needs make made see get got let its it's i'll i'm don't
doesn't isn't can't won't there's that's let's they them their these those than too very via per each other some such
same been being were had his her him she he us am off over under again still back down well much many most own both
і й та що це як на в у з до не так є а але по за від для щоб його її вони він вона ми ти я мені тут там все вже ще чи
або то би же який яка яке які цей ця ці той коли де чому бо треба можна""".split())


def _stem(w: str) -> str:
    """A word without its ending, so that upgrade, upgrades, upgraded and upgrading are one word (a final e goes too:
    "upgrades" lost its "es" and "upgrade" kept its e, and the two never matched)."""
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w[:-1] if len(w) > 4 and w.endswith("e") else w


def words(text: str, skip: frozenset = frozenset()) -> list:
    """[(stem, word)]: the words of a text, lower case, starting with a letter, two letters or more, without common
    English and Ukrainian words or `skip` (the person's own name, which is in many task names and says nothing)."""
    return [(_stem(w), w) for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) <= 30
            and w not in skip]


def note_index(entries: list, skip: frozenset = frozenset()) -> dict:
    """Each task's words: its name and project, and every note you wrote on it, as a TF-IDF vector of unit length;
    and the day each task was last logged. Built in memory from the entries, never kept."""
    names: dict = {}
    notes: dict = {}
    last: dict = {}
    from .askclaude import _plain
    for r in sorted((r for r in entries if r.get("taskId")), key=lambda r: r.get("date") or ""):
        t = r["taskId"]
        names[t] = f"{r.get('task') or ''} {r.get('project') or ''}"
        last[t] = r.get("date") or ""
        if r.get("note"):
            notes.setdefault(t, []).append(_plain(r["note"]))
    tf: dict = {}
    for t, name in names.items():
        c: dict = {}
        for w, _ in words(name + " " + " ".join(notes.get(t, [])), skip):
            c[w] = c.get(w, 0) + 1
        tf[t] = c
    df: dict = {}
    for c in tf.values():
        for w in c:
            df[w] = df.get(w, 0) + 1
    n = len(tf) or 1
    idf = {w: math.log((n + 1) / (f + 0.5)) for w, f in df.items()}
    vec = {}
    for t, c in tf.items():
        v = {w: (1 + math.log(f)) * idf[w] for w, f in c.items()}
        size = math.sqrt(sum(x * x for x in v.values())) or 1
        vec[t] = {w: x / size for w, x in v.items()}
    return {"idf": idf, "vec": vec, "last": last}


def text_match(index: dict, text: str, date: str, skip: frozenset = frozenset(),
               half_life: float = HALF_LIFE) -> dict:
    """{task: (score, [up to three shared words])}: how much the work's words and each task's words agree (cosine),
    halved every HALF_LIFE days since the task was last logged before or on `date`."""
    found = words(text, skip)
    idf = index.get("idf") or {}
    count: dict = {}
    shown: dict = {}
    for w, word in found:
        if w in idf:
            count[w] = count.get(w, 0) + 1
            shown.setdefault(w, word)
    q = {w: (1 + math.log(f)) * idf[w] for w, f in count.items()}
    size = math.sqrt(sum(x * x for x in q.values())) or 1
    day0 = datetime.strptime(date, "%Y-%m-%d")
    out = {}
    for t, v in (index.get("vec") or {}).items():
        parts = {w: x * v[w] for w, x in q.items() if w in v}
        if not parts:
            continue
        try:
            age = max((day0 - datetime.strptime(index["last"][t], "%Y-%m-%d")).days, 0)
        except (KeyError, ValueError):
            age = LEARN_DAYS
        score = sum(parts.values()) / size * 0.5 ** (age / half_life)
        out[t] = (score, [shown[w] for w, _ in sorted(parts.items(), key=lambda kv: -kv[1])[:3]])
    return out


def work_steps(entry: dict, t0: float, projects_dir: str | None = None) -> list:
    """The row's steps (askclaude.steps: at most 15, what you typed and Claude's last words), read from the session
    files, never kept."""
    from . import askclaude, sessions
    titles = list(entry.get("titles") or [])
    return askclaude.steps(t0, titles, entry["s"], entry["e"], projects_dir or sessions.PROJECTS_DIR) if titles else []


# Ukrainian words of the work that task names say in English: a session titled "Оновлення бази даних Drupal 11" is the
# "Drupal 11 – DB Upgrade" task (2026-11-10: it matched nothing, and the checkout's branch named another task)
EN_WORDS = ((re.compile(r"\bбаз[аиуіоу]\w*\s+даних\b|\bбд\b", re.I), "database db"),
            (re.compile(r"\bоновлен\w*|\bоновит\w*|\bапдейт\w*", re.I), "update upgrade"))


def english(text: str) -> str:
    """`text` with the English words of the Ukrainian ones EN_WORDS knows added after it."""
    extra = [en for rx, en in EN_WORDS if rx.search(text or "")]
    return f"{text} {' '.join(extra)}" if extra else text


def work_text(entry: dict, t0: float, projects_dir: str | None = None, steps: list | None = None,
              replies: bool = False, branch: bool = True) -> str:
    """The words of a Likely row's work: its sessions' titles, what you typed in them then (400 characters a message),
    with `replies` Claude's last words of each step too, and your commit subjects (commits.py)."""
    steps = work_steps(entry, t0, projects_dir) if steps is None else steps
    said = [x.get("typed") or "" for x in steps] + ([x.get("reply") or "" for x in steps] if replies else [])
    # the git branch names the work too: "listing-labels-cta", "feature/content-our-office-is-open"; `branch` False
    # leaves it out (the checkout's branch can be another task's while a session works on something else)
    names = [re.sub(r"[/_-]+", " ", b) for b in entry.get("branches") or []] if branch else []
    # and the titles of the repository's pull requests and issues open during the work (sessions.github_of)
    return english(" ".join(list(entry.get("titles") or []) + said + list(entry.get("commits") or []) + names
                            + list(entry.get("github") or [])))


# ---- tasks you never logged to --------------------------------------------------------------------------------------

TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,5}\b")
NUMBER = re.compile(r"(?<![\w#&/])#(\d{2,6})\b")  # "#56", "PR #728"; not "&#39;" or a URL's "#anchor"
NEW_DAYS = 30     # a task created this many days back, in a project you log to, may be today's
NEW_MIN = 0.5     # the words a new task must share with the work (cosine); a ticket key or #number named is enough
                  # (on the replay, below 0.5 the first one found was the task logged then for 1% of the minutes)
NEW_LIMIT = 2     # new tasks offered per row


def ticket_keys(text: str) -> set:
    """The ticket keys ("UTFLM-271") and numbers ("#728") a text names."""
    return {k.upper() for k in TICKET.findall(text or "")} | {"#" + n for n in NUMBER.findall(text or "")}


def new_task_pool(tasks: list, history: list, date: str, person: str, days: int = NEW_DAYS) -> list:
    """Of `tasks` (productive.recent_tasks), the ones with no entry of yours in `history` that may be `date`'s work:
    not closed before that day and not created after it, and assigned to you, or created in the last `days` days in a
    project you logged to in the last 30 days."""
    day0 = datetime.strptime(date, "%Y-%m-%d")
    lo = (day0 - timedelta(days=days)).strftime("%Y-%m-%d")
    lo30 = (day0 - timedelta(days=30)).strftime("%Y-%m-%d")
    known = {str(r["taskId"]) for r in history if r.get("taskId")}
    projects = {r.get("project") for r in history if lo30 <= str(r.get("date") or "") < date and r.get("project")}
    out = []
    for t in tasks:
        created, closed = str(t.get("created") or "")[:10], str(t.get("closed") or "")[:10]
        if str(t.get("taskId")) in known or not created or created > date or (closed and closed < date):
            continue
        if (person and str(t.get("assignee")) == str(person)) or (t.get("project") in projects and created >= lo):
            out.append(t)
    return out


def new_task_index(pool: list, skip: frozenset = frozenset()) -> dict:
    """The pool's titles and projects as TF-IDF vectors of unit length, and each task's ticket keys."""
    tf = {}
    for t in pool:
        c: dict = {}
        for w, _ in words(f"{t.get('title') or ''} {t.get('project') or ''}", skip):
            c[w] = c.get(w, 0) + 1
        tf[str(t["taskId"])] = c
    df: dict = {}
    for c in tf.values():
        for w in c:
            df[w] = df.get(w, 0) + 1
    n = len(tf) or 1
    idf = {w: math.log((n + 1) / (f + 0.5)) for w, f in df.items()}
    vec = {}
    for t, c in tf.items():
        v = {w: (1 + math.log(f)) * idf[w] for w, f in c.items()}
        size = math.sqrt(sum(x * x for x in v.values())) or 1
        vec[t] = {w: x / size for w, x in v.items()}
    return {"idf": idf, "vec": vec, "keys": {str(t["taskId"]): ticket_keys(t.get("title") or "") for t in pool},
            "task": {str(t["taskId"]): t for t in pool}}


def _scores(index: dict, text: str, skip: frozenset = frozenset()) -> list:
    """[(taskId, cosine, ticket keys named, the words shared as written, most telling first)] of an index
    (new_task_index) against a text."""
    idf = index.get("idf") or {}
    count: dict = {}
    shown: dict = {}
    for w, word in words(text, skip):
        if w in idf:
            count[w] = count.get(w, 0) + 1
            shown.setdefault(w, word)
    q = {w: (1 + math.log(f)) * idf[w] for w, f in count.items()}
    size = math.sqrt(sum(x * x for x in q.values())) or 1
    named = ticket_keys(text)
    out = []
    for t, v in (index.get("vec") or {}).items():
        parts = {w: x * v[w] for w, x in q.items() if w in v}
        out.append((t, sum(parts.values()) / size, (index.get("keys") or {}).get(t, set()) & named,
                    [shown[w] for w, _ in sorted(parts.items(), key=lambda kv: -kv[1])]))
    return out


def new_tasks(index: dict, text: str, skip: frozenset = frozenset(), limit: int = NEW_LIMIT) -> list:
    """The pool's tasks the work names, best first: [{taskId, title, project, why, score, key}]. A ticket key or
    #number of the task named in the work comes first; else the words they share (cosine, at least NEW_MIN)."""
    found = []
    for t, score, keys, shared in _scores(index, text, skip):
        if not keys and score < NEW_MIN:
            continue
        task = index["task"][t]
        why = f"the work names {sorted(keys)[0]}" if keys else "named in this work: " + ", ".join(shared[:3])
        found.append({"taskId": t, "title": task.get("title") or "", "project": task.get("project") or "",
                      "why": why + " (no entry of yours yet)", "score": score + (1 if keys else 0), "key": bool(keys),
                      "new": True})
    return sorted(found, key=lambda x: -x["score"])[:limit]


NEW_PLACE = 4      # the fifth place, after the first four learned tasks (a ticket key named: first, unless the first
                   # is the same session's vote); at the second place they pushed out tasks you had logged before
NEW_REPLIES = True  # Claude's last words of each step count as evidence for a new task (without: fewer found)


def with_new(learned: list, found: list) -> list:
    """The learned list with the new tasks found in the work: never above the same session's vote."""
    ids = {f["taskId"] for f in found}
    out = [x for x in learned if x["taskId"] not in ids]
    strong = bool(out) and out[0]["why"].startswith("logged while in this Claude session")
    place = None
    for f in found:
        if place is None:
            place = 0 if f.get("key") and not strong else min(NEW_PLACE, len(out))
        out.insert(min(place, len(out)), f)
        place += 1
    return out


# ---- what you are doing, first --------------------------------------------------------------------------------------

WORK_MIN = 0.3   # the words (cosine) a task of the work's own project must share with the work (its sessions' titles,
                 # what you typed, your commits) to go first, above where you usually log. 2026-11-09: "D11 db
                 # upgrade" on a site's repository, and the first offer was a task once logged there
WORK_TIE = 0.9   # a task this close to the best goes first with it (one name, a PM task and a Dev task)
WORK_LIMIT = 2
WORK_WORDS = 3   # words in common that put it above the same session's own vote; with fewer it comes just after
                 # it. Replay of 19 days (13 Oct to 6 Nov 2026): with 1 or 2 words the session's vote was right 34 times
                 # and the work's task 23; with 3 or more the work's task 24 times and what was first before 20


def work_projects(at: str, repo: dict, project_of, tasks: list) -> set:
    """The Productive projects of the work's repository: those with FIT_SHARE of its votes, once it has FIT_MIN of
    them; with less history, the projects of tasks whose names name the repository ("… - acme - Maintenance")."""
    votes = {t: w for t, (w, _) in (repo.get(at) or {}).items()}
    total = sum(votes.values())
    if total >= attribution.FIT_MIN:
        by: dict = {}
        for t, w in votes.items():
            p = project_of(t)
            if p:
                by[p] = by.get(p, 0.0) + w
        return {p for p, w in by.items() if w / total >= attribution.FIT_SHARE}
    name = (at or "").lower()
    if len(name) < 3:
        return set()
    return {t["project"] for t in tasks if t.get("project") and name in {w for _, w in words(t.get("title") or "")}}


def work_match(text: str, projects: set, tasks: list, skip: frozenset = frozenset()) -> list:
    """The tasks of the work's projects whose names share most words with the work, best first, the ones almost as
    good with it (WORK_TIE), yours before the others among them: [{taskId, title, project, why, score, work}]. Only
    task names count, not the project's name, which all of them have. tasks: [{taskId, title, project, new, mine}]
    (new: no entry of yours yet; mine: you logged to it or it is assigned to you)."""
    cands = {str(t["taskId"]): t for t in tasks if t.get("taskId") and t.get("project") in projects}
    if not cands or not (text or "").strip():
        return []
    index = new_task_index([{"taskId": t, "title": x.get("title") or ""} for t, x in cands.items()], skip)
    hits = sorted((h for h in _scores(index, text, skip) if h[1] >= WORK_MIN), key=lambda h: -h[1])
    hits = [h for h in hits if h[1] >= hits[0][1] * WORK_TIE] if hits else []
    out = []
    for t, score, _, shared in sorted(hits, key=lambda h: (not cands[h[0]].get("mine"), -h[1]))[:WORK_LIMIT]:
        x = cands[t]
        out.append({"taskId": t, "title": x.get("title") or "", "project": x.get("project") or "", "score": score,
                    "why": "what you typed names it: " + ", ".join(shared[:3])
                           + (" (no entry of yours yet)" if x.get("new") else ""), "work": True, "words": len(shared),
                    **({"neverLogged": True} if x.get("new") else {})})
    return out


def with_work(learned: list, work: list) -> list:
    """What you are doing first (work_match), then where you usually log; just after the same session's own vote
    when it shares fewer than WORK_WORDS words with the work. The ones placed first say so ("first")."""
    ids = {w["taskId"] for w in work}
    rest = [x for x in learned if x["taskId"] not in ids]
    # the session's own vote first, unless it is the work's task too (then that one is first either way)
    second = bool(work) and work[0].get("words", 0) < WORK_WORDS and bool(learned) \
        and learned[0]["taskId"] not in ids and learned[0]["why"].startswith("logged while in this Claude session")
    work = [{**w, "first": not second} for w in work]
    return rest[:1] + work + rest[1:] if second else work + rest


SURE_SHARE = 0.6  # a session's vote with this share of its votes is sure: nothing goes above it
FRESH_DAYS = 3    # a task assigned to you this many days before the work at most is offered second


def assigned_named(tasks: list, text: str, skip: frozenset = frozenset()) -> dict | None:
    """Of the tasks assigned to you lately (annotate: in the work's projects, no entry of yours yet, FRESH_DAYS old at
    most), the one whose name shares most words with the work, the words of its project's name left out (every task of
    the project has them); the newest on a tie. None when none shares a word.

    A new assignment is usually the new work: on the replay of 43 days (2026-11-10), as the plan's pick over the task
    you logged last in the project it was right on 52% of its minutes, where that task was right on 11%. Without a word
    in common the newest assignment was right less often than the last day's task, so it stays an offer (fresh)."""
    have = {w for w, _ in words(text, skip)}
    best = None
    for t in tasks:
        own = {w for w, _ in words(t.get("project") or "")}
        shown: dict = {}
        for w, word in words(t.get("title") or "", skip):
            if w in have and w not in own:
                shown.setdefault(w, word)
        rank = (len(shown), str(t.get("created") or ""), str(t.get("taskId")))
        if shown and (best is None or rank > best[0]):
            best = (rank, t, list(shown.values()))
    if not best:
        return None
    t = best[1]
    return {"taskId": str(t["taskId"]), "title": t.get("title") or "", "project": t.get("project") or "",
            "why": f"assigned to you on {str(t.get('created'))[:10]}, the work names it: {', '.join(best[2][:3])}",
            "assigned": True, "named": True}


def first_offers(learned: list, last: dict | None = None, fresh: dict | None = None,
                 named: dict | None = None, previous: dict | None = None) -> list:
    """The offers with more: the task you logged last in the repository's project first, unless the first offer is
    sure (a session's vote of SURE_SHARE, or the work naming a task you logged by WORK_WORDS words); the newest task
    assigned to you in that project second (half of the work on tasks new to you was on one assigned to you within
    FRESH_DAYS days); and the task you logged there before the last one third. Replay of 43 days: the right task among
    the first three on 61% of rows with none of them, 65% with the first two, 67.1% with all three (74.2% of their
    minutes instead of 72.5% with two): work goes back to the ticket before the last one often enough.

    named: a task assigned to you that the work names (assigned_named) goes above the last day's task, as the plan
    picks it, or second after a sure offer (replay, 2026-11-10: the first offer right on 50.3% of rows instead of
    48.8%, 59.5% of the minutes instead of 57.7%; the first three unchanged).
"""
    def put(lst: list, x: dict | None, at: int) -> list:
        if not x or x["taskId"] in [y["taskId"] for y in lst[:at + 1]]:
            return lst
        rest = [y for y in lst if y["taskId"] != x["taskId"]]
        return rest[:at] + [x] + rest[at:]
    top = learned[0] if learned else {}
    sure = top.get("share", 0) >= SURE_SHARE \
        or (bool(top.get("work")) and top.get("words", 0) >= WORK_WORDS and not top.get("neverLogged"))
    if sure:
        out = put(learned, named, 1)
    else:
        out = put(put(learned, last, 0), named, 0)
    return put(put(out, fresh, 1), previous, 2)


def annotate(proposal: dict, date: str, settings: dict, entries_before: list, entries_today: list, tz,
             pool: list | None = None, warm_days: bool = True) -> None:
    """Add "learned" (tasks, why) to each row of a day's proposal, and "learning" ({done, of}) while past days are
    still being worked out. `pool` (productive.recent_tasks): tasks you never logged to, offered when the work names
    them. warm_days=False (the self-check, selfcheck.py): from the past days kept already, none worked out for it.
    Mutates the proposal; never raises."""
    try:
        if warm_days:
            warm(date, settings)
        days = {d: rows for d in _days_before(date) for rows in [_stored(d)] if rows}
        days[date] = rows_of(proposal)  # rows already logged today teach the ones after them
        t0_of = lambda d: datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=tz).timestamp()  # noqa: E731
        from . import daymarks
        not_example = daymarks.no_learn()  # marked not an example (daymarks.py); server.history_rows leaves them out too
        entries = [r for r in entries_before + entries_today
                   if r.get("taskId") and str(r.get("id") or "") not in not_example]
        half_life = config.advanced(settings)["memory"]  # Settings > Time and activity > Advanced
        sess, repo, sr = votes(days, entries, t0_of, date, half_life)
        names = {r["taskId"]: r for r in sorted(entries, key=lambda r: r.get("date") or "")}
        skip = frozenset(w for _, w in words(settings.get("person_name") or ""))
        index = note_index(entries, skip)
        unseen = new_task_pool(pool, entries, date, str(settings.get("person_id") or "")) if pool else []
        fresh = new_task_index(unseen, skip) if pool else None
        closed = {str(t.get("taskId")) for t in pool or [] if str(t.get("closed") or "")[:10]
                  and str(t.get("closed"))[:10] < date}
        me = str(settings.get("person_id") or "")
        known = [{"taskId": str(t), "title": n.get("task") or "", "project": n.get("project") or "", "mine": True}
                 for t, n in names.items()]
        work_tasks = [t for t in known + [{"taskId": str(t["taskId"]), "title": t.get("title") or "",
                                            "project": t.get("project") or "", "new": True,
                                            "mine": bool(me) and str(t.get("assignee") or "") == me} for t in unseen]
                      if t["taskId"] not in closed]
        project_of = lambda t: (names.get(t) or {}).get("project")  # noqa: E731
        t0 = t0_of(date)
        front = [e for e in proposal.get("entries") or [] if e.get("e", 0) - e.get("s", 0) >= 5]
        back = [e for e in proposal.get("background") or [] if e.get("e", 0) - e.get("s", 0) >= 5]
        stored = days[date]
        # a Productive task page in front during the work ("<task> - Tasks - <project> - Productive"): that task, as
        # one the session opened (sessions.pages_of), when its name and project are a task's you know of
        by_page = {(str(t.get("title") or "").strip().lower(), str(t.get("project") or "").strip().lower()): t["taskId"]
                   for t in work_tasks}
        for e in (proposal.get("entries") or []) + (proposal.get("background") or []):
            # only a page of the repository's own project: one opened on the way to other work names that work
            ok = work_projects(attribution.repo_key(e.get("project")), repo, project_of, work_tasks)
            ids = [by_page[(a.lower(), b.lower())] for a, b in e.get("taskPages") or []
                   if (a.lower(), b.lower()) in by_page and b in ok]
            if ids:
                e["seenTasks"] = list(dict.fromkeys(ids + list(e.get("seenTasks") or [])))[:4]
                e["tasks"] = list(dict.fromkeys(list(e.get("repoLinks") or []) + e["seenTasks"]))[:4]
        lastday = attribution.last_day_tasks(entries, date)
        latest = attribution.latest_tasks(entries, date)
        day0 = datetime.strptime(date, "%Y-%m-%d")

        def offers(places: set, said: str = "") -> tuple:
            """(the task you logged last in these projects, the newest task assigned to you in them, the one of those
            the work's words name, the task you logged there before the last one): first_offers."""
            got = attribution.last_of(lastday, places)
            last = None
            if got:
                n = names.get(got[1]) or {}
                last = {"taskId": str(got[1]), "title": n.get("task") or "", "project": n.get("project") or got[4],
                        "why": f"the task you logged last in {got[4]} ({got[0]})"}
            before = attribution.latest_in(latest, places, {str(got[1])} if got else set())
            previous = None
            if before:
                n = names.get(before[1]) or {}
                previous = {"taskId": str(before[1]), "title": n.get("task") or "", "project": n.get("project") or before[2],
                            "why": f"you logged it in {before[2]} on {before[0]}"}
            mine = [t for t in unseen if me and str(t.get("assignee") or "") == me and t.get("project") in places
                    and str(t.get("taskId")) not in closed
                    and 0 <= (day0 - datetime.strptime(str(t.get("created"))[:10], "%Y-%m-%d")).days <= FRESH_DAYS]
            new = max(mine, key=lambda t: str(t.get("created"))) if mine else None
            fresh = {"taskId": str(new["taskId"]), "title": new.get("title") or "", "project": new.get("project") or "",
                     "why": f"assigned to you on {str(new.get('created'))[:10]}, no entry of yours yet",
                     "assigned": True} if new else None
            return last, fresh, assigned_named(mine, said, skip), previous

        named: dict = {}  # (session keys, repository) -> the work's task named earlier today (work_match)
        named_at: dict = {}  # the same, with the start of each row that named it
        pairs = list(zip(front, stored)) + list(zip(back, stored[len(front):]))
        for e, row in sorted(pairs, key=lambda p: p[0].get("s", 0)):
            row = {**row, "single": bool(e.get("single"))}
            found: list = []
            work: list = []
            places: set = set()
            said = ""
            try:
                steps = work_steps(e, t0)
                said = work_text(e, t0, steps=steps)
                text = text_match(index, said, date, skip, half_life)
                if fresh and fresh["vec"]:
                    found = new_tasks(fresh, work_text(e, t0, steps=steps, replies=NEW_REPLIES), skip)
                at = attribution.repo_key(row.get("p"))
                # what the session itself names first; the checkout's branch only when that names no task (2026-11-10:
                # "Оновлення бази даних" in a checkout left on another task's branch went to that task)
                places = work_projects(at, repo, project_of, work_tasks)
                work = work_match(work_text(e, t0, steps=steps, branch=False), places, work_tasks, skip) \
                    or work_match(said, places, work_tasks, skip)
                # a later stretch of the same session in the same repository that names nothing ("go on", "done")
                # goes on with the task the work named earlier that day
                here = (tuple(row.get("k") or ()), at)
                if work:
                    named[here] = work
                    named_at.setdefault(here, []).append((e["s"], work))
                elif row.get("k") and here in named:
                    work = [{**w, "carried": True} for w in named[here]]
            except (OSError, ValueError) as exc:  # the votes alone, as before
                logbook.record("learn", f"Pulsar could not match what a session says to tasks: {exc}", kind="warning",
                               detail=type(exc).__name__)
                text = {}
            e["learned"] = first_offers(with_work(with_new(suggest(row, sess, repo, names, text=text, sr=sr), found),
                                                  work), *offers(places, said))
        # rows under five minutes are not read for words (work by turns makes many): they go on with the task their
        # session's work named before them in the same repository; nothing else is added to them
        for e in (proposal.get("entries") or []) + (proposal.get("background") or []):
            if e.get("e", 0) - e.get("s", 0) >= 5:
                continue
            here = (tuple(k for k in (key(t) for t in e.get("titles") or []) if k), attribution.repo_key(e.get("project")))
            got = [w for s0, w in named_at.get(here, []) if s0 <= e["s"]] if here[0] else []
            if got:
                e["learned"] = with_work(e.get("learned") or [], [{**w, "carried": True} for w in got[-1]])
        proposal["learning"] = dict(_state) if _state["of"] and _state["done"] < _state["of"] else None
    except Exception as exc:
        logbook.record("learn", f"Pulsar could not suggest tasks for the Likely rows: {exc}", kind="warning",
                       detail=type(exc).__name__)
