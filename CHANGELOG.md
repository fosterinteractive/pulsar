# Changelog

What each Pulsar release changed, newest first.

<!-- A release needs its entry before macos/release.sh runs: a "## X.Y.Z (YYYY-MM-DD)" heading and one "- " line.
     "Unreleased" collects what is merged but not released: at release, rename it to the version and date.
     Settings and release.sh read only version headings, so the lines under "Unreleased" show nowhere until then. -->

## Unreleased
- Log this time never opens on time already logged; Skip in the log dialog moves on to the next stretch.
- Undo after deleting an entry or dragging its edge.
- Logged cards lead with the task; the project is the colour stripe and the second line.
- Keys: L Log the day, N + Log, ? lists every shortcut; Tab reaches every button; ⌘↩ shows on the Log button.
- Settings asks before closing with changes not saved; Settings → Claude no longer runs off its edge.
- The now line moves every minute, and at midnight (or when the Mac wakes) today becomes the new day.
- Log the day is the main button while time is unlogged, and "Active, not logged" opens it; one zoom menu, one refresh; the error log moved to Settings → Updates.
- Log the day lists what is still to log; what is logged folds into one line (To review / All).
- No more crumbs: entries run up to the entry next to them, and stretches shorter than the rounding step are not marked unlogged.
- Log the day: "Tick all with a task", Enter ticks the open row and opens the next; the confirm step marks Claude's notes and searches.
- Log the day: "to check" counts down as you open and tick rows, opens the next one, and says "All checked" at the end.
- Task search puts yours first: tasks you logged to lately, then those assigned to you; closed tasks last.
- Log the day: a row opened with no note gets one drafted by Claude from its sessions and commits, and picking another task keeps it.
- Log the day: entries already in Productive have a green bar, a green wash and a check, apart from what is still to log.

## 1.5.4 (2026-11-07)
- A task you never logged to takes the service you used last in its project, in the log dialog and in Log the day.
- Log the day's "Review with Claude" turns like Ask Claude while Claude reviews the day.

## 1.5.3 (2026-11-07)
- A bridge release: copies older than 1.5.1, which ignored 1.5.2 ("not signed with the release key"), update to this one and from then on trust the Touch ID key too. No other change.

## 1.5.2 (2026-11-07)
- Log the day: the confirm step shows every entry again, ticking a row keeps the list where it was, the Likely column stays beside it, and a row can create its task.
- Signed with the Touch ID release key; the install line checks Pulsar.zip with it.
- Writes Productive answers unclearly are held, never resent; pulsar_log on a long day answers in time; clock-change nights count real minutes.

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
- Log dialog: Create a task in Productive, assigned to you, when none fits the work, with a drafted title and description (by Haiku with Claude Code on); it is then picked.
- Pulsar in Claude Code: the tools are answered inside Pulsar; the key alone opens nothing else and writes only previewed entries, marked "from Claude Code".
- Pulsar in Claude Code: errors are fixed phrases, and a note in the summary cannot pass for another entry.
- A full budget and a closed service are said in plain words when Productive refuses an entry.
- Log dialog: an End time, kept in step with Start and Minutes; an entry cannot run past midnight.
- Pulsar in Claude Code: reasons are fixed phrases only, names are cleaned, and Ask Claude's note drafts come back only when safe.
- Log dialog: time that runs into an entry already logged can be logged around it, as one or more entries.
- Logging from the yellow rail, a drag, + Log or Log and next drafts the note too, from the Likely work in that time (with Claude Code on, Claude drafts it from that session).
- A dragged or picked stretch leads with Log the day's task and note for its main work, and offers a split when it mixes work.
- Log the day: a row whose note Claude drafted, or whose task Claude found by searching Productive, starts unticked.
- Ask Claude: the work it reads is data, never instructions; it may only search Productive's tasks.
- Drafted notes: dropped when they hold a sum of money, another client's project name or your own name.
- Notes from commits: only commits whose author and committer are you, by your own git settings.
- Claude Code runs for Ask Claude get a short list of Pulsar's environment, and no claude.ai connectors.
- The Claude Code switch keeps Claude Code's file tools out of Pulsar's folders (deny rules).
- Asking before every Claude Code session changes Productive is now a separate setting, off by default; Pulsar takes back the rules 1.3 and 1.4 added for it, so tick it to keep that protection.
- Settings > Claude: says when managed settings block Pulsar's rules or server, and which Claude account and plan Ask Claude uses.
- Pulsar.app installs the code it carries only when Pulsar's release key signed it, and says so when not.
- An update's description expires 60 days after its release, so an old one served again is ignored.
- After an update the window signs in again through its launcher, which checks the server; a sign-in ends with its server.
- A port held for a moment while Pulsar starts no longer marks a good version bad.
- The error log also removes bare keys and tokens, UUIDs, and fields named as keys or secrets.
- The install line downloads into a private temporary folder and calls curl and ditto by their full paths.
- Pulsar in Claude Code and the launcher check that the server holds this user's key for their own port before they show it.
- Create a task: after an answer that is not clear the task may exist, so the same title is held for ten minutes; search for it first.
- A failure behind Productive's MCP (a server error, a timeout) and a created answer without the new entry are "may exist", never "refused".
- Pulsar in Claude Code: pulsar_log answers within five minutes with what it wrote, and never says "Open Pulsar" while it may be writing.
- Pulsar in Claude Code: time logged with no start time is never offered again as ready, and the summary names it.
- Pulsar in Claude Code: a source that could not be read is said in the warnings.
- Log dialog: a task just created stays picked when the stretch's answer comes later; a drag keeps its exact minutes.
- The night the clocks change, End, a drag, a dragged edge and the split count real minutes.
- The split: a task Claude found by searching, or a note Claude drafted, waits for a tick.
- Task refusals keep Productive's own words after the plain sentence.
- Earlier days: time logged with no start counts, and a tracker that gave nothing is said.
- Likely: a past day worked out while no tracker answered is worked out again once one does.
- Settings: "Ask before any Claude Code session changes Productive" says it waits for the Claude Code switch.
- Ask Claude: the cost shown counts both runs when Claude was asked twice.

## 1.4.0 (2026-11-06)
- Log the day: Earlier days, to catch up on several days one after another.
- Ticket flags: a closed task, no budget on that date, or no estimate.
- Round entries to 5, 10 or 15 minutes; new tasks suggested from the day's work (Pulsar never creates them).
- Likely and Ask Claude's picks stay with their day, across day changes and restarts.
- Fixes from a full interface test, and safer test runs.

## 1.3.0 (2026-11-06)
- Log the day: the whole day proposed for review, written one entry at a time, with Undo.
- Edit, delete and resize logged entries.
- Pulsar in Claude Code (MCP): ask a session what is not logged, or to log it; it asks before each write.
- One Claude Code switch in Settings: it adds Pulsar to Claude Code (`~/.claude.json`) and rules to `~/.claude/settings.json` that bind every Claude Code session; notes drafted from your commits and sessions.
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
