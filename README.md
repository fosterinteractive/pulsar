<p align="center"><img src="macos/AppIcon-1024.png" width="128" alt="Pulsar's icon: a pulsar, a bright core with two beams, on a dark tile"></p>

<h1 align="center">Pulsar</h1>

Your working day as your computer saw it, next to what you logged in Productive. It shows when you worked, on
what, and which active time has no entry yet. It runs on your own computer, and window titles never leave it. Parts
of your Claude Code sessions go to Anthropic only if you switch on Claude Code in Settings (see below).
It writes to Productive only when you ask it to, and each time you confirm first: logging time, changing or deleting
an entry, Log the day and its Undo, creating a task, or a Claude Code session logging time for you. That is so in
normal use; a session with full access to the computer can get around any confirmation, as with any other app (see
**Only you can use your Pulsar** below).

- **Day**: one time axis with up to four columns: your meetings (Meetings, once Google Calendar is connected), what
  was on screen (Activity), what you most likely worked on (Likely, for Claude Code users), and your Productive
  entries (Logged). A yellow rail marks active time with no entry, a hatched rail time away from the computer, and a
  dashed card an entry you log at this time most days. On the side: work time against your daily target, totals per
  project and budget, and the day's tasks against their estimates.
- **Report**: today, this week (where it opens), last week, this or last month, 30 days, or any range up to 93 days: logged against active time by day, time by project, top apps and sites,
  projects by day, when in the week you work, and a table of every day.

## Install on a Mac

Pulsar runs on macOS 12 Monterey or newer, Apple Silicon or Intel.

1. Install **ActivityWatch**, the free tracker Pulsar reads (skip this if you use MemTime): download it from
   [activitywatch.net](https://activitywatch.net/downloads/), drag it into Applications and open it. When macOS
   asks, allow it in **System Settings → Privacy & Security → Accessibility**; without that it sees no windows.
   Once Pulsar runs, **⚙ Settings → Time and activity → Away** checks that ActivityWatch opens at login and counts a
   break as Pulsar does, and fixes either with a click.
2. Open **Terminal**, paste this line and press Return. It downloads Pulsar, checks its signature, puts it into
   Applications and opens it:

   ```bash
   d=$(/usr/bin/mktemp -d) && /usr/bin/curl -fsSL https://github.com/fosterinteractive/pulsar/releases/latest/download/Pulsar.zip -o "$d/Pulsar.zip" && /usr/bin/curl -fsSL https://github.com/fosterinteractive/pulsar/releases/latest/download/Pulsar.zip.sig -o "$d/Pulsar.zip.sig" && printf '%s\n' '-----BEGIN PUBLIC KEY-----' 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAECcw9Ny0xyPDVAsyM2aac+nwH5IZ5' 'EEdlXCwE6MMw3HpRi03aBa7effm9G1JudLuMLdFWxsAfKF43N/piDHieQA==' '-----END PUBLIC KEY-----' > "$d/key.pem" && /usr/bin/openssl dgst -sha256 -verify "$d/key.pem" -signature "$d/Pulsar.zip.sig" "$d/Pulsar.zip" && /usr/bin/ditto -x -k "$d/Pulsar.zip" "$d/new" && /bin/rm -rf /Applications/Pulsar.app && /bin/mv "$d/new/Pulsar.app" /Applications/ && /usr/bin/open /Applications/Pulsar.app; /bin/rm -rf "$d"
   ```

   It prints `Verified OK` when Pulsar.zip carries the signature of Pulsar's release key, whose public half is written
   in the line itself; anything else (a file swapped on GitHub, a download cut short) prints `Verification Failure`
   and nothing is installed. That check needs nothing but macOS's own `openssl`, and it holds even against someone
   who can publish a release on GitHub without the key; it trusts the line, so copy it from this page on GitHub or
   from the onboarding document, not from a message. It downloads into a private temporary folder, which it removes,
   so no other account on the Mac can swap the file between the download and the unpacking, and it calls every
   program by its full path, so an older copy earlier in your PATH (MAMP has one) is not used. It replaces an
   older Pulsar.app whole rather than unpacking over it, so no file of the old app is left inside the new one (its
   code check would refuse it); your settings, token and installed versions live elsewhere and stay. Pulsar is not signed by
   Apple. A file a browser downloads is marked as from the internet, and macOS then refuses to open an unsigned app
   until you confirm it; a file `curl` downloads is not marked, so Pulsar opens at once. Once installed, the app also
   installs the code it carries only when the release key signed it, and its own updates are signed too. A
   company-managed Mac may still block it: then IT has to allow it.
3. If Pulsar says it needs Apple's developer tools, click **Install**. macOS asks for an administrator password
   the first time. Wait until the installation has finished (about 5 minutes, only once), then open Pulsar
   again.
4. Pulsar opens in its own window. If you use Claude Code with its Productive connection, Pulsar uses that and there
   is nothing to set up. Otherwise click **⚙ Settings → API token** and paste a token from Productive: **Settings →
   API integrations → Generate new token**, with **Read/Write** access (read-only lets Pulsar show your time but not
   log it). Click **Save**.

**Without Terminal:** download **Pulsar.zip** from the link you were sent, open it and drag **Pulsar** into
Applications (nothing checks Pulsar.zip itself this way; the app still installs only the code the release key
signed). The first time, macOS says it cannot verify Pulsar: click **Done**, open **System Settings → Privacy
& Security**, scroll down to the line about Pulsar, click **Open Anyway** and confirm (on macOS 14 and older,
right-click Pulsar, **Open**, then **Open** again). Only once.

After that, open Pulsar like any other app. If it is already running, its window comes to the front. Downloads
ask where to save; **File → Print** (Cmd+P), or **Print or save as PDF** in the Report, opens the print dialog. Cmd+= and Cmd+- make the page
bigger or smaller. Keys: **L** Log the day, **N** + Log, **←** **→** another day, **T** today, **⌘↩** logs in the log
dialog, **?** lists them all; Tab reaches every button. The window needs Safari 16.2 or newer (macOS 13 has it; on macOS 12, update Safari); on an
older Safari, Pulsar opens in your browser instead.

- **Menus and keys**: every command is in the menu bar with its shortcut, and **Help** searches them. New Entry
  Cmd+N, Log the Day Cmd+L, Day Cmd+1, Report Cmd+2, Today Cmd+T, previous and next day Cmd+Left and Cmd+Right,
  Refresh Cmd+R. Cmd+W closes an open dialog or popover first, then the window. Right-click an entry, an activity
  block or a gap for what can be done with it. A pinch on the trackpad changes the rows (1 minute to 1 hour).
- **Closing the window** keeps Pulsar running in the Dock: its icon shows today's active time with no entry ("3h"),
  and at 17:30 on working days a notification says how much is not logged, with **Log the Day**. Click the Dock icon
  to bring the window back. (Where macOS does not let Pulsar notify, the Dock icon bounces once instead.)
- **Open it at login**: **System Settings → General → Login Items** (Login Items & Extensions on newer macOS),
  click **+** under *Open at Login* and choose Pulsar. It then starts when you log in and opens its window, without
  taking the keyboard from what you are doing.
- **Updates**: when a new version is out, **Settings → Update** installs it in a few seconds; you never download
  Pulsar again. It installs nothing by itself. Now and then a release needs a newer Pulsar app (its window and
  menus): **Settings → Updates** then offers **Reinstall Pulsar**, which downloads it, checks it like an update and
  puts it in Applications; Pulsar quits and opens again (an app older than 1.6 asks you to press Cmd+Q). If Pulsar
  cannot change the app in Applications (another account installed it), it shows the install line above instead.
- **What changed**: [CHANGELOG.md](CHANGELOG.md), also under **Settings → Updates → Changelog**.
- **Stop it**: press Cmd+Q. Nothing keeps running in the background then. If something would be lost (a time
  being logged, changes in Log the day), Pulsar asks first. (If Pulsar went to your browser because Safari is too
  old, it keeps running after you close the tab; logging out stops it.)
- **Reinstalling by hand**: quit Pulsar first (Cmd+Q), then run the install line again. A Pulsar left running keeps
  its old window until it is quit.
- **If it says it could not start**: click **Show log** and send what it shows to the person who shared
  Pulsar with you. The log is `~/Library/Logs/Pulsar.log`.

## What you need

- **Python 3.9 or newer.** On a Mac it comes with Apple's developer tools; Pulsar.app offers to install them
  the first time if they are missing.
- **An activity tracker**: [ActivityWatch](https://activitywatch.net) (free; Mac, Windows, Linux) or MemTime.
  Pulsar reads whichever has more of each day. Without a tracker, only the Logged column shows.
- **A Productive API token** (or Claude Code connected to Productive; see below).

## Start it

```bash
git clone <this repository> ~/pulsar
python3 ~/pulsar/pulsar.py
```

It opens <http://127.0.0.1:5610/> in your browser. Keep the Terminal window open while you use it; Ctrl+C
stops it. `--port 5611` picks another port, `--no-open` skips opening the browser.

## Connect Productive

1. In Productive: **Settings → API integrations → Generate new token**, with **Read/Write** access. Read-only works too, but then Pulsar can show your time and not log it.
2. In Pulsar: **⚙ Settings → API token**, paste it, **Save**. Pulsar checks the connection and says
   who it is connected as.

The token is kept on your computer only: in the login keychain on a Mac, elsewhere in
`~/.config/pulsar/token`, readable by you alone. It sees what you see in Productive, no more.

If Pulsar cannot find your organization or person by itself, Settings asks for them:

- **Organization ID**: the number at the start of your Productive address, `app.productive.io/12345-company/…`.
- **Your person ID**: the number in the address of your own profile.

**Claude Code users** need no token: Pulsar uses the Productive connection Claude Code already has (Mac
only). To do that it reads Claude Code's keychain item and uses only the Productive access token in it; nothing
else in that item is used or stored. That token lasts an hour. When it has expired, Pulsar runs
`claude mcp get productive`, Claude Code's own check of the connection (about 2 seconds, no AI model involved),
during which Claude Code renews the token and keeps the new one; then Pulsar reads it again. Pulsar never uses
the renewal key itself, so Claude Code's connection keeps working. This needs the `claude` command, or the
Claude Code extension in VS Code or Cursor. If Claude Code cannot renew it, Pulsar says so: run `/mcp` in Claude
Code and reconnect Productive. A token of your own never expires, so it is the steadier choice. Settings →
*Connect with* chooses between the two.

## Settings

| Setting | Default |
|---|---|
| Time zone | this computer's |
| Daily target | your organization's working day in Productive, else 8 h (Pulsar reads it through Claude Code's connection; with an API token it is 8 h unless you set it) |
| Round entries to | 5 minutes (or 10 or 15): Log the day's entries start and end on this step (or right against an entry already logged), a block you log is rounded to it, and active time shorter than it is not marked as unlogged |
| Away after | 15 minutes (or 10, 20 or 30) without typing or moving the mouse: time away, and the end of a sitting (Likely, Log the day); a shorter pause between two stretches of work counts as work. With ActivityWatch, its AFK timeout and Login Items are checked here too |
| Personal windows | Telegram, WhatsApp, YouTube, Netflix, Instagram, Facebook, TikTok, Reddit (one word per line; a window whose app, title or site contains one counts as personal) |
| ActivityWatch address | `http://localhost:5600` |
| Likely column | shown when Claude Code sessions are on this computer (`~/.claude/projects`) |
| Claude Code | off; one switch turns on Check my day with Claude (task picks, then a review of the day), drafted notes and tasks, and logging from a session (needs Claude Code on this Mac) |
| Claude's models | Task picks: Sonnet; the review, the note and a new task's draft: Haiku. **Settings → Claude** lists everything Pulsar asks Claude, what each sends, and a model (Haiku, Sonnet or Opus) to pick for each |
| Google Calendar | not connected; **Connect** signs in with Google (fosterinteractive.com accounts only) |

Settings are saved in `~/.config/pulsar/settings.json`; names, copies of fetched days and what Likely learned are
cached in `~/.cache/pulsar/`. Delete either folder to start again.

## Good to know

- **ActivityWatch counts input, MemTime the window in front.** Reading or a call with no typing is time away once it
  lasts **Away after** (Settings → Time and activity, 15 minutes unless you choose 10, 20 or 30); a shorter pause
  between two stretches of work counts as work. ActivityWatch writes a pause only after its own AFK timeout (3
  minutes unless changed), so a timeout longer than Away after hides the shorter breaks: Settings says so and **Fix**
  sets it to Away after (a copy of its file is kept, and the change applies to new data only). The day view's
  Activity column can switch tracker; each Report day uses the one that saw more of it.
- **If Productive is unreachable**, the day view and the report show the last copy fetched for each day, and say so.
  A read that Productive drops without answering is tried up to twice more; time you log is never sent twice.
- **Only you can use your Pulsar.** It listens on 127.0.0.1 only. Its data and actions need a session key that only
  your account can read (`~/.config/pulsar/secret`); the app signs its window in when it opens. Other
  websites, other accounts on the Mac and containers (Docker, DDEV) get nothing. Programs you run yourself are
  another matter, a Claude Code session included: they can read that key. With the key alone Pulsar only answers
  Pulsar in Claude Code's tools (the same cut-down answers, never window titles, meetings or the notes of logged
  entries) and quits; its one
  write is a previewed new entry, never over another one, shown as "from Claude Code" in the Logged column. Reading
  the day as the page shows it, changing or deleting an entry, settings and updates need Pulsar's window. A program
  set on it could still get further (for example by signing a window in itself), so treat a session in bypass mode
  as able to log time, and see "Claude Code: recommended settings" in [How it works](docs/HOW-IT-WORKS.md#3-security).
  If the window says it is not
  signed in, click **Sign in**. To use Pulsar in a browser too while it runs, run
  `/usr/bin/python3 ~/Library/Application\ Support/Pulsar/current/pulsar.py --signin-url` (from a git checkout:
  `python3 pulsar.py --signin-url`) and open the address it prints within two minutes; it works once.
- **Updates.** Pulsar asks GitHub every 6 hours whether a newer version exists (it sends nothing about you) and
  shows it next to the version at the top and in Settings. It installs a new version only when you click **Update**:
  then it downloads it, checks its signature and runs its self-test before restarting into it. If you are two or more
  releases behind, it reminds you once every two days; **Skip this release** stops the reminder until a newer one is
  out. Only releases signed with the maintainer's release key are accepted. The repository must stay public: Pulsar downloads without signing in to GitHub, so from a private
  repository it gets nothing (and the Pulsar.zip link stops working too).
- **Error log.** Errors and warnings are kept for 31 days in `~/.cache/pulsar/logs/`, with tokens removed.
  **Settings → Updates → Error log** shows them (while there are errors of the last 24 hours, a small **!** at the top
  opens it too); its **Download** button saves a text file to send to whoever maintains Pulsar. Productive
  not being connected is shown on the page, not logged; a Claude Code token that could not be renewed is.
- **The warp.** An update draws the page in towards a black hole (a bright one on the light theme) with a ring of
  light around it while it loads, then lets go with a small flash. In the
  Pulsar window the page really bends around it: the window draws it with Core Image on the graphics card, so it stays
  smooth on any Mac (a Pulsar.app downloaded before 1.0.12 shows the version without the bend; download Pulsar.zip
  again to get it). In a browser it is the version without the bend. Turn it off in **Settings → Updates**; it is off anyway while macOS has
  **Reduce motion** on.
- **Likely, for Claude Code users.** Pulsar reads your Claude Code sessions (`~/.claude/projects`, on this Mac
  only) and shows, minute by minute, which session you were most likely working in: the one whose title your editor
  showed, else one that was running, else the one you used last in that sitting. Each block is named after the git
  repository the session's files are in. Meetings and your personal windows go to no session. Nothing is sent
  anywhere (unless you switch on Claude Code, below), and it never logs anything by itself. **Log this time** on a block offers first: a Productive task you
  pasted into that session in the three hours before; the tasks you logged while in the same Claude session, then in
  the same repository, over the last 60 days (worked out once in the background; only times, repository names and a
  hash of each session title are kept, in `~/.cache/pulsar/likely-days/`).
- **Claude Code (one switch, off by default).** Switch on **Use Claude Code with Pulsar** in **Settings → Claude**
  and Save. That is all; it turns on:
  - **Check my day with Claude**, in Log the day, the one button that runs Claude there: your own Claude Code first
    suggests a task and a note for each block Pulsar is unsure of (Ask Claude, when the day has no picks yet), then
    reviews the whole day with FI's rules; you accept or reject each suggestion. Ask Claude may search Productive
    (read only; tools that change anything are refused). Its tooltip names the models and the most each run may
    cost; pick the models in **Settings → Claude**: Sonnet (default) for the picks, Haiku for the review. It takes
    15 to 60 seconds each. Nothing runs by itself.
  - **Notes from the session** in the log dialog, and in a row of Log the day when you click in its empty note: Haiku
    (or the model you pick) drafts the note from that session and your commits in a few seconds; it never replaces what
    you typed.
  - **Logging from a session:** Pulsar adds itself to Claude Code, so in any new session you can ask "what did I not
    log today?" or "log the time of this session". The session reads your day from Pulsar and shows the entries;
    Claude Code asks you before each write, showing Pulsar's own summary of it. One write may hold up to 40
    entries, so one yes can log a whole day: read the summary. Pulsar must be open.
  - **Claude Code asks first** before Pulsar's log tool, and its file tools are kept out of Pulsar's folders: its
    settings (key, sign-in code, settings), the installed code, the window's own data and the cache (an ask rule and
    deny rules in `~/.claude/settings.json`; a backup of that file is kept beside it). The deny rules stop
    Claude Code's file tools and the file commands it recognizes in a shell; a script is stopped only with Claude
    Code's sandbox on.
  - **Ask before any Claude Code session changes Productive** (a separate box, off by default): ask rules for the
    Productive server named `productive`, which then apply to every Claude Code session on the Mac. A Productive
    connection under another name, such as a claude.ai connector, is not covered.

  **What goes to Anthropic.** These run your own Claude Code, so what they send goes to Anthropic under your Claude
  plan or account and its terms:
  - Ask Claude, for each block of 10 minutes or more: its time, the repository's name, up to 3 session titles, up
    to 15 of your messages (400 characters each) with Claude's reply at the end of each step (900 characters), up to
    10 commit subjects, and up to 5 of your earlier notes on its likely tasks. Also the tasks you logged to in the
    last 30 days, with their projects and minutes. Window titles, other apps and meetings are not sent.
  - A note: the same for that one block. With the switch on the log dialog asks for one by itself for any stretch
    you pick in the Day view (a drag, a rail, **+ Log**, **Log and next**, an Activity block), from the session that
    holds most of it.
  - A new task's draft: opening **Create a task** asks Haiku (or the model you pick) at once for a title and a
    description, from that time's messages, Claude's replies and your commits, the session titles, and up to 8 of
    your recent task titles. Nothing is created until you click **Create in Productive**.
  - Logging from a session: what the session reads from Pulsar becomes part of that session, like anything else you
    show Claude Code: your entries' times, projects and tasks (not their notes), unlogged time, the session's own
    title and note drafts. Never window titles, other sessions or meeting titles.

  Ask Claude runs with none of your Claude Code settings, CLAUDE.md files or auto memory.

  **What it costs.** Your Claude usage, not money to Pulsar. Each Ask Claude run may cost at most $0.25 (Haiku),
  $0.75 (Sonnet) or $2 (Opus). When Claude answers with no picks, Pulsar asks once more, so one Ask Claude click may
  cost up to twice that; the cost shown is what both runs cost together. Claude runs only when you click **Check my day with Claude**. A day with Sonnet has cost about $0.10 to $0.25. A note may cost at most $0.10 with Haiku
  ($0.30 with Sonnet, $0.80 with Opus) and usually costs under half a cent. Asking again after Pulsar restarted, or
  after the day's blocks changed, asks Claude again and costs again.

  **What is kept.** Ask Claude's last answer for each day (the tasks it picked, its reasons and its drafted notes,
  which come from your sessions' text), the notes it drafted for Log the day's rows and the day's last review are kept
  on this Mac for 60 days, in `~/.cache/pulsar/claude-picks/`: the day shows them again after a restart, and the same
  question is not asked (or paid for) twice. With the switch off, neither the page nor Log the day uses them.

  Switching it off takes back everything it added, and nothing else.
- **The daily meeting, ready to log.** An entry you log at the same time on most days (a daily scrum) is noticed from
  your last four weeks in Productive. When the tracker sees a call near that time (Zoom, Meet, Teams, a Slack huddle),
  the Logged column shows it as a dashed card with the task, the usual note and the call's length. One click opens it
  in the log dialog; nothing is logged until you click **Log in Productive**.
- **No task for the work yet.** The log dialog can create one, drafted from the work, on a click, and pick it. If
  Productive gives no clear answer, the task may exist: Pulsar will not create the same title in that project again
  for ten minutes, so search for it first.
- **Time that runs into an entry already logged.** The log dialog shows the free time around it and offers to log
  that instead (**Log 2 entries around it**); **Log anyway** is still there.
- **Google Calendar.** In **Settings → Google Calendar**, **Connect** opens Google's sign-in; after **Allow**, a
  Meetings column shows your meetings next to the rest of the day (↻ reads them again at once). Pulsar can only read
  your events, never change them. **Log this time** on a meeting fills in its title as the note and offers first the
  task you logged during its earlier ones, then your tasks whose names share a word with its title.
  **Disconnect** removes the access, at Google too (in Pulsar Preview only on this Mac, because Google would take
  Pulsar's away with it). Only fosterinteractive.com accounts can sign in.
- **Change what you logged.** Click an entry in Logged: **Edit** opens it in the log dialog, **Delete** asks first
  (Productive keeps deleted entries in its Recycle Bin). Drag an entry's top or bottom edge to make it start or end
  earlier or later; Pulsar asks before it changes anything. After a delete or a drag, **Undo** in the note at the
  bottom puts it back. Pulsar does not refuse an approved entry, because a
  budget without an approval step approves every entry at once and you can still change it. Where an approver
  approved it, Productive refuses the change and Pulsar shows Productive's reason.
- **Log the day.** **Log the day** in the Logged column proposes the whole day at once, from your activity, Claude
  sessions, meetings, daily repeats, rules and what you logged before, and lists it in the side panel for review:
  each entry with its task, service, how sure Pulsar is and why. Unsure entries start unticked; you can change the
  task, service, start, length and note, or remove an entry. **Tick all with a task** ticks every entry that has a
  task (one whose task Claude found by searching Productive waits until you open it), and Enter on an open entry
  ticks it and opens the next. Nothing is written until you confirm; then the entries
  go to Productive one at a time, stopping at the first one that is refused, and **Undo this run** deletes only what
  that run wrote. Notes come only from a meeting's title, your usual note for a repeat, your commits, or Ask
  Claude's draft. A meeting's title is a note only when you or someone of your own company (your Google account's
  domain) organized it and it reads like a meeting's name (not an instruction, a sum or an address); a series renamed
  since you last logged it starts unticked.
- **Day done, and time that was not work.** A day whose logged time reaches your daily target is done (or click
  **Mark the day done** under Work time): its yellow rails turn grey and it leaves Earlier days; **Not done** undoes
  it. **Not work** in the log dialog or on a Log the day row says a stretch was not work: it is no longer marked or
  proposed (click its grey hatched rail to undo). Both stay on this Mac and are never sent to Productive.
- **Catching up.** **Earlier days…** in Log the day lists your last workdays with the active time no entry covers.
  Tick the days, and each one opens in turn as Log the day; each is written only when you confirm it.
- **Ticket flags.** The log dialog and Log the day say when a task is closed, when no budget takes time on that
  date, or when it has no estimate while the project's other tasks have one, with a link to fix it in Productive.
  Pulsar only reads these; it never changes the task.
- **Report downloads.** In the Report, **Download** gives the days or the time entries as CSV (for Excel or
  Numbers), or a print view that saves as PDF.

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

## If something goes wrong

1. Open **Settings → Updates → Error log** (or the **!** at the top, when it shows), choose how far back (24 hours, 7 or 30 days) and click **Download**. It
   saves a text file with the errors, your macOS and Pulsar versions, and no tokens.
2. Send that file to the person who shared Pulsar with you, with what you did, what you expected and what you saw
   (a screenshot helps).

If Pulsar does not open at all, send `~/Library/Logs/Pulsar.log` instead: in Finder, **Go → Go to Folder…**, paste
`~/Library/Logs` and look for Pulsar.log.

## Where Pulsar keeps things

| What | Where |
|---|---|
| The app | `/Applications/Pulsar.app` |
| The code it runs, with its updates | `~/Library/Application Support/Pulsar/` |
| Settings, rules, the sign-in key and sessions | `~/.config/pulsar/` |
| Copies of fetched days, names, the error log | `~/.cache/pulsar/` |
| What the app wrote while starting | `~/Library/Logs/Pulsar.log` |
| Your Productive API token | the login keychain, item `pulsar` (open Keychain Access to see it) |
| Your Google Calendar access | the login keychain, item `pulsar`, account `google-calendar` |
| The window's own data (its sign-in, theme, size) | `~/Library/WebKit/com.fosterinteractive.pulsar/`, `~/Library/Caches/com.fosterinteractive.pulsar/`, `~/Library/Preferences/com.fosterinteractive.pulsar.plist` |

Pulsar Preview, if you have it, keeps the same things under its own names: `/Applications/Pulsar Preview.app`,
`~/.config/pulsar-preview`, `~/.cache/pulsar-preview`, `~/Library/Logs/Pulsar-Preview.log`, the keychain service
`pulsar-preview`, and `com.fosterinteractive.pulsar.preview` in place of `com.fosterinteractive.pulsar`.

Nothing is kept anywhere else, and nothing leaves your Mac except what Pulsar asks Productive, Google Calendar (when
you connect it) and GitHub (for updates), and, with Claude Code switched on, what goes to Anthropic (see **Claude
Code** above).

## Remove Pulsar

1. If **Use Claude Code with Pulsar** is on (**Settings → Claude**), switch it off and Save, in Pulsar and in Pulsar
   Preview: that takes each out of Claude Code, and its rules go with the last one. If Pulsar is already gone,
   run `claude mcp remove pulsar --scope user` (and `pulsar-preview` for Pulsar Preview) and remove the
   `mcp__productive__…` and `…pulsar_log` ask rules and the `Read(~/.config/pulsar…)` and `Edit(~/.config/pulsar…)`
   deny rules from `~/.claude/settings.json` yourself. Pulsar's copy of that
   file from before its first change, `~/.claude/settings.json.pulsar-backup`, stays until you delete it (when
   `settings.json` is a link to a file elsewhere, such as a dotfiles folder, the copy is beside that file).
2. Quit Pulsar (Cmd+Q), and Pulsar Preview if you have it.
3. Drag **Pulsar** (and **Pulsar Preview**) from Applications to the Trash. If you added it to **Login Items**,
   remove it there too.
4. To remove what it kept as well, paste this into Terminal:

   ```bash
   rm -rf ~/Library/Application\ Support/Pulsar ~/.config/pulsar ~/.cache/pulsar ~/Library/Logs/Pulsar.log \
     ~/Library/WebKit/com.fosterinteractive.pulsar ~/Library/Caches/com.fosterinteractive.pulsar \
     ~/.config/pulsar-preview ~/.cache/pulsar-preview ~/Library/Logs/Pulsar-Preview.log \
     ~/Library/WebKit/com.fosterinteractive.pulsar.preview ~/Library/Caches/com.fosterinteractive.pulsar.preview
   defaults delete com.fosterinteractive.pulsar
   defaults delete com.fosterinteractive.pulsar.preview
   for s in pulsar pulsar-preview; do for a in productive google-calendar; do
     security delete-generic-password -s $s -a $a; done; done
   ```

   The last lines remove your Productive token and Google Calendar access from the keychain; each says the item
   could not be found if you never saved one (the same for anything of Pulsar Preview you never had).
5. The token still works in Productive until you revoke it there: **Settings → API integrations**, delete the token
   you made for Pulsar. If you connected Google Calendar, also remove Pulsar at
   [myaccount.google.com/connections](https://myaccount.google.com/connections) (Disconnect in Settings does both).

Your time entries in Productive are not touched. If you also want Claude Code or ActivityWatch gone, they have their
own uninstallers.

## For developers

Python standard library only, no build step. How everything works, piece by piece: [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

```
pulsar.py          start the server (--self-test, --signin-url, --no-open, --port; --mcp for Claude Code,
                   --instance preview for Pulsar Preview)
pulsar/
  config.py            settings, time zone, the tokens (keychain or private file), session key, sign-in codes
  trackers.py          ActivityWatch and MemTime readers
  awsetup.py           ActivityWatch's AFK timeout (read, and Fix on a click) and its Login Items
  productive.py        Productive: REST with a token, or Claude Code's MCP connection (renewed through
                       `claude mcp get`); billing; tasks; writing
  likely.py            the Likely column: sessions.py, or the fi-time-from-activity skill where it is installed
  sessions.py          Claude Code sessions -> which project each minute of work went to; task links typed in them
  learn.py             tasks for Likely rows, learned from what you logged in the same session or repository
  commits.py           your commits during a Likely row, for the note (git run inertly)
  askclaude.py         Ask Claude and notes from a session (claude -p, Productive read only)
  claudeuses.py        everything Pulsar asks Claude: what each sends, its model and its cost limit
  claudelink.py        the one Claude Code switch: Pulsar in Claude Code, its ask and deny rules
  mcp.py               Pulsar in Claude Code: the MCP server over stdio, a thin proxy to the running Pulsar
  mcptools.py          the Claude Code tools' answers, computed in Pulsar, and pulsar_preview / pulsar_log
  dayplan.py           Log the day: the whole day proposed, the journal of what each run wrote, Undo
  dayreview.py         Log the day with Claude: FI's rules and Claude's review of the plan
  ranges.py            what a stretch picked in the Day view holds, and its split into entries
  newtask.py           a new task from the log dialog: its draft and where it can go
  flags.py             ticket flags: closed, no budget, no estimate (read only)
  calendar.py          Google Calendar: sign-in (OAuth, PKCE), a day's meetings, their history
  google_client.json   the Google sign-in client: placeholders in git, the real one only in releases
  changelog.py         CHANGELOG.md for Settings and release.sh
  names.py             each minor version's pulsar name
  report.py            a range of days in numbers (no I/O)
  server.py            HTTP routes, the JSON API and its guards; away time, repeats, a meeting's task
  updater.py           signed self-update from GitHub releases
  release_sig.py       the release keys and the signature checks: latest.json, and the code Pulsar.app installs
  logbook.py           the error log
web/
  index.html, style.css and one stylesheet per part (meetings, flow, range, newtask, logday, ask)
  js/theme-early.js    the saved theme before the first paint
  js/core.js           state, formatting, theme, toast
  js/datepick.js       the calendar behind every date field
  js/day.js            the day view
  js/meetings.js       the Meetings column and the Google Calendar part of Settings
  js/report.js         the report, its charts and downloads
  js/settings.js       the settings dialog and updates
  js/log.js            logging time and rules
  js/flow.js           Log and next, Start and End, links to Productive, the first-run checklist
  js/range.js          what a picked stretch holds, and its split
  js/newtask.js        Create a task in the log dialog
  js/logday.js         Log the day: review, confirm, write, Undo
  js/ldreview.js       Log the day with Claude's suggestions
  js/catchup.js        Earlier days: catching up on several days
  js/ask.js            Ask Claude while it works
  js/logs.js           the error log dialog
  js/main.js           views and start-up
macos/                 the app: its window (Swift), its launcher, the icon and the release script (see macos/README.md)
docs/HOW-IT-WORKS.md   how it all works
CHANGELOG.md           one line per change, per release
tests/                 python3 -m unittest discover -s tests

Tests run under tests/isolation.py: no keychain, network, Claude Code or real files, and the run fails if a
test reaches for one. Test servers: PULSAR_READ_ONLY=1 makes writing to Productive impossible; use `"dry": true` on
/api/log.
```

Chart colours follow a validated palette: one blue for logged time, a de-emphasis grey for active time, and a
single-hue blue ramp for the heatmaps, in both light and dark themes.
