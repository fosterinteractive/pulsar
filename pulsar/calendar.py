"""Your meetings for a day, from your Google Calendar, through Google's own sign-in. Read only.

Connecting (OAuth 2.0 for an installed app, with PKCE):
1. Settings > Connect Google Calendar: start() makes a `state` (random, used once, valid 10 minutes) and a PKCE pair,
   and gives Google's sign-in address. The browser opens it; the person signs in and clicks Allow.
2. Google sends that browser back to this computer: http://127.0.0.1:<port>/oauth/google?code=…&state=…. finish()
   does nothing without a state it handed out and has not used; with one, it trades the code and the PKCE verifier
   for tokens at Google.
3. The refresh token goes to the keychain (config.set_google_token). The access token stays in memory and is renewed
   when it runs out. Disconnect revokes the token at Google and deletes it here. In Pulsar Preview it only deletes it
   here: Preview signs in with Pulsar's own Google client, and revoking at Google withdraws that client's access for
   the account, Pulsar's included.

The only scope asked for is calendar.events.readonly: it reads events and nothing else, and events.list on the primary
calendar accepts it. The account shown is the primary calendar's name, which Google sets to its owner's email.

The Google Cloud client (a "Desktop app" client under an "Internal" consent screen) is read from google_client.json
next to this file: the JSON Google Cloud Console downloads for it. Google treats a desktop client's secret as public,
and "Internal" lets only the organization's own accounts sign in. While the file holds placeholders, Pulsar says the
connection is not set up yet.

day() gives one item per event: {s, e, title, all_day, declined, recurring, uid, external, note, url?, link?}, with
`external` true when someone outside the person's own company (their Google account's domain) organized it, and `note`
the title as a log entry's note, or "" (note_of). s and e are in minutes from the day's midnight on the wall clock,
cut to the day (an event across midnight shows on both days). `url` is the link to join (Meet, Zoom, Teams, Webex),
`link` the event in Google Calendar. Google expands repeating events itself
(singleEvents). Working-location, out-of-office and birthday entries are left out: they are not meetings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from . import __version__, config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
CALLBACK = "/oauth/google"
CLIENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "google_client.json")
STATE_TTL = 600   # seconds a sign-in may take, from the click in Settings to Allow
FRESH = 300       # seconds a day's meetings are kept before Google is asked again (the ↻ button asks at once)
# what Google sends of each event: never its description or the guests (attendees: only "me", maxAttendees=1); the
# organizer's address only to tell an event of the person's own company from one anyone outside sent (`outside`)
FIELDS = ("summary,nextPageToken,items(id,status,summary,start,end,eventType,htmlLink,hangoutLink,recurringEventId,"
          "location,organizer(email,self),attendees(self,responseStatus),conferenceData(entryPoints(entryPointType,uri)))")
# addresses anyone can have: they name no company, so only the person's own events count as theirs there
PUBLIC_MAIL = {"gmail.com", "googlemail.com"}
NOT_MEETINGS = {"workingLocation", "outOfOffice", "birthday"}
JOIN = re.compile(r"https://(?:[A-Za-z0-9-]+\.)*(?:zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|"
                  r"webex\.com)(?:/[^\s\"'<>\\]*)?", re.I)

NOT_SET_UP = ("Google Calendar is not set up in this copy of Pulsar yet: it needs the organization's Google sign-in "
              "client first (pulsar/google_client.json).")
LAPSED = ("Google no longer lets Pulsar read your calendar (the access was removed or has lapsed). Connect Google "
          "Calendar again in Settings.")

open_browser = webbrowser.open  # replaced in tests


class CalendarError(Exception):
    """A calendar that could not be read, in words for the page. Never carries a token or a code."""


# ---- HTTPS to Google -----------------------------------------------------------------------------

_CTX = ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a token only ever goes to the address it was meant for


def http(method: str, url: str, form: Optional[dict] = None, headers: Optional[dict] = None,
         timeout: float = 30) -> Tuple[int, dict]:
    """One request to Google: (status, the JSON answer or {}). Status 0 when nothing answered, with the reason in
    words under "error". Every address is one of the constants above."""
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    head = {"User-Agent": f"Pulsar/{__version__}", "Accept": "application/json", **(headers or {})}
    if body is not None:
        head["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, method=method, headers=head)
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_CTX))
    try:
        with opener.open(request, timeout=timeout) as resp:
            status, raw = resp.status, resp.read(5_000_000)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, (exc.read(100_000) if exc.fp else b"")
    except Exception as exc:  # never the exception's own text: it may quote the request
        return 0, {"error": _net_reason(exc)}
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    return status, doc if isinstance(doc, dict) else {}


def _net_reason(exc: BaseException) -> str:
    import socket
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return "Google could not be looked up: offline, or a DNS problem"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "Google took too long to answer"
    if isinstance(reason, ssl.SSLError):
        return "the secure connection to Google failed"
    return f"no answer from Google ({type(reason).__name__})"


def _google_reason(status: int, doc: dict) -> str:
    """Why Google said no, in a few words: its error code and description, never anything sent to it."""
    if status == 0:
        return str(doc.get("error") or "no answer")
    err = doc.get("error")
    if isinstance(err, dict):  # the Calendar API: {"error": {"code", "message", "status", "errors": [{"reason"}]}}
        reasons = [str(e.get("reason")) for e in err.get("errors") or [] if isinstance(e, dict) and e.get("reason")]
        return f"HTTP {status}: " + (", ".join(reasons) or str(err.get("status") or "")).strip()
    if err:  # the token endpoint: {"error": "invalid_grant", "error_description": "..."}
        return f"HTTP {status}: {err}" + (f", {doc['error_description']}" if doc.get("error_description") else "")
    return f"HTTP {status}"


# ---- the client and the connection ------------------------------------------------------------------

def client() -> Optional[dict]:
    """{"id", "secret"} of the Google Cloud client, as Google Cloud Console downloads it ({"installed": {...}}): from
    ~/.config/pulsar/google_client.json (where the person who releases Pulsar keeps it, out of git; Pulsar Preview
    looks in its own folder, then there), else from the copy next to this file (the one a release ships). None while
    all are missing or hold placeholders."""
    places = dict.fromkeys([config.CONFIG_DIR, config.MAIN_CONFIG_DIR])  # one folder, or Preview's then Pulsar's
    for path in [os.path.join(d, "google_client.json") for d in places] + [CLIENT_FILE]:
        found = _client_in(path)
        if found:
            return found
    return None


def _client_in(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    c = doc.get("installed", doc) if isinstance(doc, dict) else {}
    cid = str((c or {}).get("client_id") or "") if isinstance(c, dict) else ""
    secret = str((c or {}).get("client_secret") or "") if isinstance(c, dict) else ""
    if not re.fullmatch(r"[0-9]{5,}-[A-Za-z0-9_]{6,}\.apps\.googleusercontent\.com", cid):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,200}", secret) or "REPLACE" in secret.upper():
        return None
    return {"id": cid, "secret": secret}


def status(settings: dict) -> dict:
    """For Settings and the Day view: whether the connection can be made, whether it is, and as whom."""
    configured = client() is not None
    connected = configured and bool(config.get_google_token())
    return {"configured": configured, "connected": connected,
            "account": (settings.get("calendar_account") or "") if connected else ""}


_lock = threading.Lock()
_pending: dict = {}  # state -> {"verifier", "redirect", "expires"}: sign-ins started and not yet finished


def start(port: int) -> str:
    """Google's sign-in address for a new connection. Its state and PKCE verifier stay here, for finish()."""
    c = client()
    if not c:
        raise CalendarError(NOT_SET_UP)
    verifier = secrets.token_urlsafe(64)  # 86 characters: PKCE wants 43 to 128
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    redirect = f"http://127.0.0.1:{int(port)}{CALLBACK}"
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if v["expires"] < now]:
            del _pending[k]
        while len(_pending) >= 5:  # a few clicks on Connect at most; the oldest goes
            del _pending[min(_pending, key=lambda k: _pending[k]["expires"])]
        _pending[state] = {"verifier": verifier, "redirect": redirect, "expires": now + STATE_TTL}
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": c["id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent"})  # offline + consent: Google gives a refresh token every time


def _take(state) -> Optional[dict]:
    """The sign-in this state belongs to, taken out so it works once; None for anything else or after 10 minutes."""
    if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{20,100}", state):
        return None
    now = time.time()
    with _lock:
        for k in list(_pending):
            if hmac.compare_digest(k, state):
                pending = _pending.pop(k)
                return pending if pending["expires"] >= now else None
    return None


def finish(query: dict) -> dict:
    """Google's answer at the callback: {"ok", "message", "known"} ("known": the state was one Pulsar handed out).
    The only thing it can do is finish the one sign-in that state belongs to."""
    pending = _take(query.get("state"))
    if pending is None:
        return {"ok": False, "known": False,
                "message": "Pulsar was not waiting for this answer: it was used already, the sign-in took more than "
                           "10 minutes, or it did not come from Pulsar. Start again from Pulsar's Settings."}
    if query.get("error"):
        refused = query["error"] == "access_denied"
        return {"ok": False, "known": True,
                "message": "Google Calendar was not connected: " + (
                    "you chose not to allow it." if refused else f"Google said “{str(query['error'])[:80]}”.")}
    code, c = str(query.get("code") or ""), client()
    if not code or len(code) > 2048 or not c:
        return {"ok": False, "known": True, "message": NOT_SET_UP if not c else "Google sent no code. Start again."}
    got, doc = http("POST", TOKEN_URL, {"code": code, "client_id": c["id"], "client_secret": c["secret"],
                                        "redirect_uri": pending["redirect"], "grant_type": "authorization_code",
                                        "code_verifier": pending["verifier"]})
    if got != 200 or not doc.get("access_token"):
        return {"ok": False, "known": True,
                "message": f"Google did not give Pulsar access ({_google_reason(got, doc)}). Start again from Settings."}
    if SCOPE not in str(doc.get("scope") or "").split():
        return {"ok": False, "known": True,
                "message": "Google Calendar was connected without permission to see your events (that box was left "
                           "unticked). Connect again and allow Pulsar to view your calendar events."}
    if not doc.get("refresh_token"):
        return {"ok": False, "known": True,
                "message": "Google gave Pulsar no lasting access. Connect again from Settings."}
    config.set_google_token(doc["refresh_token"])
    _keep_access(doc)
    forget()
    account, tz = "", config.tz(config.load())
    try:  # the primary calendar's name is its owner's email: one read of today says who is connected
        account = _account(_list(datetime.now(tz).strftime("%Y-%m-%d"), tz, _access["token"]).get("summary"))
    except (CalendarError, _Expired):
        pass  # connected all the same; the account shows after the first day is read
    config.update({"calendar_account": account})
    return {"ok": True, "known": True,
            "message": f"Pulsar can now read the events of {account or 'your Google Calendar'}. "
                       "You can close this tab and go back to Pulsar."}


def _account(summary) -> str:
    s = str(summary or "").strip()
    return s if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else ""


def disconnect(settings: dict) -> dict:
    """Revoke the token at Google, then forget it here whatever Google said. Preview only forgets (above)."""
    if config.read_only() and config._keychain():  # a test server shares the keychain and Google client with Pulsar
        raise ValueError("A read-only run does not disconnect Google Calendar.")
    refresh, told, preview = config.get_google_token(), True, config.is_preview()
    if refresh and not preview:
        got, _ = http("POST", REVOKE_URL, {"token": refresh})
        told = got in (200, 400)  # 400: Google had already let it go
    config.delete_google_token()
    config.update({"calendar_account": ""})
    _access.update(token=None, expires=0.0)
    forget()
    with _lock:
        _pending.clear()
    message = ("Pulsar Preview has forgotten its access to Google Calendar. Google was not told, because that would "
               "disconnect Pulsar too; to withdraw it for both: myaccount.google.com/connections." if preview else
               "Google Calendar is disconnected, and Google has withdrawn Pulsar's access." if told else
               "Pulsar has forgotten its access to Google Calendar, but Google could not be told (offline?). To "
               "withdraw it there too: myaccount.google.com/connections.")
    return {"ok": True, "told": told, "message": message, "calendar": status({**settings, "calendar_account": ""})}


# ---- the access token, in memory ---------------------------------------------------------------------

_access: dict = {"token": None, "expires": 0.0}
_token_lock = threading.Lock()


def _keep_access(doc: dict) -> None:
    try:
        life = float(doc.get("expires_in") or 3600)
    except (TypeError, ValueError):
        life = 3600.0
    _access.update(token=str(doc["access_token"]), expires=time.time() + life)


def _access_token(renew: bool = False) -> str:
    """A working access token: the one in memory, or a new one made from the refresh token."""
    with _token_lock:
        if not renew and _access["token"] and time.time() < _access["expires"] - 60:
            return _access["token"]
        refresh, c = config.get_google_token(), client()
        if not (refresh and c):
            raise CalendarError(NOT_SET_UP if not c else LAPSED)
        got, doc = http("POST", TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": refresh,
                                            "client_id": c["id"], "client_secret": c["secret"]})
        if got == 400 and doc.get("error") == "invalid_grant":  # revoked in the Google account, or lapsed
            config.delete_google_token()
            config.update({"calendar_account": ""})
            _access.update(token=None, expires=0.0)
            forget()
            raise CalendarError(LAPSED)
        if got != 200 or not doc.get("access_token"):
            raise CalendarError(f"Google did not renew Pulsar's access to your calendar ({_google_reason(got, doc)}).")
        if doc.get("refresh_token") and doc["refresh_token"] != refresh:
            config.set_google_token(doc["refresh_token"])
        _keep_access(doc)
        return _access["token"]


# ---- reading a day ----------------------------------------------------------------------------------

class _Expired(Exception):
    pass


def _bounds(day: str, tz) -> tuple:
    midnight = datetime.strptime(day, "%Y-%m-%d")
    return midnight, midnight.replace(tzinfo=tz), (midnight + timedelta(days=1)).replace(tzinfo=tz)


def _list(day: str, tz, token: str, days: int = 1) -> dict:
    """{"summary", "items"}: the events of `days` days from `day` on the primary calendar, each repeat as its own."""
    _, t0, _ = _bounds(day, tz)
    t1 = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).replace(tzinfo=tz)
    query = {"timeMin": t0.isoformat(), "timeMax": t1.isoformat(), "singleEvents": "true", "orderBy": "startTime",
             "maxResults": "250", "maxAttendees": "1", "fields": FIELDS}  # maxAttendees=1: only "me" among the guests
    out: dict = {"summary": "", "items": []}
    for _ in range(4):
        got, doc = http("GET", EVENTS_URL + "?" + urllib.parse.urlencode(query), headers={"Authorization": "Bearer " + token})
        if got == 401:
            raise _Expired()
        if got != 200:
            raise CalendarError(_api_problem(got, doc))
        out["summary"] = out["summary"] or str(doc.get("summary") or "")
        out["items"] += [x for x in doc.get("items") or [] if isinstance(x, dict)]
        if not doc.get("nextPageToken"):
            break
        query["pageToken"] = str(doc["nextPageToken"])
    return out


def _api_problem(got: int, doc: dict) -> str:
    reason = _google_reason(got, doc)
    if got == 0:
        return f"Google Calendar could not be reached ({reason})."
    if "accessNotConfigured" in reason or "SERVICE_DISABLED" in reason:
        return "The Google Calendar API is not enabled in Pulsar's Google Cloud project. Foster needs to enable it."
    if "insufficientPermissions" in reason or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in reason:
        return "Pulsar's access does not include your calendar events. Disconnect and connect Google Calendar again."
    if got == 429 or "rateLimitExceeded" in reason:
        return "Google is limiting requests; Pulsar tries again in a few minutes."
    return f"Google Calendar did not give the day's events ({reason})."


def _read(day: str, tz) -> dict:
    try:
        return _list(day, tz, _access_token())
    except _Expired:  # revoked or expired early: one new token, one more try
        try:
            return _list(day, tz, _access_token(renew=True))
        except _Expired:
            raise CalendarError(LAPSED) from None


def _moment(value) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _https(value) -> Optional[str]:
    return value if isinstance(value, str) and value.startswith("https://") and len(value) < 2000 else None


def own_domain(account: str) -> str:
    """The email domain of the person's own Google account (the primary calendar's name), "" when unknown or public."""
    domain = str(account or "").rpartition("@")[2].strip().lower()
    return "" if "@" not in str(account or "") or domain in PUBLIC_MAIL else domain


def outside(organizer, domain: str) -> bool:
    """True unless the person organized the event or someone of their own company did (an address of `domain` or
    one of its subdomains). Anyone can send an invitation that Google puts on the calendar by itself, and its
    organizer can rename it at any time: its title is never a note (dayplan). Unknown is outside."""
    if not isinstance(organizer, dict):
        return True
    if organizer.get("self") is True:
        return False
    email = str(organizer.get("email") or "").strip().lower()
    host = email.rpartition("@")[2]
    return not (domain and "@" in email and (host == domain or host.endswith("." + domain)))


def note_of(title: str, external: bool) -> str:
    """The title as a meeting's note, or "": only from the person's own company, and only through the note's checks
    (askclaude.guard_note: no instruction to a tool, sum, address, look-alike or invisible character)."""
    from .askclaude import guard_note
    return "" if external else guard_note(title)[0]


def events_on(items: list, day: str, tz, domain: str = "") -> list:
    """Google's events as the page draws them, all-day ones first, then by start. `domain`: the person's own
    (own_domain), for `external` and `note`."""
    midnight, t0, t1 = _bounds(day, tz)
    a0, a1 = t0.timestamp(), t1.timestamp()

    def minutes(ts: float) -> float:  # on the wall clock, like the trackers' items, on the days the clocks change too
        return round((datetime.fromtimestamp(ts, tz).replace(tzinfo=None) - midnight).total_seconds() / 60, 2)

    out = []
    for x in items:
        if x.get("status") == "cancelled" or x.get("eventType") in NOT_MEETINGS:
            continue
        start, end = x.get("start") or {}, x.get("end") or {}
        entry = None
        for p in ((x.get("conferenceData") or {}).get("entryPoints") or []):
            if isinstance(p, dict) and p.get("entryPointType") == "video" and _https(p.get("uri")):
                entry = p["uri"]
                break
        found = JOIN.search(str(x.get("location") or ""))
        conference = _https(x.get("hangoutLink")) or entry or (found.group(0).rstrip(".,;)") if found else None)
        title, external = str(x.get("summary") or "")[:300], outside(x.get("organizer"), domain)
        item = {"title": title,
                "declined": any(isinstance(a, dict) and a.get("self") and a.get("responseStatus") == "declined"
                                for a in x.get("attendees") or []),
                "recurring": bool(x.get("recurringEventId")),
                "uid": str(x.get("recurringEventId") or x.get("id") or ""),
                "external": external, "note": note_of(title, external)}
        item.update({k: v for k, v in (("url", conference), ("link", _https(x.get("htmlLink")))) if v})
        if "date" in start:  # all day: the end date is the first day after
            try:
                first = date.fromisoformat(str(start["date"]))
                last = date.fromisoformat(str(end.get("date"))) if end.get("date") else first + timedelta(days=1)
            except ValueError:
                continue
            if first <= midnight.date() < last:
                out.append({"s": 0, "e": 1440, "all_day": True, **item})
            continue
        a, b = _moment(start.get("dateTime")), _moment(end.get("dateTime"))
        if a is None:
            continue
        sa = a.timestamp()
        sb = max(sa, b.timestamp()) if b else sa
        if sa < a1 and (sb > a0 or (sb == sa and sa >= a0)):
            out.append({"s": minutes(max(sa, a0)), "e": minutes(min(sb, a1)), "all_day": False, **item})
    out.sort(key=lambda x: (not x["all_day"], x["s"], x["e"], x["title"]))
    return out


_history: dict = {}  # (first day, days, zone) -> (when, [meetings])


def history(first: str, days: int, settings: dict) -> list:
    """The meetings of `days` days from `first`, as moments: [{s, e (epoch seconds), uid, title, declined}], to match
    them with the time logged then. Kept an hour. [] when not connected or Google cannot be read: it is a hint."""
    if not status(settings)["connected"]:
        return []
    tz, key = config.tz(settings), (first, days, config.timezone_name(settings))
    with _cache_lock:
        kept = _history.get(key)
    if kept and time.time() - kept[0] < 3600:
        return kept[1]
    try:
        try:
            doc = _list(first, tz, _access_token(), days)
        except _Expired:
            doc = _list(first, tz, _access_token(renew=True), days)
    except (CalendarError, _Expired):
        return []
    out = []
    for x in doc["items"]:
        a, b = _moment((x.get("start") or {}).get("dateTime")), _moment((x.get("end") or {}).get("dateTime"))
        if x.get("status") == "cancelled" or x.get("eventType") in NOT_MEETINGS or not a or not b:
            continue  # all-day entries have dates, not times
        out.append({"s": a.timestamp(), "e": b.timestamp(), "uid": str(x.get("recurringEventId") or x.get("id") or ""),
                    "title": str(x.get("summary") or "")[:300],
                    "declined": any(isinstance(g, dict) and g.get("self") and g.get("responseStatus") == "declined"
                                    for g in x.get("attendees") or [])})
    with _cache_lock:
        _history[key] = (time.time(), out)
        for old in sorted(_history, key=lambda k: _history[k][0])[:-10]:
            del _history[old]
    return out


_cache: dict = {}  # (day, zone) -> (when, {"events", "fetched"})
_cache_lock = threading.Lock()


def forget() -> None:
    """Drop every day kept: the connection changed."""
    with _cache_lock:
        _cache.clear()
        _history.clear()


def day(day_iso: str, settings: dict, fresh: bool = False) -> dict:
    """{"configured", "connected", "account", "events", "problem", "fetched"} for one day. Kept FRESH seconds;
    fresh=True asks Google at once. Never raises for a calendar that cannot be read: the problem is text for the
    page, and the last copy of the day, if any, stands in."""
    datetime.strptime(day_iso, "%Y-%m-%d")  # a bad date is the request's fault: ValueError
    base = status(settings)
    if not base["connected"]:
        return {**base, "events": [], "problem": None, "fetched": None}
    tz, key = config.tz(settings), (day_iso, config.timezone_name(settings))
    with _cache_lock:
        kept = _cache.get(key)
    if kept and not fresh and time.time() - kept[0] < FRESH:
        return {**base, **kept[1], "problem": None}
    try:
        doc = _read(day_iso, tz)
    except CalendarError as exc:
        if not config.get_google_token():  # the access lapsed and is gone: the page hides the column and says why
            return {**status(settings), "events": [], "problem": str(exc), "fetched": None}
        if kept:
            return {**base, **kept[1], "problem": f"{exc} Showing the meetings as read at {kept[1]['fetched']}."}
        return {**base, "events": [], "problem": str(exc), "fetched": None}
    account = _account(doc.get("summary"))
    if account and account != base["account"]:  # connected before the account was known, or renamed
        config.update({"calendar_account": account})
        base["account"] = account
    result = {"events": events_on(doc["items"], day_iso, tz, own_domain(base["account"])),
              "fetched": datetime.now(tz).strftime("%H:%M")}
    with _cache_lock:
        _cache[key] = (time.time(), result)
        for old in sorted(_cache, key=lambda k: _cache[k][0])[:-60]:  # a couple of months of days at most
            del _cache[old]
    return {**base, **result, "problem": None}
