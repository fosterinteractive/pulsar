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
# The app is ad-hoc signed only: macOS asks for confirmation the first time it is opened (README.md).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MACOS="$ROOT/macos"
DIST="$ROOT/dist"
APP="$DIST/Pulsar.app"

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

echo "Building Pulsar.app $VERSION"
rm -rf "$APP" "$DIST/Pulsar.zip"
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
  <key>CFBundleName</key><string>Pulsar</string>
  <key>CFBundleDisplayName</key><string>Pulsar</string>
  <key>CFBundleIdentifier</key><string>com.fosterinteractive.pulsar</string>
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

RES="$APP/Contents/Resources/app"
cp "$ROOT/pulsar.py" "$ROOT/README.md" "$RES/"
# Only the app's own files: the .py modules, the page's html, css and js, and the Google sign-in client
# (pulsar/google_client.json, public by design). Anything else that happens to sit in these folders (a .env, a log,
# editor settings) never reaches the app or the public Pulsar.zip.
rsync -a --prune-empty-dirs --include '*/' --include '*.py' --include '*.html' --include '*.css' --include '*.js' \
  --include 'pulsar/google_client.json' \
  --exclude '__pycache__/' --exclude '.*' --exclude '*' "$ROOT/pulsar" "$ROOT/web" "$RES/"

# Ad-hoc signature: lets it run on Apple Silicon. It is not a Developer ID signature, so Gatekeeper
# still asks for confirmation on first open.
xattr -cr "$APP"
codesign --force --deep -s - "$APP"
codesign --verify --deep --strict "$APP"

ditto -c -k --keepParent "$APP" "$DIST/Pulsar.zip"

# A build is not an install: keep this copy out of Spotlight and the Apps list, so the Mac shows one Pulsar,
# the one in Applications. (Opening this copy registers it again.)
touch "$DIST/.metadata_never_index"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$APP" 2>/dev/null || true

echo "Done:"
echo "  $APP"
echo "  $DIST/Pulsar.zip ($(du -h "$DIST/Pulsar.zip" | cut -f1 | tr -d ' '))"
