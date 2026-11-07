# Changelog

What each Pulsar release changed, newest first.

<!-- A release needs its entry before macos/release.sh runs: a "## X.Y.Z (YYYY-MM-DD)" heading and one "- " line.
     "Unreleased" collects what is merged but not released: at release, rename it to the version and date.
     Settings and release.sh read only version headings, so the lines under "Unreleased" show nowhere until then. -->

## 1.5.1 (2026-11-07)
- Two more release keys are trusted: a Touch ID key in the publisher's Mac, which signs the next releases, and an offline reserve.
- Pulsar.zip is signed, and the install line checks it with the Mac's own openssl before it unpacks anything.
- release.sh signs only main as it is on GitHub; an update older than one already seen is refused.
- Calendar titles become notes only through the note checks, only from your own company's organizers; a renamed series starts unticked.
- Claude's note drafts go only to the session they came from; the note checks see look-alike and invisible letters, tool names and sums in words.
- Tests can no longer touch a real git repository or your git settings.

## 1.5.0 (2026-11-07)
- After an update, a window from an older Pulsar.app signs in again instead of staying signed out.
- The install line replaces an older Pulsar.app instead of unpacking over it.
- A stretch dragged in the Logged column keeps the minutes dragged.
- Releases can be signed with a key in the Mac's Secure Enclave, a fingerprint for each signature (`macos/sign.sh`); release.sh refuses a key the newest released version does not list.
- Log the day with Claude (experimental): FI's rules and Claude's review of the whole day, as suggestions to accept or reject.
- Settings > Claude lists everything Pulsar asks Claude and what each sends, with a model to pick for each.
- Log dialog, Create a task: the status, estimate and service for this time are proposed, each with why; the service is never sent with the task.
- Dragging in the Logged column moves in 1-minute steps, not 5, so short entries can be changed.
- Log dialog: Create a task when none fits the work, with a drafted title and description; it is then picked.
- Pulsar in Claude Code: the tools are answered inside Pulsar; the key alone opens nothing else and writes only previewed entries, marked "from Claude Code".
- Pulsar in Claude Code: errors are fixed phrases, and a note in the summary cannot pass for another entry.
- A full budget and a closed service are said in plain words when Productive refuses an entry.
- Log dialog: an End time, kept in step with Start and Minutes; an entry cannot run past midnight.
- Pulsar in Claude Code: reasons are fixed phrases only, names are cleaned, and Ask Claude's note drafts come back only when safe.
- Log dialog: time that runs into an entry already logged can be logged around it, as one or more entries.
- Logging from the yellow rail, a drag, + Log or Log and next drafts the note too, from the Likely work in that time.
- A dragged or picked stretch leads with Log the day's task and note for its main work, and offers a split when it mixes work.
- Log the day: a row whose note Claude drafted, or whose task Claude found by searching Productive, starts unticked.
- Ask Claude: the work it reads is data, never instructions; it may only search Productive's tasks.
- Drafted notes: dropped when they hold a sum of money, another client's project name or your own name.
- Notes from commits: only commits whose author and committer are you, by your own git settings.
- Claude Code runs for Ask Claude get a short list of Pulsar's environment, and no claude.ai connectors.
- The Claude Code switch keeps Claude Code's file tools out of Pulsar's folders (deny rules).
- Asking before every Claude Code session changes Productive is now a separate setting, off by default; Pulsar takes back the rules it added for it.
- Settings > Claude: says when managed settings block Pulsar's rules or server, and which Claude account and plan Ask Claude uses.
- Pulsar.app installs the code it carries only when Pulsar's release key signed it, and says so when not.
- An update's description expires 60 days after its release, so an old one served again is ignored.
- After an update the window signs in again through its launcher, which checks the server; a sign-in ends with its server.
- A port held for a moment while Pulsar starts no longer marks a good version bad.
- The error log also removes bare keys and tokens, UUIDs, and fields named as keys or secrets.
- The install line downloads into a private temporary folder and calls curl and ditto by their full paths.

## 1.4.0 (2026-11-06)
- Log the day: Earlier days, to catch up on several days one after another.
- Ticket flags: a closed task, no budget on that date, or no estimate.
- Round entries to 5, 10 or 15 minutes; new tasks found from the day's work.
- Likely and Ask Claude's picks stay with their day, across day changes and restarts.
- Fixes from a full interface test, and safer test runs.

## 1.3.0 (2026-11-06)
- Log the day: the whole day proposed for review, written one entry at a time, with Undo.
- Edit, delete and resize logged entries.
- Pulsar in Claude Code (MCP): ask a session what is not logged, or to log it; it asks before each write.
- One Claude Code switch in Settings; notes drafted from your commits and sessions.
- Better task suggestions, a safer Ask Claude, and many fixes.

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
