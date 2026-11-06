"""Imported first by every test module, before anything from pulsar/: from then on no test can reach anything real.

- One scratch folder per run holds everything a run writes: temporary files (tempfile and TMPDIR), settings and
  caches (XDG_CONFIG_HOME, XDG_CACHE_HOME) and a home folder of its own (HOME), so ~/.claude, ~/.config/pulsar,
  ~/.cache/pulsar, MemTime's database and the installed copy are out of reach. It is removed when the run ends.
- PULSAR_TESTS_OFFLINE is set, so Pulsar itself skips the keychain, Claude Code and the network (config.tests_offline).
  Behind that, for a test that switches it off or code that forgets it:
- The keychain (`security`), Claude Code (`claude`), `osascript`, `open` and os.exec* cannot be started.
- Nothing leaves this computer, and on it a connection goes only to a port this process opened itself: never the
  real Pulsar, a Preview or ActivityWatch.

A test that tries any of these fails with RealWorld, which `except Exception` cannot swallow; and since code under
test may still swallow it (a bare except, a server thread), the run itself then ends with exit status 1 and a list
of what was tried.
"""

import atexit
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile

SCRATCH = tempfile.mkdtemp(prefix="pulsar-tests-")
for _name in ("tmp", "home", "config", "cache"):
    os.makedirs(os.path.join(SCRATCH, _name))
tempfile.tempdir = os.path.join(SCRATCH, "tmp")
os.environ.update(TMPDIR=tempfile.tempdir, HOME=os.path.join(SCRATCH, "home"),
                  XDG_CONFIG_HOME=os.path.join(SCRATCH, "config"), XDG_CACHE_HOME=os.path.join(SCRATCH, "cache"),
                  PULSAR_TESTS_OFFLINE="1")  # Pulsar's own switch (config.tests_offline): no keychain, network or Claude
os.environ.pop("CLAUDE_CONFIG_DIR", None)  # Claude Code's own folder, when moved: claudelink would read and write it
if "pulsar" in sys.modules:  # its paths are read once, at import: too late to move them
    raise RuntimeError("tests/isolation.py must be imported before anything from pulsar/")

BLOCKED = {"security", "claude", "osascript", "open"}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
TRIED: list = []      # what a test tried and was refused, for the report at the end of the run
_expected: list = []  # while refused() is active: refusals a test provokes on purpose
_bound: set = set()   # the ports this process listens on


class RealWorld(BaseException):
    """A test reached for something real. Not an Exception, so `except Exception` in the code cannot hide it."""


def _refuse(what: str):
    (_expected[-1] if _expected else TRIED).append(what)
    raise RealWorld(f"tests may not reach {what}")


@contextlib.contextmanager
def refused():
    """For the test of this guard itself: the refusals inside are listed here, not counted against the run."""
    seen: list = []
    _expected.append(seen)
    try:
        yield seen
    finally:
        _expected.pop()


_popen_init = subprocess.Popen.__init__


def _guarded_popen(self, args, *a, **k):
    argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
    program = (os.path.basename(os.fsdecode(argv[0])).split() or [""])[0] if argv else ""
    if program in BLOCKED:
        _refuse(f"the program {program!r} ({' '.join(map(str, argv))[:80]})")
    return _popen_init(self, args, *a, **k)


subprocess.Popen.__init__ = _guarded_popen
for _exec in ("execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe"):
    setattr(os, _exec, lambda *a, _n=_exec, **k: _refuse(f"os.{_n}"))

_bind, _connect, _connect_ex, _getaddrinfo = (socket.socket.bind, socket.socket.connect, socket.socket.connect_ex,
                                              socket.getaddrinfo)


def _guarded_bind(self, address):
    _bind(self, address)
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _bound.add(self.getsockname()[1])


def _check(sock, address) -> None:
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return
    host, port = address[0], address[1]
    if host not in LOOPBACK:
        _refuse(f"the network ({host}:{port})")
    if port not in _bound:
        _refuse(f"a server this test did not start (port {port})")


def _guarded_connect(self, address):
    _check(self, address)
    return _connect(self, address)


def _guarded_connect_ex(self, address):
    _check(self, address)
    return _connect_ex(self, address)


def _guarded_getaddrinfo(host, *a, **k):
    name = host.decode() if isinstance(host, bytes) else host
    if name not in LOOPBACK and name not in (None, ""):
        _refuse(f"a name lookup ({name})")
    return _getaddrinfo(host, *a, **k)


socket.socket.bind, socket.socket.connect, socket.socket.connect_ex = _guarded_bind, _guarded_connect, _guarded_connect_ex
socket.getaddrinfo = _guarded_getaddrinfo


@atexit.register
def _end_of_run() -> None:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    if TRIED:
        print("\nTests reached for something real, which isolation.py refused:", file=sys.stderr)
        for what in dict.fromkeys(TRIED):
            print(f"  - {what}", file=sys.stderr)
        sys.stderr.flush()
        os._exit(1)
