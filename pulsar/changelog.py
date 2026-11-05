"""What each release changed, from CHANGELOG.md next to pulsar.py: Settings lists it, and release.sh refuses a
version that has no entry there. An entry is a "## X.Y.Z (YYYY-MM-DD)" heading and its "- " lines; anything else in
the file is ignored."""

from __future__ import annotations

import os
import re

from . import names

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md")
HEADING = re.compile(r"^##\s+v?([0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})(?![0-9.])(.*)$")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
LINE = re.compile(r"^[-*]\s+(\S.*?)\s*$")


def parse(text: str) -> list:
    """[{version, date, codename, lines}], in the order of the file (newest first)."""
    releases: list = []
    for row in text.splitlines():
        head = HEADING.match(row)
        if head:
            date = DATE.search(head.group(2))
            releases.append({"version": head.group(1), "date": date.group(0) if date else "",
                             "codename": names.codename(head.group(1))[0], "lines": []})
            continue
        line = LINE.match(row)
        if line and releases:
            releases[-1]["lines"].append(line.group(1))
    return releases


def read(path: str = PATH) -> list:
    """This copy's changelog, or [] when it has none (copies released before it existed)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return parse(fh.read(512 * 1024))
    except (OSError, UnicodeDecodeError):
        return []
