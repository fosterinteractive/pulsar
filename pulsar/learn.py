"""Tasks for the Likely rows, learned from your own logging: the task you logged while working in the same Claude
Code session, else in the same repository, over the last LEARN_DAYS days. Older logging counts less: a day's votes
halve every HALF_LIFE days. Then one task found by its words: the task whose name, project and your own notes on it
share most words with the row's work (its session titles, what you typed then, your commits), recent tasks first; it
takes the fifth place, after the first four votes. Local and free; no one is asked anything.

On 30 days of one person's logging (337 rows), each day learned from the days before it only, the right task was
first for 43% of the minutes and among the first five for 57% with the votes alone (39% and 52% when every day counted
the same). With the words in the fifth place, tuned on the first 15 days and checked on the other 15: among the first
five for 59% of those 15 days' minutes instead of 52%, first unchanged (38%); over all 30 days 66% instead of 57%.

A past day's Likely rows take a few seconds each to work out, so they are worked out once, in the background, and kept
in ~/.cache/pulsar/likely-days/<date>.json: the times, the repository and a hash of each session title (never the
titles or anything typed). Until they are all there, the suggestions use the days that are. The words are never
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

from . import config, logbook

LEARN_DAYS = 60
HALF_LIFE = 7  # days: a day's votes count half as much this many days later (as in dayplan.py)
TEXT_PLACE = 5  # the place of the task found by its words: the votes keep the places before it
FORMAT = 2  # 2: a day is kept only once it is over (a format-1 file may hold a day cut off at the hour it was seen)
DIR = os.path.join(config.CACHE_DIR, "likely-days")
_warming = threading.Lock()
_state = {"done": 0, "of": 0}


def key(title: str) -> str:
    from .sessions import norm_title
    t = norm_title(title)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16] if len(t) >= 5 else ""


def _path(date: str) -> str:
    return os.path.join(DIR, f"{date}.json")


def _stored(date: str):
    try:
        with open(_path(date), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc["rows"] if doc.get("format") == FORMAT and isinstance(doc.get("rows"), list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def rows_of(proposal: dict) -> list:
    return [{"k": [k for k in (key(t) for t in e.get("titles") or []) if k], "p": e.get("project") or "",
             "s": e["s"], "e": e["e"]} for e in proposal.get("entries") or [] if e.get("e", 0) - e.get("s", 0) >= 5]


def _days_before(date: str) -> list:
    d = datetime.strptime(date, "%Y-%m-%d")
    return [(d - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, LEARN_DAYS + 1)]


def warm(date: str, settings: dict) -> None:
    """Work out, in the background, the past days not kept yet. One run at a time; a second call returns at once."""
    if not _warming.acquire(blocking=False):
        return

    def run():
        from . import likely
        try:
            today = datetime.now(config.tz(settings)).strftime("%Y-%m-%d")  # a day is kept on disk only once it is over
            todo = [d for d in _days_before(date) if d < today and _stored(d) is None]
            _state.update(done=LEARN_DAYS - len(todo), of=LEARN_DAYS)
            os.makedirs(DIR, mode=0o700, exist_ok=True)
            for d in todo:
                got = likely.proposal(d, config.timezone_name(settings), today, False, settings, keep=False)
                if not got.get("error"):
                    config.write_atomic(_path(d), json.dumps({"format": FORMAT, "rows": rows_of(got)}))
                _state["done"] += 1
        except Exception as exc:  # a hint: the suggestions just use fewer days
            logbook.record("learn", f"Learning tasks from past days: {type(exc).__name__}: {exc}", kind="warning")
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


def votes(days: dict, entries: list, t0_of, date: str) -> tuple:
    """({session key: {task: [weight, minutes]}}, {repository: {task: [weight, minutes]}}) from past days' rows and
    your entries, for the rows of `date`. The minutes are the real ones; the weight is the minutes halved every
    HALF_LIFE days before `date`, and sets the order. days: {date: rows}; t0_of(date) gives the day's midnight in
    epoch seconds."""
    by_day: dict = {}
    for r in entries:
        by_day.setdefault(r.get("date"), []).append(r)
    day0 = datetime.strptime(date, "%Y-%m-%d")
    sess: dict = {}
    repo: dict = {}

    def add(table: dict, at: str, task: str, w: float, m: float) -> None:
        got = table.setdefault(at, {}).setdefault(task, [0.0, 0])
        got[0] += w
        got[1] += m

    for d, rows in days.items():
        t0 = t0_of(d)
        fade = 0.5 ** (max((day0 - datetime.strptime(d, "%Y-%m-%d")).days, 0) / HALF_LIFE)
        for row in rows:
            task = main_task(by_day.get(d, []), t0 + row["s"] * 60, t0 + row["e"] * 60)
            if not task:
                continue
            m = row["e"] - row["s"]
            for k in row["k"]:
                add(sess, k, task, m * fade, m)
            add(repo, row["p"], task, m * fade, m)
    return sess, repo


def suggest(row: dict, sess: dict, repo: dict, names: dict, limit: int = 5, text: dict | None = None) -> list:
    """The tasks for one row, the session's first, then the repository's, each in the order of its weight (recent
    logging first); each with why, in words, with the real minutes. text ({task: (score, words)}, text_match): the
    best task by words not among the first TEXT_PLACE - 1 takes place TEXT_PLACE; more of them fill a shorter list."""
    out: dict = {}
    s_votes: dict = {}
    for k in row["k"]:
        for t, (w, m) in sess.get(k, {}).items():
            got = s_votes.setdefault(t, [0.0, 0])
            got[0] += w
            got[1] += m
    for t, (w, m) in sorted(s_votes.items(), key=lambda kv: -kv[1][0]):
        out.setdefault(t, f"logged while in this Claude session: {_dur(m)}")
    for t, (w, m) in sorted(repo.get(row["p"], {}).items(), key=lambda kv: -kv[1][0]):
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
             "why": out[t]} for t in order]


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
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


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


def text_match(index: dict, text: str, date: str, skip: frozenset = frozenset()) -> dict:
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
        score = sum(parts.values()) / size * 0.5 ** (age / HALF_LIFE)
        out[t] = (score, [shown[w] for w, _ in sorted(parts.items(), key=lambda kv: -kv[1])[:3]])
    return out


def work_text(entry: dict, t0: float, projects_dir: str | None = None) -> str:
    """The words of a Likely row's work: its sessions' titles, what you typed in them then (askclaude.steps: at most
    15 messages, 400 characters each) and your commit subjects (commits.py). Read from the session files, never kept."""
    from . import askclaude, sessions
    titles = list(entry.get("titles") or [])
    steps = askclaude.steps(t0, titles, entry["s"], entry["e"], projects_dir or sessions.PROJECTS_DIR) if titles else []
    return " ".join(titles + [x.get("typed") or "" for x in steps] + list(entry.get("commits") or []))


def annotate(proposal: dict, date: str, settings: dict, entries_before: list, entries_today: list, tz) -> None:
    """Add "learned" (tasks, why) to each row of a day's proposal, and "learning" ({done, of}) while past days are
    still being worked out. Mutates the proposal; never raises."""
    try:
        warm(date, settings)
        days = {d: rows for d in _days_before(date) for rows in [_stored(d)] if rows}
        days[date] = rows_of(proposal)  # rows already logged today teach the ones after them
        t0_of = lambda d: datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=tz).timestamp()  # noqa: E731
        entries = [r for r in entries_before + entries_today if r.get("taskId")]
        sess, repo = votes(days, entries, t0_of, date)
        names = {r["taskId"]: r for r in sorted(entries, key=lambda r: r.get("date") or "")}
        skip = frozenset(w for _, w in words(settings.get("person_name") or ""))
        index = note_index(entries, skip)
        t0 = t0_of(date)
        for e, row in zip([e for e in proposal.get("entries") or [] if e.get("e", 0) - e.get("s", 0) >= 5],
                          days[date]):
            try:
                text = text_match(index, work_text(e, t0), date, skip)
            except (OSError, ValueError) as exc:  # the votes alone, as before
                logbook.record("learn", f"Words of the work: {type(exc).__name__}: {exc}", kind="warning")
                text = {}
            e["learned"] = suggest(row, sess, repo, names, text=text)
        proposal["learning"] = dict(_state) if _state["of"] and _state["done"] < _state["of"] else None
    except Exception as exc:
        logbook.record("learn", f"Suggesting tasks: {type(exc).__name__}: {exc}", kind="warning")
