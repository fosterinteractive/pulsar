"""Imported first by every test module, before anything from pulsar/: from then on no test can reach anything real.

- One scratch folder per run holds everything a run writes: temporary files (tempfile and TMPDIR), settings and
  caches (XDG_CONFIG_HOME, XDG_CACHE_HOME) and a home folder of its own (HOME), so ~/.claude, ~/.config/pulsar,
  ~/.cache/pulsar, MemTime's database and the installed copy are out of reach. It is removed when the run ends.
- PULSAR_TESTS_OFFLINE is set, so Pulsar itself skips the keychain, Claude Code and the network (config.tests_offline).
  Behind that, for a test that switches it off or code that forgets it:
- Git sees no repository, global or system settings of the person's: every GIT_* variable is dropped (git exports
  GIT_DIR into hooks, so a hook that runs the tests would otherwise point them at the real repository), and its
  global settings file is one in the scratch folder.
- The keychain (`security`), Claude Code (`claude`), `osascript` and `open` cannot be started: not directly, not
  through `env`, `sh -c` or shell=True, os.system or os.posix_spawn; os.exec* not at all.
- Nothing leaves this computer, and on it a connection goes only to a port this process opened itself: never the
  real Pulsar, a Preview or ActivityWatch. No name is looked up but this computer's own, no UDP datagram goes to a
  port this process did not open, and a Unix socket only inside the scratch folder.

Not covered (a test would have to go out of its way): ctypes calls into libc, and the guards of a Python child
process, which inherits the environment above but not these checks.

A test that tries any of these fails with RealWorld, which `except Exception` cannot swallow; and since code under
test may still swallow it (a bare except, a server thread), the run itself then ends with exit status 1 and a list
of what was tried.
"""

import atexit
import contextlib
import os
import re
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
for _name in [k for k in os.environ if k.startswith("GIT_")]:  # GIT_DIR, GIT_WORK_TREE, GIT_CONFIG_GLOBAL, ...
    del os.environ[_name]
os.environ.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.path.join(SCRATCH, "home", ".gitconfig"))
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


SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
WRAPPERS = {"env", "nohup", "exec", "command", "nice", "time"}


def _programs(argv: list, shell: bool = False) -> list:
    """The programs a command line would start: its first word, the one after `env` and the like (and VAR=value
    words), and in a shell's command (shell=True, `sh -c ...`) the first word of each command in it."""
    words = [os.fsdecode(w) for w in argv]
    if shell:
        return [p for part in re.split(r"[;&|\n()`{}]+|\$\(", " ".join(words)) for p in _programs(part.split())]
    while words and (os.path.basename(words[0]) in WRAPPERS or re.match(r"[A-Za-z_]\w*=", words[0])):
        words = words[1:]
        while words and words[0].startswith("-"):  # env -i, nice -n 5: the wrapper's own options
            words = words[1:]
    if not words:
        return []
    first = os.path.basename(words[0]).split()
    if first and first[0] in SHELLS:
        flag = next((i for i, w in enumerate(words[1:], 1) if w.startswith("-") and not w.startswith("--") and "c" in w), None)
        if flag is not None and flag + 1 < len(words):
            return [first[0]] + _programs([words[flag + 1]], shell=True)
    return first[:1]


def _check_programs(argv: list, shell: bool = False) -> None:
    for program in _programs(argv, shell):
        if program in BLOCKED:
            _refuse(f"the program {program!r} ({' '.join(map(os.fsdecode, argv))[:80]})")


def _guarded_popen(self, args, *a, **k):
    argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args)
    shell = bool(k.get("shell", a[7] if len(a) > 7 else False))  # Popen(args, bufsize, executable, ..., shell, ...)
    _check_programs(argv, shell)
    if k.get("executable"):
        _check_programs([k["executable"]])
    return _popen_init(self, args, *a, **k)


subprocess.Popen.__init__ = _guarded_popen
for _exec in ("execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe"):
    setattr(os, _exec, lambda *a, _n=_exec, **k: _refuse(f"os.{_n}"))
_spawn = {n: getattr(os, n) for n in ("posix_spawn", "posix_spawnp") if hasattr(os, n)}


def _guarded_system(command):
    _check_programs([command], shell=True)
    return _system(command)


_system = os.system
os.system = _guarded_system
for _n, _fn in _spawn.items():
    setattr(os, _n, lambda path, argv, *a, _fn=_fn, **k: (_check_programs([path]), _check_programs(list(argv)),
                                                          _fn(path, argv, *a, **k))[-1])

_bind, _connect, _connect_ex, _sendto, _getaddrinfo = (socket.socket.bind, socket.socket.connect,
                                                       socket.socket.connect_ex, socket.socket.sendto, socket.getaddrinfo)


def _guarded_bind(self, address):
    _bind(self, address)
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _bound.add(self.getsockname()[1])


def _check(sock, address) -> None:
    if sock.family == getattr(socket, "AF_UNIX", None):  # a Unix socket: only one of this run's own
        path = os.fsdecode(address) if isinstance(address, (str, bytes)) else ""
        if not os.path.realpath(path).startswith(os.path.realpath(SCRATCH) + os.sep):
            _refuse(f"a Unix socket outside the tests' folder ({path[:80]})")
        return
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


def _guarded_sendto(self, data, *rest):
    _check(self, rest[-1])  # sendto(data, address) or sendto(data, flags, address)
    return _sendto(self, data, *rest)


def _lookup(name) -> None:
    name = name.decode() if isinstance(name, bytes) else name
    if name not in LOOPBACK and name not in (None, ""):
        _refuse(f"a name lookup ({name})")


def _guarded_getaddrinfo(host, *a, **k):
    _lookup(host)
    return _getaddrinfo(host, *a, **k)


def _guarded_lookup(fn):
    def lookup(host, *a, **k):
        _lookup(host[0] if isinstance(host, tuple) else host)  # getnameinfo((host, port), flags)
        return fn(host, *a, **k)
    return lookup


socket.socket.bind, socket.socket.connect, socket.socket.connect_ex = _guarded_bind, _guarded_connect, _guarded_connect_ex
socket.socket.sendto = _guarded_sendto
socket.getaddrinfo = _guarded_getaddrinfo
for _n in ("gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
    setattr(socket, _n, _guarded_lookup(getattr(socket, _n)))


@atexit.register
def _end_of_run() -> None:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    if TRIED:
        print("\nTests reached for something real, which isolation.py refused:", file=sys.stderr)
        for what in dict.fromkeys(TRIED):
            print(f"  - {what}", file=sys.stderr)
        sys.stderr.flush()
        os._exit(1)
