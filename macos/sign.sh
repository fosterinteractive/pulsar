#!/bin/bash
# Signs with the release key, the one way release.sh and build_app.sh sign anything:
#
#   macos/sign.sh KEY IN OUT [WHAT]   the signature of IN into OUT (ECDSA P-256 over its SHA-256, DER)
#   macos/sign.sh keygen [KEYFILE]    a new key in this Mac's Secure Enclave; prints its public key
#   macos/sign.sh pubkey [KEYFILE]    that public key again
#
# KEY ending in .se is a key in this Mac's Secure Enclave (macos/sign/pulsar-sign.swift): it never leaves the chip,
# and each signature asks for a fingerprint, in a Touch ID dialog that names WHAT and the start of IN's SHA-256.
# Any other KEY is a PEM private key file, signed with /usr/bin/openssl. Both make the signature
# pulsar/release_sig.py checks. KEYFILE defaults to ~/.config/pulsar-release/update-signing-key.se.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SE_KEY="$HOME/.config/pulsar-release/update-signing-key.se"

die() { echo "sign.sh: $*" >&2; exit 1; }

# The Secure Enclave tool, compiled from the repository's source with the Mac's own swiftc into a private folder
# that goes when this script ends: nothing prebuilt is ever run.
se_tool() {
  [ -x /usr/bin/swiftc ] || die "swiftc is missing: install Apple's Command Line Tools (xcode-select --install)"
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/pulsar-sign.XXXXXX")"
  trap 'rm -rf "$WORK"' EXIT
  /usr/bin/swiftc -O -o "$WORK/pulsar-sign" "$ROOT/macos/sign/pulsar-sign.swift" 2>"$WORK/build.log" ||
    { cat "$WORK/build.log" >&2; die "the Secure Enclave tool did not build"; }
  TOOL="$WORK/pulsar-sign"
}

case "${1:-}" in
  keygen)
    KEYFILE="${2:-$SE_KEY}"
    [ -e "$KEYFILE" ] && die "$KEYFILE exists already: a key is never overwritten"
    mkdir -p "$(dirname "$KEYFILE")" && chmod 700 "$(dirname "$KEYFILE")"
    se_tool
    "$TOOL" keygen "$KEYFILE"
    echo
    echo "Made $KEYFILE. Its private key is in this Mac's Secure Enclave and signs only with your fingerprint."
    echo "Next (macos/README.md, 'Moving to a Touch ID key'): add the public key above to PUBLIC_KEYS in"
    echo "pulsar/release_sig.py and release that version with the old key, so installed copies learn the new one."
    ;;
  pubkey)
    se_tool
    "$TOOL" pubkey "${2:-$SE_KEY}"
    ;;
  -h|--help|"")
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    [ $# -ge 3 ] || die "usage: macos/sign.sh KEY IN OUT [WHAT]"
    KEY="$1" IN="$2" OUT="$3" WHAT="${4:-$(basename "$2")}"
    [ -f "$KEY" ] || die "the release key is missing: $KEY"
    [ -f "$IN" ] || die "nothing to sign: $IN"
    case "$KEY" in
      *.se) se_tool; "$TOOL" sign "$KEY" "$IN" "$OUT" "$WHAT" ;;
      *) /usr/bin/openssl dgst -sha256 -sign "$KEY" -out "$OUT" "$IN" ;;
    esac
    ;;
esac
