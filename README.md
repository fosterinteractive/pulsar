<p align="center"><img src="macos/AppIcon-1024.png" width="128" alt="Pulsar's icon: a pulsar, a bright core with two beams, on a dark tile"></p>

<h1 align="center">Pulsar</h1>

Pulsar shows your working day as your computer saw it, next to what you logged in Productive: when you worked, on what,
and which active time has no entry yet. It runs on your Mac, and window titles never leave it. If you switch on Claude
Code in Settings, the parts of your day Claude needs go to Anthropic when Claude runs
([What goes to Anthropic](#what-goes-to-anthropic)).

Pulsar writes to Productive only when you ask, and you confirm each time: logging time, changing or deleting an entry,
Log the day and its Undo (with a ticket's status after logging), creating a task, or a Claude Code session logging time
for you. That holds in normal use; a
program with full access to your account can get around any confirmation, as with any app ([Security](#security)).

- **Day**: one time axis with up to five columns: **Planned** (your bookings in Productive's Resource Planner, opened
  from the calendar icon above the time axis), **Meetings** (once Google Calendar is connected), **Activity** (what
  was on screen), **Likely** (what you most likely worked on, for Claude Code users) and **Logged** (your Productive
  entries). A yellow rail marks active time with no entry; a hatched rail, a pause or time away (named, never offered
  for logging); a dashed card, an entry you log at this time most days. Under the date: the week's logged and active
  hours. On the side: work time against your daily target, time per project and budget, and the day's tasks against
  their estimates.
- **Report**: today, this week (where it opens), last week, this or last month, the last 30 days, or any range up to 93
  days: logged against active time by day, time by project, top apps and sites, projects by day, when in the week you
  work, tasks against their estimates, **parallel work** (by project, how long Claude Code agents ran in the
  background while you did other work, and about how much of it no entry covers), and every day in a table. **Download** saves the report as a PDF, an **invoice
  summary** (your name, the period, then your tasks by project, billable projects first and the most time first, each
  with its subtotal, its share of the time, and its tasks' time and billable time; as PDF or CSV), or the days or the
  time entries as CSV (for Excel or Numbers); **Print…** prints the report. A Pulsar window installed before 1.6.14
  opens the print dialog for a PDF: choose Save as PDF there. Coming back to the Report reads it again when it is more
  than a minute old, or after you logged something.

## Install on a Mac

Pulsar needs macOS 12 Monterey or newer, on Apple Silicon or Intel.

1. **ActivityWatch**, the free tracker Pulsar reads (skip this if you use MemTime): download it from
   [activitywatch.net](https://activitywatch.net/downloads/), drag it into Applications and open it. When macOS asks,
   allow it in **System Settings → Privacy & Security → Accessibility**; without that it sees no windows.
2. **Pulsar**: open **Terminal**, paste this line and press Return. It downloads Pulsar, checks its signature, puts it
   into Applications and opens it:

   ```bash
   d=$(/usr/bin/mktemp -d) && /usr/bin/curl -fsSL https://github.com/fosterinteractive/pulsar/releases/latest/download/Pulsar.zip -o "$d/Pulsar.zip" && /usr/bin/curl -fsSL https://github.com/fosterinteractive/pulsar/releases/latest/download/Pulsar.zip.sig -o "$d/Pulsar.zip.sig" && printf '%s\n' '-----BEGIN PUBLIC KEY-----' 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAECcw9Ny0xyPDVAsyM2aac+nwH5IZ5' 'EEdlXCwE6MMw3HpRi03aBa7effm9G1JudLuMLdFWxsAfKF43N/piDHieQA==' '-----END PUBLIC KEY-----' > "$d/key.pem" && /usr/bin/openssl dgst -sha256 -verify "$d/key.pem" -signature "$d/Pulsar.zip.sig" "$d/Pulsar.zip" && /usr/bin/ditto -x -k "$d/Pulsar.zip" "$d/new" && /bin/rm -rf /Applications/Pulsar.app && /bin/mv "$d/new/Pulsar.app" /Applications/ && /usr/bin/open /Applications/Pulsar.app; /bin/rm -rf "$d"
   ```

   It prints `Verified OK` when the file carries the signature of Pulsar's release key; anything else prints
   `Verification Failure` and installs nothing ([About the install line](#about-the-install-line)).
3. **Apple's developer tools**: if Pulsar says it needs them (they bring Python), click **Install** and enter an
   administrator password. It takes about 5 minutes, only once, and Pulsar goes on by itself when they are installed.
4. **Productive**: with Claude Code's Productive connection there is nothing to do. Otherwise paste a token into
   **⚙ Settings → Connections → API token** and click **Save** ([Connect Productive](#connect-productive)).
5. **Check ActivityWatch**: **Settings → Time and activity → Away** shows whether ActivityWatch counts a break as Pulsar
   does and opens at login, and fixes either with a click. **Away after** is the one away time: Save sets
   ActivityWatch's AFK timeout to it. For Login Items, macOS asks once whether Pulsar may control
   System Events: allow it. Whenever ActivityWatch or MemTime stops recording while Pulsar is open, a notice says so
   and offers to open it.

### About the install line

- It checks Pulsar.zip with macOS's own `openssl` against the release key's public half, written in the line. A file
  swapped on GitHub or cut short fails, even if someone can publish a release there without the key. The check trusts
  the line, so copy it from this page on GitHub or from the onboarding document, not from a message.
- It works in a private temporary folder that it removes, so no other account can swap the file before it is unpacked,
  and calls every program by its full path, so an older copy earlier in your PATH (MAMP has one) is not used.
- It replaces an older Pulsar.app whole, so no old file is left inside (the app's code check would refuse it). Your
  settings, token and installed versions live elsewhere and stay.
- Pulsar is not signed by Apple. macOS blocks an unsigned app a browser downloaded until you confirm it; `curl` does not
  mark the file as downloaded, so Pulsar opens at once. A company-managed Mac may still block it: then IT must allow it.
- From then on, the app installs the code it carries, and every update, only if the release key signed it.

**Without Terminal**: download **Pulsar.zip** from the link you were sent, open it and drag **Pulsar** into
Applications. Nothing checks Pulsar.zip itself this way; the app still installs only code the release key signed. The
first time, macOS says it cannot verify Pulsar: click **Done**, open **System Settings → Privacy & Security**, scroll
down to the line about Pulsar, click **Open Anyway** and confirm (on macOS 14 and older: right-click Pulsar, **Open**,
then **Open** again). Only once.

**Once the repository is private**, the install line answers 404 (it downloads without a GitHub sign-in): sign in to
GitHub in your browser with an account that can see `fosterinteractive/pulsar`, download **Pulsar.zip** from its
Releases page and install it as **Without Terminal** says. Then sign in to GitHub in Pulsar (see [Updates](#updates)).

**Reinstalling by hand**: quit Pulsar (Cmd+Q), then run the install line again. A Pulsar left running keeps its old
window until it quits.

## Using the app

Open Pulsar like any other app; if it is running, its window comes to the front. The window needs Safari 16.2 or newer
(macOS 13 has it; on macOS 12, update Safari). With an older Safari, Pulsar opens in your browser instead, and keeps
running after you close the tab, until you log out.

- **Keys**: **L** Log the day, **N** + Log (the next time not logged), **←** **→** another day, **T** today, **↩** opens
  the block or rail in focus, **⌘↩** logs in the log dialog, **Esc** or a click outside closes a dialog, **?** lists
  them all. Tab reaches every button.
- **Menus**: every command is in the menu bar with its shortcut, and **Help** searches them: New Entry Cmd+N, Log the Day
  Cmd+L, Day Cmd+1, Report Cmd+2, Today Cmd+T, previous and next day Cmd+Left and Cmd+Right, Refresh Cmd+R, Print
  Cmd+P. Cmd+= and Cmd+- zoom the page. Cmd+W closes an open dialog or popover first, then the window. Right-click an
  entry, an activity block or a gap for what you can do with it. A trackpad pinch changes the rows (1 minute to 1
  hour). Downloads ask where to save.
- **Closing the window** keeps Pulsar running; click its Dock icon to bring the window back. **Settings → Time and
  activity → This Mac** sets, at once:
  - **Menu bar** (on): today's active and logged time ("6.2h ✓ 4.8h"), as the day view shows them, with a menu to open
    Pulsar, log the day or add an entry.
  - **Dock badge** (on): today's active time with no entry ("3h"), from 15 minutes. macOS asks once to allow it, and may
    hide it anyway because Pulsar is not signed by Apple.
  - **Reminder** (on): on weekdays at 17:30, or the time you set, a notification with **Log the Day** when half an hour
    or more is not logged. If macOS does not let Pulsar notify, the Dock icon bounces once and the window says it.
  - **Open Pulsar when I log in** (off): it then opens without taking the keyboard from what you are doing. On macOS
    12, add it by hand in **System Settings → General → Login Items** (**+** under *Open at Login*).
- **Quit** with Cmd+Q: nothing keeps running then. If something would be lost (time being logged, changes in Log the
  day, Settings not saved), Pulsar asks first.

## Updates

Pulsar asks GitHub every hour (and when the Mac wakes) whether a newer version exists (it sends nothing about you) and shows it next to the
version at the top and in **Settings → Updates**. It installs nothing by itself. When you click **Update**, it downloads
the new version, checks its signature, runs its self-test and restarts into it, in a few seconds; you never download
Pulsar again. Only releases signed with the maintainer's release key are accepted.

- When a release also needs a newer Pulsar app (its window and menus), the button says **Update to 1.x and restart** and
  updates the app after the code, with one restart (**Update and restart** when only the app is behind). The app is
  checked like an update and put into Applications; Pulsar quits and opens again (an app older than 1.6 asks you to
  press Cmd+Q). If Pulsar cannot change the app in Applications (another account installed it), it shows the install
  line instead.
- Two or more releases behind, Pulsar reminds you once every two days; **Skip this release** stops the reminder until
  a newer one is out.
- What changed: [CHANGELOG.md](CHANGELOG.md), also under **Settings → Updates → Changelog**.
- **Sign in to GitHub, once per Mac** (**Settings → Updates → Sign in to GitHub**): Pulsar's repository is going
  private, and then updates arrive only through a GitHub account that can see it. Pulsar shows a code; enter it on
  GitHub's page that opens and approve. Until you do, the version at the top says **sign in to GitHub**. The sign-in
  lets Pulsar read its own releases and nothing else, and it does not expire: you sign in again only if GitHub
  removes your access to the repository, you revoke it in GitHub's settings, or Pulsar goes unused for a year.
  Settings says when the account cannot see the repository: then ask a GitHub admin at Foster for access to
  `fosterinteractive/pulsar`.

## Connect Productive

**A token**: in Productive, **Settings → API integrations → Generate new token**, with **Read/Write** access (read-only
lets Pulsar show your time but not log it). Paste it into Pulsar's **Settings → Connections → API token** and click
**Save**; Pulsar checks the connection and says who it is connected as. The token is kept on your computer only: in the
login keychain on a Mac, elsewhere in `~/.config/pulsar/token`, readable by you alone. It sees what you see in
Productive, no more. It never expires, so it is the steadier choice.

**Claude Code's connection** (Mac only, no token): Pulsar reads Claude Code's keychain item and uses only the
Productive access token in it; nothing else in it is used or stored. The token lasts an hour. Once it expires, Pulsar
runs `claude mcp get productive`, Claude Code's own check of the connection (no AI model involved), during which Claude
Code renews the token and keeps the new one; Pulsar then reads it again. Pulsar never uses the renewal key itself, so
Claude Code's connection keeps working. This needs the `claude` command, or the Claude Code extension in VS Code or
Cursor. If Claude Code cannot renew the token, Pulsar says so: run `/mcp` in Claude Code and reconnect Productive. If
Pulsar cannot read the connection at all (Claude Code changed how it keeps it), it says so: update Pulsar, or use a
token.

**Connect with** in Settings picks one; Automatic uses your token when there is one. If Pulsar cannot find your
organization or person, Settings asks: the **Organization ID** is the number at the start of your Productive address
(`app.productive.io/12345-company/…`), **Your person ID** the number in your own profile's address.

## Settings

| Setting | Default |
|---|---|
| Connect with | Automatic: your API token, else Claude Code's connection |
| Time zone | this computer's |
| Daily target | your organization's working day in Productive (read through Claude Code's connection only), else 8 h |
| Working days | Monday to Friday: the days Earlier days offers, the week line counts and the reminder comes on |
| Round entries to | 5 minutes (or 10 or 15): Log the day's entries start and end on this step (or against an entry already logged), a block you click to log is rounded to it (a drag or typed minutes are not), and shorter active time is not marked as unlogged |
| Away after | 15 minutes (or 10, 20 or 30) without typing or moving the mouse: time away, and the end of a sitting (Likely, Log the day) |
| Appearance | Theme: same as the Mac (or Light, or Dark); ◐ at the top and View > Appearance set it too |
| Advanced | closed: join pieces of one task 30 min apart, work outside Claude an entry from 10 min, learning memory 7 days, daily meeting cards Usual, offer a split under 75%, a session's pick ticked from 60% of its votes (80% on a client's work), the "No estimate" flag on |
| This Mac | menu bar, Dock badge and reminder on; Open at login off (Pulsar's window only) |
| Personal windows | Telegram, WhatsApp, YouTube, Netflix, Instagram, Facebook, TikTok, Reddit (one word per line; a window whose app, title or site contains one is personal, never work) |
| ActivityWatch address | `http://localhost:5600` |
| Take the time ActivityWatch missed from MemTime | on (Time and activity → Activity) |
| Planned column | on (Connections → Productive): your Resource Planner bookings behind the icon above the time axis |
| Rules | none (words in a window, page or site that mean a task: that time is coloured by project, and the task comes first when you log it) |
| Likely column | shown when there are Claude Code sessions on this computer (`~/.claude/projects`) |
| Keep past days | 1 month (or 2, 3, 6 months or a year): how long past days' Likely columns stay on this Mac (Settings → Claude) |
| Use Claude Code with Pulsar | off ([Claude Code](#claude-code)) |
| Claude's models | Sonnet for every use (task picks, the review, notes, a new task's draft). **Settings → Claude** lists each use, what it sends, and a model to pick for it (Haiku, Sonnet or Opus) |
| Google Calendar | not connected |

Settings are saved in `~/.config/pulsar/settings.json`. Delete `~/.config/pulsar` or `~/.cache/pulsar` to start again.

## Your day

### Activity and time away

- **ActivityWatch is the main tracker**: it counts input, so it knows when you are at the keys. MemTime counts the
  window in front, and is the backup: where ActivityWatch recorded nothing at all for 10 minutes or more (it was quit
  or crashed), the Day view takes that time from MemTime; the Activity header then says **ActivityWatch + MemTime**
  and those cards have a dashed edge (**Settings → Time and activity → Activity** switches it off). A day
  ActivityWatch saw nothing of, or under a fifth of what MemTime saw, is MemTime's. MemTime's time is cut wherever
  ActivityWatch recorded you away. The Report takes, for each day, the tracker that saw more of it. The Activity
  column's header can switch tracker; **ActivityWatch** there shows its own record alone.
- **Breaks**: reading or a call with no typing is time away once it lasts **Away after**; a shorter pause between two
  stretches of work counts as work. A gap under an hour is a pause, an hour or more is away, three hours or more is off
  and never added to the total.
- **ActivityWatch's own timeout**: it writes a pause only after its AFK timeout (3 minutes unless changed), so a longer
  timeout than Away after hides the shorter breaks. Settings says so, and **Fix** sets it to Away after: it keeps a copy
  of ActivityWatch's file beside it, restarts ActivityWatch, and the change applies to new data only.
- **Without a tracker** the Activity column stays empty and no time is marked unlogged; Likely, Meetings and Logged
  still show.

### Likely, for Claude Code users

Pulsar reads your Claude Code sessions (`~/.claude/projects`, on this Mac only) and shows, minute by minute, which
session you were most likely working in: the one whose title your editor showed, else one whose agent was running, else
the one you used last in that sitting. Each row is named after the git repository the session worked in, so a session
that worked in two repositories gives a row for each; another session's agent working meanwhile is a thin dashed rail.
Meetings and personal windows go to no session. Time in Pulsar or Productive counts as the work around it, like any
other window. A session's turns spent logging time, from 10 minutes, show as a quieter dashed **Time logging** card
with the session's name: Log the day offers them as one unticked row for an internal task (your scrum task until you
pick another; Pulsar remembers it), and a click on the card logs them by hand. Shorter turns stay with the work
around them. Nothing is sent
anywhere unless you switch on Claude Code, and nothing is logged by itself.

**Log this time** on a row offers first a Productive task you pasted into that session in the three hours before, then
the tasks you logged in the same session, then in the same repository, over the last 60 days, then tasks whose words
match the work. That history is worked out once in the background and keeps only times, repository names and a hash of
each session title (`~/.cache/pulsar/likely-days/`). Work outside Claude Code (and every minute, for someone without
it) learns from the windows you had in front while you logged each task: kept as minutes and hashes only
(`~/.cache/pulsar/context-days/`), never the titles. Past days' Likely columns, with session titles and first messages,
are kept so they open at once (`~/.cache/pulsar/likely-full/`; a month, or as **Settings → Claude → Keep past days**
says, up to a year); ↻ works a day out again.

### Logging and changing time

- **Time that runs into an entry already logged**: the log dialog shows the free time around it and offers to log that
  instead (**Log 2 entries around it**); **Log anyway** is still there.
- **No task for the work yet**: the log dialog can create one, drafted from the work, and pick it; nothing is created
  until you click **Create in Productive**. If Productive gives no clear answer, the task may exist: Pulsar will not
  create the same title in that project again for ten minutes, so search for it first.
- **Ticket flags**: the log dialog and Log the day say when a task is closed, when no budget takes time on that date,
  when it has no estimate while the project's other tasks have one, or when this time would take it over its estimate,
  with a link to fix it in Productive. Pulsar only reads these flags; the one change it makes to a task is its status,
  from Log the day (below), when you confirm the run.
- **Changing an entry**: click it in Logged. **Edit** opens it in the log dialog; **Delete** asks first (Productive keeps
  deleted entries in its Recycle Bin). Drag its top or bottom edge to move its start or end; Pulsar asks before it
  changes anything. **Undo**, in the note at the bottom, puts back a delete or a drag (a deleted entry comes back as a
  new one; not for an entry with no start time or a formatted note). An approved entry can still be changed where the
  budget approves every entry at once; where an approver approved it, Productive refuses and Pulsar shows its reason.
- **Not an example**: in an entry's popover, mark it so Pulsar never learns from it (time moved by hand, or logged to
  the right task at the wrong time). It shows a ⊘; **Learn from it again** or Undo takes the mark back.

### Log the day

**Log the day** in the Logged column proposes the whole day at once, from your activity, Claude sessions, meetings,
daily repeats, rules and what you logged before. The side panel lists each entry with its task, service, how sure
Pulsar is and why. Unsure entries, and those whose task and note Claude picked, start unticked. You can change the
task, service, start, length and note, or remove an entry.

- **Tick all with a task** ticks every entry that has a task and a service, except one whose task Claude found by
  searching Productive, or a stretch of several tasks' short turns (each waits until you open it). Enter on an open
  entry ticks it and opens the next.
- **Status after logging**: each entry can move its ticket to another status of its workflow (QA, Done…), only once the
  entry is written; the confirm step says so, and **Undo this run** puts the status back unless someone changed it
  since. A ticket not started yet (Backlog, To do) goes to **In progress** when its entry is written, said under the row
  even when it is closed, with **Keep its status**; a ticket already started or closed stays as it is.
- **Short turns of several tasks**, none long enough for an entry, become one row with no task picked and each of those
  tasks offered, most minutes first: pick the one that time was from.
- Nothing is written until you confirm. Then the entries go to Productive one at a time, stopping at the first one that
  is refused, and **Undo this run** deletes only what that run wrote.
- With the Claude Code switch on, the notes fill in by themselves: Claude drafts one for each entry from a Claude
  session, one after another, a few seconds each, or takes the one it already drafted for that work in the log dialog.
  A note from your commits is replaced; one you typed never is. The confirm step marks each draft "Claude's note".
- Notes come only from a meeting's title, your usual note for a repeat, your commits, or Claude's drafts. A meeting's
  title is a note only when you or someone of your own company (your Google account's domain) organized it and it reads
  like a meeting's name (not an instruction, a sum or an address); a series renamed since you last logged it starts
  unticked.
- A call that runs over its meeting, or starts early, is that meeting: a 10:00 to 10:30 meeting whose call ended at
  10:55 is one 55-minute entry. Meetings back to back each keep their own time and note, even when both go to one
  task (a daily scrum, then a weekly meeting). A meeting finds the task you logged in the earlier meetings of its series.
- Your ticks, tasks, notes and deletions are kept per day while Pulsar is open; **Start over** forgets them.
- Work outside Claude Code (Slack, a browser, a document), and for someone without Claude Code the whole day, takes
  the task you logged before while the same window was in front: the Slack conversation, the site and the first parts
  of its address, the document's title. Only an internal task is named this way (a client's is left for you to pick),
  and such rows start unticked. The windows are kept on your Mac as hashed keys only.
- When you work by turns, Log the day lays a task's minutes out together from where it began, so an entry can stand
  at other times than its work: its row says where its minutes were seen ("seen at 20:00–20:04, 20:23–20:38").
- **Earlier days…** lists your last workdays with active time no entry covers. Tick the days, and each opens in turn as
  Log the day; each is written only when you confirm it.

**Self-check** (Settings → Self-check → **Check**). Pulsar works out, on your Mac, the plan Log the day would have made
for each of your last 30 days without that day's entries, compares it with what you logged, and shows a few percents:
the right task, the right client and no task (of the minutes you logged), and billable time on another client, over
and under (of the billable minutes). Then, when there is data, how often Log the day's rows went in as proposed, two
rules being tried, and for each way Log the day picks a task (the session, the last task in the project, a repeat, a
task link…) how often it was right and how much of the plan it made. The first Check takes a few minutes; after that
only new or changed days are worked out, and a new version of Pulsar works them all out again. It uses your own
entries and is never sent anywhere; **Copy** copies the percents, to share if you choose.

**Day done, and time that was not work.** A day whose logged time reaches your daily target is done (or click **Mark the
day done** under Work time): its yellow rails turn grey and it leaves Earlier days; **Not done** undoes it. **Not work**,
in the log dialog or on a Log the day row, says a stretch was not work: it is no longer marked or proposed (click its
grey hatched rail, then **Count as work again**, to undo). These marks, and Not an example, stay on this Mac and are never sent to Productive.

**Pause.** Stepping away for something that is not work? Click **Pause** at the top (or in the Dock icon's menu, or
Pulsar's menu-bar item): from then on nothing counts as work. The button turns amber with the time it began, the day
shows the paused time hatched in amber, and Log the day, the Report, the end-of-day reminder and Pulsar in Claude Code
leave it out. Your tracker goes on recording. Click it again (or **Resume** in the Dock menu) when you are back: the
paused time becomes "not work" on the day (its grey rail has **Count as work again**). If you forget, Pulsar asks when you open its
window: paused for an hour or more (Settings → Time and activity → Away → **Ask about a long pause**: 30 minutes to 4
hours, or Never) while the tracker saw you working, it offers **Resume from** the time you came back, **Resume now**
or **Stay paused**.

**Back from time away.** A call, a whiteboard, a talk at someone's desk: when you come back to the Mac after time
away (Settings' **Away after** or more), a small panel near the menu bar says how long you were away and what you
worked on before, with **Break** and **Log This Time…**. Ignore it and it stays a break. Settings → Time and activity
→ Away → **Ask when I come back** switches it off. A break on the day also has **Log this time…** in its popover.

### Meetings

- **Google Calendar**: **Settings → Connections → Connect Google Calendar** opens Google's sign-in; after **Allow**, the
  Meetings column shows your meetings (↻ reads them again at once). Pulsar can only read your main calendar's events,
  never change them. Only fosterinteractive.com accounts can sign in. **Log this time** on a meeting fills in its title
  as the note (by the rule above) and offers first the task you logged during its earlier ones, then your tasks whose
  names share a word with its title. **Disconnect** removes the access, at Google too (in Pulsar Preview only on this
  Mac, because revoking at Google would take Pulsar's access away with it).
- **The daily meeting, ready to log**: an entry you log at the same time on most days (a daily scrum) is noticed from
  your last four weeks in Productive. When the tracker sees a call near that time (Zoom, Meet, Teams, Webex, a Slack
  huddle), the Logged column shows it as a dashed card with the task, the usual note and the call's length. One click
  opens it in the log dialog; nothing is logged until you click **Log in Productive**.

### Speed, and when Productive is unreachable

- A day you open shows at once the copy of its entries Pulsar kept last time; Pulsar then checks Productive in the
  background and redraws only if something changed. Past days' Likely columns open at once too.
- If Productive cannot be reached, the Day view and the Report show the last copy of each day and say from when. A read
  that Productive drops without answering is tried up to twice more; time you log is never sent twice.
- Today's activity refreshes by itself every minute, the rest every hour; **↻** (Cmd+R) reads everything again.
- **The warp**: ↻ and updates draw the page in towards a black hole (a bright one on the light theme) with a ring of
  light while it loads, then let go with a small flash. In Pulsar's window the page really bends, drawn with Core Image
  on the graphics card, so it stays smooth on any Mac; a browser shows a version without the bend. Turn it off in
  **Settings → Updates**; it is off anyway while macOS has **Reduce motion** on.

## Claude Code

One switch, off by default: **Use Claude Code with Pulsar** in **Settings → Claude**, then **Save**. It needs Claude Code
on this Mac and turns on:

- **Check my day with Claude**, in Log the day, the one button that runs Claude there. Your own Claude Code first
  suggests a task and a note for each entry Pulsar is unsure of (only when the day has no picks yet), then reviews the
  whole day with FI's rules; you accept or reject each suggestion. Claude may search Productive, read only (tools that
  change anything are refused). The button's tooltip names the models and the most each run may cost. A run takes from
  about 15 seconds to a couple of minutes. Nothing runs by itself.
- **Notes from the session**: the log dialog drafts the note by itself when it opens on work from a session (a block, a
  rail, a drag, **+ Log**, **Log and next**), from the session that holds most of that time; Log the day drafts one
  for each row from a session by itself, one row at a time. A few seconds each; it never replaces what you typed, and
  work already drafted for is not asked again.
- **A new task's draft**: **Create a task** asks Claude at once for a title and a description.
- **Logging from a session**: Pulsar adds itself to Claude Code, so in any new session you can ask "what did I not log
  today?" or "log the time of this session". The session reads your day from Pulsar and shows the entries; Claude Code
  asks you before each write, showing Pulsar's own summary of it. One write may hold up to 40 entries, so one yes can
  log a whole day: read the summary. Pulsar must be open. These entries say "from Claude Code" in the Logged column.
- **Rules in Claude Code's settings** (`~/.claude/settings.json`; a backup of that file is kept beside it): an ask rule,
  so Claude Code asks before Pulsar's log tool, and deny rules that keep Claude Code's file tools out of Pulsar's folders
  (its key, sign-in code and settings, the installed code, the window's data, the cache). They stop Claude Code's file
  tools and the file commands it recognizes in a shell; a script is stopped only with Claude Code's sandbox on.
- **Ask before any Claude Code session changes Productive** (a separate box, off by default): ask rules for the
  Productive server named `productive`, which then apply to every Claude Code session on the Mac. A Productive
  connection under another name, such as a claude.ai connector, is not covered.

Switching it off takes back everything it added, and nothing else.

### What goes to Anthropic

These run your own Claude Code, so what they send goes to Anthropic under your Claude plan or account and its terms.
**Settings → Claude** shows which account Claude Code uses; on a consumer plan (Pro, Max), Anthropic may train on these
chats if that is on for your account. Each run Pulsar starts uses none of your Claude Code settings, CLAUDE.md files,
hooks, other MCP servers or auto memory.

- **Task picks**, for each block of 10 minutes or more: its time, the repository's name, up to 3 session titles, up to
  15 of your messages (400 characters each) with Claude's reply at the end of each step (900 characters), up to 10
  commit subjects, and up to 5 of your earlier notes on its likely tasks. Also the tasks you logged to in the last 30
  days, with their projects and minutes. No window titles, other apps or meetings.
- **The review**: a digest of the day: Log the day's plan, your messages and Claude's replies (cut short), session
  titles, commit subjects, meeting titles (not those of meetings you declined, marked private, or holding a word of
  your personal windows), app names by hour, the notes of entries already logged, and your tasks of the
  last 30 days. Never window titles, sites or addresses.
- **A note**: that stretch's time and repository, your messages and Claude's replies, commit subjects and a few of your
  earlier notes.
- **A new task's draft**: that time's messages, Claude's replies and commits, the session titles, and up to 8 of your
  recent task titles.
- **Logging from a session**: what the session reads from Pulsar becomes part of that session, like anything else you
  show Claude Code: your entries' times, projects and tasks (not their notes), unlogged time, the session's own title
  and note drafts. Never window titles, meeting titles, or other sessions' titles or messages.

### What it costs

Your Claude usage, not money to Pulsar. Each run stops at its limit:

| Run | Haiku | Sonnet | Opus |
|---|---|---|---|
| Task picks, the review | $0.25 | $0.75 | $2 |
| A note, a new task's draft | $0.10 | $0.30 | $0.80 |

When Claude answers with no picks, Pulsar asks once more, so one round of picks may cost up to twice its limit; the cost
shown is what both runs cost together. Task picks with Sonnet have cost about $0.10 to $0.25 a day; a note with Sonnet
about 1.6 cents (with Haiku about a tenth of a cent), so a day's 15 notes about $0.25 of usage.

### What is kept

Claude's answers for each day stay on this Mac for 60 days, in `~/.cache/pulsar/claude-picks/`: the tasks it picked,
with its reasons and drafted notes (which come from your sessions' text), the notes drafted for each stretch of work,
and the day's last review. They are shown again after a restart, so the same question is not asked, or paid for, twice:

- **Task picks**: asking again keeps Claude's pick for every block that has not changed since (the same time,
  repository and commits) and asks only about the new or changed ones; with nothing changed, Claude does not run.
- **The review**: opening Log the day after a restart, or later in the day, shows the last review's suggestions for
  the entries still as they were (the same time, task, work and service); the line says how many were left out
  because their entry changed or was logged. **Check again** runs a new review of the whole day.
- **Notes**: a stretch of work drafted for once is never drafted again.

With the switch off, Pulsar does not use them.

## Security

- **Only you can use your Pulsar.** It listens on 127.0.0.1 only. Its data and actions need a session key that only
  your account can read (`~/.config/pulsar/secret`); the app signs its window in when it opens. Other websites, other
  accounts on the Mac and containers (Docker, DDEV) get nothing.
- **Programs you run yourself are another matter**, a Claude Code session included: they can read that key. With the
  key alone, Pulsar only answers Pulsar's tools in Claude Code (the same cut-down answers: never window titles, meetings
  or the notes of logged entries) and can be told to quit. Its one write is a previewed set of new entries, never over
  another one, shown as "from Claude Code". Reading the day as the page shows it, changing or deleting an entry,
  settings and updates need Pulsar's window. A program set on it could still get further (for example by signing a
  window in itself), so treat a Claude Code session in bypass mode as able to log time, and see "Claude Code:
  recommended settings" in [How it works](docs/HOW-IT-WORKS.md#3-security).
- **Signing in**: if the window says Pulsar needs to sign in again, click **Sign in**. To use Pulsar in a browser too
  while it runs, run `/usr/bin/python3 ~/Library/Application\ Support/Pulsar/current/pulsar.py --signin-url` (from a git
  checkout: `python3 pulsar.py --signin-url`) and open the address it prints within two minutes; it works once.

## If something goes wrong

1. Open **Settings → Updates → Error log** (or **Help → Show Error Log**, or the **!** at the top when it shows), choose
   how far back (24 hours, 7 or 30 days) and click **Download**. It saves a text file with the errors, your macOS and
   Pulsar versions, and no tokens.
2. Send it to the person who shared Pulsar with you, with what you did, what you expected and what you saw (a
   screenshot helps).

The error log keeps errors and warnings for 31 days in `~/.cache/pulsar/logs/`, with tokens removed. The **!** shows
while there are errors of the last 24 hours newer than your last **Mark as seen**. Productive not being connected is
shown on the page, not logged; a Claude Code token that could not be renewed is logged.

If Pulsar does not open, or says it could not start, click **Show log** and send `~/Library/Logs/Pulsar.log` instead (in
Finder: **Go → Go to Folder…**, paste `~/Library/Logs`).

## Where Pulsar keeps things

| What | Where |
|---|---|
| The app | `/Applications/Pulsar.app` |
| The code it runs, with its updates | `~/Library/Application Support/Pulsar/` |
| Settings, rules and the session key | `~/.config/pulsar/` |
| Copies of each day's entries (120 days), names, Likely's history, past days' Likely columns (as Keep past days says), the windows you worked in as hashed keys (62 days), Claude's answers (60 days), day marks, the self-check's numbers (62 days), the error log (31 days) | `~/.cache/pulsar/` |
| What the app wrote while starting | `~/Library/Logs/Pulsar.log` |
| Your Productive API token | the login keychain, item `pulsar` (open Keychain Access to see it) |
| Your Google Calendar access | the login keychain, item `pulsar`, account `google-calendar` |
| Your GitHub sign-in (for updates) | the login keychain, item `pulsar`, account `github`; who signed in in `~/.config/pulsar/github.json` |
| The window's own data (its sign-in, theme, size, This Mac choices, whether Planned is open and which bookings you have seen) | `~/Library/WebKit/com.fosterinteractive.pulsar/`, `~/Library/Caches/com.fosterinteractive.pulsar/`, `~/Library/Preferences/com.fosterinteractive.pulsar.plist` |

Everything in `~/.config/pulsar` and `~/.cache/pulsar` is readable by you alone. Pulsar Preview, if you have it, keeps the same things under its own
names: `/Applications/Pulsar Preview.app`, `~/.config/pulsar-preview`, `~/.cache/pulsar-preview`,
`~/Library/Logs/Pulsar-Preview.log`, the keychain service `pulsar-preview`, and `com.fosterinteractive.pulsar.preview`
in place of `com.fosterinteractive.pulsar`.

Outside these, Pulsar changes only what you ask it to: ActivityWatch's settings file when you click **Fix** (the old one
stays beside it as `aw-watcher-afk.toml.pulsar-<date and time>.bak`), ActivityWatch's Login Items when you click to add
it, and, with the Claude Code switch on, Claude Code's settings and its list of MCP servers. Nothing leaves your Mac
except what Pulsar asks Productive, Google Calendar (when you connect it) and GitHub (for updates), and, with Claude
Code switched on, what goes to Anthropic.

## Remove Pulsar

1. If **Use Claude Code with Pulsar** is on (**Settings → Claude**), switch it off and Save, in Pulsar and in Pulsar
   Preview: that takes each out of Claude Code, and the rules go with the last one. If Pulsar is already gone, run
   `claude mcp remove pulsar --scope user` (and `pulsar-preview` for Pulsar Preview), and remove from
   `~/.claude/settings.json` the `mcp__productive__…` and `…pulsar_log` ask rules and the `Read(…)` and `Edit(…)` deny
   rules for Pulsar's folders (`~/.config/pulsar…`, `~/.cache/pulsar…`, `~/Library/Application Support/Pulsar…`,
   `~/Library/WebKit/com.fosterinteractive.pulsar…`). Pulsar's copy of that file from before its first change,
   `~/.claude/settings.json.pulsar-backup`, stays until you delete it (when `settings.json` is a link to a file
   elsewhere, such as a dotfiles folder, the copy is beside that file).
2. Quit Pulsar (Cmd+Q), and Pulsar Preview if you have it.
3. Drag **Pulsar** (and **Pulsar Preview**) from Applications to the Trash. If it opens at login, remove it from
   **System Settings → General → Login Items** too.
4. To remove what it kept as well, paste this into Terminal:

   ```bash
   rm -rf ~/Library/Application\ Support/Pulsar ~/.config/pulsar ~/.cache/pulsar ~/Library/Logs/Pulsar.log \
     ~/Library/WebKit/com.fosterinteractive.pulsar ~/Library/Caches/com.fosterinteractive.pulsar \
     ~/.config/pulsar-preview ~/.cache/pulsar-preview ~/Library/Logs/Pulsar-Preview.log \
     ~/Library/WebKit/com.fosterinteractive.pulsar.preview ~/Library/Caches/com.fosterinteractive.pulsar.preview
   defaults delete com.fosterinteractive.pulsar
   defaults delete com.fosterinteractive.pulsar.preview
   for s in pulsar pulsar-preview; do for a in productive google-calendar github; do
     security delete-generic-password -s $s -a $a; done; done
   ```

   The last lines remove your Productive token and Google Calendar access from the keychain; each says the item could
   not be found if you never saved one. A copy of ActivityWatch's settings file that **Fix** made stays in
   `~/Library/Application Support/activitywatch/aw-watcher-afk/` until you delete it.
5. The token still works in Productive until you revoke it there: **Settings → API integrations**, delete the token you
   made for Pulsar. If you connected Google Calendar, also remove Pulsar at
   [myaccount.google.com/connections](https://myaccount.google.com/connections) (Disconnect in Settings does both).

Your time entries in Productive are not touched. Claude Code and ActivityWatch have their own uninstallers.

## For developers

Python standard library only, no build step. The rules for changes are in [CLAUDE.md](CLAUDE.md); how everything works,
piece by piece, in [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

To run it from a git checkout you need Python 3.9 or newer (on a Mac it comes with Apple's developer tools), an activity
tracker ([ActivityWatch](https://activitywatch.net), free for Mac, Windows and Linux, or MemTime), and a Productive
token or Claude Code connected to Productive:

```bash
git clone <this repository> ~/pulsar
python3 ~/pulsar/pulsar.py
```

It opens <http://127.0.0.1:5610/> in your browser. Keep the Terminal window open while you use it; Ctrl+C stops it.
`--port 5611` picks another port, `--no-open` skips the browser. A checkout does not update itself; it only says that a
newer version exists.

```
pulsar.py          start the server (--self-test, --signin-url, --no-open, --port; --mcp for Claude Code,
                   --instance preview for Pulsar Preview)
pulsar/
  config.py            settings, time zone, the tokens (keychain or private file), session key, sign-in codes
  trackers.py          ActivityWatch and MemTime readers
  awsetup.py           ActivityWatch's AFK timeout (read, and Fix on a click) and its Login Items
  trackhealth.py       whether the trackers record now (the page's notice when one stopped), and opening them
  productive.py        Productive: REST with a token, or Claude Code's MCP connection (renewed through
                       `claude mcp get`); billing; tasks; writing
  likely.py            the Likely column: sessions.py, or the fi-time-from-activity skill where it is installed
  sessions.py          Claude Code sessions -> which project each minute of work went to; task links typed in them
  learn.py             tasks for Likely rows, learned from what you logged in the same session or repository
  attribution.py       parallel work: which repository and session a logged minute teaches
  parallel.py          the Report's parallel work: agents in the background by project, and what no entry covers
  context.py           tasks for work outside Claude Code, learned from the window in front (hashed keys)
  commits.py           your commits during a Likely row, for the note (git run inertly)
  askclaude.py         task picks and notes from a session (claude -p, Productive read only), kept 60 days
  claudeuses.py        everything Pulsar asks Claude: what each sends, its model and its cost limit
  claudelink.py        the one Claude Code switch: Pulsar in Claude Code, its ask and deny rules
  mcp.py               Pulsar in Claude Code: the MCP server over stdio, a thin proxy to the running Pulsar
  mcptools.py          the Claude Code tools' answers, computed in Pulsar, and pulsar_preview / pulsar_log
  dayplan.py           Log the day: the whole day proposed, the journal of what each run wrote, Undo
  dayreview.py         Log the day with Claude: FI's rules and Claude's review of the plan
  selfcheck.py         Settings > Self-check: Log the day replayed blind on your last 30 days
  daymarks.py          day done, not work, not an example (this Mac only)
  pause.py             Pause and Resume: the paused time counts nowhere, then becomes not work
  ranges.py            what a stretch picked in the Day view holds, and its split into entries
  newtask.py           a new task from the log dialog: its draft and where it can go
  flags.py             ticket flags: closed, no budget, no estimate (read only)
  calendar.py          Google Calendar: sign-in (OAuth, PKCE), a day's meetings, their history
  github.py            GitHub sign-in (device flow) for updates from a private repository
  google_client.json   the Google sign-in client: placeholders in git, the real one only in releases
  changelog.py         CHANGELOG.md for Settings and release.sh
  names.py             each minor version's pulsar name
  report.py            a range of days in numbers (no I/O)
  server.py            HTTP routes, the JSON API and its guards; away time, repeats, a meeting's task
  updater.py           signed self-update from GitHub releases
  appupdate.py         a newer Pulsar.app on the Update click: checked, swapped in, put back if it does not start
  release_sig.py       the release keys and the signature checks: latest.json, and the code Pulsar.app installs
  logbook.py           the error log
web/
  index.html, style.css and one stylesheet per part (meetings, flow, range, newtask, logday, ask)
  js/theme-early.js    the saved theme before the first paint
  js/core.js           state, formatting, theme, toast
  js/native.js         Pulsar's window: menus, Dock badge, reminder, This Mac, app updates (nothing in a browser)
  js/datepick.js       the calendar behind every date field
  js/day.js            the day view
  js/meetings.js       the Meetings column and the Google Calendar part of Settings
  js/planned.js        the Planned column: Resource Planner bookings behind the icon above the time axis
  js/report.js         the report, its charts and downloads
  js/settings.js       the settings dialog and updates
  js/log.js            logging time and rules
  js/flow.js           Log and next, Start and End, links to Productive, the first-run checklist
  js/range.js          what a picked stretch holds, and its split
  js/newtask.js        Create a task in the log dialog
  js/logday.js         Log the day: review, confirm, write, Undo
  js/ldkeep.js         what Log the day keeps of your work on a day while the page is open
  js/ldreview.js       Log the day with Claude's suggestions
  js/catchup.js        Earlier days: catching up on several days
  js/ask.js            Ask Claude while it works
  js/pause.js          Pause and Resume, and the paused stretch on the day
  js/awayback.js       Ask when I come back: the panel after time away
  js/trackcheck.js     the notice when a tracker stopped recording
  js/selfcheck.js      Settings > Self-check
  js/logs.js           the error log dialog
  js/main.js           views and start-up
macos/                 the app: its window (Swift), its launcher, the icon and the release script (see macos/README.md)
tools/replay/          Log the day replayed on real days (needs PULSAR_REPLAY_DIR, a private folder outside git)
docs/HOW-IT-WORKS.md   how it all works
CHANGELOG.md           one line per change, per release
tests/                 PULSAR_TESTS_OFFLINE=1 python3 -m unittest discover -s tests -q
```

Tests run under `tests/isolation.py`: no keychain, network, Claude Code or real files, and the run fails if a test
reaches for one. `macos/ui_smoke.sh` clicks through the page in Pulsar's real window (`tests/ui/smoke.js`, writing
nothing): every feature on the page has a check there. `python3 pulsar.py --self-test` checks that the page files are there and the server answers; with ruff
installed, `ruff check pulsar pulsar.py tests` must pass. On a test server, `PULSAR_READ_ONLY=1` makes writing to
Productive impossible; use `"dry": true` on `/api/log`.

Chart colours are validated in light and dark themes: billable blue and not billable orange (a colour-blind safe pair),
billing Productive does not show you in grey, active time in a lighter grey behind, and a single-hue blue ramp for the
heatmaps.

## Release names

Pulsars are among the most precise clocks in the universe, hence the name. Each minor version is named after a real
one, shown next to the version in Settings and in the release notes:

| Version | Name | The pulsar |
|---|---|---|
| 1.0 | CP 1919 | The first pulsar found, by Jocelyn Bell Burnell in 1967; it pulses every 1.337 seconds |
| 1.1 | Vela | A young pulsar in the Vela supernova remnant, turning 11 times a second |
| 1.2 | Crab | Born in the supernova seen from Earth in 1054, turning 30 times a second |
| 1.3 | Geminga | A nearby pulsar that is silent in radio and bright in gamma rays |
| 1.4 | Hulse-Taylor | The binary pulsar whose shrinking orbit was the first evidence of gravitational waves |
| 1.5 | Black Widow | A millisecond pulsar that is slowly boiling away its companion star |
| 1.6 | Double Pulsar | The only known pair of pulsars orbiting each other |
| 1.7 | Terzan 5ad | The fastest-spinning pulsar known, 716 turns a second |
| 1.8 | Lich | The pulsar around which the first planets beyond the Solar System were found, in 1992 |
