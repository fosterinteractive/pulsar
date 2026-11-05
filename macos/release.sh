#!/bin/bash
# Builds a Pulsar release, and with --publish puts it on GitHub, where every installed Pulsar finds it.
#
#   macos/release.sh 1.1.0 --notes "Adds the week view."             build only, then print the next steps
#   macos/release.sh 1.1.0 --notes "Adds the week view." --publish   build, commit the version, push, release
#
# Refuses a git tree with changes, a version that is not newer than pulsar/__init__.py, and failing tests.
# Then it sets __version__ and builds, in dist/:
#   release/pulsar-<version>.zip   the code, one top folder pulsar-<version>/ (what the in-app updater downloads)
#   release/latest.json            {version, url, sha256, notes, published} (what the in-app updater reads)
#   Pulsar.zip                     the whole app, for new installs (build_app.sh)
# With --publish it shows what it will do, asks, commits the version change, pushes the branch and runs
# gh release create v<version> with the three files on fosterinteractive/pulsar.

set -euo pipefail

REPO="fosterinteractive/pulsar"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "release.sh: $*" >&2; exit 1; }

VERSION="" NOTES="" PUBLISH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --publish) PUBLISH=1 ;;
    --notes) [ $# -ge 2 ] || die "--notes needs a text"; NOTES="$2"; shift ;;
    --notes=*) NOTES="${1#--notes=}" ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) [ -z "$VERSION" ] || die "one version only"; VERSION="$1" ;;
  esac
  shift
done
[ -n "$VERSION" ] || die "usage: macos/release.sh <X.Y.Z> [--notes \"...\"] [--publish]"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "the version must look like 1.2.0, not $VERSION"

# --- checks --------------------------------------------------------------------------------------------
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git repository"
[ -z "$(git status --porcelain)" ] || { git status --short >&2; die "the git tree has changes: commit or stash them first"; }

CURRENT="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' pulsar/__init__.py)"
[ -n "$CURRENT" ] || die "no __version__ in pulsar/__init__.py"
python3 -c 'import sys
v = lambda s: tuple(int(p) for p in s.split("."))
sys.exit(0 if v(sys.argv[1]) > v(sys.argv[2]) else 1)' "$VERSION" "$CURRENT" ||
  { [ "$VERSION" = "$CURRENT" ] && [ -z "$(git tag -l "v$VERSION")" ] && echo "First release of $VERSION"; } ||
  die "$VERSION is not newer than the current version $CURRENT"

echo "Running the tests"
python3 -m unittest discover -s tests -q || die "the tests fail; nothing was changed"

if [ "$PUBLISH" = 1 ]; then
  command -v gh >/dev/null || die "--publish needs the GitHub CLI (gh)"
  gh auth status >/dev/null 2>&1 || die "gh is not signed in: run gh auth login"
  gh repo view "$REPO" >/dev/null 2>&1 || die "cannot see the repository $REPO"
  ! gh release view "v$VERSION" --repo "$REPO" >/dev/null 2>&1 || die "release v$VERSION already exists on $REPO"
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
rm -f "$ZIP" "$OUT/latest.json" "$OUT/latest.json.sig"

# Only files git tracks: an ignored .log or .env sitting in pulsar/ or web/ can never ship.
git ls-files -z pulsar.py README.md pulsar web > "$OUT/files.list"
python3 - "$VERSION" "$ZIP" "$OUT/files.list" <<'PY'
import sys, zipfile
version, zip_path, listing = sys.argv[1:]
top = f"pulsar-{version}"
entries = sorted(p for p in open(listing, encoding="utf-8").read().split("\0") if p)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in entries:
        zf.write(path, f"{top}/{path}")
print(f"{zip_path}: {len(entries)} files")
PY

SHA="$(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
# Every version released so far and this one, signed with the rest: installed copies count how many they are behind.
git fetch -q --tags origin 2>/dev/null || true
VERSIONS="$( { git tag -l 'v*' | sed 's/^v//'; echo "$VERSION"; } | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -u -t. -k1,1n -k2,2n -k3,3n | paste -sd, -)"
URL="https://github.com/$REPO/releases/download/v$VERSION/pulsar-$VERSION.zip"
python3 - "$OUT/latest.json" "$VERSION" "$URL" "$SHA" "$NOTES" "$(date +%Y-%m-%d)" "$VERSIONS" <<'PY'
import json, sys
path, version, url, sha, notes, published, versions = sys.argv[1:]
with open(path, "w", encoding="utf-8") as fh:
    json.dump({"version": version, "url": url, "sha256": sha, "notes": notes, "published": published,
               "versions": [v for v in versions.split(",") if v]}, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY

# Sign latest.json with the release key. Installed copies verify this signature against the public key in
# pulsar/updater.py and ignore anything else, so a release can only come from whoever holds this key.
KEY="${PULSAR_SIGNING_KEY:-$HOME/.config/pulsar-release/update-signing-key.pem}"
[ -f "$KEY" ] || die "the release signing key is missing: $KEY (see macos/README.md, 'The release key')"
/usr/bin/openssl dgst -sha256 -sign "$KEY" -out "$OUT/latest.json.sig" "$OUT/latest.json"
# The signature must pass the very check installed copies run, against PUBLIC_KEYS as Python reads them: a key that
# is not listed, or listed in a form openssl cannot load, is caught here and not on everyone's Mac.
python3 -E -s - "$ROOT" "$OUT/latest.json" "$OUT/latest.json.sig" <<'PY' || die "installed copies would refuse this release: its key is not one of PUBLIC_KEYS in pulsar/updater.py (or one of them cannot be read)"
import sys
sys.path.insert(0, sys.argv[1])
from pulsar import updater
data, sig = open(sys.argv[2], "rb").read(), open(sys.argv[3], "rb").read()
sys.exit(0 if any(updater.verify(data, sig, key) for key in updater.PUBLIC_KEYS) else 1)
PY

"$ROOT/macos/build_app.sh" >/dev/null

echo
echo "Built Pulsar $VERSION:"
echo "  $ROOT/$ZIP"
echo "  $ROOT/$OUT/latest.json (+ latest.json.sig, signed)"
echo "  $ROOT/dist/Pulsar.zip"
echo "  sha256 $SHA"

GH=(gh release create "v$VERSION" "$ZIP" "$OUT/latest.json" "$OUT/latest.json.sig" dist/Pulsar.zip --repo "$REPO"
    --title "$TITLE" --notes "$NOTES")
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [ "$PUBLISH" != 1 ]; then
  echo
  echo "Nothing is published. pulsar/__init__.py now says $VERSION (not committed)."
  echo "To publish this build by hand:"
  echo "  git commit -m \"Pulsar $VERSION\" pulsar/__init__.py && git push origin $BRANCH"
  echo "  gh release create v$VERSION $ZIP $OUT/latest.json $OUT/latest.json.sig dist/Pulsar.zip --repo $REPO \\"
  echo "    --title \"$TITLE\" --notes \"${NOTES//\"/\\\"}\" --target <the commit you just pushed>"
  echo "Or undo the version change (git checkout pulsar/__init__.py) and run this again with --publish."
  exit 0
fi

# --- publish -------------------------------------------------------------------------------------------
echo
echo "This will:"
echo "  1. commit pulsar/__init__.py as \"Pulsar $VERSION\" on $BRANCH"
echo "  2. push $BRANCH to origin"
echo "  3. create the public release v$VERSION on $REPO at that commit, with:"
echo "       pulsar-$VERSION.zip, latest.json, Pulsar.zip"
echo "     Every installed Pulsar then updates itself to $VERSION."
[ -t 0 ] || die "publishing asks for confirmation: run it in a terminal"
read -r -p "Publish Pulsar $VERSION? [y/N] " answer
[ "$answer" = "y" ] || [ "$answer" = "Y" ] || die "not published; the build is in dist/ and the version change is not committed"

git commit -m "Pulsar $VERSION" -- pulsar/__init__.py
git push origin "$BRANCH"
"${GH[@]}" --target "$(git rev-parse HEAD)"
echo "Published: https://github.com/$REPO/releases/tag/v$VERSION"
