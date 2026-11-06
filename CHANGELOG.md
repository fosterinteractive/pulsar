# Changelog

What each Pulsar release changed, newest first.

<!-- A release needs its entry before macos/release.sh runs: a "## X.Y.Z (YYYY-MM-DD)" heading and one "- " line.
     "Unreleased" collects what is merged but not released: at release, rename it to the version and date.
     Settings and release.sh read only version headings, so the lines under "Unreleased" show nowhere until then. -->

## Unreleased
- Log the day: the whole day proposed for review, written one entry at a time, with Undo.
- Edit, delete and resize logged entries; only what changed is sent, and approval is left to Productive.
- Log and next, a first-run checklist, the week at the top, and Open in Productive on tasks.
- One Claude Code switch in Settings > Claude: Ask Claude, notes from the session, logging from a session.
- Pulsar in Claude Code (pulsar.py --mcp): what is not logged, logged after you confirm.
- Note drafts from your commits and sessions, checked on your Mac before you see them.
- Ask Claude reaches only Productive, and says when its spending limit stopped it.
- Learned tasks: recent logging counts more; a task can be found by its words.
- Pulsar Preview: a branch's code beside Pulsar, with its own settings, writing off by default.
- The keychain not answering is said as such.
- Fixed: yesterday's Likely column no longer stops at the hour you last looked at it.
- Fixed: the day the clocks change adds up, and Ask Claude's picks show on it.
- Fixed: one odd line in a Claude session no longer empties Likely for the day.
- Fixed: copies of MemTime's database no longer pile up in the temporary folder.
- Fixed: an update whose self-test timed out can be tried again; a late answer no longer replaces a newer one.
- Fixed: a self-test whose page answered too late no longer counts against the version.
- Fixed: unreadable recent entries are said, not kept; Ask Claude then does not run.
- Fixed: a past day seen before ActivityWatch started is learned from again once it runs.
- The README says what Ask Claude sends to Anthropic, what it costs, and how to remove everything.
- Pulsar's key only reads, quits and logs new entries; changing or deleting entries, settings and updates need its window.
- Notes from commits never start a program that a repository's own git settings name.
- Ask Claude leaves out Claude Code's auto memory; Claude Code asks before Pulsar logs, by an ask rule too.
- Pulsar in Claude Code returns no past notes and no note drafted for another repository.
- Pulsar Preview starts with Claude Code off, and its Disconnect leaves Pulsar's Google Calendar connected.
- Fixed: Log the day writes exactly what you confirmed, even when Claude's answer arrives late.
- Fixed: an entry with no start time is shown in Log the day, and its task is not proposed again ticked.
- Fixed: Undo goes on past an entry deleted or changed by hand; a deleted entry can be logged again.
- Fixed: a damaged Log the day journal stops writes instead of being emptied.
- Fixed: in Claude Code, a write with no clear answer says "not known", never "not logged".

## 1.2.1 (2026-11-06)
- Settings: categories with icons, and a search.
- Claude's suggestions marked with its icon; Save no longer re-checks Productive.

## 1.2.0 (2026-11-06)
- Time away from the computer, marked in the day.
- Logging a Likely block offers the tasks you used in that session or repository first.
- Ask Claude (optional, in Settings): a task for each Likely block.

## 1.1.0 (2026-11-06)
- Google Calendar: your meetings in the day, and their task offered when you log one.
- The daily meeting ready to log in one click; a task linked in a Claude session offered first.
- A new date picker, and this changelog.

## 1.0.15 (2026-11-05)
- The Likely column works for everyone who uses Claude Code.

## 1.0.14 (2026-11-05)
- Urgent tasks first when you log, with due dates; a warning on blocked tasks.

## 1.0.13 (2026-11-05)
- No duplicate entries; your full task list.

## 1.0.12 (2026-11-05)
- The warp bends the page again in the app window (download Pulsar.zip again).

## 1.0.11 (2026-11-05)
- A smoother warp on older Macs.

## 1.0.10 (2026-11-05)
- A smoother warp.

## 1.0.9 (2026-11-05)
- The version at the top turns blue when an update is out.

## 1.0.8 (2026-11-05)
- The update reminder shows at two releases behind.

## 1.0.7 (2026-11-05)
- Updates install only when you click Update; a reminder you can skip.

## 1.0.6 (2026-11-05)
- Refresh plays one short warp again.

## 1.0.5 (2026-11-05)
- The day and the report open on macOS 14 and older.

## 1.0.4 (2026-11-05)
- Steadier Productive reads; clearer messages; the warp can be turned off; the logo pulses.

## 1.0.3 (2026-11-05)
- The refresh button warps too.

## 1.0.2 (2026-11-05)
- Updates install when you close Pulsar, with a warp; billing in blue, orange and grey.

## 1.0.1 (2026-11-05)
- Safer logging; Settings shows the current connection.

## 1.0.0 (2026-11-05)
- First release: day view, report, logging to Productive, rules, its own window.
