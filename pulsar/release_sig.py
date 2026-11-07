"""Release signatures: the keys a Pulsar release is signed with, and the checks made with them.

A release signs two descriptions with the release key (ECDSA P-256, macos/release.sh):
- latest.json, which installed copies read before they download an update (pulsar/updater.py);
- app.manifest.json, inside Pulsar.app next to the code it carries (Contents/Resources/): that code's version and the
  SHA-256 of each of its files. The launcher installs the code only when this manifest is signed with one of
  PUBLIC_KEYS and the copy it made matches it file for file, none missing, none added (check_bundle).

Standard library and the Mac's own /usr/bin/openssl only, and nothing imported from the rest of Pulsar: build_app.sh
copies this file next to the launcher, outside the code it checks, and the launcher runs that copy on its own:

    python3 -I -B release_sig.py check-bundle <code folder> <manifest> <signature> <version>   exit 0: signed, as is
    python3 -I -B release_sig.py bundle-manifest <code folder> <version>                       the manifest, unsigned
    python3 -I -B release_sig.py signed-for <file with keys> <data> <signature>                exit 0: one of its keys signed it
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Optional

# The keys a release may be signed with (ECDSA P-256). The first is the one releases are signed with; any further one
# is a reserve kept offline (in the team's password manager): if the first is lost or leaks, a release signed with a
# reserve brings a new list, and installed copies follow it without anyone reinstalling. Adding a key later takes a
# release signed with a key already listed here, so the reserve must be in place before people install.
PUBLIC_KEYS = (
    """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEmCWdtNVrg3SiHdiOL1WDTXva/S7e
XbA0dD0U0N++R+VVLutC7Y66FFlNYSLKjKo+JAuRLrGtGKTXsjgo00mzLQ==
-----END PUBLIC KEY-----
""",
    # the Touch ID key (macos/sign.sh, in the release Mac's Secure Enclave): signs from 1.5.2 on
    """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAECcw9Ny0xyPDVAsyM2aac+nwH5IZ5
EEdlXCwE6MMw3HpRi03aBa7effm9G1JudLuMLdFWxsAfKF43N/piDHieQA==
-----END PUBLIC KEY-----
""",
)

BUNDLE_KIND = "pulsar-app-code"  # so latest.json, signed with the same key, can never pass for an app's manifest
IGNORED = {".DS_Store"}          # Finder leaves these in folders it has shown: never code


def openssl() -> Optional[str]:
    for path in ("/usr/bin/openssl", shutil.which("openssl")):
        if path and os.path.exists(path):
            return path
    return None


def verify(data: bytes, signature: bytes, key_pem: str) -> bool:
    """True only when `signature` is the release key's signature of `data` (checked with the system openssl,
    as Python's standard library has no signature check)."""
    tool = openssl()
    if not tool or not signature:
        return False
    work = tempfile.mkdtemp(prefix="pulsar-verify-")
    try:
        paths = {}
        for name, content in (("data", data), ("sig", signature), ("key.pem", key_pem.encode())):
            paths[name] = os.path.join(work, name)
            with open(paths[name], "wb") as fh:
                fh.write(content)
        run = subprocess.run([tool, "dgst", "-sha256", "-verify", paths["key.pem"], "-signature", paths["sig"], paths["data"]],
                             capture_output=True, text=True, timeout=30)
        return run.returncode == 0 and "Verified OK" in run.stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)


def tree(folder: str) -> dict:
    """Every file under `folder`, as its path there ('pulsar/server.py') and SHA-256. A symbolic link, or anything
    that is neither a plain file nor a folder, raises ValueError: a link could lead the copy outside itself."""
    out = {}
    for here, dirs, files in os.walk(folder):
        for name in dirs + files:
            path = os.path.join(here, name)
            rel = os.path.relpath(path, folder).replace(os.sep, "/")
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"{rel} is a symbolic link")
            if name in files and name not in IGNORED:
                if not stat.S_ISREG(mode):
                    raise ValueError(f"{rel} is not a plain file")
                digest = hashlib.sha256()
                with open(path, "rb") as fh:
                    for block in iter(lambda: fh.read(1 << 16), b""):
                        digest.update(block)
                out[rel] = digest.hexdigest()
    return out


def bundle_manifest(folder: str, version: str) -> bytes:
    """What build_app.sh writes next to the app's code, for a release to sign."""
    return (json.dumps({"kind": BUNDLE_KIND, "version": version, "files": tree(folder)}, indent=1, sort_keys=True)
            + "\n").encode()


def declared_version(folder: str) -> str:
    try:
        with open(os.path.join(folder, "pulsar", "__init__.py"), encoding="utf-8") as fh:
            m = re.search(r'^__version__\s*=\s*"([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else ""
    except (OSError, UnicodeDecodeError):
        return ""


def check_bundle(folder: str, manifest: bytes, signature: bytes, version: str, keys: Optional[tuple] = None) -> str:
    """'' when `folder` holds exactly the code a release signed as `version`; otherwise why not, in a few words that
    follow "This copy of Pulsar is not a signed release: "."""
    if not manifest:
        return "it has no description of its code to check"
    if not signature:
        return "its code is not signed"
    if not any(verify(manifest, signature, key) for key in (PUBLIC_KEYS if keys is None else keys)):
        return "its code is signed with a key that is not Pulsar's release key"
    try:
        signed = json.loads(manifest)
    except ValueError:
        signed = None
    if not isinstance(signed, dict) or signed.get("kind") != BUNDLE_KIND or not isinstance(signed.get("files"), dict):
        return "what is signed is not a description of an app's code"
    if signed.get("version") != version:
        return f"the signature is for version {str(signed.get('version'))[:20]}, not {version[:20]}"
    try:
        have = tree(folder)
    except (OSError, ValueError) as exc:
        return f"its code cannot be checked ({exc})"
    want = signed["files"]
    differ = [f"{p} changed" for p in sorted(want) if p in have and have[p] != want[p]]
    differ += [f"{p} missing" for p in sorted(set(want) - set(have))]
    differ += [f"{p} added" for p in sorted(set(have) - set(want))]
    if differ:
        more = f" and {len(differ) - 3} more" if len(differ) > 3 else ""
        return "its code is not what was signed (" + ", ".join(differ[:3]) + more + ")"
    if declared_version(folder) != version:  # a signed manifest that disagrees with its own code: a release mistake
        return f"its code says version {declared_version(folder)[:20] or 'none'}, not {version[:20]}"
    return ""


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(1 << 22)
    except OSError:
        return b""


PEM_BLOCK = re.compile(r"-----BEGIN PUBLIC KEY-----\n[A-Za-z0-9+/=\n]+?-----END PUBLIC KEY-----\n?")


def keys_in(text: str) -> list:
    """The public keys (PEM) written in a file's text: an earlier release's release_sig.py, or updater.py before
    1.5, where PUBLIC_KEYS lived. release.sh asks whether the version people have installed would accept a new
    release's signature, which is what decides whether they get it."""
    return [k if k.endswith("\n") else k + "\n" for k in PEM_BLOCK.findall(text or "")]


def main(argv: list) -> int:
    if len(argv) == 4 and argv[0] == "signed-for":
        keys = keys_in(_read(argv[1]).decode("utf-8", "replace"))
        return 0 if keys and any(verify(_read(argv[2]), _read(argv[3]), k) for k in keys) else 1
    if len(argv) == 5 and argv[0] == "check-bundle":
        problem = check_bundle(argv[1], _read(argv[2]), _read(argv[3]), argv[4])
        if problem:
            print(problem)
            return 1
        return 0
    if len(argv) == 3 and argv[0] == "bundle-manifest":
        sys.stdout.buffer.write(bundle_manifest(argv[1], argv[2]))
        return 0
    print(__doc__.split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
