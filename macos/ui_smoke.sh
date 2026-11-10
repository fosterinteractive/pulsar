#!/bin/bash
# The page in Pulsar's real window, clicked through: runs tests/ui/smoke.js (or the script given) in a test build of
# Pulsar Preview for this checkout, prints each check and exits 1 when one failed.
#
#   macos/ui_smoke.sh [script.js]
#
# The test build (PULSAR_SWIFT_FLAGS="-D PULSAR_TEST", in dist/) is made again when PulsarWindow.swift is newer than
# it. It opens on Pulsar Preview's port (5620): a Preview already running serves it (the code of its own checkout, so
# keep that checkout where this one is); otherwise the launcher starts this checkout's, and stops it when the window
# quits. The window shows for about ten seconds and quits by itself. Read only: the script writes nothing.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$(cd "$(dirname "${1:-$ROOT/tests/ui/smoke.js}")" && pwd)/$(basename "${1:-smoke.js}")"
APP="$ROOT/dist/Pulsar Preview.app"
[ -f "$SCRIPT" ] || { echo "ui_smoke.sh: no script at $SCRIPT" >&2; exit 2; }

if [ ! -x "$APP/Contents/MacOS/Pulsar" ] || [ "$ROOT/macos/PulsarWindow.swift" -nt "$APP/Contents/MacOS/Pulsar" ]; then
  echo "Building the test window…"
  PULSAR_SWIFT_FLAGS="-D PULSAR_TEST" PULSAR_PREVIEW_DIR="$ROOT" "$ROOT/macos/build_app.sh" >/dev/null
fi

OUT="$(mktemp -d "${TMPDIR:-/tmp}/pulsar-ui.XXXXXX")"
trap 'rm -rf "$OUT"' EXIT
# a new instance beside any Pulsar Preview already open; -W waits for it to quit (at most two minutes)
/usr/bin/perl -e 'alarm 120; exec @ARGV' /usr/bin/open -n -W --env PULSAR_TEST_DIR="$OUT" --env PULSAR_TEST_SCRIPT="$SCRIPT" "$APP"
[ -f "$OUT/ui.json" ] || { echo "ui_smoke.sh: the window wrote no answer (did the page load?)" >&2; exit 2; }

/usr/bin/python3 -I - "$OUT/ui.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
if isinstance(doc, dict):  # the script itself failed
    print("error:", doc.get("error")); sys.exit(1)
import os
if any(c["name"] == "the back-from-away panel is asked to draw" for c in doc):  # the window drew it into the folder
    size = open(os.path.join(os.path.dirname(sys.argv[1]), "back.txt")).read().split() if os.path.exists(
        os.path.join(os.path.dirname(sys.argv[1]), "back.txt")) else []
    doc.append({"name": "the back-from-away panel draws", "ok": size[2:] == ["330", "108"],
                "detail": "x".join(size[2:]) or "nothing drawn"})
bad = [c for c in doc if not c["ok"]]
for c in doc:
    print(("ok  " if c["ok"] else "FAIL") + "  " + c["name"] + (f"  ({c['detail']})" if c["detail"] else ""))
print(f"{len(doc) - len(bad)} of {len(doc)} passed")
sys.exit(1 if bad else 0)
PY
