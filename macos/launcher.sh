#!/bin/bash
# Pulsar.app launcher. build_app.sh copies this file to Pulsar.app/Contents/Resources/launcher.sh; the app's
# window (PulsarWindow.swift, the app's executable) runs it:
#
#   launcher.sh --window   start Pulsar and print "PULSAR_URL <address with a one-time sign-in code>" for the
#                          window to load, instead of opening the browser
#   launcher.sh --stop     stop the server, if this app started it (the app is quitting)
#   launcher.sh            start Pulsar and open it in the default browser
#
# Pulsar runs from an installed copy, not from the app:
#
#   ~/Library/Application Support/Pulsar/
#     versions/<X.Y.Z>/   full copies of the code (pulsar.py, pulsar/, web/, README.md)
#     current, previous   relative symlinks into versions/, switched atomically
#     bad-versions        versions that failed to start (or, from the updater, their self-test twice), one per line;
#                         never switched to again
#
# The app carries one version (B). The first time, or when B is newer than the installed one (C), B is
# copied into versions/ and becomes current, but only when a release signed it: Resources/app.manifest.json lists
# B's files and their SHA-256, signed with the release key, and release_sig.py (next to this script) checks the copy
# against it before the copy is put in place. When C is newer (the app updated itself since this bundle was
# built, pulsar/updater.py), C stays. Then the server starts from current in the background (it keeps
# running after this script exits) and http://127.0.0.1:5610/ opens in the window (or the default browser). If
# the new current does not start, it goes into bad-versions and previous starts instead.
#
#   PULSAR_PORT=5699          use another port (default 5610)
#   PULSAR_NO_BROWSER=1       do not open the browser (for tests)
#   PULSAR_TEST_DIALOGS=file  write each dialog's text to that file and take its default button, instead of
#                             showing it (for tests)
#   PULSAR_WINDOW_DIALOGS=1   set by the window of 1.6.0 on, with --window: dialogs and progress go to the window, which
#                             shows them as its own sheets (see dialog() and status() below)
#
# Server output goes to ~/Library/Logs/Pulsar.log. Settings and caches are the server's business
# (~/.config/pulsar, ~/.cache/pulsar; Pulsar Preview's are pulsar-preview); this script only writes the sign-in code.

set -u

PORT="${PULSAR_PORT:-5610}"
URL="http://127.0.0.1:${PORT}/"
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLED="$CONTENTS/Resources/app"
ICON="$CONTENTS/Resources/AppIcon.icns"
ROOT="$HOME/Library/Application Support/Pulsar"
BAD="$ROOT/bad-versions"
SIGNED="$CONTENTS/Resources/app.manifest.json"   # B's version and files (build_app.sh); .sig: the release signature
PORT_TAKEN=98   # pulsar.py's exit status when something else holds the port: says nothing about the version
LOG="$HOME/Library/Logs/Pulsar.log"
PY=/usr/bin/python3
MODE="${1:-}"
APP_BUNDLE="$(cd "$CONTENTS/.." && pwd -P)"   # this Pulsar.app: the server is told, for "Reinstall Pulsar" (appupdate.py)
XCODE_SELECT=/usr/bin/xcode-select
POLL=5   # seconds between looks while Apple's developer tools install
if [ -n "${PULSAR_TEST_DIALOGS:-}" ]; then  # tests only: a stand-in for xcode-select, and a shorter wait
  XCODE_SELECT="${PULSAR_TEST_XCODE_SELECT:-$XCODE_SELECT}"
  POLL="${PULSAR_TEST_POLL:-$POLL}"
fi
# The window of 1.6.0 on shows this script's dialogs as its own sheets, and what it is doing under its spinner: a line
# "PULSAR_DIALOG <text> <buttons>" or "PULSAR_STATUS <text>" (each base64) goes to it on fd 3, the script's own output
# kept apart from what $(dialog ...) captures, and a dialog's answer comes back on stdin. An older window, or the
# script run any other way, gets osascript's dialogs.
WINDOW_DIALOGS=""
if [ "$MODE" = "--window" ] && [ "${PULSAR_WINDOW_DIALOGS:-}" = "1" ]; then
  WINDOW_DIALOGS=1
  exec 3>&1
fi
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/pulsar"   # the server's session key and sign-in code live here
# Pulsar Preview (build_app.sh with PULSAR_PREVIEW_DIR): runs the code of a git checkout as it is, on its own port and
# log, never installs or updates anything, and does not write to Productive unless allowed in its Settings.
PREVIEW_DIR=""
if [ -f "$CONTENTS/Resources/preview-dir" ]; then
  PREVIEW_DIR="$(/usr/bin/head -1 "$CONTENTS/Resources/preview-dir")"
  PORT="${PULSAR_PORT:-5620}"
  URL="http://127.0.0.1:${PORT}/"
  LOG="$HOME/Library/Logs/Pulsar-Preview.log"
  # Its own settings, sign-in, sessions and keychain items (PULSAR_INSTANCE=preview), so nothing it does reaches
  # Pulsar's. The checkout's code says where it keeps them: an older checkout that does not know PULSAR_INSTANCE keeps
  # Pulsar's, and is read-only anyway (PULSAR_READ_ONLY, below). Only once the developer tools are there: before
  # that, /usr/bin/python3 is a stub that asks to install them.
  if /usr/bin/xcode-select -p >/dev/null 2>&1; then
    dir="$(PULSAR_INSTANCE=preview "$PY" -I -B -c 'import sys
sys.path.insert(0, sys.argv[1])
from pulsar import config
print(config.CONFIG_DIR)' "$PREVIEW_DIR" 2>/dev/null)"
    [ -n "$dir" ] && CONFIG_DIR="$dir"
  fi
fi

log() {
  /bin/mkdir -p "$(dirname "$LOG")"
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"
}

# What a command says when it fails goes to the log too, which the failure dialog points to.
/bin/mkdir -p "$(dirname "$LOG")" && exec 2>>"$LOG"

b64() { printf '%s' "$1" | /usr/bin/base64 | /usr/bin/tr -d '\n'; }

# Shows a dialog; the last button is the default. Prints the button clicked (nothing if cancelled).
dialog() {
  if [ -n "$WINDOW_DIALOGS" ]; then  # a sheet on the window, which answers with the button's title
    local answer=""
    printf 'PULSAR_DIALOG %s %s\n' "$(b64 "$1")" "$(b64 "$(printf '%s\n' "${@:2}")")" >&3
    IFS= read -r answer || answer=""
    printf '%s\n' "$answer"
    return 0
  fi
  if [ -n "${PULSAR_TEST_DIALOGS:-}" ]; then
    printf '%s\n---\n' "$1" >>"$PULSAR_TEST_DIALOGS"
    printf '%s\n' "${@: -1}"
    return 0
  fi
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

# What the launcher is doing, under the window's spinner (the window of 1.6.0 on; nothing otherwise).
status() {
  [ -n "$WINDOW_DIALOGS" ] && printf 'PULSAR_STATUS %s\n' "$(b64 "$1")" >&3
  return 0
}

fail_dialog() {
  local choice
  choice="$(dialog "$1

The details are in the log: ${LOG/#$HOME/~}" "Show log" "OK")"
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
  else  # the browser, through osascript reading its script on stdin: /usr/bin/open would show the code in ps
    printf 'open location "%s?code=%s"\n' "$URL" "$code" | /usr/bin/osascript - >/dev/null
  fi
}

# True when the Pulsar on PORT holds this user's session key: it answers a random challenge with an HMAC that only
# the key's holder can compute, bound to PORT ("whoami-v1|<port>|<nonce>"), so a program on PORT that passes the
# question on to this user's Pulsar on another port gets an answer for the wrong port. Another account's Pulsar, or
# anything else on the port, fails. A Pulsar of 1.4.0 or older answers only the older form (no port in it): that
# answer counts only from a listener this user's own process holds, running pulsar.py, as during an update from one;
# lsof does not show another account's process, and a program of this user could read the key anyway.
is_ours() {
  local nonce answer proof proof1 want1 want0 pid
  [ -r "$CONFIG_DIR/secret" ] || return 1
  nonce="$(/usr/bin/openssl rand -hex 16)"
  answer="$(/usr/bin/curl -sf -m 2 "${URL}api/whoami?nonce=$nonce" 2>/dev/null)"
  proof1="$(printf '%s' "$answer" | /usr/bin/grep -o '"proof1": *"[0-9a-f]*"' | /usr/bin/sed 's/.*"\([0-9a-f]*\)"$/\1/')"
  proof="$(printf '%s' "$answer" | /usr/bin/grep -o '"proof": *"[0-9a-f]*"' | /usr/bin/sed 's/.*"\([0-9a-f]*\)"$/\1/')"
  read -r want1 want0 <<<"$("$PY" -c 'import hashlib, hmac, sys
key = open(sys.argv[1]).read().strip().encode()
print(hmac.new(key, ("whoami-v1|%d|%s" % (int(sys.argv[2]), sys.argv[3])).encode(), hashlib.sha256).hexdigest(),
      hmac.new(key, sys.argv[3].encode(), hashlib.sha256).hexdigest())' "$CONFIG_DIR/secret" "$PORT" "$nonce" 2>/dev/null)"
  [ -n "$want1" ] || return 1
  if [ -n "$proof1" ]; then
    [ "$proof1" = "$want1" ]
    return
  fi
  pid="$(/usr/sbin/lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/head -1)"
  [ -n "$pid" ] && [[ "$(/bin/ps -o command= -p "$pid" 2>/dev/null)" == *pulsar.py* ]] &&
    [ -n "$proof" ] && [ "$proof" = "$want0" ]
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
  [[ "$cmd" == *"/Library/Application Support/Pulsar/versions/"* || "$cmd" == *"/Contents/Resources/app/pulsar.py"* ]] ||
    { [ -n "$PREVIEW_DIR" ] && [[ "$cmd" == *"$PREVIEW_DIR/pulsar.py"* ]]; }
}

# Starts the copy in folder $1 in the background and waits for it to answer with this user's key (is_ours): a
# version answering on the port is not enough, since something else may hold the port while this copy failed to get
# it. Returns 0 once it answers; 2 when the port stayed taken (pulsar.py exits PORT_TAKEN: tried again, five times in
# all, and never the copy's fault); 1 if it stops or does not answer within 15 seconds (then it is stopped).
start_server() {
  local real="$1" server rc try holder
  for try in 1 2 3 4 5; do
    log "Pulsar $(version_of "$real") starting on port $PORT from $real"
    cd "$real" || return 1
    # Its own session, so it outlives this script and the app's process group. -B: no __pycache__ written.
    PULSAR_APP_BUNDLE="$APP_BUNDLE" /usr/bin/nohup "$PY" -c 'import os, sys
if os.getpid() != os.getpgrp():
    os.setsid()
os.execv("/usr/bin/python3", ["/usr/bin/python3", "-E", "-s", "-B"] + sys.argv[1:])' \
      "$real/pulsar.py" --no-open --port "$PORT" >>"$LOG" 2>&1 </dev/null 3>&- &   # 3>&-: none of the window's pipe
    server=$! rc=""
    for _ in $(seq 1 60); do
      sleep 0.25
      [ -n "$(running_version)" ] && is_ours && return 0
      kill -0 "$server" 2>/dev/null || { wait "$server"; rc=$?; break; }
    done
    [ "$rc" = "$PORT_TAKEN" ] || break
    holder="$(listener_pid)"
    log "Port $PORT was taken while Pulsar $(version_of "$real") started (try $try of 5)${holder:+, by: $(/bin/ps -o command= -p "$holder" 2>/dev/null)}"
    sleep 1
  done
  [ "$rc" = "$PORT_TAKEN" ] && return 2
  is_ours && return 0
  kill "$server" 2>/dev/null
  if [ -n "$(running_version)" ]; then
    log "Pulsar $(version_of "$real") did not start: port $PORT answers as Pulsar, but not with this user's key"
  else
    log "Pulsar $(version_of "$real") did not start"
  fi
  return 1
}

# Prints nothing and returns 0 when folder $1 holds exactly the code a release signed as version B: the app's
# manifest and its signature, checked against the release keys by release_sig.py, the same check the updater makes,
# run from the copy beside this script (outside the code it checks). Otherwise prints why not and returns 1.
code_signed() {
  [ -f "$CONTENTS/Resources/release_sig.py" ] || { echo "it has no release check"; return 1; }
  "$PY" -I -B "$CONTENTS/Resources/release_sig.py" check-bundle "$1" "$SIGNED" "$SIGNED.sig" "$B"
}

# Puts the app's code into versions/B: a copy, checked file by file against the signed manifest before it is moved
# into place, so what is installed is what was checked. Returns 1, with the reason in WHY, when the app's code is not
# a signed release; then nothing is installed.
install_bundled() {
  local dest="$ROOT/versions/$B" tmp="$ROOT/versions/.$B.tmp-launcher"
  WHY="$(code_signed "$dest")" && return 0   # already there, exactly as signed
  /bin/rm -rf "$tmp"
  /usr/bin/ditto --noqtn "$BUNDLED" "$tmp" || fail_dialog "Pulsar could not install version $B."
  if ! WHY="$(code_signed "$tmp")"; then
    /bin/rm -rf "$tmp"
    return 1
  fi
  { /bin/rm -rf "$dest" && /bin/mv "$tmp" "$dest"; } || fail_dialog "Pulsar could not install version $B."
}

# The app is quitting: stop the server if this app started it and it holds this user's key. A server started any
# other way (python3 pulsar.py in a git checkout) keeps running.
if [ "$MODE" = "--stop" ]; then
  pid="$(listener_pid)"
  if [ -n "$pid" ] && started_by_app "$pid" && is_ours; then
    log "Stopping Pulsar: the app is quitting"
    quit_server
  fi
  exit 0
fi

# 1. Python 3.9+. On a fresh Mac /usr/bin/python3 is only a stub until Apple's Command Line Tools are
#    installed; check for the tools first so the stub's own prompt does not appear.
if ! "$XCODE_SELECT" -p >/dev/null 2>&1; then
  if [ "$MODE" != "--window" ]; then
    choice="$(dialog "Pulsar needs Apple's free developer tools (about 5 minutes, one time). Install now?

macOS asks for an administrator password. When the installation has finished, open Pulsar again." "Not now" "Install")"
    [ "$choice" = "Install" ] && "$XCODE_SELECT" --install >/dev/null 2>&1
    exit 0
  fi
  # The window waits with the person, and Pulsar goes on by itself once the tools are there.
  choice="$(dialog "Pulsar needs Apple's free developer tools (about 5 minutes, one time). Install now?

macOS asks for an administrator password. Pulsar goes on by itself once they are installed." "Not now" "Install")"
  [ "$choice" = "Install" ] || exit 0
  log "Installing Apple's developer tools"
  "$XCODE_SELECT" --install >/dev/null 2>&1
  waited=0
  until "$XCODE_SELECT" -p >/dev/null 2>&1; do
    status "Installing Apple's developer tools. Pulsar goes on by itself when they are installed."
    if [ "$waited" -ge 3600 ]; then
      dialog "Apple's developer tools are not installed yet. When their installation has finished, open Pulsar again." "OK" >/dev/null
      exit 0
    fi
    sleep "$POLL"
    waited=$((waited + POLL))
  done
  log "Apple's developer tools are installed; going on"
  status "Starting Pulsar…"
fi
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
  dialog "Pulsar needs Python 3.9 or newer, which comes with Apple's developer tools.

Update them in System Settings > General > Software Update, then open Pulsar again." "OK" >/dev/null
  exit 1
fi

# Pulsar Preview: the checkout as it is. Already running: open it. Otherwise start it (quitting the app stops it,
# so opening it again always runs the code as it is now).
if [ -n "$PREVIEW_DIR" ]; then
  [ -f "$PREVIEW_DIR/pulsar.py" ] || fail_dialog "Pulsar Preview found no code at $PREVIEW_DIR."
  pid="$(listener_pid)"
  if [ -n "$pid" ]; then
    is_ours && { open_page; exit 0; }
    fail_dialog "Pulsar Preview could not start: another program is using port $PORT."
  fi
  # PULSAR_READ_ONLY=preview: this code writes to Productive only when its Settings allow it; code that predates
  # Preview takes any value as read-only and writes nothing.
  export PULSAR_PREVIEW=1 PULSAR_INSTANCE=preview
  [ -n "${PULSAR_READ_ONLY:-}" ] || export PULSAR_READ_ONLY=preview
  start_server "$PREVIEW_DIR"
  case $? in
    0) open_page; exit 0 ;;
    2) fail_dialog "Pulsar Preview could not start: another program is using port $PORT." ;;
  esac
  fail_dialog "Pulsar Preview could not start."
fi

# 2. Install or upgrade from the app's own copy, only as a release signed it (install_bundled). Code changed after the
#    release was built, or an app built without the release key, installs nothing: such a copy could otherwise put a
#    version higher than any release in place, which no real update would ever replace.
B="$(version_of "$BUNDLED")"
[[ "$B" =~ ^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$ ]] || fail_dialog "This copy of Pulsar is damaged. Download it again."
CUR_REAL="$(real_of current)"
C="$([ -n "$CUR_REAL" ] && version_of "$CUR_REAL")"
if [ -z "$C" ] || { [ "$(vercmp "$B" "$C")" = 1 ] && ! is_bad "$B"; }; then
  /bin/mkdir -p "$ROOT/versions" || fail_dialog "Pulsar could not create its folder in Application Support."
  if install_bundled; then
    [ -n "$C" ] && [ "$C" != "$B" ] && set_link previous "$(/usr/bin/readlink "$ROOT/current")"
    set_link current "versions/$B" || fail_dialog "Pulsar could not switch to version $B."
    log "Installed Pulsar $B from the app${C:+ (was $C)}"
    CUR_REAL="$(real_of current)"
    C="$B"
  else
    log "Did not install Pulsar $B from the app: ${WHY:-the check gave no reason}"
    refused="This copy of Pulsar is not a signed release: ${WHY:-it could not be checked}. Pulsar did not install it.

Download Pulsar.zip again from github.com/fosterinteractive/pulsar/releases."
    [ -n "$C" ] || fail_dialog "$refused"
    dialog "$refused

Pulsar $C, installed before, opens instead." "OK" >/dev/null
  fi
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
  CUR_LINK="$(/usr/bin/readlink "$ROOT/current")" PREV_LINK="$(/usr/bin/readlink "$ROOT/previous" 2>/dev/null)"
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
  # An older Pulsar may move current while it quits (an update it had ready): this launcher's choice stands.
  if [ "$(/usr/bin/readlink "$ROOT/current")" != "$CUR_LINK" ]; then
    log "The older Pulsar moved current while quitting; back to $CUR_LINK"
    set_link current "$CUR_LINK"
    [ -n "$PREV_LINK" ] && [ -d "$ROOT/$PREV_LINK" ] && set_link previous "$PREV_LINK"
  fi
fi

# 4. Start current. If it does not come up, try previous: if previous starts where current did not, current is the
#    problem (whatever its self-test said), so it is marked bad and previous stays. If neither starts, the cause is
#    the Mac (the port, a slow start), not a version: nothing is marked and a dialog says so. A port that stays taken
#    while current starts is never current's fault: then nothing is marked and previous is not tried.
start_server "$CUR_REAL"
case $? in
  0) open_page; exit 0 ;;
  2) fail_dialog "Pulsar could not start: another program is using port $PORT. Nothing was changed; try opening it again." ;;
esac
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
