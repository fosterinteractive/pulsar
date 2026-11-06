"""Pulsar updates itself from the GitHub releases of fosterinteractive/pulsar, when it runs from an installed copy.

The installed layout (the Mac launcher creates it), under ~/Library/Application Support/Pulsar/:
    versions/<X.Y.Z>/   a full copy of the app (pulsar.py, pulsar/, web/, README.md)
    current, previous   relative symlinks into versions/
    bad-versions        versions that failed to start; never used again
A release is `pulsar-<version>.zip` (one top folder `pulsar-<version>/`), `latest.json`
({version, url, sha256, notes, published}) and `latest.json.sig`: an ECDSA P-256 signature of latest.json made with
the release key, which only the maintainer holds (see macos/README.md). The key's public half is below; a manifest
that does not verify against it is ignored, so nobody else can ship code to installed copies, even with write
access to the repository. A newer version is downloaded, its checksum checked, unpacked next to
the others and asked to test itself (`pulsar.py --self-test`); only then does `current` move to it, and the running
server restarts into it once nobody has used the page for a while (or at once from Settings).
A copy run from a git checkout only says that a newer version exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Optional

from . import __version__, config

DEFAULT_MANIFEST = "https://github.com/fosterinteractive/pulsar/releases/latest/download/latest.json"
ROOT = os.path.expanduser("~/Library/Application Support/Pulsar")
CHECK_EVERY = 6 * 3600       # seconds between checks
MAX_ZIP = 30 * 1024 * 1024
RELEASES = "https://github.com/fosterinteractive/pulsar/releases/download/"
VERSION_RE = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")  # always used with fullmatch
MAX_UNPACKED = 60 * 1024 * 1024
# The keys a release may be signed with (ECDSA P-256). The first is the one releases are signed with; any further one
# is a reserve kept offline (in the team's password manager): if the first is lost or leaks, a release signed with a
# reserve brings a new list, and installed copies follow it without anyone reinstalling. Adding a key later takes a
# release signed with a key already listed here, so the reserve must be in place before people install.
PUBLIC_KEYS = (
    """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEmCWdtNVrg3SiHdiOL1WDTXva/S7e
XbA0dD0U0N++R+VVLutC7Y66FFlNYSLKjKo+JAuRLrGtGKTXsjgo00mzLQ==
-----END PUBLIC KEY-----
""",
)


def overrides_allowed() -> bool:
    """The test switches (PULSAR_UPDATE_URL, PULSAR_UPDATE_PUBKEY) never apply to an installed copy, so nothing that
    can set an environment variable for the user can swap the update source or the trusted key."""
    return not installed()


def public_keys() -> tuple:
    path = os.environ.get("PULSAR_UPDATE_PUBKEY") if overrides_allowed() else None  # tests sign their own releases
    if path:
        with open(path, encoding="utf-8") as fh:
            return (fh.read(),)
    return PUBLIC_KEYS


def openssl() -> Optional[str]:
    for path in ("/usr/bin/openssl", shutil.which("openssl")):
        if path and os.path.exists(path):
            return path
    return None


def verify(data: bytes, signature: bytes, key_pem: str) -> bool:
    """True only when `signature` is the release key's signature of `data` (checked with the system openssl,
    as Python's standard library has no signature check)."""
    tool = openssl()
    if not tool or not signature:
        return False
    work = tempfile.mkdtemp(prefix="pulsar-verify-")
    try:
        paths = {}
        for name, content in (("data", data), ("sig", signature), ("key.pem", key_pem.encode())):
            paths[name] = os.path.join(work, name)
            with open(paths[name], "wb") as fh:
                fh.write(content)
        run = subprocess.run([tool, "dgst", "-sha256", "-verify", paths["key.pem"], "-signature", paths["sig"], paths["data"]],
                             capture_output=True, text=True, timeout=30)
        return run.returncode == 0 and "Verified OK" in run.stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)


def vtuple(v: str) -> tuple:
    parts = [int(x) for x in re.findall(r"\d+", v or "")][:3]
    return tuple(parts + [0] * (3 - len(parts)))


def app_dir() -> str:
    """The folder that holds pulsar.py for this running copy."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def installed(root: str = ROOT) -> bool:
    versions = os.path.realpath(os.path.join(root, "versions")) + os.sep
    return os.path.realpath(app_dir()).startswith(versions)


def bad_versions(root: str = ROOT) -> set:
    try:
        with open(os.path.join(root, "bad-versions"), encoding="utf-8") as fh:
            return {line.strip() for line in fh if line.strip()}
    except OSError:
        return set()


def mark_bad(version: str, root: str = ROOT) -> None:
    with open(os.path.join(root, "bad-versions"), "a", encoding="utf-8") as fh:
        fh.write(version + "\n")


def link(name: str, target: str, root: str = ROOT) -> None:
    """Point root/name at target (relative), atomically."""
    tmp = os.path.join(root, f".{name}.tmp")
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, os.path.join(root, name))


def read_version(copy: str) -> str:
    try:
        with open(os.path.join(copy, "pulsar", "__init__.py"), encoding="utf-8") as fh:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', fh.read())
        return m.group(1) if m else ""
    except OSError:
        return ""


def fetch(url: str, limit: int, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"Pulsar/{__version__}"})
    if config.tests_offline():
        raise OSError("A test tried to reach the network; tests must fake it.")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("the download is larger than expected")
    return data


def unpack(data: bytes, version: str, root: str = ROOT) -> str:
    """The release zip into versions/<version>/, checked before it is put in place. Returns that folder."""
    if not VERSION_RE.fullmatch(version or ""):
        raise ValueError(f"not a version: {version!r}")
    versions = os.path.join(root, "versions")
    os.makedirs(versions, exist_ok=True)
    final = os.path.join(versions, version)  # always unpacked fresh from the verified bytes
    work = tempfile.mkdtemp(prefix=f".{version}-", dir=versions)
    try:
        with zipfile.ZipFile(_bytes_io(data)) as zf:
            top = f"pulsar-{version}/"
            total = 0
            for info in zf.infolist():
                name = info.filename
                if not name.startswith(top) or ".." in name.split("/") or name.startswith("/"):
                    raise ValueError(f"unexpected file in the release: {name}")
                total += info.file_size
            if total > MAX_UNPACKED:
                raise ValueError("the release unpacks to more than expected")
            zf.extractall(work)
        unpacked = os.path.join(work, f"pulsar-{version}")
        if not os.path.exists(os.path.join(unpacked, "pulsar.py")):
            raise ValueError("the release has no pulsar.py")
        if read_version(unpacked) != version:
            raise ValueError("the release does not hold the version it claims")
        if os.path.exists(final):
            shutil.rmtree(final)
        os.replace(unpacked, final)
        return final
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _bytes_io(data: bytes):
    import io
    return io.BytesIO(data)


def self_test(copy: str) -> bool:
    """Runs the new copy's own test; when it fails, its output goes to the error log."""
    from . import logbook
    try:
        run = subprocess.run([sys.executable, "-E", "-s", "-B", os.path.join(copy, "pulsar.py"), "--self-test"],
                             capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logbook.record("updater", f"The new version's self-test did not run: {exc}")
        return False
    if run.returncode != 0:
        logbook.record("updater", f"The new version failed its self-test (exit {run.returncode}).",
                       detail=(run.stdout + run.stderr)[-3000:])
    return run.returncode == 0


class Updater:
    def __init__(self, manifest_url: Optional[str] = None, root: str = ROOT):
        if manifest_url is None:  # the env var points tests at a local server; ignored once installed
            manifest_url = (os.environ.get("PULSAR_UPDATE_URL") if overrides_allowed() else None) or DEFAULT_MANIFEST
        self.manifest_url, self.root = manifest_url, root
        self.state = {"status": "idle", "current": __version__, "latest": None, "behind": 0, "message": "", "notes": "",
                      "checked": None}
        self.ready: Optional[str] = None
        self.lock = threading.Lock()

    def public(self) -> dict:
        """What Settings shows: the version, when this copy was put in place, the last check, and what to do."""
        try:
            stamp = os.path.getmtime(app_dir() if installed(self.root) else os.path.join(app_dir(), "pulsar", "__init__.py"))
            since = time.strftime("%d %b %Y, %H:%M", time.localtime(stamp))
        except OSError:
            since = ""
        return {**self.state, "installed": installed(self.root), "since": since}

    def _set(self, status: str, message: str = "", **extra) -> dict:
        self.state.update(status=status, message=message, **extra)
        if status == "failed":
            from . import logbook
            logbook.record("updater", message)
        return self.public()

    def check(self, install: bool = False) -> dict:
        """Asks GitHub what the latest release is. Downloads, verifies and tests it only with install=True (the
        Update button); Pulsar never installs anything by itself."""
        with self.lock:
            self._set("checking", "Checking for a new version…")
            self.state["checked"] = time.strftime("%d %b %Y, %H:%M")  # an attempt counts as a check, whatever comes back
            try:
                raw = fetch(self.manifest_url, 64 * 1024, timeout=15)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    if installed(self.root):  # an installed copy came from a release, so one existed: say so in the log
                        from . import logbook
                        logbook.record("updater", "The update address answers 404: the release is gone or the "
                                       "repository is private (it must be public). No updates arrive until it is fixed.",
                                       kind="warning", once_per=86400)
                    return self._set("latest", f"No release of Pulsar is published yet; {__version__} is the latest.")
                return self._set("failed", f"The update server answered HTTP {exc.code}.")
            except Exception as exc:
                return self._set("failed", f"Could not reach the update server: {exc}")
            # Nothing from the manifest is believed, not even for what the page shows, until its signature checks out.
            try:
                signature = fetch(self.manifest_url + ".sig", 4096, timeout=15)
            except Exception as exc:  # not the same as a release without a valid signature: say what happened
                return self._set("failed", f"The release's signature could not be downloaded ({exc}); nothing was changed.")
            if not any(verify(raw, signature, key) for key in public_keys()):
                return self._set("failed", "The latest release is not signed with the release key, so it was ignored.")
            try:
                manifest = json.loads(raw)
            except ValueError:
                manifest = None
            if not isinstance(manifest, dict):
                return self._set("failed", "The update server sent something that is not a release description.")
            latest = str(manifest.get("version") or "")
            if not VERSION_RE.fullmatch(latest):
                return self._set("failed", "The update server sent a version Pulsar does not understand; nothing was changed.")
            # how many releases are newer than this copy: the signed list of every version, when the release has one
            listed = [v for v in manifest.get("versions") or [] if isinstance(v, str) and VERSION_RE.fullmatch(v)]
            behind = len({v for v in listed + [latest] if vtuple(v) > vtuple(__version__)})
            self.state.update(latest=latest, notes=str(manifest.get("notes") or ""), behind=behind)
            if vtuple(latest) <= vtuple(__version__):
                return self._set("latest", f"Pulsar {__version__} is the latest version.")
            if latest in bad_versions(self.root):
                return self._set("skipped", f"Pulsar {latest} did not start on this computer before, so it is skipped.")
            if not installed(self.root):
                return self._set("available", f"Pulsar {latest} is out. This copy runs from source: update it with git pull.")
            if self.ready and read_version(self.ready) == latest:  # already downloaded and tested
                return self._set("ready", f"Pulsar {latest} is downloaded and tested.")
            if not install:
                more = f" You are {behind} releases behind." if behind > 1 else ""
                return self._set("available", f"Pulsar {latest} is out.{more} Click Update to install it.")
            url = str(manifest.get("url") or "")
            if not (overrides_allowed() and os.environ.get("PULSAR_UPDATE_URL")) and not url.startswith(f"{RELEASES}v{latest}/"):
                return self._set("failed", f"Pulsar {latest} points to a download outside its GitHub release; ignored.")
            try:
                data = fetch(url, MAX_ZIP, timeout=120)
                if hashlib.sha256(data).hexdigest() != str(manifest.get("sha256") or "").lower():
                    return self._set("failed", f"The download of Pulsar {latest} did not match its checksum; nothing was changed.")
                copy = unpack(data, latest, self.root)
            except Exception as exc:
                return self._set("failed", f"Pulsar {latest} could not be downloaded: {exc}")
            if not self_test(copy):
                mark_bad(latest, self.root)
                shutil.rmtree(copy, ignore_errors=True)
                return self._set("failed", f"Pulsar {latest} failed its own test, so it was not installed.")
            self.ready = copy
            return self._set("ready", f"Pulsar {latest} is downloaded and tested.")

    def _switch(self) -> None:
        """Point `current` at the ready version (and `previous` at the one running now). Called with the lock held."""
        cur = os.path.join(self.root, "current")
        if os.path.islink(cur):
            link("previous", os.readlink(cur), self.root)
        link("current", os.path.relpath(self.ready, self.root), self.root)
        self._cleanup()

    def apply(self) -> None:
        """Move `current` to the ready version and restart this server in it (same arguments, same port)."""
        if not self.ready:
            return
        with self.lock:
            self._switch()
            target = os.path.join(self.ready, "pulsar.py")
            from . import trackers
            trackers._forget_memtime_copy()  # nothing registered for exit runs across execv
            os.execv(sys.executable, [sys.executable, "-E", "-s", "-B", target] + sys.argv[1:])

    def _cleanup(self) -> None:
        """Keep the current and the previous version, remove older ones."""
        keep = {os.path.realpath(os.path.join(self.root, n)) for n in ("current", "previous")}
        keep.add(os.path.realpath(self.ready or ""))
        versions = os.path.join(self.root, "versions")
        for name in os.listdir(versions):
            path = os.path.join(versions, name)
            if os.path.isdir(path) and os.path.realpath(path) not in keep and not name.startswith("."):
                shutil.rmtree(path, ignore_errors=True)

    def install_now(self) -> dict:
        """The "Update" button: fetch, verify and test the new version, then restart into it."""
        state = self.check(install=True)
        if self.ready:
            threading.Timer(0.5, self.apply).start()  # answer the page first
        return state

    def run_forever(self) -> None:
        """Looks for a new release every few hours, so Settings and the reminder know about it. Never installs."""
        while True:
            try:
                self.check()
            except Exception as exc:  # the loop must survive anything
                self._set("failed", str(exc))
            time.sleep(CHECK_EVERY)

    def start(self) -> None:
        threading.Thread(target=self.run_forever, daemon=True, name="pulsar-updater").start()
