<img src="macos/AppIcon-1024.png" width="112" align="right" alt="Pulsar's icon: a pulsar, a bright core with two beams, on a dark tile">

# Pulsar

Your working day as your computer saw it, next to what you logged in Productive. It shows when you worked, on
what, and which active time has no entry yet. It runs on your own computer, and window titles never leave it.
It writes to Productive only when you log time yourself: you pick the task and press "Log in Productive".

- **Day**: one time axis with three columns: what was on screen (Activity), what you most likely worked on
  (Likely, for Claude Code users), and your Productive entries (Logged). A yellow rail marks active time with no
  entry. On the side: work time against your daily target, and totals per project and budget.
- **Report**: any range up to 93 days: logged against active time by day, time by project, top apps and sites,
  projects by day, when in the week you work, and a table of every day.

## Install on a Mac (no Terminal)

Pulsar runs on macOS 12 Monterey or newer, Apple Silicon or Intel.

1. Install **ActivityWatch**, the free tracker Pulsar reads (skip this if you use MemTime): download it from
   [activitywatch.net](https://activitywatch.net/downloads/), drag it into Applications and open it. When macOS
   asks, allow it in **System Settings → Privacy & Security → Accessibility**; without that it sees no windows.
   Add it to your **Login Items** too, so it records every day. By default it counts you as away after 3 minutes
   without typing or moving the mouse; reading and calls then look like time away. To count 30 minutes instead, open
   `~/Library/Application Support/activitywatch/aw-watcher-afk/aw-watcher-afk.toml` (in Finder: **Go → Go to
   Folder…**), set `timeout = 1800` under `[aw-watcher-afk]`, and quit and reopen ActivityWatch.
2. Download **Pulsar.zip** from the link you were sent and open it. It unpacks into **Pulsar**, next to the zip.
3. Drag **Pulsar** into your **Applications** folder.
4. Open it the first time like this, because the app is not signed by Apple and macOS asks you to confirm it
   once: right-click **Pulsar** in Applications, choose **Open**, then **Open** again.
   On macOS 15 Sequoia or newer that message only offers **Done**. Click it, open **System Settings → Privacy &
   Security**, scroll down to the line about Pulsar, click **Open Anyway** and confirm.
   A company-managed Mac may block Pulsar completely, because it is not signed with a Developer ID. Then IT has
   to allow it, or Pulsar has to be signed and notarized by Apple first: tell the person who shared it.
5. If Pulsar says it needs Apple's developer tools, click **Install**. macOS asks for an administrator password
   the first time. Wait until the installation has finished (about 5 minutes, only once), then open Pulsar
   again.
6. Pulsar opens in its own window. Click **⚙ Settings → API token** and paste a token
   from Productive: **Settings → API integrations → Generate new token**, with **Read/Write** access (read-only lets Pulsar show your time but not log it). Click **Save**.

After that, open Pulsar like any other app. If it is already running, its window comes to the front. Downloads
ask where to save; **Print or save as PDF** in the Report opens the print dialog. Cmd+= and Cmd+- make the page
bigger or smaller. The window needs Safari 16.2 or newer (macOS 13 has it; on macOS 12, update Safari); on an
older Safari, Pulsar opens in your browser instead.

- **Open it at login**: **System Settings → General → Login Items** (Login Items & Extensions on newer macOS),
  click **+** under *Open at Login* and choose Pulsar. It then starts when you log in and opens its window.
- **Updates**: Pulsar updates itself; you do not need to download it again.
- **Stop it**: close the window or press Cmd+Q. Nothing keeps running in the background. (If Pulsar went to your
  browser because Safari is too old, it keeps running after you close the tab; logging out stops it.)
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
| Daily target | your organization's working day in Productive, else 8 h |
| Personal windows | Telegram, WhatsApp, YouTube, Netflix, Instagram, Facebook, TikTok, Reddit (one word per line; a window whose app, title or site contains one counts as personal) |
| ActivityWatch address | `http://localhost:5600` |
| Likely column | shown when Claude Code sessions and the `fi-time-from-activity` skill are on this computer |

Settings are saved in `~/.config/pulsar/settings.json`; names and copies of fetched days are cached in
`~/.cache/pulsar/`. Delete either folder to start again.

## Good to know

- **ActivityWatch counts input, MemTime the window in front.** Reading or a call with no typing reads as away in
  ActivityWatch after its AFK timeout (3 minutes by default; 30 is closer to how people work). The day view's
  Activity column can switch tracker; each Report day uses the one that saw more of it.
- **If Productive is unreachable**, the day view and the report show the last copy fetched for each day, and say so.
  A read that Productive drops without answering is tried once more; time you log is never sent twice.
- **Only you can use your Pulsar.** It listens on 127.0.0.1 only. Its data and actions need a session key that only
  your account can read (`~/.config/pulsar/secret`); the app signs its window in when it opens. Other
  websites, other accounts on the Mac and containers (Docker, DDEV) get nothing. If the window says it is not
  signed in, click **Sign in**. To use Pulsar in a browser too while it runs, run
  `/usr/bin/python3 ~/Library/Application\ Support/Pulsar/current/pulsar.py --signin-url` (from a git checkout:
  `python3 pulsar.py --signin-url`) and open the address it prints within two minutes; it works once.
- **Updates.** Pulsar asks GitHub every 6 hours whether a newer version exists (it sends nothing about you) and,
  unless you turn it off in Settings, installs it by itself. Only releases signed with the maintainer's release key
  are accepted. The repository must stay public: Pulsar downloads without signing in to GitHub, so from a private
  repository it gets nothing (and the Pulsar.zip link stops working too).
- **Logs.** Errors and warnings are kept for 31 days in `~/.cache/pulsar/logs/`, with tokens removed. **Logs**
  at the top shows them; its **Download** button saves a text file to send to whoever maintains Pulsar. Productive
  not being connected is shown on the page, not logged; a Claude Code token that could not be renewed is.
- **The warp.** An update and the refresh button bend the page like a gravitational lens while they load, then let
  go with a small flash. Turn it off in **Settings → Version and updates**; it is off anyway while macOS has
  **Reduce motion** on.
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

1. Click **Logs** at the top of Pulsar, choose how far back (24 hours, 7 or 30 days) and click **Download**. It
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
| The window's own data (its sign-in, theme, size) | `~/Library/WebKit/com.fosterinteractive.pulsar/`, `~/Library/Caches/com.fosterinteractive.pulsar/`, `~/Library/Preferences/com.fosterinteractive.pulsar.plist` |

Nothing is kept anywhere else, and nothing leaves your Mac except what Pulsar asks Productive and GitHub (for
updates).

## Remove Pulsar

1. Quit Pulsar (Cmd+Q).
2. Drag **Pulsar** from Applications to the Trash. If you added it to **Login Items**, remove it there too.
3. To remove what it kept as well, paste this into Terminal:

   ```bash
   rm -rf ~/Library/Application\ Support/Pulsar ~/.config/pulsar ~/.cache/pulsar ~/Library/Logs/Pulsar.log \
     ~/Library/WebKit/com.fosterinteractive.pulsar ~/Library/Caches/com.fosterinteractive.pulsar
   defaults delete com.fosterinteractive.pulsar
   security delete-generic-password -s pulsar -a productive
   ```

   The last line removes your Productive token from the keychain; it says the item could not be found if you never
   saved one. Your time entries in Productive are not touched. If you also want Claude Code or ActivityWatch gone,
   they have their own uninstallers.

## For developers

Python standard library only, no build step.

```
pulsar.py          start the server (--self-test, --signin-url, --no-open, --port)
pulsar/
  config.py            settings, time zone, the token (keychain or private file), session key, sign-in codes
  trackers.py          ActivityWatch and MemTime readers
  productive.py        Productive: REST with a token, or Claude Code's MCP connection (renewed through
                       `claude mcp get`); billing; tasks; writing
  likely.py            the Likely column (runs fi-time-from-activity's activity_blocks.py)
  report.py            a range of days in numbers (no I/O)
  server.py            HTTP routes, the JSON API and its guards
  updater.py           signed self-update from GitHub releases
  logbook.py           the error log
web/
  index.html, style.css
  js/theme-early.js    the saved theme before the first paint
  js/core.js           state, formatting, theme, toast
  js/day.js            the day view
  js/report.js         the report, its charts and downloads
  js/settings.js       the settings dialog and updates
  js/log.js            logging time and rules
  js/logs.js           the error log dialog
  js/main.js           views and start-up
macos/                 the app: its window (Swift), its launcher, the icon and the release script (see macos/README.md)
tests/                 python3 -m unittest discover -s tests

Test servers: PULSAR_READ_ONLY=1 makes writing to Productive impossible; use `"dry": true` on /api/log.
```

Chart colours follow a validated palette: one blue for logged time, a de-emphasis grey for active time, and a
single-hue blue ramp for the heatmaps, in both light and dark themes.
