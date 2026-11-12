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
ESTIMATES_AGE = 86400  # how many of a project's recent tasks have an estimate (flags.py): read again after a day


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
        if key.startswith("estimates:"):
            return ESTIMATES_AGE
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
    Reads reuse a kept-alive connection per host (a new TLS connection to Productive costs ~160 ms) and try again on a
    fresh one if the server had dropped it, or had closed a fresh one without answering: three tries at most, never
    after a timeout. A write always uses a new connection and is never retried: a second attempt could log the same
    time twice."""
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
        return 0, "its answer could not be read"


def network_reason(exc: BaseException) -> str:
    """Why a request got no answer, in words a person can act on."""
    import socket
    if isinstance(exc, socket.gaierror):
        return "Productive's address could not be looked up (offline, or a DNS problem)"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "Productive took too long to answer"
    if isinstance(exc, ssl.SSLError):
        return f"the secure connection to Productive failed ({exc})"
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, http.client.RemoteDisconnected)):
        return "Productive closed the connection"
    return f"the connection to Productive failed ({exc})"


def why(status: int, doc) -> str:
    """'HTTP 503', '429 …' or the reason there was no answer, for messages."""
    if status == 0:
        return doc if isinstance(doc, str) and doc else "no answer"
    if status == 429:
        return "Productive is limiting requests; try again in a minute"
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
            raise ProductiveError(f"Productive refused the API token (HTTP {status}). Check that the whole token was "
                                  "pasted" + (" and that the organization ID is right." if org_hint else
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
            raise ProductiveError("Productive refused the API token (HTTP %d). Check it in Settings." % status)
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
        return None, "Claude Code's Productive connection is read from the macOS keychain; use your API token instead.", False, False, ""
    try:
        run = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                             capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        config.keychain_timed_out()
        return None, "The keychain did not answer when Pulsar asked for Claude Code's Productive connection.", False, False, ""
    try:
        doc = json.loads(run.stdout)
    except ValueError:
        return None, "Claude Code has no Productive connection on this computer.", False, False, ""
    try:
        return _token_in(doc)
    except (AttributeError, TypeError, ValueError):  # Claude Code keeps it in a shape this Pulsar does not know
        return None, ("Claude Code keeps its Productive connection in a form this Pulsar cannot read. Update Pulsar, "
                      "or use your API token in Settings."), True, False, ""


def _token_in(doc) -> tuple:
    """_claude_code_token's answer from Claude Code's credentials as the keychain holds them (JSON already read)."""
    servers = doc.get("mcpOAuth", {}) if isinstance(doc, dict) else {}
    for key, value in (servers.items() if isinstance(servers, dict) else ()):
        if not isinstance(value, dict):
            continue
        if key.split("|")[0] == "productive":  # the key itself contains a pipe
            if urllib.parse.urlparse(value.get("serverUrl") or MCP_URL).hostname != "mcp.productive.io":
                return None, "Claude Code's \"productive\" connection points somewhere other than Productive; use your API token.", True, False, ""
            token, expires = value.get("accessToken"), int(value.get("expiresAt") or 0)
            if token is not None and not isinstance(token, str):
                raise TypeError("accessToken")
            if not token:
                return None, "Claude Code's Productive connection is not signed in. Run /mcp in Claude Code and connect Productive.", True, False, ""
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
# JSON-RPC errors that mean the tool never ran: not parsed, not a request, no such method, bad parameters
NEVER_RAN = (-32700, -32600, -32601, -32602)
# a failure behind Productive's MCP server, passed on as its own error text: a server error (5xx), a timeout, a dropped
# connection. The write may have reached Productive before it, so it is never read as a refusal
UPSTREAM = re.compile(r'"status"\s*:\s*"?5\d\d\b|\b(?:HTTP|status(?: code)?|error|code)\W{0,3}5\d\d\b|bad gateway|'
                      r"service unavailable|gateway time-?out|internal server error|timed out|\btime-?out\b|"
                      r"\bE(?:CONN\w+|TIMEDOUT|AI_AGAIN|PIPE)\b|fetch failed|socket hang up|network error", re.I)


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
            raise ProductiveUnclear(UNCLEAR_SAID.format(f"HTTP {status}"))
        raise ProductiveError(f"Productive answered HTTP {status}.")
    text = raw.decode("utf-8", "replace")
    msg = None
    for line in text.splitlines():
        if line.startswith("data:"):
            msg = json.loads(line[5:])
    msg = msg or json.loads(text)
    if msg.get("error"):
        said = str(msg["error"].get("message") or "Productive gave an error without saying why.")
        if tool in WRITE_TOOLS and msg["error"].get("code") not in NEVER_RAN:  # it may have run before it failed
            raise ProductiveUnclear(UNCLEAR_SAID.format(said[:300]))
        raise ProductiveError(said)
    content = "".join(c.get("text", "") for c in msg.get("result", {}).get("content", []))
    if msg.get("result", {}).get("isError"):  # the tool ran and failed: its text is the reason, never a result
        if tool in WRITE_TOOLS and UPSTREAM.search(content):  # failed behind the MCP server, maybe after the write
            raise ProductiveUnclear(UNCLEAR_SAID.format(content[:300]))
        raise ProductiveError(plain_refusal(content, "it"))
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
    raise ProductiveError(content[:300] or "Productive gave an empty answer.")


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
    if failed:  # the entries show without those names until Productive gives them: said in the log, never silent
        from . import logbook
        logbook.record("productive", f"Productive did not give the names of {len(failed) // 2} projects or services "
                       "of your entries; Pulsar asks again later.", kind="warning", once_per=3600)
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
    except Exception as exc:  # try again next time
        from . import logbook
        logbook.record("productive", "Productive did not say which of your services are billable; Pulsar asks again "
                       "next time.", kind="warning", detail=f"{type(exc).__name__}: {exc}", once_per=3600)
        return
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
                "No Productive API token saved yet: add one in Settings."}
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
        from . import logbook
        return [], logbook.said("productive", "Your entries could not be read from Productive. Pulsar tries again "
                                "when the day is next read.", f"{type(exc).__name__}: {exc}"), conn["kind"]
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
            "remaining": None if f.get("remaining_time") is None else minutes_of(f.get("remaining_time")),
            "closed": bool(f.get("closed_at") or f.get("is_closed"))}


TASK_FIELDS = ["title", "task_number", "initial_estimate", "worked_time", "remaining_time", "project.name", "service",
               "closed_at"]


_task_cache = config.Newest(300)  # task id -> (time, details)


def forget_task(task_id) -> None:
    """Read the task again next time (time was just logged to it, so its time worked changed)."""
    _task_cache.pop(str(task_id), None)


def task_details(ids: list, settings: dict, reasons: Optional[dict] = None, transient: Optional[set] = None) -> dict:
    """Each task's estimate, time worked by everyone, and what is left, as Productive has them (cached 5 minutes).
    When none could be given because of the connection (the token refused or expired, Productive unreachable or down),
    it raises ProductiveError saying so, rather than answer as if the tasks did not exist. `reasons`, if given,
    gets the reason for each task that could not be read; `transient`, the ids that failed because of the connection
    (asking again later may work), unlike a task that is deleted or not visible."""
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
                    return tid, None, (f"Productive could not answer ({why(status, doc)})" if down else
                                       f"HTTP {status}: deleted, or not visible to you"), down
                d = doc.get("data") or {}
                proj = _index(doc).get(("projects", _rel(d, "project") or ""))
                return tid, {"taskId": tid, "title": _attr(d, "title") or "", "number": str(_attr(d, "task_number") or ""),
                             "project": _attr(proj, "name") or "", "projectId": _rel(d, "project") or "",
                             "serviceId": _rel(d, "service") or "",
                             "estimate": _attr(d, "initial_estimate"), "worked": _attr(d, "worked_time"),
                             "remaining": _attr(d, "remaining_time"), "closed": bool(_attr(d, "closed_at"))}, "", False
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
    if transient is not None:
        transient.update(tid for tid, (_, connection_problem) in failed.items() if connection_problem)
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
        # The filters are checked again below on what comes back: their REST spellings are not yet tried against
        # Productive, and one it ignored would otherwise offer closed tasks, or every task, in the pickers.
        words = next((str(c["value"]).lower() for c in conditions if c["field"] == "title"), None)
        categories = next(({str(v) for v in c["value"]} for c in conditions if c["field"] == "workflow_status_category"), None)
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
                closed = bool(_attr(d, "closed_at"))
                category = str(_attr(status, "category_id") or ("3" if closed else ""))  # 3: closed
                if (categories and category and category not in categories) or \
                        (words and words not in (_attr(d, "title") or "").lower()):
                    continue
                out.append(_task_item(str(d.get("id")), (_attr(d, "title") or "") + (" (closed)" if closed else ""),
                                      _attr(project, "name") or "",
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


def recent_tasks(since: str, settings: dict, limit: int = 2000) -> list:
    """Tasks anyone worked on since `since` (YYYY-MM-DD), in the projects you can see, open or closed, the latest
    activity first: [{taskId, title, number, project, projectId, created, closed, assignee}]. For the tasks you have
    never logged to (learn.new_tasks). Read only."""
    conn = _need(settings)
    out: list = []

    def item(tid, title, number, project, project_id, created, closed, assignee):
        return {"taskId": str(tid), "title": title or "", "number": str(number or ""), "project": project or "",
                "projectId": str(project_id or ""), "created": created or "", "closed": closed or "",
                "assignee": str(assignee or "")}

    if conn["kind"] == "token":
        # REST: newest activity first, stopped at `since` here (the spelling of a date filter is not yet tried)
        for page in range(1, 1 + (limit + 199) // 200):
            status, doc = rest_get("/tasks", {"include": "project", "sort": "-last_activity_at", "page[size]": 200,
                                              "page[number]": page}, conn["token"], settings.get("org_id"))
            if status != 200 or not isinstance(doc, dict):
                raise ProductiveError(f"Productive did not list tasks ({why(status, doc)}).")
            inc = _index(doc)
            rows = doc.get("data") or []
            for d in rows:
                if str(_attr(d, "last_activity_at") or "")[:10] < since:
                    return out
                project = inc.get(("projects", _rel(d, "project") or ""))
                out.append(item(d.get("id"), _attr(d, "title"), _attr(d, "task_number"), _attr(project, "name"),
                                _rel(d, "project"), _attr(d, "created_at"), _attr(d, "closed_at"), _rel(d, "assignee")))
            if len(rows) < 200 or len(out) >= limit:
                return out[:limit]
        return out[:limit]
    for offset in range(0, limit, 200):
        size = min(200, limit - offset)
        d = parse_mcp(mcp_text("query_resources", {
            "resource_type": "tasks", "limit": size, "offset": offset, "sort": ["-last_activity_at"],
            "filters": {"op": "and", "conditions": [{"field": "last_activity_at", "operator": "gt_eq", "value": since}]},
            "include_fields": ["title", "task_number", "project", "project.name", "created_at", "closed_at", "assignee"]},
            conn["token"]))
        cols = d["items"]["columns"]
        inc = d.get("included") or {}
        related = {f"{r[0]}:{r[1]}": dict(zip(inc.get("columns", []), r)) for r in inc.get("rows", [])}
        rows = d["items"]["rows"]
        for row in rows:
            r = dict(zip(cols, row))
            ref = lambda k: (r.get(k) or ":").split(":")[1]  # noqa: E731
            out.append(item(r.get("id"), r.get("title"), r.get("task_number"),
                            (related.get(r.get("project") or "") or {}).get("name"), ref("project"), r.get("created_at"),
                            r.get("closed_at"), ref("assignee")))
        if len(rows) < size:
            break
    return out


ACTIVITY_PAGES = 3  # of 200: a person's own activity over three days fits one
# activity about time itself (an entry logged, a timer, a booking): the self-check reads this to test a rule blind, so
# the entries of the day it plans must never come back through it
_TIME_KINDS = re.compile(r"time|timer|booking", re.I)


def _activity_task(r: dict) -> Optional[str]:
    """The task an activity is about: the task itself, or the task its comment, to-do or file is on. None for one
    about time (an entry, a timer, a booking)."""
    if any(_TIME_KINDS.search(str(r.get(k) or "")) for k in ("item_type", "parent_type", "root_type")):
        return None
    if r.get("task_id"):
        return str(r["task_id"])
    for kind, ref in (("item_type", "item_id"), ("parent_type", "parent_id"), ("root_type", "root_id")):
        if str(r.get(kind) or "").lower() == "task" and r.get(ref):
            return str(r[ref])
    return None


def my_activity(d0: str, d1: str, settings: dict) -> list:
    """What you did on tasks in Productive from d0 to d1 (YYYY-MM-DD, a day either side read): the tasks you created,
    changed or commented on, with the to-dos and files you added, [{at: epoch, task: id}]; never time logged. Only
    your own (the creator is checked here too, whatever the filter did) and read only: one request a day
    (ACTIVITY_PAGES at most). Used by the self-check alone (selfcheck.py: dayplan's TOUCH rule, tried, never applied)."""
    conn = _need(settings)
    person = str(settings.get("person_id") or "")
    if not person.isdigit():  # a filter on "" could match anyone's activity
        raise ProductiveError("Productive did not say who you are, so no activity was read: open Settings.")
    after, before = day_shift(d0, -1), day_shift(d1, 1)
    out: list = []
    for page in range(ACTIVITY_PAGES):
        if conn["kind"] == "token":
            status, doc = rest_get("/activities", {"filter[creator_id]": person, "filter[after]": after,
                                                   "filter[before]": before, "page[size]": 200, "page[number]": page + 1},
                                   conn["token"], settings.get("org_id"))
            if status != 200 or not isinstance(doc, dict):
                raise ProductiveError(f"Productive did not list your activity ({why(status, doc)}).")
            rows = [{**(d.get("attributes") or {}), "creator": _rel(d, "creator")}
                    for d in doc.get("data") or [] if isinstance(d, dict)]
        else:
            d = parse_mcp(mcp_text("query_resources", {
                "resource_type": "activities", "limit": 200, "offset": page * 200,
                "filters": {"op": "and", "conditions": [{"field": "creator", "operator": "eq", "value": person},
                                                        {"field": "after", "operator": "gt_eq", "value": after},
                                                        {"field": "before", "operator": "lt_eq", "value": before}]},
                "include_fields": ["created_at", "item_type", "item_id", "parent_type", "parent_id", "root_type",
                                   "root_id", "task_id", "creator"]}, conn["token"]))
            if not isinstance(d.get("items"), dict):
                raise ProductiveError("Productive answered without a list of your activity.")
            cols = d["items"]["columns"]
            rows = [dict(zip(cols, r)) for r in d["items"]["rows"]]
            for r in rows:
                r["creator"] = str(r.get("creator") or "").split(":")[-1] or None
        for r in rows:
            task, at = _activity_task(r), epoch(r.get("created_at"))
            if task and at is not None and str(r.get("creator") or "") == person:
                out.append({"at": at, "task": task})
        if len(rows) < 200:
            break
    return sorted(out, key=lambda x: x["at"])


SEARCH_WORD = re.compile(r"[^\W_]+")


def search_words(text: str) -> list:
    """The words of a search, longest first, as typed: dashes, "#", "/" and the like are not words, and a single letter
    is too short to narrow anything (a number is kept)."""
    words = dict.fromkeys(w for w in SEARCH_WORD.findall(str(text or "")) if len(w) >= 2 or w.isdigit())
    return sorted(words, key=len, reverse=True)


def has_words(words: list, *texts) -> bool:
    """Whether every word is somewhere in the texts, whole or in part, in any order and any case."""
    hay = " ".join(SEARCH_WORD.findall(" ".join(str(t or "") for t in texts).lower()))
    return all(w.lower() in hay for w in words)


def search_tasks(q: str, settings: dict) -> list:
    """The tasks whose title, or title and project, hold every word of `q`, in any order and in part: "drupal shop" and
    "upgr SHOP" find "Drupal 11 – DB Upgrade – Shop" (2026-11-12: only the exact run of characters was found).
    Productive filters a title on one piece of text, so it is asked for the longest word, and the rest are checked here;
    when that word was the project's, the next longest is tried (three at most)."""
    words = search_words(q)
    if len(words) < 2:
        return _task_rows([{"field": "title", "operator": "contains", "value": words[0] if words else q.strip()}],
                          settings, limit=25)
    for word in words[:3]:
        rows = _task_rows([{"field": "title", "operator": "contains", "value": word}], settings, limit=200)
        hits = [t for t in rows if has_words(words, t.get("title"), t.get("project"))]
        if hits:
            return hits[:25]
    return []


def bookable_services(task_id: str, date: str, settings: dict, names: Optional[Names] = None) -> dict:
    """The services of the task's project that take time on that date (a budget that has ended does not)."""
    reasons: dict = {}
    task = task_details([task_id], settings, reasons).get(task_id)  # raises when the connection is the reason
    if not task or not task.get("projectId"):
        reason = reasons.get(task_id) or ("it has no project" if task else "")
        raise ProductiveError("That task's services could not be read" + (f" ({reason})." if reason else "."))
    return {"task": task, "services": project_services(task["projectId"], date, settings, names)}


def project_services(project_id: str, date: str, settings: dict, names: Optional[Names] = None) -> list:
    """The services of a project that take time on that date, [{id, name, budget, billable…}]. Read only."""
    if not str(project_id or "").isdigit():
        raise ProductiveError("no project")
    conn = _need(settings)
    names = names or shared_names()
    if conn["kind"] == "token":
        status, doc = rest_get("/services", {"filter[project_id]": str(project_id), "filter[bookable_date]": date,
                                             "include": "deal", "page[size]": 200}, conn["token"], settings.get("org_id"))
        if status != 200 or not isinstance(doc, dict):
            raise ProductiveError(f"Productive did not list the services ({why(status, doc)}).")
        inc = _index(doc)
        services = [{"id": str(d.get("id")), "name": _attr(d, "name") or "",
                     "budget": _attr(inc.get(("deals", _rel(d, "deal") or "")), "name") or "",
                     **billable_of(_attr(d, "billing_type")),
                     **budget_time(_attr(d, "budgeted_time"), _attr(d, "worked_time"))} for d in doc.get("data") or []]
    else:
        d = parse_mcp(mcp_text("query_resources", {
            "resource_type": "services", "limit": 100,
            "include_fields": ["name", "deal", "deal.name", "budgeted_time", "worked_time"],
            "filters": {"op": "and", "conditions": [{"field": "project", "operator": "eq", "value": str(project_id)},
                                                    {"field": "bookable_date", "operator": "eq", "value": date}]}}, conn["token"]))
        cols, inc = d["items"]["columns"], d.get("included") or {}
        deals = {f"{r[0]}:{r[1]}": dict(zip(inc.get("columns", []), r)).get("name") for r in inc.get("rows", [])}
        rows = [dict(zip(cols, r)) for r in d["items"]["rows"]]
        names.set_many({f"deal:services:{r['id']}": (r.get("deal") or ":").split(":")[1] for r in rows if r.get("deal")})
        _mcp_billing([f"services:{r['id']}" for r in rows], conn["token"], names)
        services = [{"id": str(r["id"]), "name": r.get("name") or "", "budget": deals.get(r.get("deal") or "") or "",
                     **billable_of(names.get(f"billing:services:{r['id']}")),
                     **budget_time(r.get("budgeted_time"), r.get("worked_time"))} for r in rows]
    return services


def budget_time(budgeted, worked) -> dict:
    """{"budgetTime": {"budgeted", "worked"}} in minutes, for a service with a time budget whose fields this person may
    read; {} otherwise. Productive leaves both empty for a role without "View budgets where a user is a member" (Staff),
    so most people see nothing here."""
    def minutes(v):
        try:
            return None if v is None or isinstance(v, bool) else float(v)
        except (TypeError, ValueError):
            return None
    b, w = minutes(budgeted), minutes(worked)
    return {"budgetTime": {"budgeted": b, "worked": w}} if b and b > 0 and w is not None and w >= 0 else {}


ESTIMATE_SAMPLE = 100  # a project's most recently active tasks, read to tell whether it uses estimates


def project_estimates(project_id: str, settings: dict) -> dict:
    """{"n": tasks read, "with": ids of those with an estimate, "sizes": [[minutes, 1 for a subtask else 0, title]] of
    those}: the project's most recently active tasks (its first ones through the token, which has no such order), for
    the flag "no estimate, while others in the project have one" (flags.py) and the estimate offered for a new task
    (newtask.propose). Read only; raises ProductiveError when it cannot be read."""
    if not str(project_id or "").isdigit():
        raise ProductiveError("no project")
    conn = _need(settings)
    if conn["kind"] == "token":
        status, doc = rest_get("/tasks", {"filter[project_id]": project_id, "page[size]": ESTIMATE_SAMPLE},
                               conn["token"], settings.get("org_id"))
        if status != 200 or not isinstance(doc, dict):
            raise ProductiveError(f"Productive did not list the project's tasks ({why(status, doc)}).")
        rows = [(str(d.get("id")), _attr(d, "initial_estimate"), bool(_rel(d, "parent_task")), _attr(d, "title"))
                for d in doc.get("data") or [] if isinstance(d, dict)]
    else:
        d = parse_mcp(mcp_text("query_resources", {
            "resource_type": "tasks", "limit": ESTIMATE_SAMPLE, "sort": ["-last_activity_at"],
            "include_fields": ["initial_estimate", "title", "parent_task"],
            "filters": {"op": "and", "conditions": [{"field": "project", "operator": "eq", "value": str(project_id)}]}},
            conn["token"]))
        cols = d["items"]["columns"]
        rows = [(str(r.get("id")), r.get("initial_estimate"), bool(r.get("parent_task")), r.get("title"))
                for r in (dict(zip(cols, x)) for x in d["items"]["rows"])]
    rows = [(i, minutes_of(est) if est not in (None, "") else 0, sub, title) for i, est, sub, title in rows]
    return {"n": len(rows), "with": [i for i, m, _, _ in rows if m > 0],
            "sizes": [[round(m), int(sub), str(title or "")[:80]] for _, m, sub, title in rows if m > 0]}


def workflow_statuses(project_id: str, settings: dict) -> list:
    """The statuses a task of the project can take, in the workflow's order: [{id, name, category, position}], the
    category "1" not started, "2" started, "3" closed. Two reads: the project's workflow, then its statuses. Read
    only; raises ProductiveError when they cannot be read."""
    if not str(project_id or "").isdigit():
        raise ProductiveError("Pick a project.")
    conn = _need(settings)
    if conn["kind"] == "token":  # REST spellings not yet tried against Productive
        status, doc = rest_get(f"/projects/{int(project_id)}", {}, conn["token"], settings.get("org_id"))
        if status != 200 or not isinstance(doc, dict):
            raise ProductiveError(f"Productive did not return the project ({why(status, doc)}).")
        workflow = _rel(doc.get("data") or {}, "workflow") or ""
        if not workflow.isdigit():
            raise ProductiveError("Productive did not say which workflow the project uses.")
        status, doc = rest_get("/workflow_statuses", {"filter[workflow_id]": workflow, "page[size]": 200},
                               conn["token"], settings.get("org_id"))
        if status != 200 or not isinstance(doc, dict):
            raise ProductiveError(f"Productive did not list the workflow's statuses ({why(status, doc)}).")
        rows = [(d.get("id"), _attr(d, "name"), _attr(d, "category_id"), _attr(d, "position"))
                for d in doc.get("data") or [] if isinstance(d, dict)]
    else:
        item = parse_mcp(mcp_text("load_resource", {"resource_type": "projects", "id": str(project_id),
                                                    "include_fields": ["workflow"]}, conn["token"])).get("item") or {}
        ref = (item.get("fields") or {}).get("workflow")
        workflow = str((ref.get("id") if isinstance(ref, dict) else _id_of(ref)) or "")
        if not workflow.isdigit():
            raise ProductiveError("Productive did not say which workflow the project uses.")
        found, _ = _mcp_items(parse_mcp(mcp_text("query_resources", {
            "resource_type": "workflow_statuses", "limit": 200, "include_fields": ["name", "position", "workflow_status_category"],
            "filters": {"op": "and", "conditions": [{"field": "workflow", "operator": "eq", "value": workflow}]}},
            conn["token"])))
        rows = [(r.get("id"), r.get("name"), r.get("workflow_status_category"), r.get("position")) for r in found]
    out = []
    for sid, name, category, position in rows:  # the MCP gives the category's label, REST its number
        category = {"not started": "1", "started": "2", "closed": "3"}.get(str(category or "").strip().lower(), str(category or ""))
        if str(sid).isdigit() and name and category in ("1", "2", "3"):
            out.append({"id": str(sid), "name": str(name), "category": category,
                        "position": position if isinstance(position, (int, float)) and not isinstance(position, bool) else 0})
    return sorted(out, key=lambda x: (x["category"], x["position"]))


def _write_conn(settings: dict) -> dict:
    if config.writes_blocked():  # tests, demos, and a preview not allowed to write: nothing may reach Productive
        raise ProductiveError(config.writes_blocked())
    return _need(settings)


# Productive's refusals that people meet, said in plain words (the code stays at the end, for Logs and support)
REFUSALS = {
    "budget_limit_overrun": "the budget behind this service is full: Productive takes no more time on it. Pick another "
                            "service, or ask whoever runs the budget to raise its limit",
    "service_not_accessible": "this service does not take your time (it is closed, or you are not on it): pick another "
                              "service",
}


def plain_refusal(text: str, what: str = "the entry", status: int | None = None) -> str:
    """Productive's refusal as a sentence the person can act on, when its code is a known one; else its own words.
    A JSON answer (the API token's) gives its words, the detail or title of each error, never the JSON itself, which
    goes to the error log (2026-11-12: '(HTTP 422): {"errors":[…' in a message); its code and the HTTP status stay in
    parentheses, for Claude Code (mcptools.error_out) and for whoever reads the log."""
    raw = str(text or "")
    for code, said in REFUSALS.items():
        if code in raw:
            return f"Productive refused {what}: {said} ({code})."
    try:
        errs = json.loads(raw).get("errors")
    except (ValueError, AttributeError):
        errs = None
    if not isinstance(errs, list):
        return f"Productive refused {what}" + (f" ({why(status, None)})" if status else "") + f": {raw[:300]}"
    from . import logbook  # imported here, as elsewhere in this module
    errs = [e for e in errs if isinstance(e, dict)]
    words = []
    for e in errs:
        w = " ".join(str(e.get("detail") or e.get("title") or "").split()).rstrip(".")
        if w and w not in words:
            words.append(w)
    codes = [str(e["code"]) for e in errs if re.fullmatch(r"[a-z][a-z0-9_]{2,40}", str(e.get("code") or ""))]
    tag = ", ".join(codes[:1] + ([why(status, None)] if status else []))
    logbook.record("productive", f"Productive refused {what}" + (f" ({why(status, None)})" if status else "") + ".",
                   kind="warning", detail=raw[:2000])
    return f"Productive refused {what}: {'; '.join(words)[:200] or 'it gave no reason'}" + (f" ({tag})." if tag else ".")


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
        raise ProductiveError(plain_refusal(raw[:20000].decode("utf-8", "replace"), what, status))


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
    """Writes one time entry, as the person in Settings, from server.api_log: the log dialog's button, Log the day's
    confirm step, or pulsar_log from Claude Code after its preview (mcptools). Only an answer that names the new entry
    is "created"; any other after sending is ProductiveUnclear (it may exist), never a refusal."""
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
            raise ProductiveError(plain_refusal(raw[:20000].decode("utf-8", "replace"), "the entry", status))
        try:  # created (2xx): only an answer that names the new entry counts as logged
            new_id = str((((json.loads(raw or b"{}") or {}).get("data") or {}).get("id")) or "")
        except (ValueError, AttributeError):
            new_id = ""
        if not new_id.isdigit():
            raise ProductiveUnclear(f"Productive answered HTTP {status} without the new entry in it, so the entry may or "
                                    "may not exist. Check the Logged column (it has been refreshed) before logging again.")
        return {"id": new_id}
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


# ---- a new task: the log dialog's "Create a task" (newtask.py) ------------------------------------

def _mcp_items(doc: dict) -> Tuple[list, dict]:
    """A query's rows as dicts, and its included records by "type:id"."""
    cols = doc["items"]["columns"]
    inc = doc.get("included") or {}
    related = {f"{r[0]}:{r[1]}": dict(zip(inc.get("columns", []), r)) for r in inc.get("rows", [])}
    return [dict(zip(cols, r)) for r in doc["items"]["rows"]], related


def _id_of(ref) -> str:
    """"projects:12" (the MCP's table form) -> "12"; "" for none."""
    return str(ref).split(":")[-1] if ref else ""


def active_projects(settings: dict) -> list:
    """The active projects you can see, [{id, name}], by name (Productive shows only those your role may see). Read
    only."""
    conn = _need(settings)
    out: list = []
    if conn["kind"] == "token":
        for page in range(1, 6):  # the status filter is not yet tried here: archived ones are left out below too
            status, doc = rest_get("/projects", {"filter[status]": 1, "page[size]": 200, "page[number]": page},
                                   conn["token"], settings.get("org_id"))
            if status != 200 or not isinstance(doc, dict):
                raise ProductiveError(f"Productive did not list the projects ({why(status, doc)}).")
            out += [{"id": str(d.get("id")), "name": _attr(d, "name") or ""} for d in doc.get("data") or []
                    if isinstance(d, dict) and not _attr(d, "archived_at")]
            pages = (doc.get("meta") or {}).get("total_pages")
            if not isinstance(pages, int) or page >= pages:
                break
    else:
        for offset in range(0, 1000, 200):
            rows, _ = _mcp_items(parse_mcp(mcp_text("query_resources", {
                "resource_type": "projects", "limit": 200, "offset": offset, "sort": ["name"],
                "filters": {"op": "and", "conditions": [{"field": "status", "operator": "eq", "value": "1"}]},
                "include_fields": ["name"]}, conn["token"])))
            out += [{"id": str(r.get("id")), "name": r.get("name") or ""} for r in rows]
            if len(rows) < 200:
                break
    return sorted((p for p in out if p["id"].isdigit() and p["name"]), key=lambda p: p["name"].lower())


def project_places(project_id: str, settings: dict) -> dict:
    """Where a new task can go in a project, read only: {"tasks": its open top-level tasks, newest first, [{taskId,
    title, list, assignee, created}], "lists": its active task lists [{id, name}]}. newtask.places chooses from them."""
    if not str(project_id or "").isdigit():
        raise ProductiveError("Pick a project.")
    conn = _need(settings)
    if conn["kind"] == "token":  # REST spellings of these filters are not yet tried: the rows are checked again below
        status, doc = rest_get("/tasks", {"filter[project_id]": project_id, "filter[status]": 1, "sort": "-created_at",
                                          "page[size]": 200}, conn["token"], settings.get("org_id"))
        if status != 200 or not isinstance(doc, dict):
            raise ProductiveError(f"Productive did not list the project's tasks ({why(status, doc)}).")
        tasks = [{"taskId": str(d.get("id")), "title": _attr(d, "title") or "", "list": _rel(d, "task_list") or "",
                  "assignee": _rel(d, "assignee") or "", "created": str(_attr(d, "created_at") or "")}
                 for d in doc.get("data") or [] if isinstance(d, dict) and not _attr(d, "closed_at")
                 and not _rel(d, "parent_task") and _rel(d, "project") in (None, str(project_id))]
        status, doc = rest_get("/task_lists", {"filter[project_id]": project_id, "filter[status]": 1, "page[size]": 200},
                               conn["token"], settings.get("org_id"))
        if status != 200 or not isinstance(doc, dict):
            raise ProductiveError(f"Productive did not list the project's task lists ({why(status, doc)}).")
        lists = [{"id": str(d.get("id")), "name": _attr(d, "name") or ""} for d in doc.get("data") or []
                 if isinstance(d, dict) and not _attr(d, "archived_at")]
    else:
        rows, _ = _mcp_items(parse_mcp(mcp_text("query_resources", {
            "resource_type": "tasks", "limit": 200, "sort": ["-created_at"],
            "filters": {"op": "and", "conditions": [{"field": "project", "operator": "eq", "value": str(project_id)},
                                                    {"field": "task_type", "operator": "eq", "value": "1"},
                                                    {"field": "workflow_status_category", "operator": "not_eq", "value": "3"}]},
            "include_fields": ["title", "task_list", "assignee", "created_at"]}, conn["token"])))
        tasks = [{"taskId": str(r.get("id")), "title": r.get("title") or "", "list": _id_of(r.get("task_list")),
                  "assignee": _id_of(r.get("assignee")), "created": str(r.get("created_at") or "")} for r in rows]
        rows, _ = _mcp_items(parse_mcp(mcp_text("query_resources", {
            "resource_type": "task_lists", "limit": 200,
            "filters": {"op": "and", "conditions": [{"field": "project", "operator": "eq", "value": str(project_id)},
                                                    {"field": "status", "operator": "eq", "value": "1"}]},
            "include_fields": ["name"]}, conn["token"])))
        lists = [{"id": str(r.get("id")), "name": r.get("name") or ""} for r in rows]
    return {"tasks": [t for t in tasks if t["taskId"].isdigit()], "lists": [x for x in lists if x["id"].isdigit()]}


# Productive's refusals of a new task, in words the person can act on: by its codes and status, not by any English
# phrase ("Assignee is not allowed", "not allowed under a closed task" are other reasons)
TASK_REFUSALS = (
    (re.compile(r"invalid level of subtasks", re.I),
     "a subtask cannot have subtasks of its own. Put it under another task, or in a task list"),
    (re.compile(r"task_list is required", re.I), "this project needs a task list for it: pick one"),
    (re.compile(r"\b403\b|\bforbidden\b", re.I),
     "your role may not add tasks to this project. Ask its PM, or pick another project"),
)


def task_refusal(text: str) -> str:
    """The refusal in plain words when it is a known one, followed by Productive's own words: the page and Logs keep
    the original, which may say more (a read-only token, a member missing from the project)."""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    for rx, said in TASK_REFUSALS:
        if rx.search(raw):
            return f"Productive did not create the task: {said}. Productive said: {raw[:300]}"
    return f"Productive did not create the task: {raw[:300]}"


def _description_html(text: str) -> str:
    """The description for the REST API, which keeps rich text as HTML: "- " lines as a list, other lines as
    paragraphs, everything escaped."""
    import html
    out, items = [], []
    for line in str(text or "").splitlines():
        line = line.strip()
        bullet = re.match(r"[-*•]\s+", line)
        if bullet:
            items.append("<li>" + html.escape(line[bullet.end():]) + "</li>")
            continue
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items = []
        if line:
            out.append(f"<p>{html.escape(line)}</p>")
    if items:
        out.append("<ul>" + "".join(items) + "</ul>")
    return "".join(out)


TASK_UNCLEAR = ("No clear answer from Productive ({}), so the task may or may not exist. Search for it in the Log "
                "dialog's task search before creating it again.")


ESTIMATE_MAX = 500 * 60  # minutes: the largest estimate a new task may be given (500 h)


def create_task(task: dict, settings: dict) -> dict:
    """Creates one task, assigned to you: its title, description and project, and either a parent task (a subtask, as
    FI puts each issue under the period's contingency task), a task list, or neither (directly in the project); and,
    when given, its workflow status (else Productive puts it in the workflow's first) and its estimate in minutes.
    Never a service: a developer's role may not set it, a PM does (fi-productive-process; HOW-IT-WORKS 4.2). Called
    only from an explicit click in the page. {"taskId"}."""
    conn = _write_conn(settings)
    person = str(settings.get("person_id") or "")
    if not person.isdigit():
        raise ProductiveError("Pulsar does not know your Productive person ID yet, so nothing was created. Open "
                              "Settings: it fills in by itself once Productive answers, or type it there.")
    project, parent, task_list, status_id = (str(task.get(k) or "") for k in ("project_id", "parent_id", "task_list_id",
                                                                              "status_id"))
    if not project.isdigit() or (parent and not parent.isdigit()) or (task_list and not task_list.isdigit()):
        raise ProductiveError("Pick a project, and where in it the task goes.")
    estimate = task.get("estimate")
    if (status_id and not (status_id.isascii() and status_id.isdigit())) or (estimate is not None and (
            isinstance(estimate, bool) or not isinstance(estimate, int) or not 1 <= estimate <= ESTIMATE_MAX)):
        raise ProductiveError("The status or the estimate is not one Productive takes, so nothing was created.")
    title, description = str(task.get("title") or "").strip(), str(task.get("description") or "").strip()
    if conn["kind"] == "token":
        rels = {"project": {"data": {"type": "projects", "id": project}},
                "assignee": {"data": {"type": "people", "id": person}}}
        if parent:
            rels["parent_task"] = {"data": {"type": "tasks", "id": parent}}
        if task_list:
            rels["task_list"] = {"data": {"type": "task_lists", "id": task_list}}
        if status_id:
            rels["workflow_status"] = {"data": {"type": "workflow_statuses", "id": status_id}}
        attrs = {"title": title, **({"description": _description_html(description)} if description else {}),
                 **({"initial_estimate": estimate} if estimate else {})}  # minutes, as REST keeps it
        body = {"data": {"type": "tasks", "attributes": attrs, "relationships": rels}}
        try:
            status, raw = https("POST", API + "/tasks", json.dumps(body).encode("utf-8"), _rest_headers(conn, settings),
                                timeout=30, write=True)
        except (OSError, http.client.HTTPException) as exc:
            raise ProductiveUnclear(TASK_UNCLEAR.format(exc))
        if status >= 500:  # a server error after the task was sent: it may exist
            raise ProductiveUnclear(TASK_UNCLEAR.format(f"HTTP {status}"))
        if status not in (200, 201):
            raise ProductiveError(task_refusal(f"HTTP {status} " + raw[:600].decode("utf-8", "replace")))
        try:
            tid = str(((json.loads(raw or b"{}") or {}).get("data") or {}).get("id") or "")
        except ValueError:
            tid = ""
        if not tid.isdigit():
            raise ProductiveUnclear(TASK_UNCLEAR.format("no task in the answer"))
        return {"taskId": tid}
    # The MCP's own spelling, as Claude Code sessions create tasks: bare ids, and task_list null under a parent
    # (without it Productive answers "task_list is required (unless task_list=null)")
    item = {"title": title, "project": project, "task_list": task_list or None, "assignee": person}
    if parent:
        item["parent_task"] = parent
    if description:
        item["description"] = description
    if status_id:
        item["workflow_status"] = status_id
    if estimate:
        item["initial_estimate"] = f"{estimate} min"  # the MCP's time format, as for a time entry
    try:
        text = mcp_text("create_resource", {"resource_type": "tasks", "items": [item]}, conn["token"])
    except ProductiveUnclear as exc:  # a server error or a timeout behind the MCP: it may exist
        raise ProductiveUnclear(TASK_UNCLEAR.format(str(exc).split("Productive said: ")[-1][:200]))
    except ProductiveError as exc:  # the tool said no (isError): nothing was created
        raise ProductiveError(task_refusal(str(exc).replace("Productive refused it: ", "")))
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise ProductiveUnclear(TASK_UNCLEAR.format(exc))
    # only Productive's success sentence counts: "task created (ID: 12345670)." (one), "Created 2 tasks (ID: …)"
    ids = re.findall(r"(?:\btask created|\bcreated 1 tasks?) \(IDs?:\s*(\d+)", text, re.I)
    if ids:
        return {"taskId": ids[0]}
    if re.match(r"\s*(?:Failed to create|Item 1: Invalid)", text) and not UPSTREAM.search(text):  # a refusal in plain
        raise ProductiveError(task_refusal(text))  # text: nothing was created
    raise ProductiveUnclear("Productive's answer was unclear, so the task may or may not exist. Search for it in the "
                           "Log dialog's task search before creating it again. Productive said: " + text[:200])
