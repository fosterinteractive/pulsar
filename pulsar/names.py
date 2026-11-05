"""Each minor version of Pulsar is named after a real pulsar, in the order below. The name shows next to the
version in Settings, in the window's About panel and in the release notes; the line about it goes in the notes."""

from __future__ import annotations

NAMES = {
    (1, 0): ("CP 1919", "the first pulsar found, by Jocelyn Bell Burnell in 1967; it pulses every 1.337 seconds"),
    (1, 1): ("Vela", "a young pulsar in the Vela supernova remnant, turning 11 times a second"),
    (1, 2): ("Crab", "born in the supernova seen from Earth in 1054, turning 30 times a second"),
    (1, 3): ("Geminga", "a nearby pulsar that is silent in radio and bright in gamma rays"),
    (1, 4): ("Hulse-Taylor", "the binary pulsar whose shrinking orbit was the first evidence of gravitational waves"),
    (1, 5): ("Black Widow", "a millisecond pulsar that is slowly boiling away its companion star"),
    (1, 6): ("Double Pulsar", "the only known pair of pulsars orbiting each other"),
    (1, 7): ("Terzan 5ad", "the fastest-spinning pulsar known, 716 turns a second"),
    (1, 8): ("Lich", "the pulsar around which the first planets beyond the Solar System were found, in 1992"),
}


def codename(version: str) -> tuple:
    """(name, the line about it) for a version such as "1.2.3", or ("", "") past the end of the list."""
    try:
        major, minor = (int(x) for x in version.split(".")[:2])
    except ValueError:
        return "", ""
    return NAMES.get((major, minor), ("", ""))
