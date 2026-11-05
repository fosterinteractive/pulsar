#!/bin/bash
# Pulsar.app launcher. build_app.sh copies this file to Pulsar.app/Contents/Resources/launcher.sh; the app's
# window (PulsarWindow.swift, the app's executable) runs it:
#
#   launcher.sh --window   start Pulsar and print "PULSAR_URL <address with a one-time sign-in code>" for the
#                          window to load, instead of opening the browser
#   launcher.sh --stop     stop the server, if this app started it (the window was closed)
#   launcher.sh            start Pulsar and open it in the default browser
#
# Pulsar runs from an installed copy, not from the app:
#
#   ~/Library/Application Support/Pulsar/
#     versions/<X.Y.Z>/   full copies of the code (pulsar.py, pulsar/, web/, README.md)
#     current, previous   relative symlinks into versions/, switched atomically
#     bad-versions        versions that failed to start, one per line; never switched to again
#
# The app carries one version (B). The first time, or when B is newer than the installed one (C), B is
# copied into versions/ and becomes current. When C is newer (the app updated itself since this bundle was
# built, pulsar/updater.py), C stays. Then the server starts from current in the background (it keeps
# running after this script exits) and http://127.0.0.1:5610/ opens in the window (or the default browser). If
# the new current does not start, it goes into bad-versions and previous starts instead.
#
#   PULSAR_PORT=5699      use another port (default 5610)
#   PULSAR_NO_BROWSER=1   do not open the browser (for tests)
#
# Server output goes to ~/Library/Logs/Pulsar.log. Settings and caches are the server's business
# (~/.config/pulsar, ~/.cache/pulsar); this script never touches them.

set -u

PORT="${PULSAR_PORT:-5610}"
URL="http://127.0.0.1:${PORT}/"
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLED="$CONTENTS/Resources/app"
ICON="$CONTENTS/Resources/AppIcon.icns"
ROOT="$HOME/Library/Application Support/Pulsar"
BAD="$ROOT/bad-versions"
LOG="$HOME/Library/Logs/Pulsar.log"
PY=/usr/bin/python3
MODE="${1:-}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/pulsar"   # the server's session key and sign-in code live here

log() {
  /bin/mkdir -p "$(dirname "$LOG")"
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"
}

# What a command says when it fails goes to the log too, which the failure dialog points to.
/bin/mkdir -p "$(dirname "$LOG")" && exec 2>>"$LOG"

# Shows a dialog; the last button is the default. Prints the button clicked (nothing if cancelled).
dialog() {
  /usr/bin/osascript \
    -e 'on run argv' \
    -e '  try' \
    -e '    set ic to (POSIX file (item 1 of argv)) as alias' \
    -e '  on error' \
    -e '    set ic to note' \
    -e '  end try' \
    -e '  set btns to items 3 thru -1 of argv' \
    -e '  display dialog (item 2 of argv) with title "Pulsar" with icon ic buttons btns default button (count of btns)' \
    -e '  return button returned of result' \
    -e 'end run' \
    "$ICON" "$@" 2>/dev/null || true
}

fail_dialog() {
  local choice
  choice="$(dialog "$1

The details are in the log: ~/Library/Logs/Pulsar.log" "Show log" "OK")"
  [ "$choice" = "Show log" ] && /usr/bin/open -a Console "$LOG"
  exit 1
}

# Opens the page signed in: a one-time code (valid two minutes, owner-only file) that the page trades for a session
# token. The code never appears in a process list (the window reads it from this script's output, a pipe); the
# long-lived key never leaves its file.
open_page() {
  [ "${PULSAR_NO_BROWSER:-}" = "1" ] && [ "$MODE" != "--window" ] && return 0
  local code
  code="$(/usr/bin/openssl rand -hex 16)"
  ( umask 077; /bin/mkdir -p "$CONFIG_DIR" && printf '%s %s' "$code" "$(date +%s)" >"$CONFIG_DIR/signin-code" )
  if [ "$MODE" = "--window" ]; then
    printf 'PULSAR_URL %s?code=%s\n' "$URL" "$code"
  else
    /usr/bin/open "${URL}?code=$code"
  fi
}

# True when the Pulsar on PORT holds this user's session key: it answers a random challenge with an HMAC that only
# the key's holder can compute. Another account's Pulsar, or anything else on the port, fails.
is_ours() {
  local nonce proof expected
  [ -r "$CONFIG_DIR/secret" ] || return 1
  nonce="$(/usr/bin/openssl rand -hex 16)"
  proof="$(/usr/bin/curl -sf -m 2 "${URL}api/whoami?nonce=$nonce" 2>/dev/null \
    | /usr/bin/grep -o '"proof": *"[0-9a-f]*"' | /usr/bin/sed 's/.*"\([0-9a-f]*\)"$/\1/')"
  expected="$("$PY" -c 'import hashlib, hmac, sys
key = open(sys.argv[1]).read().strip().encode()
print(hmac.new(key, sys.argv[2].encode(), hashlib.sha256).hexdigest())' "$CONFIG_DIR/secret" "$nonce" 2>/dev/null)"
  [ -n "$proof" ] && [ "$proof" = "$expected" ]
}

# POST /api/quit with this user's key in a header, passed on stdin so it never shows in a process list.
quit_server() {
  [ -r "$CONFIG_DIR/secret" ] || return 1
  printf 'X-Pulsar-Key: %s\n' "$(/bin/cat "$CONFIG_DIR/secret")" \
    | /usr/bin/curl -s -m 3 -X POST -H @- -H 'X-Pulsar: 1' -H 'Content-Type: application/json' -d '{}' \
      "${URL}api/quit" >/dev/null 2>&1
}

# The version of the Pulsar answering on PORT, or nothing if none does.
running_version() {
  /usr/bin/curl -sf -m 2 "${URL}api/version" 2>/dev/null \
    | /usr/bin/grep -o '"version": *"[^"]*"' | /usr/bin/head -1 | /usr/bin/sed 's/.*"\([^"]*\)"$/\1/'
}

listener_pid() {
  /usr/sbin/lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/head -1
}

# __version__ of a copy of the code (a folder holding pulsar/__init__.py), or nothing.
version_of() {
  /usr/bin/sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$1/pulsar/__init__.py" 2>/dev/null | /usr/bin/head -1
}

# vercmp A B prints 1, 0 or -1: numeric per part, so 1.10.0 > 1.9.0.
vercmp() {
  local IFS=. i n a b
  local -a x y
  x=($1) y=($2)
  n=${#x[@]}
  [ "${#y[@]}" -gt "$n" ] && n=${#y[@]}
  for ((i = 0; i < n; i++)); do
    a="${x[i]:-0}" b="${y[i]:-0}"
    a="${a//[!0-9]/}" b="${b//[!0-9]/}"
    a=$((10#${a:-0})) b=$((10#${b:-0}))
    [ "$a" -gt "$b" ] && { echo 1; return; }
    [ "$a" -lt "$b" ] && { echo -1; return; }
  done
  echo 0
}

is_bad() {
  [ -n "$1" ] && [ -f "$BAD" ] && /usr/bin/grep -qxF "$1" "$BAD"
}

# Points ROOT/<name> at <target> (relative), atomically: a new link under a temporary name, renamed over the old.
set_link() {
  local tmp="$ROOT/.$1.tmp-launcher"
  /bin/rm -f "$tmp"
  /bin/ln -s "$2" "$tmp" &&
    "$PY" -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$tmp" "$ROOT/$1"
}

# The real folder behind ROOT/<name>, or nothing if the link is missing or broken.
real_of() {
  [ -d "$ROOT/$1" ] && (cd "$ROOT/$1" && pwd -P)
}

# A server this app started (from an installed copy, or from inside an older app bundle). A server started
# any other way, such as python3 pulsar.py in a git checkout, is never stopped.
started_by_app() {
  local cmd
  cmd="$(/bin/ps -o command= -p "$1" 2>/dev/null)"
  [[ "$cmd" == *"/Library/Application Support/Pulsar/versions/"* || "$cmd" == *"/Contents/Resources/app/pulsar.py"* ]]
}

# Starts the copy in folder $1 in the background and waits for it to answer. Returns 1 if it stops or
# does not answer within 15 seconds (then it is stopped).
start_server() {
  local real="$1" server
  log "Pulsar $(version_of "$real") starting on port $PORT from $real"
  cd "$real" || return 1
  # Its own session, so it outlives this script and the app's process group. -B: no __pycache__ written.
  /usr/bin/nohup "$PY" -c 'import os, sys
if os.getpid() != os.getpgrp():
    os.setsid()
os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", "-B"] + sys.argv[1:])' \
    "$real/pulsar.py" --no-open --port "$PORT" >>"$LOG" 2>&1 </dev/null &
  server=$!
  for _ in $(seq 1 60); do
    sleep 0.25
    [ -n "$(running_version)" ] && return 0
    kill -0 "$server" 2>/dev/null || break
  done
  [ -n "$(running_version)" ] && return 0
  kill "$server" 2>/dev/null
  log "Pulsar $(version_of "$real") did not start"
  return 1
}

# The window was closed: stop the server if this app started it and it holds this user's key. A server started any
# other way (python3 pulsar.py in a git checkout) keeps running.
if [ "$MODE" = "--stop" ]; then
  pid="$(listener_pid)"
  if [ -n "$pid" ] && started_by_app "$pid" && is_ours; then
    log "Stopping Pulsar: its window was closed"
    quit_server
  fi
  exit 0
fi

# 1. Python 3.9+. On a fresh Mac /usr/bin/python3 is only a stub until Apple's Command Line Tools are
#    installed; check for the tools first so the stub's own prompt does not appear.
if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
  choice="$(dialog "Pulsar needs Apple's free developer tools (about 5 minutes, one time). Install now?

macOS asks for an administrator password. When the installation has finished, open Pulsar again." "Not now" "Install")"
  [ "$choice" = "Install" ] && /usr/bin/xcode-select --install >/dev/null 2>&1
  exit 0
fi
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
  dialog "Pulsar needs Python 3.9 or newer, which comes with Apple's developer tools.

Update them in System Settings > General > Software Update, then open Pulsar again." "OK" >/dev/null
  exit 1
fi

# 2. Install or upgrade from the app's own copy.
B="$(version_of "$BUNDLED")"
[[ "$B" =~ ^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$ ]] || fail_dialog "This copy of Pulsar is damaged. Download it again."
CUR_REAL="$(real_of current)"
C="$([ -n "$CUR_REAL" ] && version_of "$CUR_REAL")"
if [ -z "$C" ] || { [ "$(vercmp "$B" "$C")" = 1 ] && ! is_bad "$B"; }; then
  /bin/mkdir -p "$ROOT/versions" || fail_dialog "Pulsar could not create its folder in Application Support."
  dest="$ROOT/versions/$B"
  if [ "$(version_of "$dest")" != "$B" ]; then
    tmp="$ROOT/versions/.$B.tmp-launcher"
    /bin/rm -rf "$tmp"
    /usr/bin/ditto --noqtn "$BUNDLED" "$tmp" && /bin/rm -rf "$dest" && /bin/mv "$tmp" "$dest" ||
      fail_dialog "Pulsar could not install version $B."
  fi
  [ -n "$C" ] && [ "$C" != "$B" ] && set_link previous "$(/usr/bin/readlink "$ROOT/current")"
  set_link current "versions/$B" || fail_dialog "Pulsar could not switch to version $B."
  log "Installed Pulsar $B from the app${C:+ (was $C)}"
  CUR_REAL="$(real_of current)"
  C="$B"
fi

# 3. Already running? Open the page. A server of another version than current that this app started (an
#    older copy, or one the app has since updated past) is stopped and current starts instead.
running="$(running_version)"
pid="$(listener_pid)"
if [ -n "$running" ] || [ -n "$pid" ]; then
  if [ -z "$running" ]; then
    # Something holds the port but does not answer as Pulsar.
    if [ -n "$pid" ] && [[ "$(/bin/ps -o command= -p "$pid" 2>/dev/null)" == *pulsar.py* ]] && is_ours; then
      open_page
      exit 0
    fi
    log "Port $PORT is used by another program: $(/bin/ps -o command= -p "$pid" 2>/dev/null)"
    fail_dialog "Pulsar could not start: another program is using port $PORT."
  fi
  if ! is_ours; then
    # A Pulsar that does not hold this user's key: another account's on this Mac (lsof cannot see its process),
    # or something pretending. Never open it: it would see this user's sign-in code.
    log "Port $PORT answers as Pulsar $running but not with this user's key"
    fail_dialog "Another Pulsar is already running on this Mac (perhaps another account's). Quit it, or start this one on another port."
  fi
  if [ "$running" = "$C" ] || [ -z "$pid" ] || ! started_by_app "$pid"; then
    open_page
    exit 0
  fi
  log "Stopping Pulsar $running to start $C"
  quit_server
  for _ in $(seq 1 20); do
    [ -z "$(listener_pid)" ] && break
    sleep 0.25
  done
  if [ -n "$(listener_pid)" ]; then
    kill "$pid" 2>/dev/null
    for _ in $(seq 1 12); do
      [ -z "$(listener_pid)" ] && break
      sleep 0.25
    done
  fi
  # A port still taken is not the new version's fault: say so, and mark nothing bad.
  [ -n "$(listener_pid)" ] && fail_dialog "Pulsar could not start: the older Pulsar on port $PORT did not stop."
fi

# 4. Start current. If it does not come up, try previous: if previous starts where current did not, current is the
#    problem (whatever its self-test said), so it is marked bad and previous stays. If neither starts, the cause is
#    the Mac (the port, a slow start), not a version: nothing is marked and a dialog says so.
start_server "$CUR_REAL" && { open_page; exit 0; }
PREV_REAL="$(real_of previous)"
if [ -n "$PREV_REAL" ] && [ "$PREV_REAL" != "$CUR_REAL" ]; then
  log "Pulsar $C did not start; trying $(version_of "$PREV_REAL")"
  if start_server "$PREV_REAL"; then
    is_bad "$C" || echo "$C" >>"$BAD"
    log "Pulsar $C marked bad; back on $(version_of "$PREV_REAL")"
    set_link current "$(/usr/bin/readlink "$ROOT/previous")"
    open_page
    exit 0
  fi
fi
fail_dialog "Pulsar could not start. Nothing was changed; try opening it again."
