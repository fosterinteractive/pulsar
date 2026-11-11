"""Signing in to GitHub, so Pulsar keeps getting its updates once its repository is private.

Foster's GitHub App "Pulsar updates" (CLIENT_ID; the id is public, and the device flow needs no secret) may only read
the contents of fosterinteractive/pulsar, and only for people who can see that repository themselves. Signing in is
GitHub's device flow: Pulsar asks GitHub for a short code, the person enters it at github.com/login/device and
approves, and Pulsar receives a user token, kept in the keychain (config.get_github_token). The App's tokens do not
expire ("Expire user authorization tokens" is off), so one sign-in per Mac lasts until the person loses access to the
repository, revokes the App in GitHub's settings, or leaves Pulsar unused for a year (GitHub drops unused tokens).

Signed in, updater.fetch reads the release files through GitHub's API (fetch) instead of the public download links.
The release key's signature still decides what is installed: this changes how the files arrive, never which are
trusted. The token is sent to api.github.com only, never to the storage a download is redirected to.

Whether the account can see the repository is GitHub's own list of the repositories the App's token may read
(check): asked at sign-in and once a day, so a missing access shows while the repository is still public.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import __version__, config

CLIENT_ID = ""  # the GitHub App's client id (Iv…); empty: signing in is not offered
OWNER, REPO = "fosterinteractive", "pulsar"
API = "https://api.github.com"
DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
VERIFY_URL = "https://github.com/login/device"
# The only links fetch() serves: a file of the latest release, or of one tagged release (updater.DEFAULT_MANIFEST,
# updater.RELEASES).
RELEASE_LINK = re.compile(r"^https://github\.com/fosterinteractive/pulsar/releases/"
                          r"(?:latest/download|download/(v[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}))/([A-Za-z0-9._-]{1,100})$")
ASSETS = f"{API}/repos/{OWNER}/{REPO}/releases/assets/"
MAX_JSON = 1024 * 1024
RECHECK = 24 * 3600  # how often the hourly update check also asks whether the account still sees the repository

SAY = {  # what GitHub's device flow errors mean to the person
    "expired_token": "The code expired before it was entered on GitHub. Click Sign in to GitHub again.",
    "access_denied": "The sign-in was cancelled on GitHub. Click Sign in to GitHub to try again.",
    "device_flow_disabled": "Signing in is not switched on for Pulsar's GitHub App. Tell the maintainer.",
    "incorrect_client_credentials": "GitHub does not know Pulsar's GitHub App. Tell the maintainer.",
}

_lock = threading.Lock()
_flow: dict = {"gen": 0}  # the sign-in in progress: gen, code, url, until, error
_sleep = time.sleep  # the tests make waiting instant


class SignInProblem(Exception):
    """GitHub refused the token (revoked, or the person lost access to the App): download as if not signed in."""


def available() -> bool:
    return bool(CLIENT_ID)


def handles(url: str) -> bool:
    return bool(RELEASE_LINK.match(url or ""))


def token_for(url: str) -> Optional[str]:
    """The token to read `url` with: only for Pulsar's own release links, and only when signed in."""
    if not (available() and handles(url)):
        return None
    return config.get_github_token()


# ---- what Pulsar remembers (no secret) ---------------------------------------------------

def _state_path() -> str:
    return os.path.join(config.CONFIG_DIR, "github.json")


def _state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _note(**changes) -> None:
    with _lock:
        s = {**_state(), **changes}
        try:
            config.ensure_dirs()
            config.write_atomic(_state_path(), json.dumps(s, ensure_ascii=False))
        except OSError:
            pass


def _stamp(t) -> str:
    return time.strftime("%d %b %Y, %H:%M", time.localtime(t)) if isinstance(t, (int, float)) and t > 0 else ""


# ---- talking to GitHub ---------------------------------------------------------------------

def _call(url: str, data: Optional[dict] = None, token: Optional[str] = None, accept: str = "application/vnd.github+json",
          limit: int = MAX_JSON, timeout: float = 30, raw: bool = False):
    """One request. JSON back, or the bytes with raw=True. A token goes to api.github.com only, as a header urllib
    does not carry over to a redirect (a download is redirected to GitHub's file storage, which must not see it)."""
    if config.tests_offline():
        raise OSError("A test tried to reach GitHub; tests must fake it.")
    if token and not url.startswith(API + "/"):
        raise ValueError("The GitHub sign-in is only ever sent to api.github.com.")
    headers = {"User-Agent": f"Pulsar/{__version__}", "Accept": accept}
    if url.startswith(API + "/"):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    body = urllib.parse.urlencode(data).encode("ascii") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    if token:
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = resp.read(limit + 1)
    if len(out) > limit:
        raise ValueError("the download is larger than expected")
    if raw:
        return out
    got = json.loads(out.decode("utf-8") or "null")
    return got if isinstance(got, dict) else {}


def fetch(url: str, limit: int, timeout: float, token: str) -> bytes:
    """A release file, through the API: the release (latest, or by tag), then its file of that name. A file that is
    not there, or a repository this account cannot see, is HTTP 404, as on the public link."""
    m = RELEASE_LINK.match(url or "")
    if not m:
        raise ValueError("Not a link to a file of a Pulsar release.")
    tag, name = m.group(1), m.group(2)
    where = f"{API}/repos/{OWNER}/{REPO}/releases/" + (f"tags/{tag}" if tag else "latest")
    try:
        release = _call(where, token=token, timeout=min(timeout, 30))
        asset = next((a for a in release.get("assets") or [] if isinstance(a, dict) and a.get("name") == name), None)
        link = str((asset or {}).get("url") or "")
        if not link.startswith(ASSETS):
            raise urllib.error.HTTPError(url, 404, "no such file in the release", None, None)  # type: ignore[arg-type]
        data = _call(link, token=token, accept="application/octet-stream", limit=limit, timeout=timeout, raw=True)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            _note(problem="refused")
            raise SignInProblem("GitHub no longer accepts this sign-in.") from exc
        raise
    _note(worked=time.time(), problem="")
    return data


# ---- signing in ----------------------------------------------------------------------------

def start() -> dict:
    """Asks GitHub for a code and waits for the person to enter it (in the background). Returns the code and the page."""
    if not available():
        raise ValueError("Signing in to GitHub is not set up in this copy of Pulsar.")
    try:
        got = _call(DEVICE_URL, data={"client_id": CLIENT_ID}, accept="application/json", timeout=20)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"GitHub did not answer ({exc}). Check the connection and try again.") from exc
    code, device = str(got.get("user_code") or ""), str(got.get("device_code") or "")
    if not code or not device:
        why = str(got.get("error") or "")
        raise RuntimeError(SAY.get(why) or f"GitHub did not start a sign-in ({got.get('error_description') or why or 'no code'}).")
    url = str(got.get("verification_uri") or "")
    url = url if url.startswith("https://github.com/") else VERIFY_URL
    until = time.time() + max(60, min(int(got.get("expires_in") or 900), 1800))
    interval = max(5, min(int(got.get("interval") or 5), 60))
    with _lock:
        gen = _flow["gen"] + 1
        _flow.clear()
        _flow.update(gen=gen, code=code, url=url, until=until, error="")
    threading.Thread(target=_wait, args=(device, interval, until, gen), daemon=True, name="pulsar-github").start()
    return {"code": code, "url": url}


def _end(gen: int, error: str = "") -> None:
    with _lock:
        if _flow.get("gen") == gen:
            _flow.update(code="", error=error)


def _wait(device: str, interval: int, until: float, gen: int) -> None:
    """Asks GitHub every few seconds whether the code was entered, as the device flow says, until it was, the person
    cancelled, or the code expired. A newer sign-in (another click) ends this one."""
    while time.time() < until:
        _sleep(interval)
        if _flow.get("gen") != gen:
            return
        try:
            got = _call(TOKEN_URL, data={"client_id": CLIENT_ID, "device_code": device,
                                         "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                        accept="application/json", timeout=20)
        except (OSError, ValueError):
            continue  # the network: ask again at the next turn
        err = str(got.get("error") or "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval = max(interval + 5, min(int(got.get("interval") or 0), 60))
            continue
        if err:
            return _end(gen, SAY.get(err) or f"GitHub said: {got.get('error_description') or err}")
        try:
            config.set_github_token(str(got.get("access_token") or ""))
        except (ValueError, RuntimeError) as exc:
            return _end(gen, str(exc))
        _note(login="", access=None, problem="", checked=0)
        check()
        return _end(gen)
    _end(gen, SAY["expired_token"])


def sign_out() -> None:
    """Forgets the sign-in on this Mac (GitHub keeps the App authorised until the person revokes it there)."""
    with _lock:
        _flow.update(gen=_flow["gen"] + 1, code="", error="")
    config.delete_github_token()
    _note(login="", access=None, problem="", checked=0, worked=0)


def check(token: Optional[str] = None) -> None:
    """Who is signed in, and whether that account can read the repository through the App: GitHub's list of the
    repositories the App's token may read for the person, which needs explicit access (a team or an invitation), so
    it answers the same before and after the repository goes private."""
    token = token or config.get_github_token()
    if not token:
        return
    try:
        login = str(_call(f"{API}/user", token=token).get("login") or "")
        found = _call(f"{API}/user/installations?per_page=100", token=token).get("installations") or []
        inst = next((i for i in found if isinstance(i, dict)
                     and str((i.get("account") or {}).get("login") or "").lower() == OWNER), None)
        access = False
        if inst:
            repos = _call(f"{API}/user/installations/{int(inst['id'])}/repositories?per_page=100",
                          token=token).get("repositories") or []
            access = any(isinstance(r, dict) and str(r.get("full_name") or "").lower() == f"{OWNER}/{REPO}"
                         for r in repos)
        _note(login=login, access=access, problem="", checked=time.time())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            _note(problem="refused", checked=time.time())
        else:
            _record(f"GitHub answered HTTP {exc.code} when Pulsar checked its access to the repository.")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _record(f"Pulsar could not check its GitHub access: {exc}")


def maybe_recheck() -> None:
    """Once a day, from the hourly update check: the access may have been given or taken away."""
    if available() and config.get_github_token() and time.time() - float(_state().get("checked") or 0) > RECHECK:
        check()


def _record(message: str) -> None:
    from . import logbook
    logbook.record("github", message, kind="warning", once_per=86400)


# ---- what the page shows -------------------------------------------------------------------

def status() -> dict:
    """Settings > Updates: signed in or not, as whom, whether that account sees the repository, and a sign-in in
    progress (its code). Never the token."""
    signed = bool(available() and config.get_github_token())
    s = _state() if signed else {}
    with _lock:
        flow = dict(_flow)
    pending = bool(flow.get("code")) and time.time() < float(flow.get("until") or 0)
    login, problem, access = str(s.get("login") or ""), str(s.get("problem") or ""), s.get("access")
    if pending:
        message = "Enter this code on GitHub's page and approve Pulsar. Pulsar is waiting."
    elif flow.get("error"):
        message = str(flow["error"])
    elif not signed:
        message = ("Not signed in. Pulsar's repository on GitHub is going private; then updates arrive only through "
                   "a GitHub sign-in, once per Mac.")
    elif problem == "refused":
        message = "GitHub no longer accepts this sign-in. Sign in to GitHub again."
    elif access is False:
        message = (f"Signed in as {login or 'your account'}, but it cannot see Pulsar's repository, so updates will "
                   f"not arrive once it is private. Ask a GitHub admin at Foster for access to {OWNER}/{REPO}.")
    elif access:
        message = f"Signed in as {login}. Updates arrive through this sign-in."
    else:
        message = f"Signed in{' as ' + login if login else ''}."
    return {"available": available(), "signedIn": signed, "login": login, "access": access if signed else None,
            "problem": problem, "checked": _stamp(s.get("checked")), "worked": _stamp(s.get("worked")),
            "pending": {"code": flow["code"], "url": flow["url"]} if pending else None,
            "error": str(flow.get("error") or ""), "message": message,
            "warn": bool(flow.get("error")) or problem == "refused" or (signed and access is False)}


def update_hint() -> str:
    """What the update check says when the release cannot be found: a private repository needs the sign-in."""
    if not available():
        return ""
    if not config.get_github_token():
        return "Pulsar's repository is private: sign in to GitHub below to get updates."
    s = _state()
    if s.get("problem") == "refused":
        return "GitHub no longer accepts your sign-in: sign in to GitHub again below to get updates."
    if s.get("access") is False:
        return f"Your GitHub account cannot see Pulsar's repository: ask a GitHub admin at Foster for access to {OWNER}/{REPO}."
    return ""
