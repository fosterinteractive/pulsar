#!/bin/bash
# Builds dist/Pulsar.app and dist/Pulsar.zip from this checkout. Runs from any directory.
#
#   macos/build_app.sh            build with the committed icon, macos/AppIcon.icns
#   macos/build_app.sh --icon     first re-render macos/AppIcon.icns from macos/AppIcon.svg
#                                 (needs Node 18+, playwright-core and Google Chrome; see render_icon.mjs)
#
# The app's executable is the window, compiled from PulsarWindow.swift for Apple Silicon and Intel (needs Apple's
# developer tools, which Pulsar needs anyway). It runs launcher.sh from the app's Resources.
#
# PULSAR_SWIFT_FLAGS="-D PULSAR_TEST" builds the test window (it photographs, downloads and prints by itself).
#
# The code the app carries (Resources/app) comes with its description, Resources/app.manifest.json: its version and
# each file's SHA-256. PULSAR_BUNDLE_SIGNING_KEY=<private key> signs it (release.sh passes the release key); the
# launcher installs the code only when that signature is by one of PUBLIC_KEYS (pulsar/release_sig.py), so an app
# built without the key runs the version already installed and installs nothing.
#
# The app is ad-hoc signed only: macOS asks for confirmation the first time it is opened (README.md).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MACOS="$ROOT/macos"
DIST="$ROOT/dist"
# PULSAR_PREVIEW_DIR=<a git checkout> builds "Pulsar Preview.app" instead: it runs that checkout's code as it is, on its
# own port (5620) and log, beside Pulsar, to try a branch before it is released (macos/README.md, "Preview").
PREVIEW_DIR="${PULSAR_PREVIEW_DIR:-}"
NAME="Pulsar"; BUNDLE_ID="com.fosterinteractive.pulsar"
if [ -n "$PREVIEW_DIR" ]; then
  [ -f "$PREVIEW_DIR/pulsar.py" ] || { echo "No pulsar.py in $PREVIEW_DIR" >&2; exit 1; }
  NAME="Pulsar Preview"; BUNDLE_ID="com.fosterinteractive.pulsar.preview"
fi
APP="$DIST/$NAME.app"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/pulsar/__init__.py")"
[ -n "$VERSION" ] || { echo "No __version__ in pulsar/__init__.py" >&2; exit 1; }

# Icon: rendered once and committed, so building the app does not need Node.
if [ "${1:-}" = "--icon" ] || [ ! -f "$MACOS/AppIcon.icns" ]; then
  echo "Rendering the icon from AppIcon.svg"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  node "$MACOS/render_icon.mjs" "$MACOS/AppIcon.svg" "$tmp/AppIcon.iconset" >/dev/null
  iconutil -c icns "$tmp/AppIcon.iconset" -o "$MACOS/AppIcon.icns"
  cp "$tmp/AppIcon.iconset/icon_512x512@2x.png" "$MACOS/AppIcon-1024.png"
fi

command -v swiftc >/dev/null || { echo "swiftc not found: install Apple's developer tools (xcode-select --install)" >&2; exit 1; }

echo "Building $NAME.app $VERSION"
rm -rf "$APP"
[ -n "$PREVIEW_DIR" ] || rm -f "$DIST/Pulsar.zip"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

# The window, one binary for both kinds of Mac. macOS 12 is the oldest it runs on; Swift's runtime is part of macOS.
build="$(mktemp -d)"
trap 'rm -rf "$build" ${tmp:+"$tmp"}' EXIT
for arch in arm64 x86_64; do
  # shellcheck disable=SC2086
  swiftc -O ${PULSAR_SWIFT_FLAGS:-} -target "$arch-apple-macos12" "$MACOS/PulsarWindow.swift" -o "$build/Pulsar-$arch"
done
lipo -create "$build/Pulsar-arm64" "$build/Pulsar-x86_64" -o "$APP/Contents/MacOS/Pulsar"

cat >"$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>Pulsar</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null

cp "$MACOS/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cp "$MACOS/launcher.sh" "$APP/Contents/Resources/launcher.sh"
chmod 755 "$APP/Contents/MacOS/Pulsar" "$APP/Contents/Resources/launcher.sh"
[ -n "$PREVIEW_DIR" ] && printf '%s\n' "$PREVIEW_DIR" >"$APP/Contents/Resources/preview-dir"

RES="$APP/Contents/Resources/app"
cp "$ROOT/pulsar.py" "$ROOT/README.md" "$ROOT/CHANGELOG.md" "$RES/"
# Only the app's own files: the .py modules, the page's html, css and js, and the Google sign-in client
# (pulsar/google_client.json). Anything else that happens to sit in these folders (a .env, a log, editor settings)
# never reaches the app or the public Pulsar.zip.
rsync -a --prune-empty-dirs --include '*/' --include '*.py' --include '*.html' --include '*.css' --include '*.js' \
  --include 'pulsar/google_client.json' \
  --exclude '__pycache__/' --exclude '.*' --exclude '*' "$ROOT/pulsar" "$ROOT/web" "$RES/"
# The real Google sign-in client stays out of git, with whoever builds releases; the repo holds placeholders.
CLIENT="${PULSAR_GOOGLE_CLIENT:-$HOME/.config/pulsar/google_client.json}"
[ -f "$CLIENT" ] && cp "$CLIENT" "$RES/pulsar/google_client.json"

# The launcher's own copy of the release check, outside the code it checks, and the description of that code to sign.
cp "$ROOT/pulsar/release_sig.py" "$APP/Contents/Resources/release_sig.py"
if [ -z "$PREVIEW_DIR" ]; then  # Preview never installs anything
  MANIFEST="$APP/Contents/Resources/app.manifest.json"
  /usr/bin/python3 -I -B "$ROOT/pulsar/release_sig.py" bundle-manifest "$RES" "$VERSION" >"$MANIFEST"
  if [ -n "${PULSAR_BUNDLE_SIGNING_KEY:-}" ]; then
    /usr/bin/openssl dgst -sha256 -sign "$PULSAR_BUNDLE_SIGNING_KEY" -out "$MANIFEST.sig" "$MANIFEST"
    # the very check the launcher makes, with the copy the app carries: a key that is not listed fails here
    why="$(/usr/bin/python3 -I -B "$APP/Contents/Resources/release_sig.py" check-bundle "$RES" "$MANIFEST" "$MANIFEST.sig" "$VERSION")" ||
      { echo "The launcher would refuse this app's code: $why (is the key one of PUBLIC_KEYS in pulsar/release_sig.py?)" >&2; exit 1; }
  else
    echo "Not signed (no PULSAR_BUNDLE_SIGNING_KEY): this app installs nothing, it runs the version already installed." >&2
  fi
fi

# Ad-hoc signature: lets it run on Apple Silicon. It is not a Developer ID signature, so Gatekeeper
# still asks for confirmation on first open.
xattr -cr "$APP"
codesign --force --deep -s - "$APP"
codesign --verify --deep --strict "$APP"

if [ -n "$PREVIEW_DIR" ]; then  # never zipped or released: it is for the person who builds it
  echo "Done: $APP (runs $PREVIEW_DIR on port 5620; copy it to Applications to keep it)"
  exit 0
fi
ditto -c -k --keepParent "$APP" "$DIST/Pulsar.zip"

# A build is not an install: keep this copy out of Spotlight and the Apps list, so the Mac shows one Pulsar,
# the one in Applications. (Opening this copy registers it again.)
touch "$DIST/.metadata_never_index"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$APP" 2>/dev/null || true

echo "Done:"
echo "  $APP"
echo "  $DIST/Pulsar.zip ($(du -h "$DIST/Pulsar.zip" | cut -f1 | tr -d ' '))"
