# Changelog

What each Pulsar release changed, newest first.

<!-- A release needs its entry before macos/release.sh runs: a "## X.Y.Z (YYYY-MM-DD)" heading and one "- " line.
     "Unreleased" collects what is merged but not released: at release, rename it to the version and date.
     Settings and release.sh read only version headings, so the lines under "Unreleased" show nowhere until then. -->

## Unreleased
- A click on a grey "not work" rail no longer counts it as work at once: it says what it is, with Count as work again (and Undo after it).
- Two clients at once: the work in front comes first; another client's agent that ran beside it is ignored under 15 minutes of a sitting, and from 15 gets half of that time, taken from the work in front, so the clients together never get more than the time sat. A client's agent beside internal work in front is the client's (shared) also in a repository logged to both kinds, judged by its task.
- Log the day leaves no holes where it knows what you did: short turns of one piece of work between longer turns of another (Pulsar in 1 to 8 minute turns between a client's 10 to 15 minute ones) joined the client's entry, the "no more than seen" cap cut them back, and the minutes showed as "not logged" (on 10 November three holes of 15 minutes). Those minutes now go to the work that was in front then: a client's tasks from what that client's project was seen, never more; internal work to the nearest step, but never inside a client's work. A short turn of internal work between two turns of one client's work is that client's also when the repository is logged to both kinds ("mixed", as NLv3): the client is told by the task, so Pulsar turns between Northleaf turns go to Northleaf.
- One away time everywhere: Settings > Away > Away after. Save sets ActivityWatch's AFK timeout to it (longer or shorter) and restarts ActivityWatch, the panel after time away uses it, and so does the fi-time-from-activity skill. Pulsar Preview never changes ActivityWatch on Save.
- Ask when I come back (on by default, Settings > Time and activity > Away): back at the Mac after time away (Away after or more, under three hours), a small panel near the menu bar says "Away 14:10–14:45 · 35 min" and the task before you left, with Break and Log This Time…. Ignored, it goes after 20 seconds and the time stays a break. Not while paused, nor for time already logged or in a meeting. Needs the new Pulsar.app (Reinstall Pulsar).
- A click on the paused stretch of the day no longer resumes: it opens the pause's question (Stay paused, Resume now, Resume from when you came back), with Stay paused focused. Every resume (the button at the top, the question, the Dock menu) has Undo for 10 seconds: the pause comes back from when it began, and the day's marks as they were.
- A pause or time away on the day has Log this time… (in its popover and the right-click menu): for a call or a whiteboard away from the keyboard, the log dialog opens on that stretch, leading with the task you logged just before you left. Away time is still never proposed by itself, and time off (three hours or more) has no button. The right-click menu's Log This Time… on a break used to only explain it.
- Text reads at 4.5:1 or more in both themes: the hour labels, "3 min ago" beside ↻ and other small grey text were 2.4 to 2.8:1, and white on the Mac's accent colour (the main buttons, Settings' selected tab) 4.0:1 with system blue. The accent is darkened only as far as it takes, for any accent colour. Buttons move down a pixel while pressed.
- Pause, at the top and in the Dock icon's and menu-bar item's menus: from the click until Resume nothing counts as work (the day, Log the day, the Report, the reminder, Pulsar in Claude Code). The button turns amber with the time it began, and the tracker goes on recording. Resume makes the paused time "not work" on the day (its rail has Count as work again). Paused for an hour or more while the tracker sees you working, Pulsar asks when you open it: resume from when you came back, now, or stay paused (Settings > Time and activity > Away > Ask about a long pause).
- Earlier days and the Report leave time marked "not work" out of "not logged" by the minute, the reminder and the Dock badge too.
- When a tracker you use stops recording (ActivityWatch or MemTime not running, ActivityWatch's window watcher silent, or no tracker at all), a notice like the update's says so within a minute and offers to open it, or restart ActivityWatch, and to add ActivityWatch to Login Items in Settings. A tracker whose data has not changed for 30 days counts as one you left, and says nothing. Later quiets it for an hour.
- Every use of Claude is Sonnet's by default, as the task picks were: Check my day's review (on the replay a little righter and two to three times faster than with Haiku), the notes (as fast as Haiku on a day's 15, fewer left empty) and a new task's draft. A model chosen in Settings > Claude stays as chosen.
- Beside ↻, how long ago the view was read from Productive ("3 min ago"; the time in its tooltip): the day's entries in the Day view, the range in the Report.
- Going to the Report reads it again when it is more than a minute old, or right after you logged or changed an entry, instead of showing what it read the first time until ↻.

## 1.6.11 (2026-11-09)
- Log the day fills in the notes by itself: Claude drafts one for each row of a Claude session in turn, or takes the one it already drafted for that work from the Likely column (Ask Claude's note only with its own task). A note from the commits is replaced, one you typed never. Before, a note came only after a click in it.
- Settings > Time and activity > Advanced, closed by default: how far apart pieces of one task still join, how long work outside Claude must be to get its own entry, how fast older logging fades in learning, the daily meeting cards (Usual, Strict or Off), when a selected stretch is offered as a split, and the "No estimate" flag. The usual values are what Pulsar did before.
- Working days in Settings > Time and activity > Time: Earlier days, the week line and the end-of-day reminder follow them, not Monday to Friday (the reminder still never comes on a Saturday or Sunday: Pulsar's window decides that).
- The git branch of the work's repository counts as words of the work ("campaign-guides-cta"), and a ticket key in it as a ticket.
- "Check my day with Claude" no longer sends meetings you declined, and sends "private event" instead of the title of a meeting marked private in Google Calendar or holding a word of Settings' personal windows.
- A meeting's title finds its task by word stems ("upgrades" is "upgrade") and prefers the project of the work logged within the hour around it: a meeting about one client's D11 upgrade no longer goes to another client's.
- The titles of the repository's pull requests and issues open during the work count as words of the work.
- A Productive task page open during the work ("… - Tasks - <project> - Productive") is that work's task, when it is of the repository's own project.
- A task the session's Claude opened in Productive, or gave the link of, is that work's task (after a link you typed, before where you usually log): found on this Mac, no one asks Claude.
- A session titled with its task's number ("Productive task 12345678"), or a message naming one, picks that task, as a pasted task link does.

## 1.6.10 (2026-11-09)
- A few minutes of a task right before or after its own logged entry are offered, however short, instead of left as a gap; when two short pieces join, the shorter one goes with the longer one's task.

## 1.6.9 (2026-11-09)
- Time in Pulsar or Productive counts as the work around it, instead of time spent logging: a look at Pulsar in the middle of other work no longer leaves a gap there.
- Pulsar looks for a new version every hour and when the Mac wakes, instead of every 6 hours.
- A short turn of internal work (under 10 minutes) in the middle of a client's work goes to that client, as the Likely column shows the stretch, instead of making a separate internal entry.

## 1.6.8 (2026-11-09)
- A minute in the browser, Slack or another work window goes to the session whose tab you came from, not to another session whose agent ran last in the background.
- Short turns of a few minutes go on with the task their session's work named earlier, as longer ones do.
- Ask Claude sees the tasks Pulsar would pick for each block, with why, and weighs them first: it no longer takes the first task with the site's name in it for other work. The tasks you never logged to are among them, as in the Likely column.
- Two works by turns (a few minutes of each) are one entry each, one after the other, each its own minutes, instead of one entry cut back and the rest left as not logged.
- A client's agent running in the background over internal work in short turns gets those minutes, as a shared row, also when the turns are under 10 minutes each and the repository is logged both ways.
- Ask Claude is back in the Likely column's header (with the Claude Code switch on): Claude's task for each block, on the blocks and first in the log dialog. Asking again about the same blocks, also after a restart, reuses the kept answer and costs nothing; Check my day does the same.

## 1.6.7 (2026-11-09)
- Today's activity refreshes every minute, also while Pulsar's window is hidden, so the Dock badge and the menu bar line keep up with it (a local read of the tracker, about 0.02 s).

## 1.6.6 (2026-11-09)
- A session that did the same work for one client after another (a database upgrade on four sites) no longer offers the first client's task on the next site: its tasks carry into a repository with no history only when most of them are one client's.
- A command that names its folder as `~/Sites/…` or `$HOME/Sites/…` counts for that repository: such a stretch was named after the session's title instead, with the tasks logged under that title.
- What you are doing comes before where you usually log: the task of the repository's project whose name the work names ("D11 db upgrade" → "D11 Upgrades - Dev work") is first in the log dialog's list, yours before a near twin assigned to someone else; in Log the day it comes right after the session's own task. On 19 past days the dialog's first task was right 33% of the time instead of 31%.
- Today's activity refreshes every 5 minutes instead of every hour, so the Activity column keeps up with the now line.
- A later stretch of the same session in the same repository goes on with the task its work named earlier that day ("go on" names nothing).
- A stretch that mixes a client's work with internal work leads with the client's when it holds at least as much of it, even split over two tasks.
- The menu bar shows today's numbers as the day view does, with no read of its own while the day view shows today.
- Days are worked out about 40% faster: session lines outside the day are skipped before they are read, and a day reads each session once.
- The theme button is back in Pulsar's window, and Settings has Appearance > Theme (same as the Mac, Light or Dark).
- Words match across their endings: "upgrade" and "upgrades" (or "update" and "updates") are the same word when a task is found by its words.

## 1.6.5 (2026-11-09)
- A day opens at once with the copy of its Productive entries Pulsar kept last time; Productive's own answer follows in a moment and only what changed is redrawn.
- Past days open at once: the background run that learns from them also keeps them for the day view, so yesterday is not worked out again the first time you open it, and "reading Claude sessions…" no longer flashes on a day already kept.
- A call that runs over its meeting (or starts early) is that meeting in Log the day: a 10:00-10:30 meeting whose call ended at 10:55 is one 55-minute entry, not 30 minutes and a call to pick a task for; meetings back to back each keep their own time.
- A click outside a dialog closes it, as its ✕ does (Settings still asks about unsaved changes); a click outside Log the day's task picker closes it too.
- If Claude Code changes how it keeps its Productive connection, Pulsar says it cannot read it (update Pulsar, or use a token) instead of failing with an error.
- The warp in Pulsar's window no longer shows the page behind it: during an update the new version showed round the edges and through the middle while it loaded. Needs the new app (Update and restart).
- Testing Pulsar counts as work on Pulsar: its own window, while a session in Pulsar's repository is at work, is that session's time in the Likely column instead of time spent logging (on one day, 80 more minutes in front and 71 fewer in the background).
- Over the estimate: the log dialog and Log the day say when the time logged would take a task over its estimate ("+1 h 30 min over the estimate (5 h 30 min of 4 h)"); the dialog follows the length as you type it.
- The error log has Mark as seen: the ! at the top waits for a new error. The log keeps everything.
- Not an example: a logged entry can be marked so Pulsar never learns from it (time moved by hand, or logged to the right task at the wrong time). In the entry's popover, with Undo; a ⊘ on the entry; kept on this Mac.
- Every dialog has its ✕ again in Pulsar's window, and the update reminder has one too.

## 1.6.4 (2026-11-07)
- Pauses and time away are named on the day ("Pause · 12 min", "Away · 1 h 20 min") wherever no card covers them, and a click on one only says what it is: Pulsar never offers to log away time.
- Update also updates the Pulsar app when the window has changed: one click, one restart, and the button says so ("Update to 1.6.4 and restart"). No separate app box in Settings > Updates any more.

## 1.6.3 (2026-11-07)
- The menu bar shows today's active and logged time ("6:12 · 4:48"), with a menu to open Pulsar, log the day or add an entry; on by default, in Settings > This Mac. It needs no permission, unlike the Dock badge, which macOS hides for an app not signed by Apple.
- The menu bar counts in decimal hours with marks: the clock for active time, ✓ for logged ("6.2h  ✓ 4.8h").
- A pause still going on shows as away once it is Away after long, not only after the next activity.
- ActivityWatch is the main tracker: Auto picks it whenever it saw the day, and MemTime's time is cut where ActivityWatch saw you away.
- A gap under an hour is a pause, an hour or more is away: drawn apart in the Activity column.

## 1.6.2 (2026-11-07)
- The warp plays on ↻ (and ⌘R) again, as before 1.6.0.
- The Dock badge asks macOS once for permission to badge the icon: recent macOS shows it only then (Reinstall Pulsar to get it).

## 1.6.1 (2026-11-07)
- Settings → Time and activity → Away after: 10, 15 (the default), 20 or 30 minutes without input is time away and ends a sitting; a shorter pause between two stretches of work counts as work.
- Settings checks ActivityWatch's AFK timeout against Away after and fixes a longer one with a click (a copy of its file is kept, ActivityWatch restarts, new data only), and checks and adds ActivityWatch to Login Items.
- Parallel work: the Likely column is at the real time of the work, one row per repository a session worked in (by what its tool calls did there, subagents included; with the fi-time-from-activity skill, each turn named as the skill names it), with the agents' background work as a thin dashed rail.
- Log the day: a client's agent that ran on your typed request while internal work was on screen gets those minutes first, as a shared row, ticked, with Client, Half each or Internal; a Parallel chip; agent time that ran in parallel is noted, never moved.
- Log the day: a billable entry never has more minutes than the work was seen, and the cap cuts internal work first.
- Tasks: a session's history counts in the repository it was learned in; a minute logged while a client's agent ran teaches that client's repository, not the one on screen; on internal work a client's task only from a rule, a link or a ticket key.
- Claude Code: pulsar_propose marks shared rows and takes prefer; pulsar_session splits by repository and says what was given away; the preview's summary names a shared entry.

## 1.6.0 (2026-11-07)
- Report: a Today range, and it opens on this week.
- In Pulsar's window: 12-hour times when the Mac uses them (fields take "2:30 pm"), dates in the Mac's region order, weeks from its first weekday.
- In Pulsar's window: Settings has the Dock badge, the end-of-day reminder and Open at Login; the Mac's accent colour; no wordmark or ✕ the window already gives.
- Delete asks with Cancel first and a red Delete; the logo pulses only while loading; refresh has no warp; more contrast when macOS asks for it.
- One Claude button, in Log the day: "Check my day with Claude" picks tasks when needed, then reviews; it names the models and their limits; nothing runs by itself.
- Day done: a day at its target (or marked done) greys its rails and leaves Earlier days; "Not work" marks a stretch that was not work.
- Log the day keeps your ticks, tasks, notes and deletions per day while Pulsar is open; Start over forgets them.
- The day's review and Claude's note drafts are kept for 60 days and reused, so the same work is not asked about twice.
- A row's note is drafted when you click in it, not when the row opens.
- Log this time never opens on time already logged; Skip in the log dialog moves on to the next stretch.
- Undo after deleting an entry or dragging its edge.
- Logged cards lead with the task; the project is the colour stripe and the second line.
- Keys: L Log the day, N + Log, ? lists every shortcut; Tab reaches every button; ⌘↩ shows on the Log button.
- Settings asks before closing with changes not saved; Settings → Claude no longer runs off its edge.
- The now line moves every minute, and at midnight (or when the Mac wakes) today becomes the new day.
- Log the day is the main button while time is unlogged, and "Active, not logged" opens it; one zoom menu, one refresh; the error log moved to Settings → Updates.
- Log the day lists what is still to log; what is logged folds into one line (To review / All).
- No more crumbs: entries run up to the entry next to them, and stretches shorter than the rounding step are not marked unlogged.

## 1.5.5 (2026-11-07)
- Log the day: "Tick all with a task", Enter ticks the open row and opens the next; the confirm step marks Claude's notes and searches.
- Log the day: "to check" counts down as you open and tick rows, opens the next one, and says "All checked" at the end.
- Task search puts yours first: tasks you logged to lately, then those assigned to you; closed tasks last.
- Log the day: a row opened with no note gets one drafted by Claude from its sessions and commits, and picking another task keeps it.
- Log the day: entries already in Productive have a green bar, a green wash and a check, apart from what is still to log.
- The Pulsar app (a new install, or Settings, Updates, Reinstall Pulsar): full menus with shortcuts (New Entry Cmd+N, Day Cmd+1, Report Cmd+2, Today Cmd+T, Previous and Next Day Cmd+Left and Cmd+Right, Refresh Cmd+R, Print Cmd+P, Help), and Cmd+W closes an open dialog before the window.
- The Pulsar app: closing the window keeps Pulsar in the Dock, with today's time not logged on its icon and a reminder at 17:30 on working days; Cmd+Q asks first when something would be lost.
- The Pulsar app: right-click an entry, an activity block or a gap for its own menu; the title bar takes the page's colours and theme; a pinch changes the rows; after a sleep the day moves on to today.
- The Pulsar app: its first-start dialogs are sheets on its window, with a spinner while it starts, and it goes on by itself once Apple's developer tools are installed.
- A release can ask for a newer Pulsar app: Settings, Updates offers Reinstall Pulsar, checked like an update, and the old app comes back if the new one does not start.

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
