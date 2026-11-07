#!/usr/bin/env python3
"""Pulsar: your working day from ActivityWatch or MemTime, next to what you logged in Productive.

    python3 pulsar.py [--port 5610] [--no-open]
    python3 pulsar.py --mcp [--port 5610]     # for Claude Code: an MCP server over the running Pulsar (pulsar/mcp.py)
    python3 pulsar.py --instance preview ...  # Pulsar Preview's own folders and keychain items (launcher.sh)

Then open http://127.0.0.1:5610/. Activity stays on this computer; Productive is written only after you confirm:
the log dialog, Log the day, a task created in the page, or pulsar_log once Claude Code asked you (in normal use: a
program of yours with full access can get around any confirmation, docs/HOW-IT-WORKS.md section 3).
Python 3.9 or newer, standard library only.
"""

import argparse
import os
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # its own package first, however it was started
from pulsar import __version__  # noqa: E402


WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PORT_TAKEN = 98  # exit status when the port is already taken: says nothing about this copy (launcher.sh tries again)


def page_files(web: str = WEB) -> list:
    """The page and everything it loads from /static/ (scripts and styles), read from index.html itself, so a file
    added to the page is checked without anyone remembering to list it here."""
    import re
    try:
        with open(os.path.join(web, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
    except (OSError, UnicodeDecodeError):
        return ["index.html"]
    return ["index.html"] + list(dict.fromkeys(re.findall(r'(?:src|href)="/static/([^"?#]+)"', html)))


def self_test(web: str = WEB) -> int:
    """Imports every module, checks that the page and every file it loads are there and served, and that the server
    answers with this version: the updater runs this before it trusts a new version."""
    from pulsar import askclaude, claudeuses, config, dayplan, flags, learn, likely, mcp, mcptools, names, productive, report, server, sessions, trackers, updater  # noqa: F401
    files = page_files(web)
    missing = [n for n in files + ["js/core.js", "js/main.js", "style.css"] if not os.path.isfile(os.path.join(web, n))]
    if missing:
        print("Pulsar self-test: missing " + ", ".join(dict.fromkeys(missing)), file=sys.stderr)
        return 1
    # serve the page as the real server would, on a free port
    import json
    import urllib.request
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/version", timeout=10) as resp:
            answered = json.loads(resp.read()).get("version")
        for name in files:
            with urllib.request.urlopen(base + ("/" if name == "index.html" else "/static/" + name), timeout=10) as resp:
                if resp.status != 200 or not resp.read():
                    raise ValueError(f"{name} came back empty")
    except Exception as exc:
        import socket
        import urllib.error
        late = (socket.timeout, TimeoutError)
        if isinstance(exc, late) or (isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, late)):
            # a busy or sleeping Mac, not a broken copy: the updater marks nothing for it (an HTTP error still fails)
            print(f"Pulsar self-test: the server did not answer in time: {exc}", file=sys.stderr)
            return updater.UNFINISHED
        print(f"Pulsar self-test: the server did not serve the page: {exc}", file=sys.stderr)
        return 1
    finally:
        httpd.shutdown()
        httpd.server_close()
    if answered != __version__:
        print(f"Pulsar self-test: the server answered {answered}, not {__version__}", file=sys.stderr)
        return 1
    print(f"Pulsar {__version__} self-test: OK ({len(files)} page files)")
    return 0


def tidy() -> None:
    """Owner-only permissions on what older versions wrote, no copies of entries older than 120 days, no error
    logs older than 31 days."""
    import os
    import time
    from pulsar import config, logbook, trackers
    logbook.prune()
    trackers.sweep_memtime_copies()  # copies of MemTime's database that a killed or restarted Pulsar left behind
    try:
        os.remove(config.SESSIONS_FILE)  # where older versions kept sessions: now they live and die with the server
    except OSError:
        pass
    for base in (config.CONFIG_DIR, config.CACHE_DIR):
        for folder, _dirs, files in os.walk(base):
            try:
                os.chmod(folder, 0o700)
            except OSError:
                pass
            for name in files:
                path = os.path.join(folder, name)
                try:
                    os.chmod(path, 0o600)
                    in_days = os.path.basename(os.path.dirname(folder)) == "days"  # days/<person>/<date>.json
                    if name.startswith(("proposal-", "askclaude-mcp-")) or os.path.basename(folder) == "days" or \
                            (in_days and time.time() - os.path.getmtime(path) > 120 * 86400):
                        os.remove(path)  # prompts and Ask Claude's MCP files are not kept; copies from before they
                        # were kept per person go
                except OSError:
                    pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=None, help="default 5610")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    ap.add_argument("--version", action="version", version=f"Pulsar {__version__}")
    ap.add_argument("--self-test", action="store_true", help="check this copy can run, then exit")
    ap.add_argument("--signin-url", action="store_true", help="print a one-time address that signs a browser in, then exit")
    ap.add_argument("--mcp", action="store_true",
                    help="serve Pulsar's tools to Claude Code over stdio (MCP); Pulsar itself must be running")
    ap.add_argument("--instance", choices=["preview"],
                    help="Pulsar Preview's own settings, sign-in and keychain items (its launcher sets this itself)")
    args = ap.parse_args()
    if args.mcp:  # first: a repository's settings may have set variables for it (mcp.clean_environment)
        from pulsar import mcp
        mcp.clean_environment()
    if args.instance:  # before pulsar.config is imported: it reads this once
        os.environ.update({"PULSAR_INSTANCE": args.instance, "PULSAR_PREVIEW": "1"})
    if args.port is None:
        args.port = 5610
    if args.mcp:  # stdout is the protocol: nothing else may print there
        return mcp.main(args.port)
    if args.self_test:
        return self_test()
    if args.signin_url:
        from pulsar import config
        os.umask(0o077)
        print(f"http://127.0.0.1:{args.port}/?code={config.new_signin_code()}")
        return 0
    os.umask(0o077)  # settings, caches and logs: readable by this user only
    from pulsar.server import serve
    tidy()
    try:
        server = serve(args.port)
    except OSError as exc:
        import errno
        print(f"Pulsar could not listen on port {args.port}: {exc}. Is it already running?", file=sys.stderr)
        return PORT_TAKEN if exc.errno == errno.EADDRINUSE else 1
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Pulsar {__version__}: {url}  (Ctrl+C to stop)")
    if not args.no_open:
        from pulsar import config
        signin = f"{url}?code={config.new_signin_code()}"  # the browser opens signed in
        threading.Timer(0.5, lambda: webbrowser.open(signin)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
