#!/usr/bin/env python3
"""Pulsar: your working day from ActivityWatch or MemTime, next to what you logged in Productive.

    python3 pulsar.py [--port 5610] [--no-open]

Then open http://127.0.0.1:5610/. Activity stays on this computer; Productive gets a time entry only when you
log one yourself in the page.
Python 3.9 or newer, standard library only.
"""

import argparse
import os
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # its own package first, however it was started
from pulsar import __version__  # noqa: E402
from pulsar.server import serve  # noqa: E402


def self_test() -> int:
    """Imports every module and checks the page's files: the updater runs this before it trusts a new version."""
    import os
    from pulsar import askclaude, config, dayplan, learn, likely, names, productive, report, server, sessions, trackers, updater  # noqa: F401
    web = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    need = ["index.html", "style.css", "meetings.css", "logday.css"] + [
        f"js/{n}.js" for n in ("core", "day", "report", "settings", "meetings", "log", "logday", "main")]
    missing = [n for n in need if not os.path.exists(os.path.join(web, n))]
    if missing:
        print("Pulsar self-test: missing " + ", ".join(missing), file=sys.stderr)
        return 1
    # serve one request on a free port, as the real server would
    import json
    import urllib.request
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/version", timeout=10) as resp:
            answered = json.loads(resp.read()).get("version")
    except Exception as exc:
        print(f"Pulsar self-test: the server did not answer: {exc}", file=sys.stderr)
        return 1
    finally:
        httpd.shutdown()
    if answered != __version__:
        print(f"Pulsar self-test: the server answered {answered}, not {__version__}", file=sys.stderr)
        return 1
    print(f"Pulsar {__version__} self-test: OK")
    return 0


def tidy() -> None:
    """Owner-only permissions on what older versions wrote, no copies of entries older than 120 days, no error
    logs older than 31 days."""
    import os
    import time
    import glob
    import shutil
    import tempfile
    from pulsar import config, logbook
    logbook.prune()
    for old in glob.glob(os.path.join(tempfile.gettempdir(), "pulsar-memtime-*")):  # copies a restart left behind
        try:
            if time.time() - os.path.getmtime(old) > 3600:
                shutil.rmtree(old, ignore_errors=True)
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
                    if name.startswith("proposal-") or os.path.basename(folder) == "days" or \
                            (in_days and time.time() - os.path.getmtime(path) > 120 * 86400):
                        os.remove(path)  # prompts are not kept; copies from before they were kept per person go
                except OSError:
                    pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=5610)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    ap.add_argument("--version", action="version", version=f"Pulsar {__version__}")
    ap.add_argument("--self-test", action="store_true", help="check this copy can run, then exit")
    ap.add_argument("--signin-url", action="store_true", help="print a one-time address that signs a browser in, then exit")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.signin_url:
        from pulsar import config
        os.umask(0o077)
        print(f"http://127.0.0.1:{args.port}/?code={config.new_signin_code()}")
        return 0
    os.umask(0o077)  # settings, caches and logs: readable by this user only
    tidy()
    try:
        server = serve(args.port)
    except OSError as exc:
        print(f"Pulsar could not listen on port {args.port}: {exc}. Is it already running?", file=sys.stderr)
        return 1
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
