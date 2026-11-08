# Working on Pulsar

Pulsar is a small Mac app: a Python server (`pulsar/`), a page in plain JavaScript (`web/`), a native window and
launcher (`macos/`). How each part works is in `docs/HOW-IT-WORKS.md`; read the section you change first.

## Rules

- **Python 3.9+, standard library only**, in everything Pulsar runs. No pip packages, no vendored libraries.
- **No build step for the page.** `web/` runs as written in Safari 16.2+ (WebKit): no bundler, no transpiler, no npm.
- **HTML from data goes through `esc()`** (`web/js/core.js`) inside every template string given to `innerHTML`,
  `insertAdjacentHTML` or `setHTML`; plain text goes in with `textContent` (`setText`). Window titles, session text,
  commit subjects, task and project names, meeting titles and notes all come from outside.
- **Files under `~/.config/pulsar` and `~/.cache/pulsar` are written atomically** with `config.write_atomic` (a
  temporary file in the same folder, then `os.replace`), under a lock when threads share the file. The error log is
  the one file appended to (`logbook.record`).
- **Productive writes** go only through `productive.py`'s request path, which checks `config.writes_blocked()`, and
  are never retried: a lost answer must never log an entry twice.
- **This repository is public.** No client, project or person names, Productive ids, local paths or emails in code,
  tests, docs or commit messages.
- Settings people chose, and behaviour listed in `CHANGELOG.md`, change only when asked.

## Checks

- Tests: `PULSAR_TESTS_OFFLINE=1 python3 -m unittest discover -s tests -q`. Every test module imports
  `tests/isolation.py` first; never work around it (no real network, keychain, Claude Code or home folder).
- New logic gets a unit test; the whole suite runs once before a commit that ends the work, not after every edit.
- `python3 pulsar.py --self-test` checks that the page files are there and the server answers.
- `ruff check pulsar pulsar.py tests` (development only, `pyproject.toml`: unused and undefined names, syntax) must
  pass when ruff is installed (`python3 -m pip install --user ruff`).
- `npx -p typescript@5.6.3 tsc -p jsconfig.json` (development only) type-checks the page's scripts as the one global
  scope they share: an unknown or misspelt name, or a wrong number of arguments, across files. It must report 0
  errors. `types/page.d.ts` says what Pulsar's window adds and what the page's elements are; JSDoc types the rest.
- `macos/ui_smoke.sh` clicks through the page in Pulsar's real window (`tests/ui/smoke.js`, read only: dialogs, breaks,
  Not an example, the estimate line, Update's restart, the day's copy) and must pass after a change to the page or the
  window. A new feature on the page gets a check there.
- What a change does goes under `## Unreleased` in `CHANGELOG.md`; behaviour in `docs/HOW-IT-WORKS.md`; agreed
  next steps in `docs/TODO.md`.

## Releases

Only `macos/release.sh`, only from `main` as it is on GitHub, and only when the maintainer says so. Never read,
print or copy anything in `~/.config/pulsar-release` (the release keys) or `~/.config/pulsar/google_client.json`.
