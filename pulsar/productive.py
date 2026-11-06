"""Your time entries in Productive (and logging new ones), through one of two connections:

* token: Productive's REST API with a personal API token (Settings > API integrations; Read/Write to log time
  from Pulsar, read-only to only look). Works for anyone.
* claude-code: the Productive MCP connection Claude Code already holds (its token sits in the macOS keychain and
  lasts an hour; when it has expired, `claude mcp get` makes Claude Code renew it). Needs no setup for Claude Code
  users, but it answers in text written for an AI, so it is the fallback.

Both give the same rows: id, date, start (epoch or None), minutes, note, approved, task, taskId, service, serviceId,
project, budget, billable (True/False/None), billing (the service's billing type, in words).
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Callable, Optional, Tuple

from . import config

API = "https://api.productive.io/api/v2"
MCP_URL = "https://mcp.productive.io/mcp"
NAMES_FILE = os.path.join(config.CACHE_DIR, "names.json")

Get = Callable[[str, dict, str, Optional[str]], Tuple[int, object]]


class ProductiveError(Exception):
    pass


class ProductiveUnclear(ProductiveError):
    """A write whose answer never came, or could not be read: it may or may not have happened. Never retried."""


class TokenRefused(ProductiveError):
    """Productive said no to the token itself (HTTP 401): nothing else will work until it is renewed."""


# ---- names that rarely change: a task's project, a service's budget and billing type -------------

NAME_AGE = 7 * 86400  # a name or billing type is read again after a week: projects and budgets get renamed
HIDDEN_AGE = 86400    # a billing type this role could not see ("0") is asked again after a day


class Names:
    """Project and budget names, each service's budget and billing type, in names.json with when each was read (the
    "_at" key, which older versions keep as it is). A stale one is still shown, and read again on the next request that
    needs it. Entries an older version kept have no time: they get one spread over the period, so they are refreshed
    a few at a time, never all on the first day."""

    def __init__(self, path: str = NAMES_FILE):
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}
        at = self.data.pop("_at", None)
        self.at = {k: v for k, v in at.items() if isinstance(v, (int, float))} if isinstance(at, dict) else {}
        now, untimed = time.time(), [k for k in self.data if k not in self.at]
        for k in untimed:
            self.at[k] = now - zlib.crc32(k.encode("utf-8")) % self.max_age(k)
        if untimed:
            self._write()

    def max_age(self, key: str) -> int:
        return HIDDEN_AGE if key.startswith("billing:") and self.data.get(key) == "0" else NAME_AGE

    def get(self, key: str):
        return self.data.get(key)

    def fresh(self, key: str) -> bool:
        """Known, and read recently enough to trust."""
        return key in self.data and time.time() - self.at.get(key, 0) < self.max_age(key)

    def set_many(self, pairs: dict, at: Optional[float] = None) -> None:
        """Remember these, read now (or at `at`: a failed re-read keeps the old value and asks again later)."""
        if not pairs:
            return
        with self.lock:
            self.data.update(pairs)
            self.at.update(dict.fromkeys(pairs, time.time() if at is None else at))
            self._write()

    def keep_for_now(self, keys) -> None:
        """A re-read failed: keep the old values and ask again in a day, not on every request."""
        self.set_many({k: self.data[k] for k in keys if k in self.data}, at=time.time() - NAME_AGE + HIDDEN_AGE)

    def _write(self) -> None:
        try:
            config.write_atomic(self.path, json.dumps({**self.data, "_at": self.at}, ensure_ascii=False))
        except OSError:
            pass  # a cache: the names are still in memory


_shared: Optional[Names] = None


def shared_names() -> Names:
    global _shared
    if _shared is None:
        _shared = Names()
    return _shared


def day_shift(date: str, days: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---- REST, with a personal token ------------------------------------------------------------

# ---- HTTPS to Productive ---------------------------------------------------------------------------

_CTX = ssl.create_default_context()
_pool: dict = {}
_pool_lock = threading.Lock()


def https(method: str, url: str, body: Optional[bytes] = None, headers: Optional[dict] = None,
          timeout: float = 60, write: bool = False, head: Optional[dict] = None) -> Tuple[int, bytes]:
    """One HTTPS request. Redirects are never followed, so a token only ever goes to the address it was meant for.
    `head`, if given, gets the answer's headers (names in lower case).
    Reads reuse a kept-alive connection per host (a new TLS connection to Productive costs ~160 ms) and retry once
    on a fresh one if the server had dropped it, or had closed a fresh one without answering. A write always uses a
    new connection and is never retried: a second attempt could log the same time twice."""
    if config.tests_offline():
        raise ProductiveError("A test tried to reach the network; tests must fake it.")
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        raise ProductiveError("Refusing a non-HTTPS address.")
    if write and config.writes_blocked():  # every write goes through here, whatever asked for it
        raise ProductiveError(config.writes_blocked())
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    tries = 1 if write else 3
    for attempt in range(tries):
        conn = None
        if not write:
            with _pool_lock:
                idle = _pool.setdefault(u.hostname, [])
                conn = idle.pop() if idle else None
        fresh = conn is None
        if fresh:
            conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=_CTX)
        elif conn.sock is not None:
            conn.sock.settimeout(timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
        except (http.client.HTTPException, OSError) as exc:
            conn.close()
            hung_up = isinstance(exc, (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError,
                                       BrokenPipeError))
            if write or attempt == tries - 1 or not hung_up:  # a timeout is never retried: it would only wait again
                raise
            if fresh:  # Productive hung up on a new connection: it is busy, so wait a moment before asking again
                time.sleep(0.4 * (attempt + 1))
            else:  # a kept-alive connection had gone stale: the others kept as long are stale too, so drop them all
                with _pool_lock:
                    for old in _pool.pop(u.hostname, []):
                        old.close()
            continue
        if head is not None:
            head.update({k.lower(): v for k, v in resp.getheaders()})
        if write or resp.will_close:
            conn.close()
        else:
            with _pool_lock:
                idle = _pool.setdefault(u.hostname, [])
                (idle.append(conn) if len(idle) < 8 else conn.close())
        return resp.status, data
    raise ProductiveError("Productive could not be reached.")


def rest_get(path: str, query: dict, token: str, org: Optional[str]) -> Tuple[int, object]:
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    headers = {"X-Auth-Token": token, "Content-Type": "application/vnd.api+json", "Accept": "application/vnd.api+json"}
    if org:
        headers["X-Organization-Id"] = str(org)
    try:
        status, raw = https("GET", url, headers=headers, timeout=30)
    except (OSError, http.client.HTTPException, ProductiveError) as exc:
        return 0, network_reason(exc)  # status 0: no answer; the second item says why, in words
    if status != 200:
        return status, None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return 0, "the answer was not JSON"


def network_reason(exc: BaseException) -> str:
    """Why a request got no answer, in words a person can act on."""
    import socket
    if isinstance(exc, socket.gaierror):
        return "Productive's address could not be looked up (offline, or a DNS problem)"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "Productive took too long to answer"
    if isinstance(exc, ssl.SSLError):
        return f"the secure connection failed ({exc.__class__.__name__}: {exc})"
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, http.client.RemoteDisconnected)):
        return "Productive closed the connection"
    return f"{exc.__class__.__name__}: {exc}"


def why(status: int, doc) -> str:
    """'HTTP 503', '429 …' or the reason there was no answer, for messages."""
    if status == 0:
        return doc if isinstance(doc, str) and doc else "no answer"
    if status == 429:
        return "HTTP 429: Productive is limiting requests; try again in a minute"
    return f"HTTP {status}"


def _index(doc: dict) -> dict:
    return {(x.get("type"), str(x.get("id"))): x for x in (doc.get("included") or []) if isinstance(x, dict)}


def _rel(obj: dict, name: str) -> Optional[str]:
    data = ((obj.get("relationships") or {}).get(name) or {}).get("data")
    return str(data["id"]) if isinstance(data, dict) and data.get("id") is not None else None


def _attr(obj: Optional[dict], name: str):
    return ((obj or {}).get("attributes") or {}).get(name)


def rest_detect(token: str, org_hint: str = "", get: Get = rest_get) -> dict:
    """Which organization and person the token belongs to. Productive's docs do not name an endpoint for
    this, so two likely ones are tried; what they cannot tell is asked for in Settings."""
    found = {"org_id": org_hint or "", "org_name": "", "person_id": "", "person_name": ""}
    for org in ([org_hint] if org_hint else []) + [None]:
        status, doc = get("/organization_memberships", {"include": "organization,person"}, token, org)
        if status in (401, 403) and (org_hint or org is None):
            raise ProductiveError(f"Productive refused the token (HTTP {status}). Check that the whole token was pasted"
                                  + (" and that the organization ID is right." if org_hint else
                                     "; if it is right, type your organization ID in Settings."))
        if status == 200 and isinstance(doc, dict) and len(doc.get("data") or []) > 1:
            break  # several people (an admin token?): never guess; Settings asks for the person ID
        if status == 200 and isinstance(doc, dict) and doc.get("data"):
            m = doc["data"][0]
            inc = _index(doc)
            found["org_id"] = found["org_id"] or (_rel(m, "organization") or "")
            found["person_id"] = _rel(m, "person") or ""
            org_obj, person = inc.get(("organizations", found["org_id"])), inc.get(("people", found["person_id"]))
            found["org_name"] = _attr(org_obj, "name") or ""
            found["person_name"] = " ".join(x for x in (_attr(person, "first_name"), _attr(person, "last_name")) if x)
            break
    return found


def rest_entries(d0: str, d1: str, token: str, org: str, person: str, names: Names, get: Get = rest_get) -> list:
    if not (org and person):
        raise ProductiveError("Productive needs your organization ID and person ID: open Settings.")
    # One day either side and a filter here, so the answer is right whether Productive reads after/before
    # as inclusive or not.
    base = {"filter[person_id]": person, "filter[after]": day_shift(d0, -1), "filter[before]": day_shift(d1, 1),
            "page[size]": 200}
    # the person too, so a change can check the entry is yours; a role that may not include something gets less
    includes = ["task,task.project,service,service.deal,person", "task,service,person", "task,service"]
    rows, inc, page = [], {}, 1
    while True:
        status, doc = get("/time_entries", {**base, "include": includes[0], "page[number]": page}, token, org)
        if status in (400, 403) and len(includes) > 1:  # a nested include the role may not see
            includes, page, rows, inc = includes[1:], 1, [], {}
            continue
        if status in (401, 403):
            raise ProductiveError("Productive refused the token (HTTP %d). Check it in Settings." % status)
        if status != 200 or not isinstance(doc, dict):
            raise ProductiveError(f"Productive did not give the time entries ({why(status, doc)}).")
        rows += [d for d in doc.get("data") or [] if isinstance(d, dict)]
        inc.update(_index(doc))
        pages = (doc.get("meta") or {}).get("total_pages")
        if not isinstance(pages, int) or page >= pages or page >= 50:
            break
        page += 1

    learned, out = {}, []
    for d in rows:
        date = _attr(d, "date") or ""
        if not (d0 <= date <= d1):
            continue
        task_id, service_id = _rel(d, "task"), _rel(d, "service")
        task, service = inc.get(("tasks", task_id)), inc.get(("services", service_id))
        project = _attr(inc.get(("projects", _rel(task or {}, "project"))), "name")
        budget = _attr(inc.get(("deals", _rel(service or {}, "deal"))), "name")
        if task_id and project:
            learned[f"tasks:{task_id}"] = project
        if service_id and budget:
            learned[f"services:{service_id}"] = budget
        out.append({"id": str(d.get("id")), "date": date, "start": epoch(_attr(d, "started_at")),
                    "minutes": int(_attr(d, "time") or 0), "note": strip_html(_attr(d, "note") or ""),
                    "noteRich": rich_note(_attr(d, "note") or ""), "personId": _rel(d, "person") or "",
                    "approved": bool(_attr(d, "approved_at")) or _attr(d, "approved") is True,
                    "task": _attr(task, "title") or "", "service": _attr(service, "name") or "",
                    "project": project or "", "budget": budget or "",
                    **billable_of(_attr(service, "billing_type")),
                    "_task": task_id, "_service": service_id})
    names.set_many(learned)
    _fill_rest_names(out, token, org, names, get)
    return out


def _fill_rest_names(rows: list, token: str, org: str, names: Names, get: Get) -> None:
    """Project and budget names the list answer left out: from the cache while fresh, else one lookup per task or
    service, remembered."""
    todo = sorted({("tasks", r["_task"]) for r in rows
                   if r["_task"] and not r["project"] and not names.fresh(f"tasks:{r['_task']}")} |
                  {("services", r["_service"]) for r in rows
                   if r["_service"] and not r["budget"] and not names.fresh(f"services:{r['_service']}")})

    def one(ref):
        kind, rid = ref
        status, doc = get(f"/{kind}/{rid}", {"include": "project" if kind == "tasks" else "deal"}, token, org)
        if status != 200 or not isinstance(doc, dict):
            return ref, None
        obj = doc.get("data") or {}
        rel = _rel(obj, "project" if kind == "tasks" else "deal")
        return ref, _attr(_index(doc).get(("projects" if kind == "tasks" else "deals", rel)), "name") or ""

    with ThreadPoolExecutor(6) as pool:
        answers = [(f"{k}:{i}", n) for (k, i), n in pool.map(one, todo)]
    names.set_many({ref: n for ref, n in answers if n is not None})
    names.keep_for_now(ref for ref, n in answers if n is None)
    for r in rows:
        r["project"] = r["project"] or names.get(f"tasks:{r['_task']}") or "(no project)"
        r["budget"] = r["budget"] or names.get(f"services:{r['_service']}") or ""
        r["taskId"], r["serviceId"] = r.pop("_task") or "", r.pop("_service") or ""


def strip_html(text: str) -> str:
    import html
    return html.unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ").strip()


def rich_note(text: str) -> bool:
    """A note with formatting the plain text above leaves out: more than one paragraph, or any tag but <p>
    (a list, a link, a line break, bold). Changing such a note in Pulsar saves it as plain text, so the page says so."""
    tags = [t.lower() for t in re.findall(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)", text or "")]
    return any(t != "p" for t in tags) or tags.count("p") > 2


# ---- Claude Code's Productive MCP connection --------------------------------------------------

_cc_cache: list = [0.0, None]


def claude_code_token() -> Tuple[Optional[str], Optional[str], bool]:
    """Claude Code's Productive token, read from the keychain at most once a minute. When it has expired, Claude Code
    is asked to renew it (see _renew_claude_code) and it is read again."""
    if time.time() - _cc_cache[0] < 60 and _cc_cache[1] is not None:
        return _cc_cache[1]
    result = _claude_code_token()
    if result[0] is None and result[3]:
        with _renew_lock:  # one renewal at a time; requests that waited read the renewed token
            again = _claude_code_token()
            if again[0] is None and again[3] and _renew_claude_code(again[4]):
                again = _claude_code_token()
            result = again
    _cc_cache[:] = [time.time(), result[:3]]
    return result[:3]


def _claude_code_token() -> tuple:
    """(token, problem, connected, expired, server name): connected says Claude Code has a Productive connection at
    all, so its problem (an expired token) is worth showing even when Pulsar was not asked to use it."""
    if sys.platform != "darwin" or config.tests_offline():
        return None, "Claude Code's Productive connection is read from the macOS keychain; use a token instead.", False, False, ""
    try:
        run = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                             capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        config.keychain_timed_out()
        return None, "The keychain did not answer when Pulsar asked for Claude Code's Productive connection.", False, False, ""
    try:
        servers = json.loads(run.stdout).get("mcpOAuth", {})
    except ValueError:
        return None, "Claude Code has no Productive connection on this computer.", False, False, ""
    for key, value in servers.items():
        if key.split("|")[0] == "productive":  # the key itself contains a pipe
            if urllib.parse.urlparse(value.get("serverUrl") or MCP_URL).hostname != "mcp.productive.io":
                return None, "Claude Code's \"productive\" connection points somewhere else than Productive; use a token.", True, False, ""
            token, expires = value.get("accessToken"), int(value.get("expiresAt") or 0)
            if not token:
                return None, "Claude Code's Productive connection is not authorised (/mcp in Claude Code).", True, False, ""
            if expires and expires < time.time() * 1000:
                at = time.strftime("%H:%M", time.localtime(expires / 1000))
                return None, (f"Claude Code's Productive token expired at {at} and Claude Code could not renew it. "
                              "Run /mcp in Claude Code and reconnect Productive."), True, True, value.get("serverName") or "productive"
            return token, None, True, False, ""
    return None, "Claude Code has no Productive connection on this computer.", False, False, ""


_renew_lock = threading.Lock()
_renewed_at = [0.0]


def _claude_binary() -> Optional[str]:
    """The claude command: on the PATH, where its installers put it, or the one inside the VS Code or Cursor
    extension. The app starts Pulsar with a bare PATH, so the usual places are looked at too."""
    if config.tests_offline():  # tests never start Claude Code
        return None
    import glob
    import shutil
    home = os.path.expanduser("~")
    found = [shutil.which("claude")] + [os.path.join(home, p) for p in (".local/bin/claude", ".claude/local/claude",
                                                                         ".npm-global/bin/claude")]
    found += ["/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    found += sorted(glob.glob(os.path.join(home, ".*/extensions/anthropic.claude-code-*/resources/native-binary/claude")),
                    key=os.path.getmtime, reverse=True)
    return next((p for p in found if p and os.path.isfile(p) and os.access(p, os.X_OK)), None)


def _renew_claude_code(name: str) -> bool:
    """Ask Claude Code to renew its own Productive token. `claude mcp get <name>` checks the connection, and Claude
    Code renews an expired token on the way with its refresh token, keeping the new one in its keychain item. Pulsar
    never uses the refresh token itself: if Productive issues a new one at each renewal, a renewal that only Pulsar
    knew about would leave Claude Code holding a dead one. No AI model is involved. At most once in 5 minutes."""
    if time.time() - _renewed_at[0] < 300 or not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", name or ""):
        return False
    _renewed_at[0] = time.time()
    binary = _claude_binary()
    if not binary:
        return False
    try:
        run = subprocess.run([binary, "mcp", "get", name], capture_output=True, text=True, timeout=45,
                             cwd=os.path.expanduser("~"), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        from . import logbook
        logbook.record("productive", f"Could not ask Claude Code to renew its Productive token: {exc}", kind="warning")
        return False
    status = next((line.split(":", 1)[1].strip() for line in run.stdout.splitlines() if line.strip().startswith("Status:")), "")
    if "connected" not in status.lower() or "not" in status.lower():
        from . import logbook
        logbook.record("productive", f"Claude Code could not renew its Productive token (status: {status or 'no answer'}).",
                       kind="warning")
        return False
    return True


WRITE_TOOLS = {"create_resource", "update_resource", "delete_resource", "perform_action"}


_mcp_slots = threading.BoundedSemaphore(4)  # Productive's MCP endpoint hangs up when asked too much at once
_bulk_slots = threading.BoundedSemaphore(2)  # secondary reads take two of them at most, so the day's own never waits


def _gated(bulk: bool, fn):
    """fn, run in a slot for secondary reads when `bulk` (weeks of history, the Tasks box's estimates): together they
    hold at most two of the four MCP slots, and the day's entries and the log dialog always find one free."""
    def run(*a):
        with _bulk_slots if bulk else contextlib.nullcontext():
            return fn(*a)
    return run


def _mcp_headers(token: str, session: Optional[str]) -> dict:
    head = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    if session:
        head["Mcp-Session-Id"] = session
    return head


def mcp_text(tool: str, args: dict, token: str, session: Optional[str] = None) -> str:
    """One tool call. `session`: the MCP session of a change (mcp_session), else none, as every read is."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    with _mcp_slots:
        status, raw = https("POST", MCP_URL, body, _mcp_headers(token, session), timeout=60, write=tool in WRITE_TOOLS)
    if status == 401:
        _cc_cache[0] = 0.0  # read the keychain again next time: Claude Code may have renewed it meanwhile
        raise TokenRefused("Productive refused Claude Code's token (HTTP 401): it has expired or was revoked. Pulsar "
                           "reads the renewed one on the next try; if this stays, run /mcp in Claude Code and reconnect "
                           "Productive.")
    if status != 200:
        if tool in WRITE_TOOLS and status >= 500:  # a server error after a write was sent: it may have happened
            raise ProductiveUnclear(f"Productive answered HTTP {status}, so the change may or may not have happened.")
        raise ProductiveError(f"Productive answered HTTP {status}.")
    text = raw.decode("utf-8", "replace")
    msg = None
    for line in text.splitlines():
        if line.startswith("data:"):
            msg = json.loads(line[5:])
    msg = msg or json.loads(text)
    if msg.get("error"):
        raise ProductiveError(msg["error"].get("message", "MCP error"))
    content = "".join(c.get("text", "") for c in msg.get("result", {}).get("content", []))
    if msg.get("result", {}).get("isError"):  # the tool ran and failed: its text is the reason, never a result
        raise ProductiveError("Productive refused: " + content[:300])
    return content


def mcp_session(token: str) -> Optional[str]:
    """An MCP session for one change. Productive's MCP refuses to update a field the session has not loaded first
    ("Update of time entry aborted to avoid overwriting data you have not seen. not loaded before update: note"), and
    it keeps what a session loaded in that session; Pulsar's reads use none. None when the server gives no session:
    the change is then tried without one, and only Productive's success sentence counts as done."""
    from . import __version__
    body = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Pulsar", "version": __version__}}})
    head: dict = {}
    with _mcp_slots:
        status, _ = https("POST", MCP_URL, body.encode(), _mcp_headers(token, None), timeout=30, head=head)
    if status == 401:
        _cc_cache[0] = 0.0
        raise TokenRefused("Productive refused Claude Code's token (HTTP 401): it has expired or was revoked. Pulsar "
                           "reads the renewed one on the next try; if this stays, run /mcp in Claude Code and reconnect "
                           "Productive.")
    session = head.get("mcp-session-id") or ""
    if status != 200 or not re.fullmatch(r"[\x21-\x7e]{1,200}", session):
        return None
    with _mcp_slots:  # the protocol's "ready" note; it has no answer to read
        https("POST", MCP_URL, json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
              _mcp_headers(token, session), timeout=30)
    return session


def parse_mcp(content: str) -> dict:
    """The JSON block of an MCP answer. A query that matched nothing has no block, only "Loaded 0 ..."."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S)
    if m:
        doc = json.loads(m.group(1))
        if "items" in doc:  # a query: one record comes as a list of objects, several as columns and rows
            for key in ("items", "included"):
                if isinstance(doc.get(key), list):
                    doc[key] = _as_table(doc[key])
        return doc  # load_resource answers ({"item", "included": [objects]}) stay as they are
    if re.match(r"\s*Loaded 0 ", content):
        return {"items": {"columns": [], "rows": []}, "pagination": {"total": 0}}
    raise ProductiveError(content[:300] or "empty answer")


def _as_table(objects: list) -> dict:
    cols: list = []
    for o in objects:
        for k in o:
            if k not in cols:
                cols.append(k)
    for k in ("id", "type"):  # rows read r[0] as the type and r[1] as the id, as in the table form
        if k in cols:
            cols.remove(k)
        cols.insert(0, k)
    rows = [[_ref(o.get(c)) for c in cols] for o in objects]
    return {"columns": cols, "rows": rows}


def _ref(v):
    """A relationship in the object form ({"type": "services", "id": "1"}) as the table form writes it."""
    return f"{v['type']}:{v['id']}" if isinstance(v, dict) and "type" in v and "id" in v else v


def mcp_detect(token: str) -> dict:
    text = mcp_text("get_current_person_and_organization", {}, token)
    blocks = [json.loads(b) for b in re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)]
    person, org = (blocks + [{}, {}])[:2]
    day = re.match(r"([\d.]+)\s*h(?:\s*(\d+)\s*min)?", str(org.get("person_day") or ""))  # "8 h", "7 h 30 min"
    return {"person_id": str(person.get("id") or ""), "org_id": str(org.get("id") or ""),
            "person_name": " ".join(x for x in (person.get("firstName"), person.get("lastName")) if x),
            "org_name": org.get("name") or "", "person_day_hours": float(day.group(1)) + int(day.group(2) or 0) / 60 if day else None}


def _mcp_rows(conditions: list, person: str, token: str) -> Tuple[list, dict]:
    """Every page, each a fresh query with an offset: the endpoint keeps no session for this client, so a
    query_id continuation can land on a server that never saw it ("Query state not found")."""
    base = {"resource_type": "time_entries", "limit": 200,
            "filters": {"op": "and", "conditions": [{"field": "person_id", "operator": "eq", "value": person}, *conditions]},
            "include_fields": ["date", "started_at", "time", "note", "is_approved", "person", "task.title", "service.name"]}
    rows, included, offset = [], {}, 0
    while True:
        d = parse_mcp(mcp_text("query_resources", {**base, "offset": offset}, token))
        if not isinstance(d.get("items"), dict):
            raise ProductiveError("Productive answered without a list of entries; try again in a moment.")
        cols = d["items"]["columns"]
        rows += [dict(zip(cols, r)) for r in d["items"]["rows"]]
        inc = d.get("included") or {}
        for r in inc.get("rows", []):
            included[f"{r[0]}:{r[1]}"] = dict(zip(inc.get("columns", []), r))
        offset += 200
        if offset >= ((d.get("pagination") or {}).get("total") or 0):
            return rows, included


def minutes_of(s) -> float:
    """"1 h 30 min", "1.5 h", "-90 min" -> minutes; a number is already minutes."""
    if isinstance(s, (int, float)):
        return float(s)
    text = (s or "").strip()
    sign = -1 if text.startswith("-") else 1
    h = re.search(r"([\d.]+)\s*h", text)
    m = re.search(r"([\d.]+)\s*min", text)
    return sign * ((float(h.group(1)) * 60 if h else 0) + (float(m.group(1)) if m else 0))


def mcp_entries(d0: str, d1: str, token: str, person: str, names: Names, bulk: bool = False) -> list:
    """bulk: a long read in the background (weeks of history), which leaves room for the day's own reads."""
    if not str(person or "").isdigit():  # a filter on "" could match anyone's entries
        raise ProductiveError("Productive did not say who you are, so no entries were read: open Settings.")
    first, last = datetime.strptime(d0, "%Y-%m-%d"), datetime.strptime(d1, "%Y-%m-%d")
    dates = [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((last - first).days + 1)]
    # three days per query, four at a time: each answer fits one page, and a month takes seconds
    chunks = [(dates[i], dates[min(i + 2, len(dates) - 1)]) for i in range(0, len(dates), 3)]
    with ThreadPoolExecutor(4) as pool:
        parts = list(pool.map(_gated(bulk, lambda c: _mcp_rows([{"field": "date", "operator": "gt_eq", "value": c[0]},
                                                                {"field": "date", "operator": "lt_eq", "value": c[1]}],
                                                               person, token)), chunks))
    rows = [r for part, _ in parts for r in part]
    included = {k: v for _, inc in parts for k, v in inc.items()}

    # names, and each service's budget id (to tell whether it is billable): looked up when unknown or a week old
    todo = sorted({r[k] for r in rows for k in ("task", "service") if r.get(k) and (
        not names.fresh(r[k]) or (k == "service" and not names.fresh("deal:" + r[k])))})

    def one(ref: str):
        kind, rid = ref.split(":")
        try:
            d = parse_mcp(mcp_text("load_resource", {"resource_type": kind, "id": rid,
                                                     "include_fields": ["project.name"] if kind == "tasks" else ["deal.name"]}, token))
        except Exception:
            return ref, None, None
        name = next((x.get("fields", {}).get("name") for x in d.get("included", []) if x.get("fields", {}).get("name")), "")
        deal = ((d.get("item") or {}).get("fields") or {}).get("deal") or {}
        return ref, name, (str(deal["id"]) if kind == "services" and isinstance(deal, dict) and deal.get("id") else None)

    learned, failed = {}, []
    with ThreadPoolExecutor(6) as pool:
        for ref, name, deal in pool.map(_gated(bulk, one), todo):
            if name is not None:
                learned[ref] = name
            else:
                failed += [ref, "deal:" + ref]
            if deal:
                learned["deal:" + ref] = deal
    names.set_many(learned)
    names.keep_for_now(failed)
    _mcp_billing(sorted({r["service"] for r in rows if r.get("service")}), token, names, bulk)
    out = []
    for r in rows:
        task, service = included.get(r.get("task") or "", {}), included.get(r.get("service") or "", {})
        billing = names.get("billing:" + (r.get("service") or ""))
        out.append({"id": str(r["id"]), "date": r.get("date"), "start": epoch(r.get("started_at")),
                    "minutes": round(minutes_of(r.get("time"))), "note": strip_html(r.get("note") or ""),
                    "noteRich": rich_note(r.get("note") or ""), "personId": (r.get("person") or ":").split(":")[1],
                    "approved": bool(r.get("is_approved")), "task": task.get("title") or "",
                    "service": service.get("name") or "",
                    "project": names.get(r.get("task") or "") or "(no project)",
                    "budget": names.get(r.get("service") or "") or "", **billable_of(billing),
                    "taskId": (r.get("task") or ":").split(":")[1], "serviceId": (r.get("service") or ":").split(":")[1]})
    return out


BILLING = {"1": "Fixed price", "2": "Time and materials", "3": "Non-billable", "4": "Percentage"}


def billable_of(billing_type) -> dict:
    """A time entry is billable when its service is: Fixed price, Time and materials or Percentage."""
    bt = str(billing_type) if billing_type is not None else ""
    return {"billable": None if bt not in BILLING else bt != "3", "billing": BILLING.get(bt, "")}


def _mcp_billing(services: list, token: str, names: Names, bulk: bool = False) -> None:
    """Each service's billing type. Productive hides the field from some roles but still filters on it, so
    the services of the budgets in use are listed once per billing type, and what comes back is remembered: read
    again after a week, and after a day for a service found in no list (hidden from this role, "0")."""
    unknown = [r for r in services if not names.fresh("billing:" + r)]
    deals = sorted({names.get("deal:" + r) for r in unknown if names.get("deal:" + r)})
    if not deals:
        return

    def listing(job):
        bt, chunk = job
        ids, offset = [], 0
        while True:
            d = parse_mcp(mcp_text("query_resources", {
                "resource_type": "services", "limit": 200, "offset": offset, "include_fields": ["name"],
                "filters": {"op": "and", "conditions": [{"field": "billing_type", "operator": "eq", "value": bt},
                                                        {"field": "deal", "operator": "any_of", "value": chunk}]}}, token))
            cols = d["items"]["columns"]
            ids += [str(dict(zip(cols, row)).get("id")) for row in d["items"]["rows"]]
            offset += 200
            if offset >= ((d.get("pagination") or {}).get("total") or 0):
                return bt, ids

    jobs = [(bt, deals[i:i + 40]) for bt in BILLING for i in range(0, len(deals), 40)]
    found: dict = {}
    try:
        with ThreadPoolExecutor(4) as pool:
            for bt, ids in pool.map(_gated(bulk, listing), jobs):
                found.update({f"billing:services:{i}": bt for i in ids})
    except Exception:
        return  # try again next time
    for r in unknown:
        found.setdefault("billing:" + r, "0")  # in none of the lists: not visible to this role; asked again in a day
    names.set_many(found)


# ---- which one -----------------------------------------------------------------------------------

def connection(settings: dict) -> dict:
    """Which source answers, and why not when none does."""
    want = settings.get("productive_source") or "auto"
    token = config.get_token() if want in ("auto", "token") else None
    if token:
        return {"kind": "token", "token": token}
    if want == "token":
        return {"kind": None, "problem": config.KEYCHAIN_STUCK if config.keychain_stuck() else
                "No Productive token saved yet: add one in Settings."}
    cc, problem, connected = claude_code_token()
    if cc:
        return {"kind": "claude-code", "token": cc}
    if want == "claude-code":
        return {"kind": None, "problem": problem}
    if connected:  # say what went wrong with the connection in use, and the way out of it
        return {"kind": None, "problem": problem + " An API token in Settings does not expire."}
    if config.keychain_stuck():  # not "add a token": there may be one the keychain did not give
        return {"kind": None, "problem": config.KEYCHAIN_STUCK}
    return {"kind": None, "problem": "Productive is not connected: add your API token in Settings."}


def identify(settings: dict, conn: dict) -> dict:
    """Who you are (and the organization's working day), found once; Settings keeps it. A person's id is the
    same through either source, so it is only looked up while missing."""
    if settings.get("person_id") and settings.get("org_id"):
        return {}
    if conn["kind"] == "claude-code":
        return mcp_detect(conn["token"])
    if conn["kind"] == "token":
        return rest_detect(conn["token"], settings.get("org_id") or "")
    return {}


def entries(d0: str, d1: str, settings: dict, names: Optional[Names] = None,
            bulk: bool = False) -> Tuple[list, Optional[str], Optional[str]]:
    """(rows, problem, source). Never raises: a problem comes back as text for the page. bulk: see mcp_entries."""
    conn = connection(settings)
    if not conn["kind"]:
        return [], conn["problem"], None
    names = names or shared_names()
    try:
        if conn["kind"] == "token":
            rows = rest_entries(d0, d1, conn["token"], settings.get("org_id"), settings.get("person_id"), names)
        else:
            person = settings.get("person_id") or mcp_detect(conn["token"]).get("person_id")
            rows = mcp_entries(d0, d1, conn["token"], person, names, bulk)
    except ProductiveError as exc:
        return [], str(exc), conn["kind"]
    except Exception as exc:  # network and the like
        return [], f"Productive: {type(exc).__name__}: {exc}", conn["kind"]
    return rows, None, conn["kind"]


# ---- tasks and services: estimates, suggestions, search, where time can go ------------------------

def _need(settings: dict) -> dict:
    conn = connection(settings)
    if not conn["kind"]:
        raise ProductiveError(conn["problem"])
    return conn


def _task_from_mcp(item: dict, included: list) -> dict:
    f = item.get("fields") or {}
    project = f.get("project") or {}
    names = {(x.get("type"), str(x.get("id"))): (x.get("fields") or {}).get("name") for x in included}
    service = f.get("service") or {}
    return {"taskId": str(item.get("id")), "title": f.get("title") or "", "number": f.get("task_number") or "",
            "project": names.get(("projects", str(project.get("id")))) or "", "projectId": str(project.get("id") or ""),
            "serviceId": str(service.get("id") or "") if isinstance(service, dict) else "",
            "estimate": None if f.get("initial_estimate") is None else minutes_of(f.get("initial_estimate")),
            "worked": minutes_of(f.get("worked_time")) if f.get("worked_time") is not None else None,
            "remaining": None if f.get("remaining_time") is None else minutes_of(f.get("remaining_time"))}


TASK_FIELDS = ["title", "task_number", "initial_estimate", "worked_time", "remaining_time", "project.name", "service"]


_task_cache = config.Newest(300)  # task id -> (time, details)


def task_details(ids: list, settings: dict, reasons: Optional[dict] = None) -> dict:
    """Each task's estimate, time worked by everyone, and what is left, as Productive has them (cached 5 minutes).
    When none could be given because of the connection (the token refused or expired, Productive unreachable or down),
    it raises ProductiveError saying so, rather than answer as if the tasks did not exist. `reasons`, if given,
    gets the reason for each task that could not be read."""
    conn = _need(settings)
    now = time.time()
    found = {t: d for t, (at, d) in list(_task_cache.items()) if t in ids and now - at < 300}
    todo = [i for i in ids if str(i).isdigit() and i not in found]

    def one(tid: str):
        got = _one(tid)
        return got if got[1] is not None else _one(tid)  # one retry: a busy moment should not drop a task

    def _one(tid: str) -> tuple:
        """(id, details or None, why not, whether the connection is the reason)"""
        try:
            if conn["kind"] == "token":
                status, doc = rest_get(f"/tasks/{int(tid)}", {"include": "project"}, conn["token"], settings.get("org_id"))
                if status == 401:
                    return tid, None, ("Productive refused the API token (HTTP 401): it has expired or was revoked. "
                                       "Check it in Settings."), True
                if status != 200 or not isinstance(doc, dict):
                    down = status == 0 or status == 429 or status >= 500
                    return tid, None, (f"Productive could not be asked ({why(status, doc)})" if down else
                                       f"HTTP {status}: deleted, or not visible to you"), down
                d = doc.get("data") or {}
                proj = _index(doc).get(("projects", _rel(d, "project") or ""))
                return tid, {"taskId": tid, "title": _attr(d, "title") or "", "number": str(_attr(d, "task_number") or ""),
                             "project": _attr(proj, "name") or "", "projectId": _rel(d, "project") or "",
                             "serviceId": _rel(d, "service") or "",
                             "estimate": _attr(d, "initial_estimate"), "worked": _attr(d, "worked_time"),
                             "remaining": _attr(d, "remaining_time")}, "", False
            d = parse_mcp(mcp_text("load_resource", {"resource_type": "tasks", "id": tid, "include_fields": TASK_FIELDS}, conn["token"]))
            item = d.get("item") or {}
            if not item.get("id"):
                return tid, None, "deleted, or not visible to you", False
            return tid, _task_from_mcp(item, d.get("included") or []), "", False
        except TokenRefused as exc:
            return tid, None, str(exc), True
        except (OSError, http.client.HTTPException) as exc:
            return tid, None, f"Productive could not be reached ({network_reason(exc)})", True
        except ProductiveError as exc:  # "Productive answered HTTP 503" is Productive down; a refusal is about the task
            return tid, None, str(exc), str(exc).startswith("Productive answered HTTP 5")
        except Exception as exc:
            return tid, None, f"{type(exc).__name__}: {exc}", False

    failed = {}
    with ThreadPoolExecutor(6) as pool:  # several tasks: the Tasks box, which can wait; one: the log dialog, which cannot
        for tid, t, problem, connection_problem in pool.map(_gated(len(todo) > 1 and conn["kind"] != "token", one), todo):
            if t:
                _task_cache[tid] = (now, t)
                found[tid] = t
            else:
                failed[tid] = (problem, connection_problem)
    if reasons is not None:
        reasons.update({tid: problem for tid, (problem, _) in failed.items()})
    blocking = [problem for problem, connection_problem in failed.values() if connection_problem]
    if not found and blocking:  # nothing to show, and the connection is why: say so
        raise ProductiveError(blocking[0])
    return found


def _task_rows(conditions: list, settings: dict, limit: int = 1000) -> list:
    """The tasks matching `conditions`, page after page (200 each) up to `limit`: one page used to cut a long list."""
    conn = _need(settings)
    out: list = []
    if conn["kind"] == "token":
        query, active_only = {"include": "project,workflow_status", "page[size]": min(limit, 200)}, False
        for c in conditions:  # the REST spellings of the same filters (not yet tried against Productive)
            if c["field"] == "project.status":  # no such filter here: the included project's archived_at tells
                active_only = True
                continue
            key = {"assignee": "assignee_id", "workflow_status_category": "status", "title": "title"}[c["field"]]
            value = ",".join(c["value"]) if isinstance(c["value"], list) else c["value"]
            query[f"filter[{key}]" + ("[contains]" if c["operator"] == "contains" else "")] = value
        for page in range(1, 1 + (limit + 199) // 200):
            status, doc = rest_get("/tasks", {**query, "page[number]": page}, conn["token"], settings.get("org_id"))
            if status != 200 or not isinstance(doc, dict):
                raise ProductiveError(f"Productive did not list tasks ({why(status, doc)}).")
            inc = _index(doc)
            for d in doc.get("data") or []:
                project = inc.get(("projects", _rel(d, "project") or ""))
                if active_only and _attr(project, "archived_at"):
                    continue
                status = inc.get(("workflow_statuses", _rel(d, "workflow_status") or ""))
                out.append(_task_item(str(d.get("id")), _attr(d, "title") or "", _attr(project, "name") or "",
                                      _rel(d, "project") or "", _rel(d, "service") or "", _attr(d, "due_date"),
                                      _attr(status, "name"), _rel(d, "assignee")))
            pages = (doc.get("meta") or {}).get("total_pages")
            if not isinstance(pages, int) or page >= pages:
                break
        return _really_assigned(out[:limit], conditions)
    for offset in range(0, limit, 200):
        size = min(200, limit - offset)
        d = parse_mcp(mcp_text("query_resources", {"resource_type": "tasks", "limit": size, "offset": offset,
                                                   "sort": ["-last_activity_at"], "filters": {"op": "and", "conditions": conditions},
                                                   "include_fields": ["title", "project", "project.name", "service", "closed_at",
                                                                      "due_date", "assignee", "workflow_status.name"]},
                               conn["token"]))
        cols = d["items"]["columns"]
        inc = d.get("included") or {}
        related = {f"{r[0]}:{r[1]}": dict(zip(inc.get("columns", []), r)) for r in inc.get("rows", [])}
        rows = d["items"]["rows"]
        for row in rows:
            r = dict(zip(cols, row))
            ref = lambda k: (r.get(k) or ":").split(":")[1]
            out.append(_task_item(str(r.get("id")), (r.get("title") or "") + (" (closed)" if r.get("closed_at") else ""),
                                  (related.get(r.get("project") or "") or {}).get("name") or "", ref("project"), ref("service"),
                                  r.get("due_date"), (related.get(r.get("workflow_status") or "") or {}).get("name"),
                                  ref("assignee")))
        if len(rows) < size:
            break
    # The assignee filter also matches tasks where the person only has an open to-do (Productive's own docs say so):
    # keep the tasks really assigned to them.
    return _really_assigned(out, conditions)


def _really_assigned(tasks: list, conditions: list) -> list:
    """With an assignee filter, only the tasks whose assignee is that person: Productive's filter also matches tasks
    where the person only has an open to-do (its own docs say so). The assignee is not passed on."""
    person = next((str(c["value"]) for c in conditions if c["field"] == "assignee"), None)
    return [{k: v for k, v in t.items() if k != "assignee"} for t in tasks if not person or t["assignee"] == person]


def _task_item(task_id: str, title: str, project: str, project_id: str, service_id: str, due, status, assignee) -> dict:
    """One task for the pickers: its due date and workflow status too, so the log dialog can put the urgent ones first
    and say when a task is blocked."""
    return {"taskId": task_id, "title": title, "project": project, "projectId": project_id, "serviceId": service_id,
            "due": due if isinstance(due, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) else None,
            "status": status or "", "blocked": bool(re.search(r"block", status or "", re.I)), "assignee": assignee or ""}


def assigned_tasks(settings: dict) -> list:
    """Open tasks assigned to you, in active projects only (an archived project's tasks cannot take time)."""
    return _task_rows([{"field": "assignee", "operator": "eq", "value": settings.get("person_id")},
                       {"field": "workflow_status_category", "operator": "any_of", "value": ["1", "2"]},
                       {"field": "project.status", "operator": "eq", "value": "1"}], settings)


def search_tasks(q: str, settings: dict) -> list:
    return _task_rows([{"field": "title", "operator": "contains", "value": q}], settings, limit=25)


def bookable_services(task_id: str, date: str, settings: dict, names: Optional[Names] = None) -> dict:
    """The services of the task's project that take time on that date (a budget that has ended does not)."""
    conn = _need(settings)
    reasons: dict = {}
    task = task_details([task_id], settings, reasons).get(task_id)  # raises when the connection is the reason
    if not task or not task.get("projectId"):
        reason = reasons.get(task_id) or ("it has no project" if task else "")
        raise ProductiveError("Productive did not return that task" + (f" ({reason})." if reason else "."))
    names = names or shared_names()
    if conn["kind"] == "token":
        status, doc = rest_get("/services", {"filter[project_id]": task["projectId"], "filter[bookable_date]": date,
                                             "include": "deal", "page[size]": 200}, conn["token"], settings.get("org_id"))
        if status != 200 or not isinstance(doc, dict):
            raise ProductiveError(f"Productive did not list the services ({why(status, doc)}).")
        inc = _index(doc)
        services = [{"id": str(d.get("id")), "name": _attr(d, "name") or "",
                     "budget": _attr(inc.get(("deals", _rel(d, "deal") or "")), "name") or "",
                     **billable_of(_attr(d, "billing_type"))} for d in doc.get("data") or []]
    else:
        d = parse_mcp(mcp_text("query_resources", {
            "resource_type": "services", "limit": 100, "include_fields": ["name", "deal", "deal.name"],
            "filters": {"op": "and", "conditions": [{"field": "project", "operator": "eq", "value": task["projectId"]},
                                                    {"field": "bookable_date", "operator": "eq", "value": date}]}}, conn["token"]))
        cols, inc = d["items"]["columns"], d.get("included") or {}
        deals = {f"{r[0]}:{r[1]}": dict(zip(inc.get("columns", []), r)).get("name") for r in inc.get("rows", [])}
        rows = [dict(zip(cols, r)) for r in d["items"]["rows"]]
        names.set_many({f"deal:services:{r['id']}": (r.get("deal") or ":").split(":")[1] for r in rows if r.get("deal")})
        _mcp_billing([f"services:{r['id']}" for r in rows], conn["token"], names)
        services = [{"id": str(r["id"]), "name": r.get("name") or "", "budget": deals.get(r.get("deal") or "") or "",
                     **billable_of(names.get(f"billing:services:{r['id']}"))} for r in rows]
    return {"task": task, "services": services}


def _write_conn(settings: dict) -> dict:
    if config.writes_blocked():  # tests, demos, and a preview not allowed to write: nothing may reach Productive
        raise ProductiveError(config.writes_blocked())
    return _need(settings)


def _rest_headers(conn: dict, settings: dict) -> dict:
    return {"X-Auth-Token": conn["token"], "X-Organization-Id": str(settings.get("org_id")),
            "Content-Type": "application/vnd.api+json", "Accept": "application/vnd.api+json"}


UNCLEAR = ("No clear answer from Productive ({}), so the change may or may not have happened. The Logged column has "
           "been refreshed: check it before trying again.")
UNCLEAR_SAID = ("Productive's answer was unclear, so the change may or may not have happened. The Logged column has been "
                "refreshed: check it before trying again. Productive said: {}")


def _said(pattern: str, text: str, entry_id: str) -> bool:
    """Productive's own success sentence for this entry, and nothing else: its MCP answers a refusal ("Update of time
    entry aborted …", "Invalid parameters", "Failed to update time entry … 422") as plain text, not as an error."""
    return any(i == str(entry_id) for i in re.findall(pattern, text, re.I))


def _rest_write(method: str, entry_id: str, body: Optional[dict], conn: dict, settings: dict, ok: tuple, what: str) -> None:
    try:
        status, raw = https(method, f"{API}/time_entries/{int(entry_id)}",
                            None if body is None else json.dumps(body).encode("utf-8"), _rest_headers(conn, settings),
                            timeout=30, write=True)
    except (OSError, http.client.HTTPException) as exc:
        raise ProductiveUnclear(UNCLEAR.format(exc))
    if status >= 500:  # a server error after the change was sent: it may have happened
        raise ProductiveUnclear(UNCLEAR.format(f"HTTP {status}"))
    if status not in ok:
        raise ProductiveError(f"Productive refused {what} ({why(status, None)}): {raw[:300].decode('utf-8', 'replace')}")


def update_entry(entry_id: str, change: dict, settings: dict) -> None:
    """Changes one of your time entries: start, minutes, note, task, service (only what `change` names, so nothing
    else is written back). Called only from an explicit click in the page; the caller has checked the entry is yours,
    as you saw it."""
    conn = _write_conn(settings)
    if conn["kind"] == "token":
        attrs = {k: v for k, v in (("time", change.get("minutes")), ("started_at", change.get("started_at")),
                                   ("note", change.get("note"))) if v is not None}
        rels = {k: {"data": {"type": k + "s", "id": str(change[k + "_id"])}} for k in ("task", "service") if change.get(k + "_id")}
        body = {"data": {"type": "time_entries", "id": str(entry_id), "attributes": attrs, "relationships": rels}}
        return _rest_write("PATCH", entry_id, body, conn, settings, (200,), "the change")
    item = {"id": str(entry_id)}
    if change.get("minutes") is not None:
        item["time"] = f"{int(change['minutes'])} min"
    for k in ("started_at", "note"):
        if change.get(k) is not None:
            item[k] = change[k]
    for k in ("task", "service"):
        if change.get(k + "_id"):
            item[k] = str(change[k + "_id"])
    # Productive updates only the fields this session has loaded: load them (and whose entry it is) first, in one session
    token = conn["token"]
    try:
        session = mcp_session(token)
        loaded = parse_mcp(mcp_text("load_resource", {"resource_type": "time_entries", "id": str(entry_id),
                                                      "include_fields": [k for k in item if k != "id"] + ["person"]},
                                    token, session)).get("item") or {}
    except ProductiveError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise ProductiveError(f"Productive could not be asked for the entry before changing it ({network_reason(exc)}), "
                              "so nothing was changed.")
    person = ((loaded.get("fields") or {}).get("person") or {})
    if str(loaded.get("id")) != str(entry_id) or not isinstance(person, dict) or str(person.get("id")) != str(settings.get("person_id")):
        raise ProductiveError("Productive did not show that entry as yours, so nothing was changed.")
    try:
        text = mcp_text("update_resource", {"resource_type": "time_entries", "items": [item]}, token, session)
    except ProductiveError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise ProductiveUnclear(UNCLEAR.format(exc))
    if not (_said(r"time entry updated \(IDs?:\s*(\d+)\)", text, entry_id)
            or _said(r"updated 1 time entr(?:y|ies) \(IDs?:\s*(\d+)\)", text, entry_id)):
        raise ProductiveUnclear(UNCLEAR_SAID.format(text[:300] or "nothing"))


def delete_entry(entry_id: str, settings: dict) -> None:
    """Deletes one of your time entries (Productive keeps it in its Recycle Bin). Only from an explicit click."""
    conn = _write_conn(settings)
    if conn["kind"] == "token":
        return _rest_write("DELETE", entry_id, None, conn, settings, (200, 204), "to delete it")
    try:
        text = mcp_text("delete_resource", {"resource_type": "time_entries", "items": [{"id": str(entry_id)}]}, conn["token"])
    except ProductiveError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise ProductiveUnclear(UNCLEAR.format(exc))
    if not _said(r"time entry deleted \(IDs?:\s*(\d+)\)", text, entry_id):
        raise ProductiveUnclear(UNCLEAR_SAID.format(text[:300] or "nothing"))


def create_entry(entry: dict, settings: dict) -> dict:
    """Writes one time entry, as the person in Settings. Called only from an explicit click in the page."""
    if config.writes_blocked():  # tests, demos, and a preview not allowed to write: nothing may reach Productive
        raise ProductiveError(config.writes_blocked())
    conn = _need(settings)
    if not str(settings.get("person_id") or "").isdigit():
        raise ProductiveError("Pulsar does not know your Productive person ID yet, so nothing was written. Open "
                              "Settings: it fills in by itself once Productive answers, or type it there.")
    if conn["kind"] == "token":
        body = {"data": {"type": "time_entries",
                         "attributes": {"date": entry["date"], "time": int(entry["minutes"]), "started_at": entry["started_at"],
                                        "note": entry.get("note") or ""},
                         "relationships": {"person": {"data": {"type": "people", "id": str(settings.get("person_id"))}},
                                           "service": {"data": {"type": "services", "id": str(entry["service_id"])}},
                                           "task": {"data": {"type": "tasks", "id": str(entry["task_id"])}}}}}
        try:
            status, raw = https("POST", API + "/time_entries", json.dumps(body).encode("utf-8"), {
                "X-Auth-Token": conn["token"], "X-Organization-Id": str(settings.get("org_id")),
                "Content-Type": "application/vnd.api+json", "Accept": "application/vnd.api+json"}, timeout=30, write=True)
        except (OSError, http.client.HTTPException) as exc:
            raise ProductiveUnclear(f"No clear answer from Productive ({exc}), so the entry may or may not exist. Check "
                                   "the Logged column (it has been refreshed) before logging again.")
        if status >= 500:  # a server error after the entry was sent: it may exist
            raise ProductiveUnclear(f"Productive answered HTTP {status}, so the entry may or may not exist. Check the "
                                    "Logged column (it has been refreshed) before logging again.")
        if status not in (200, 201):
            raise ProductiveError(f"Productive refused the entry (HTTP {status}): {raw[:300].decode('utf-8', 'replace')}")
        doc = json.loads(raw or b"{}")
        return {"id": str((doc.get("data") or {}).get("id") or "")}
    item = {"date": entry["date"], "time": f"{int(entry['minutes'])} min", "started_at": entry["started_at"],
            "note": entry.get("note") or "", "person": str(settings.get("person_id")),
            "service": str(entry["service_id"]), "task": str(entry["task_id"])}
    try:
        text = mcp_text("create_resource", {"resource_type": "time_entries", "items": [item],
                                            "include_fields": ["date", "time", "started_at"]}, conn["token"])
    except ProductiveError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise ProductiveUnclear(f"No clear answer from Productive ({exc}), so the entry may or may not exist. Check the "
                               "Logged column (it has been refreshed) before logging again.")
    # only Productive's success sentence counts ("time entry created (ID: 12345678)."); any other text that happens to
    # hold an "ID: n" (an error naming a service, say) is not a new entry
    ids = re.findall(r"time entr(?:y|ies) created \(IDs?:\s*(\d+)", text, re.I)
    if not ids:
        raise ProductiveUnclear("Productive's answer was unclear, so the entry may or may not exist. Check the Logged "
                               "column (it has been refreshed) before logging again. Productive said: " + text[:200])
    return {"id": ids[0]}
