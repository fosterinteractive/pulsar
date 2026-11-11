#!/bin/bash
# Builds a Pulsar release, and with --publish puts it on GitHub, where every installed Pulsar finds it.
#
#   macos/release.sh 1.1.0 --notes "Adds the week view."             build only, then print the next steps
#   macos/release.sh 1.1.0 --notes "Adds the week view." --publish   build, commit the version, push, release
#
# Refuses anything but main exactly as it is on GitHub (HEAD must be origin/main, the tree clean, checked again right
# before the first signature), a version that is not newer than pulsar/__init__.py, a version with no entry in
# CHANGELOG.md, failing tests, and a security review that did not pass (.github/workflows/security.yml).
# Then it sets __version__ and builds, in dist/:
#   release/pulsar-<version>.zip   the code, one top folder pulsar-<version>/ (what the in-app updater downloads)
#   release/latest.json            {version, url, sha256, notes, published, expires, versions, app}, signed (.sig);
#                                  app: {version, min, url, sha256} of Pulsar.zip, for "Reinstall Pulsar" (pulsar/appupdate.py)
#   Pulsar.zip                     the whole app, for new installs, its code signed too (build_app.sh)
#   Pulsar.zip.sig                 the release key's signature of Pulsar.zip itself, which README.md's install line
#                                  checks with /usr/bin/openssl before it unpacks anything
# With --publish it shows what it will do, asks, commits the version change, pushes it to main and runs
# gh release create v<version> with the five files (the code zip, latest.json and its .sig, Pulsar.zip and its .sig)
# on fosterinteractive/pulsar.

set -euo pipefail

REPO="fosterinteractive/pulsar"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "release.sh: $*" >&2; exit 1; }

# Only main as it is on GitHub is signed, so what protects main there (reviews, no force push) also decides what the
# release key signs: never another branch, a commit not pushed, or a change in the tree. $1: the one line of
# `git status --porcelain` allowed (the version this script sets), or nothing.
on_main() {
  git fetch -q origin main || die "could not fetch origin/main to compare with; nothing was signed"
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] ||
    die "HEAD ($(git rev-parse --short HEAD)) is not origin/main ($(git rev-parse --short origin/main)): release from main as it is on GitHub; nothing was signed"
  local changed
  changed="$(git status --porcelain --untracked-files=all | grep -v -x -F -e "${1:-}" || true)"
  [ -z "$changed" ] || { echo "$changed" >&2; die "the git tree has changes: commit or stash them first; nothing was signed"; }
}

VERSION="" NOTES="" PUBLISH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --publish) PUBLISH=1 ;;
    --notes) [ $# -ge 2 ] || die "--notes needs a text"; NOTES="$2"; shift ;;
    --notes=*) NOTES="${1#--notes=}" ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) [ -z "$VERSION" ] || die "one version only"; VERSION="$1" ;;
  esac
  shift
done
[ -n "$VERSION" ] || die "usage: macos/release.sh <X.Y.Z> [--notes \"...\"] [--publish]"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "the version must look like 1.2.0, not $VERSION"

# --- checks --------------------------------------------------------------------------------------------
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git repository"
on_main

CURRENT="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' pulsar/__init__.py)"
[ -n "$CURRENT" ] || die "no __version__ in pulsar/__init__.py"
python3 -c 'import sys
v = lambda s: tuple(int(p) for p in s.split("."))
sys.exit(0 if v(sys.argv[1]) > v(sys.argv[2]) else 1)' "$VERSION" "$CURRENT" ||
  { [ "$VERSION" = "$CURRENT" ] && [ -z "$(git tag -l "v$VERSION")" ] && echo "First release of $VERSION"; } ||
  die "$VERSION is not newer than the current version $CURRENT"

# What changed, for colleagues: Settings shows this copy's CHANGELOG.md, so every release needs its entry there.
python3 -E -s - "$ROOT" "$VERSION" <<'PY' || die "CHANGELOG.md has no entry for $VERSION: add \"## $VERSION ($(date +%Y-%m-%d))\" and a \"- \" line under it, then commit it"
import sys
sys.path.insert(0, sys.argv[1])
from pulsar import changelog
sys.exit(0 if any(r["version"] == sys.argv[2] and r["lines"] for r in changelog.read()) else 1)
PY

echo "Running the tests"
python3 -m unittest discover -s tests -q || die "the tests fail; nothing was changed"

if [ "$PUBLISH" = 1 ]; then
  command -v gh >/dev/null || die "--publish needs the GitHub CLI (gh)"
  gh auth status >/dev/null 2>&1 || die "gh is not signed in: run gh auth login"
  gh repo view "$REPO" >/dev/null 2>&1 || die "cannot see the repository $REPO"
  ! gh release view "v$VERSION" --repo "$REPO" >/dev/null 2>&1 || die "release v$VERSION already exists on $REPO"
fi

# A security review of what changed since the last release (.github/workflows/security.yml): Claude reads the diff on
# GitHub, and a finding it marks "block" stops the release (agreed 2026-11-11). It reads the commit on GitHub, which
# on_main has checked is this one; a review that already passed for this range is used again. Only when GitHub or
# Claude is down: PULSAR_SKIP_SECURITY="<why>" releases without it, and says so.
security_review() {
  command -v gh >/dev/null || die "the security review needs the GitHub CLI (gh); nothing was signed"
  local head base title since run=""
  head="$(git rev-parse HEAD)"
  base="$(gh release view --repo "$REPO" --json tagName -q .tagName 2>/dev/null || true)"
  [[ "$base" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "could not read the last release tag from $REPO ($base); nothing was signed"
  title="Security review $base..$head"
  run="$(gh run list --repo "$REPO" --workflow security.yml --commit "$head" --limit 20 --json databaseId,displayTitle,conclusion \
         -q "[.[] | select(.displayTitle == \"$title\" and .conclusion == \"success\")][0].databaseId // empty" 2>/dev/null || true)"
  if [ -z "$run" ]; then
    echo "Security review of $base..${head:0:7} on GitHub (a few minutes)"
    since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    gh workflow run security.yml --repo "$REPO" -f base="$base" -f head="$head" >/dev/null ||
      die "could not start the security review; nothing was signed"
    for _ in $(seq 1 30); do
      sleep 4
      run="$(gh run list --repo "$REPO" --workflow security.yml --commit "$head" --limit 20 --json databaseId,displayTitle,createdAt \
             -q "[.[] | select(.displayTitle == \"$title\" and .createdAt >= \"$since\")][0].databaseId // empty" 2>/dev/null || true)"
      [ -n "$run" ] && break
    done
    [ -n "$run" ] || die "the security review did not start on GitHub; nothing was signed"
    gh run watch "$run" --repo "$REPO" --exit-status >/dev/null 2>&1 || true
  fi
  local out; out="$(mktemp -d)"
  gh run download "$run" --repo "$REPO" -n security-review -D "$out" >/dev/null 2>&1 && cat "$out/review.md" 2>/dev/null || true
  rm -rf "$out"
  [ "$(gh run view "$run" --repo "$REPO" --json conclusion -q .conclusion)" = success ] ||
    die "the security review stopped the release: https://github.com/$REPO/actions/runs/$run; nothing was signed"
  echo "Security review passed: https://github.com/$REPO/actions/runs/$run"
}
if [ -n "${PULSAR_SKIP_SECURITY:-}" ]; then
  echo "Security review SKIPPED: $PULSAR_SKIP_SECURITY"
else
  security_review
fi

if [ -z "$NOTES" ]; then
  [ -t 0 ] || die "give the release notes with --notes \"...\""
  read -r -p "Release notes (one or two plain sentences): " NOTES
  [ -n "$NOTES" ] || die "the release notes are empty"
fi

# Each minor version is named after a real pulsar (pulsar/names.py): in the title, and one line in the notes.
IFS=$'\t' read -r CODENAME CODELINE < <(python3 -E -s -c 'import sys
sys.path.insert(0, sys.argv[1])
from pulsar import names
print("\t".join(names.codename(sys.argv[2])))' "$ROOT" "$VERSION")
TITLE="Pulsar $VERSION"
if [ -n "${CODENAME:-}" ]; then
  TITLE="Pulsar $VERSION · $CODENAME"
  NOTES="$NOTES Named after $CODENAME, $CODELINE."
fi

# --- build ---------------------------------------------------------------------------------------------
echo "Setting the version to $VERSION (was $CURRENT)"
python3 - "$VERSION" <<'PY'
import re, sys
path = "pulsar/__init__.py"
text = open(path, encoding="utf-8").read()
new, n = re.subn(r'^__version__ = "[^"]*"', f'__version__ = "{sys.argv[1]}"', text, count=1, flags=re.M)
if n != 1:
    sys.exit("could not set __version__")
open(path, "w", encoding="utf-8").write(new)
PY

OUT="dist/release"
ZIP="$OUT/pulsar-$VERSION.zip"
mkdir -p "$OUT"
rm -f "$ZIP" "$OUT/latest.json" "$OUT/latest.json.sig" dist/Pulsar.zip.sig

# Only files git tracks: an ignored .log or .env sitting in pulsar/ or web/ can never ship.
git ls-files -z pulsar.py README.md CHANGELOG.md pulsar web > "$OUT/files.list"
# The Google sign-in client (Google Calendar) is kept out of git, in ~/.config/pulsar/google_client.json of whoever
# releases; the zip carries it in place of the repo's placeholder. Without it, Settings says Calendar is not set up.
CLIENT="${PULSAR_GOOGLE_CLIENT:-$HOME/.config/pulsar/google_client.json}"
[ -f "$CLIENT" ] || echo "warning: no Google sign-in client at $CLIENT; this release cannot connect Google Calendar" >&2
python3 - "$VERSION" "$ZIP" "$OUT/files.list" "$CLIENT" <<'PY'
import os, sys, zipfile
version, zip_path, listing, client = sys.argv[1:]
top = f"pulsar-{version}"
entries = sorted(p for p in open(listing, encoding="utf-8").read().split("\0") if p)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in entries:
        real = client if path == "pulsar/google_client.json" and os.path.isfile(client) else path
        zf.write(real, f"{top}/{path}")
print(f"{zip_path}: {len(entries)} files")
PY

SHA="$(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
# Every version released so far and this one, signed with the rest: installed copies count how many they are behind.
git fetch -q --tags origin 2>/dev/null || true
VERSIONS="$( { git tag -l 'v*' | sed 's/^v//'; echo "$VERSION"; } | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -u -t. -k1,1n -k2,2n -k3,3n | paste -sd, -)"
URL="https://github.com/$REPO/releases/download/v$VERSION/pulsar-$VERSION.zip"

# The release key: the Touch ID key (in this Mac's Secure Enclave, macos/sign.sh) when there is one, else the key file.
KEY="${PULSAR_SIGNING_KEY:-}"
if [ -z "$KEY" ]; then
  KEY="$HOME/.config/pulsar-release/update-signing-key.se"
  [ -f "$KEY" ] || KEY="$HOME/.config/pulsar-release/update-signing-key.pem"
fi
[ -f "$KEY" ] || die "the release signing key is missing: $KEY (see macos/README.md, 'The release keys')"

# The app, with the code it carries described and signed with the same key: its launcher installs that code only
# with this signature (build_app.sh checks it the way the launcher will). Built before latest.json, which carries
# Pulsar.zip's SHA-256 for "Reinstall Pulsar" (pulsar/appupdate.py).
PULSAR_BUNDLE_SIGNING_KEY="$KEY" "$ROOT/macos/build_app.sh" >/dev/null
APP_SHA="$(shasum -a 256 dist/Pulsar.zip | cut -d' ' -f1)"
APP_URL="https://github.com/$REPO/releases/download/v$VERSION/Pulsar.zip"

# expires: installed copies ignore this latest.json after MANIFEST_DAYS (pulsar/updater.py), so an old one served again
# cannot hold them back for ever. The next release writes a fresh one; release at least that often.
# app.min: the oldest app (window and launcher) this release wants, MIN_APP in pulsar/appupdate.py; an older window
# is offered "Reinstall Pulsar".
python3 -E -s - "$ROOT" "$OUT/latest.json" "$VERSION" "$URL" "$SHA" "$NOTES" "$(date +%Y-%m-%d)" "$VERSIONS" "$APP_URL" "$APP_SHA" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, sys.argv[1])
from pulsar import appupdate, updater
path, version, url, sha, notes, published, versions, app_url, app_sha = sys.argv[2:]
expires = (datetime.now(timezone.utc) + timedelta(days=updater.MANIFEST_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
if updater.vtuple(appupdate.MIN_APP) > updater.vtuple(version):
    sys.exit(f"MIN_APP {appupdate.MIN_APP} in pulsar/appupdate.py is newer than this release ({version})")
app = {"version": version, "min": appupdate.MIN_APP, "url": app_url, "sha256": app_sha}
if appupdate.manifest_app({"app": app}) != app:
    sys.exit(f"the app's description would not be understood: {app}")
with open(path, "w", encoding="utf-8") as fh:
    json.dump({"version": version, "url": url, "sha256": sha, "notes": notes, "published": published,
               "expires": expires, "versions": [v for v in versions.split(",") if v], "app": app},
              fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY

# Nothing may have moved since the checks: main on GitHub, and the tree with only the version changed.
on_main " M pulsar/__init__.py"

# Sign latest.json with the release key. Installed copies verify this signature against PUBLIC_KEYS in
# pulsar/release_sig.py and ignore anything else, so a release can only come from whoever holds this key.
# A bridge while the key moves: copies older than the release that listed a new key know only the old one, and they
# read latest.json from the newest release only. So for a while latest.json is signed with the old key
# (PULSAR_UPDATE_KEY), the app's code and Pulsar.zip with the new one (macos/README.md, "Moving to a Touch ID key").
UPDATE_KEY="${PULSAR_UPDATE_KEY:-$KEY}"
[ -f "$UPDATE_KEY" ] || die "the key for latest.json is missing: $UPDATE_KEY"
"$ROOT/macos/sign.sh" "$UPDATE_KEY" "$OUT/latest.json" "$OUT/latest.json.sig" "Pulsar $VERSION, the update"
# The signature must pass the very check installed copies run, against PUBLIC_KEYS as Python reads them: a key that
# is not listed, or listed in a form openssl cannot load, is caught here and not on everyone's Mac.
python3 -E -s - "$ROOT" "$OUT/latest.json" "$OUT/latest.json.sig" <<'PY' || die "installed copies would refuse this release: its key is not one of PUBLIC_KEYS in pulsar/release_sig.py (or one of them cannot be read)"
import json, sys
sys.path.insert(0, sys.argv[1])
from pulsar import updater
data, sig = open(sys.argv[2], "rb").read(), open(sys.argv[3], "rb").read()
signed = any(updater.verify(data, sig, key) for key in updater.PUBLIC_KEYS)
sys.exit(0 if signed and not updater.manifest_problem(json.loads(data)) else 1)
PY

# What people have installed decides whether they get this release: the newest released version must list the key
# that signed it. A new key reaches them only through a release signed with a key they already trust (macos/README.md,
# "Moving to a Touch ID key"), so a release signed too early with the new key is stopped here, not on everyone's Mac.
PREV="$(git tag --list 'v[0-9]*' --sort=-v:refname | head -1)"
if [ -n "$PREV" ]; then
  KEYS_THEN="$(mktemp)"
  { git show "$PREV:pulsar/release_sig.py" 2>/dev/null; git show "$PREV:pulsar/updater.py" 2>/dev/null; } >"$KEYS_THEN" || true
  /usr/bin/python3 -I -B "$ROOT/pulsar/release_sig.py" signed-for "$KEYS_THEN" "$OUT/latest.json" "$OUT/latest.json.sig" ||
    { rm -f "$KEYS_THEN"; die "Pulsar ${PREV#v}, which people have installed, does not know the key that signed this release ($KEY): sign it with a key ${PREV#v} lists (PULSAR_SIGNING_KEY=...). See macos/README.md, 'Moving to a Touch ID key'."; }
  rm -f "$KEYS_THEN"
else
  echo "warning: no earlier release tag here (git fetch --tags), so it was not checked that installed copies know this key" >&2
fi

# Pulsar.zip itself, signed as one file, so a new install can check it before anything in it runs: README.md's
# install line verifies this signature with the Mac's own /usr/bin/openssl and the public key written in the line.
"$ROOT/macos/sign.sh" "$KEY" dist/Pulsar.zip dist/Pulsar.zip.sig "Pulsar $VERSION, the install zip"
# ...and it must pass that very check: the key in the line must be one of PUBLIC_KEYS and must verify this signature,
# so a README out of step with the keys is caught here and not by a new colleague.
python3 -E -s - "$ROOT" dist/Pulsar.zip dist/Pulsar.zip.sig <<'PY' || die "README.md's install line would refuse this Pulsar.zip: its public key is not the one that signed it, or not one of PUBLIC_KEYS"
import sys
sys.path.insert(0, sys.argv[1])
from pulsar import release_sig
data, sig = open(sys.argv[2], "rb").read(), open(sys.argv[3], "rb").read()
key = release_sig.install_line_key(open(sys.argv[1] + "/README.md", encoding="utf-8").read())
sys.exit(0 if key in release_sig.PUBLIC_KEYS and release_sig.verify(data, sig, key) else 1)
PY

echo
echo "Built Pulsar $VERSION:"
echo "  $ROOT/$ZIP"
echo "  $ROOT/$OUT/latest.json (+ latest.json.sig, signed)"
echo "  $ROOT/dist/Pulsar.zip (its code signed: Pulsar.app/Contents/Resources/app.manifest.json.sig)"
echo "  $ROOT/dist/Pulsar.zip.sig (Pulsar.zip signed, for the install line)"
echo "  sha256 $SHA"

GH=(gh release create "v$VERSION" "$ZIP" "$OUT/latest.json" "$OUT/latest.json.sig" dist/Pulsar.zip dist/Pulsar.zip.sig
    --repo "$REPO" --title "$TITLE" --notes "$NOTES")

if [ "$PUBLISH" != 1 ]; then
  echo
  echo "Nothing is published. pulsar/__init__.py now says $VERSION (not committed)."
  echo "To publish this build by hand:"
  echo "  git commit -m \"Pulsar $VERSION\" pulsar/__init__.py && git push origin HEAD:main"
  echo "  gh release create v$VERSION $ZIP $OUT/latest.json $OUT/latest.json.sig dist/Pulsar.zip dist/Pulsar.zip.sig \\"
  echo "    --repo $REPO \\"
  echo "    --title \"$TITLE\" --notes \"${NOTES//\"/\\\"}\" --target <the commit you just pushed>"
  echo "Or undo the version change (git checkout pulsar/__init__.py) and run this again with --publish."
  exit 0
fi

# --- publish -------------------------------------------------------------------------------------------
echo
echo "This will:"
echo "  1. commit pulsar/__init__.py as \"Pulsar $VERSION\" on top of origin/main"
echo "  2. push it to main on origin"
echo "  3. create the public release v$VERSION on $REPO at that commit, with:"
echo "       pulsar-$VERSION.zip, latest.json, latest.json.sig, Pulsar.zip, Pulsar.zip.sig"
echo "     Every installed Pulsar then updates itself to $VERSION."
[ -t 0 ] || die "publishing asks for confirmation: run it in a terminal"
read -r -p "Publish Pulsar $VERSION? [y/N] " answer
[ "$answer" = "y" ] || [ "$answer" = "Y" ] || die "not published; the build is in dist/ and the version change is not committed"

git commit -m "Pulsar $VERSION" -- pulsar/__init__.py
git push origin HEAD:main
"${GH[@]}" --target "$(git rev-parse HEAD)"
echo "Published: https://github.com/$REPO/releases/tag/v$VERSION"
