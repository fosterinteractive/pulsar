# How Pulsar works

This is the inside view: what runs where, where each number on the page comes from, what is kept and where, and how
a release reaches everyone. For installing and using Pulsar, see the [README](../README.md). For building and
releasing, see [macos/README.md](../macos/README.md).

Pulsar is a small local web app. A Python 3.9 server (standard library only) reads your activity tracker, your
Claude Code sessions, Productive and, if you connect it, Google Calendar. It serves one page, which draws the day and
the report. On a Mac a thin app shows that page in a window of its own. Nothing runs anywhere but on your computer,
except what the Claude Code switch sends to Anthropic when it is on (5.4c, 5.5, 5.7). In normal use nothing is written
to Productive without a click and a confirmation: logging, changing or deleting an entry, creating a task, Log the day
and its Undo, or `pulsar_log` from a Claude Code session after Claude Code asked you. Pulsar writes only after you
confirm, in normal use; a session with full access to the computer can get around any confirmation, as with any
other app (section 3, "What a program of the person's can still do").

## 1. The pieces

```mermaid
flowchart LR
  subgraph Mac["Your Mac"]
    App["Pulsar.app<br/>window (Swift, WKWebView)"] -->|runs| L["launcher.sh"]
    L -->|starts, stops| S["Python server<br/>127.0.0.1:5610"]
    App -->|loads the page| S
    S --> AW["ActivityWatch<br/>localhost:5600"]
    S --> MT["MemTime database<br/>(a private copy)"]
    S --> CC["Claude Code sessions<br/>~/.claude/projects"]
    S --> KC["Keychain<br/>tokens"]
    S --> FS["~/.config/pulsar<br/>~/.cache/pulsar"]
  end
  S -->|REST or MCP| P["Productive"]
  S -->|OAuth, events| G["Google Calendar"]
  S -->|latest.json, zip| GH["GitHub releases"]
```

| Piece | File | What it does |
|---|---|---|
| Window | `macos/PulsarWindow.swift` | The app's executable. Runs the launcher (its dialogs as sheets), loads the page, and gives it a Mac app's menus, Cmd+W and Cmd+Q, right-click menus, a title bar in its colours, the Dock badge, the end-of-day reminder, Open at Login, downloads, print, About and the native warp (with `web/js/native.js`). |
| Launcher | `macos/launcher.sh` | Installs or upgrades the code under `~/Library/Application Support/Pulsar/`, starts the server, rolls back if it does not start, stops it when the app quits. |
| Server | `pulsar.py`, `pulsar/server.py` | The page, its files and a JSON API, on 127.0.0.1 only. |
| Page | `web/` | Plain HTML, CSS and JavaScript, no build step. |
| Release tools | `macos/build_app.sh`, `macos/release.sh` | Build Pulsar.app, build and sign a release. |

## 2. Starting up

```mermaid
sequenceDiagram
  participant W as Pulsar.app
  participant L as launcher.sh
  participant S as Server
  participant P as Page
  W->>L: launcher.sh --window
  L->>L: install or upgrade versions/, pick current
  L->>S: /usr/bin/python3 -E -s -B pulsar.py (in the background)
  L->>S: GET /api/whoami?nonce= (is it really this user's server?)
  L->>L: write a one-time sign-in code
  L-->>W: PULSAR_URL http://127.0.0.1:5610/?code=…
  W->>P: load that address
  P->>S: POST /api/session {code}
  S-->>P: session token (kept in localStorage)
  P->>S: every request with X-Pulsar-Session
```

1. **The installed copy.** The app carries one version of the code. The launcher keeps full copies in
   `versions/<X.Y.Z>/` with two symlinks, `current` and `previous`, switched atomically. When the app's own version
   is newer than `current` (a new Pulsar.zip was installed), it is copied in and becomes `current`. When `current` is
   newer (Pulsar updated itself since the app was built), `current` stays.
2. **Only signed code is installed.** Next to the code, the app carries `app.manifest.json`: its version and the
   SHA-256 of each of its files, signed with the release key (`.sig`, made by `release.sh`). The launcher copies the
   code to a temporary folder, checks that copy against the manifest with `release_sig.py` (the updater's own check
   and keys, copied next to the launcher, outside the code it checks): a signature by one of `PUBLIC_KEYS`, the same
   version, every file the same, none missing, none added, no symbolic link. Only then does the copy move into
   `versions/`. Otherwise it installs nothing and says so in a dialog ("This copy of Pulsar is not a signed
   release: …"): with a version already installed, that one opens; without one, Pulsar stops there. So an app built
   without the key, or one whose code was changed after the release was built, cannot put a version higher than
   every release in place, which no real update would ever replace.
3. **Python.** Always `/usr/bin/python3` (Apple's Command Line Tools; the app offers to install them), with
   `-E -s -B`: no `PYTHON*` variables, no user site-packages, no `.pyc` files. Never `-I`: versions up to 1.0.3 need
   the script's folder on `sys.path`. `pulsar.py` puts its own folder there itself.
4. **Rollback.** If the new `current` does not answer, the launcher writes it into `bad-versions` (never used again)
   and starts `previous`. A port that is taken is never the version's fault: `pulsar.py` then exits with its own
   status, 98, the launcher tries again (five times, a second apart), and if the port stays taken it says so, marks
   nothing and does not try `previous`. Before, a program that held the port for a moment at the right time could
   get a good version marked bad for ever.
5. **Is it ours?** Before it opens anything, the launcher asks `/api/whoami?nonce=` and checks the HMAC made with this
   user's key (`~/.config/pulsar/secret`) of the nonce and the port it asked on. A server another account or a
   container started on the same port fails this check, and so does one that passes the question on to this user's
   Pulsar on another port (section 3).
6. **Signing the window in.** The launcher writes a one-time code to `~/.config/pulsar/signin-code` and prints the
   address with it. The page trades the code for a session token (`POST /api/session`). The code works once and only
   for two minutes, and a wrong guess does not spoil it. A session lives as long as the server that issued it (section
   3), so every load of the page in the window goes through the launcher: at start, **View > Reload Pulsar**, a new sign-in,
   and the page's own move into a new version.
7. **Stopping.** Closing the window keeps Pulsar running (the Dock icon brings it back); quitting it (Cmd+Q, which
   asks first when the page has work that would be lost) runs `launcher.sh --stop`, which stops only a server
   the app itself started. A server started from a git checkout keeps running.
8. **Self-test.** `pulsar.py --self-test` imports every module, reads from `index.html` every script and style the
   page loads and checks each is there, starts a server on a free port, fetches the page and each of those files,
   and asks it for its version. The updater runs it on every new version before trusting it.

Start-up also tidies old data (`pulsar.py: tidy()`): owner-only permissions on files older versions wrote, copies of
days older than 120 days removed, error logs older than 31 days removed, MemTime copies a killed Pulsar left removed.

## 3. Security

Pulsar holds two powerful things: your Productive access and your Google Calendar access. The rules below keep them
yours.

- **Local only.** The server binds 127.0.0.1. Every request must say `Host: 127.0.0.1` or `localhost`, so a page that
  rebinds its own name to 127.0.0.1 is refused.
- **No other website.** A request another site's page makes is refused: the browser marks it in `Sec-Fetch-Site`; a
  WebSocket handshake, which has no `Sec-Fetch` headers, is caught by `Origin` and `Upgrade`. A link from elsewhere
  may open the page (a navigation to `/`), and nothing more.
- **Signed in.** Everything with data needs a session token in the `X-Pulsar-Session` header. The page keeps it in
  localStorage, which no other site or port can read. The server keeps only its SHA-256, in memory: a token is good
  for the server that issued it, until it stops or restarts (and 90 days unused at most). The launcher and Pulsar in
  Claude Code (5.6) prove themselves with `X-Pulsar-Key` (the key itself). Public without a session: `/`,
  `/index.html`, `/static/…`, `/api/version` and `/api/whoami`.
- **A restart leaves the port free for a moment.** While Pulsar restarts into an update, nothing holds its port for
  about half a second, and any program, another account's too, may take it. The page's requests then go to that
  program, token and all, and a plain reload would load that program's page in Pulsar's own origin, where it can read
  localStorage. So: a token dies with the server that issued it, and a later Pulsar never accepts it; and the page
  never reloads itself into a new version. It drops its token and, in the app, asks the window, which runs the
  launcher: the launcher checks the server holds this user's key (`/api/whoami`) and only then signs the page in again
  with a fresh code (**View > Reload Pulsar** goes the same way; WebKit's own Reload is out of the window's right-click menu). An impostor fails that check, the launcher says so, and the
  window loads nothing. A browser cannot make that check, so there the page says to open Pulsar from its app icon.
- **What the key alone opens** (`server.key_refusal`). The key (`~/.config/pulsar/secret`) is a file any program of
  the person's can read, a Claude Code session too (in bypass mode it needs no prompt to `cat` it). So on its own it
  opens only what its two holders need, and no read of the day as the page sees it:
  - `POST /api/quit`, for the launcher;
  - `POST /api/mcp/<tool>`, for Pulsar in Claude Code (5.6). Each tool's answer is computed in the server
    (`pulsar/mcptools.py`) exactly as the tool returns it, already cut down: names through `name_out`, reasons as
    fixed phrases (`reason`), repositories through `Places`, flags from a fixed list, Pulsar's and Productive's errors
    as fixed phrases (`error_out`). So the key reads what the tools show and nothing more: no window titles, apps,
    addresses, typed messages, meeting titles, notes of logged entries, rules, Logs or settings. Each tool has a rate
    limit (60 calls a minute; `pulsar_preview` 20, `pulsar_log` 10), and every date is between 120 days ago and today;
  - one write, `pulsar_preview` then `pulsar_log`. The preview checks every entry (services, the note through
    `guard_note`, at most 200 characters, no double quotation mark, no character nobody sees, Pulsar's own dry run)
    and keeps the entries, the run and Pulsar's summary in the server, under a code of 16 hex characters: the caller
    gets the code and the summary back, but the entries the code stands for stay in the server and cannot be
    changed. `pulsar_log` takes that code, exactly, and that summary, word for word, and no other argument. It
    writes new entries only, as a Log the day run (journalled, never sent twice, never `force`, never the dialog's
    pieces), and the journal marks each row `via: "claude-code"`: the Logged column says "from Claude Code" on those
    entries (`server.from_claude_code`). The server sets that mark on every write made through these tools, whatever
    the request says: its `client` field is the caller's own word (a script with the key can send "claude-code"),
    and nothing in a request can remove the mark.

  Everything else with the key alone is refused with 403: every GET with data (`/api/entries`, `/api/activity`,
  `/api/config`, `/api/logs`, icons, …), `POST /api/log`, changing or deleting an entry, settings (the token,
  `preview_writes`, the Claude Code switch), creating a task or drafting one, updates, Ask Claude, Log the day with Claude's review, the session note, Google Calendar, Productive's test.
- **Is it this user's Pulsar, on this port?** `/api/whoami?nonce=` answers `proof1`, the HMAC of
  `whoami-v1|<port>|<nonce>` with the key, for the port the server listens on. The launcher (`is_ours`) and Pulsar in
  Claude Code check it with their own port before they show a sign-in code or the key, so a program on the port that
  passes the question on to this user's Pulsar on another port gets an answer for the wrong port. The server also
  answers `proof`, the older HMAC of the nonce alone: the launcher inside a Pulsar.app of 1.4.0 or earlier checks a
  server that has updated itself since. The launcher accepts that older form only when the answer has no `proof1` (a
  Pulsar of 1.4.0 or older, during an update) and the listener on the port is a `pulsar.py` process of this user: lsof
  shows no other account's process, and a program of this user could read the key anyway. Pulsar in Claude Code never
  accepts it: an older Pulsar has no `/api/mcp/` routes.
- **What a program of the person's can still do.** This is not a wall between the person's own programs and Pulsar,
  and nothing on one account can be. A process running as the person can:
  - use the key as above: read what the tools show, and log new entries through preview and log without the person's
    yes. A Claude Code session in bypass mode, or one a prompt injection leads, can do this without the `pulsar_log`
    prompt; the entries then say "from Claude Code" in the Logged column, and a person can delete them there;
  - write a sign-in code into `~/.config/pulsar/signin-code` and trade it for a page session (`POST /api/session`), or
    read the window's token from its WebKit storage, and then do anything the page does;
  - read the files Pulsar keeps (`~/.config/pulsar`; `~/.cache/pulsar`: copies of days with their notes, the journal,
    Logs), ActivityWatch on localhost:5600, and the keychain items, which `/usr/bin/security` created and so trusts.

  A confirmation click in Pulsar's window for writes made with the key was considered and left out: a program that
  can do all this can also click it through a page session, and every write from Claude Code would ask twice. What the
  narrow key does stop is the one-line `curl` that reads the day's window titles, meetings and notes, writes any
  entry, changes or deletes entries, or switches Preview's writing on.
- **Claude Code: recommended settings.** Deny rules in `~/.claude/settings.json`, so no session reads Pulsar's
  settings folders, or writes a sign-in code into them, by accident or because a prompt injection asked (the switch,
  5.7, adds these same rules):
  `"permissions": {"deny": ["Read(~/.config/pulsar/**)", "Edit(~/.config/pulsar/**)", "Read(~/.config/pulsar-preview/**)", "Edit(~/.config/pulsar-preview/**)", "Read(~/.config/pulsar-release/**)", "Edit(~/.config/pulsar-release/**)"]}`.
  The `Edit` rules matter most: they stop "write a sign-in code there and trade it". Claude Code applies them to its
  file tools and to the file commands it recognizes in Bash (`cat`, `head`, `tail`, `sed`, `tee`, a redirection), not
  to a script that opens the file itself (`python3 -c "open(…)"`). They cover the settings folders only, not
  `~/.cache/pulsar` or the window's own storage (`~/Library/WebKit/com.fosterinteractive.pulsar*`). For bypass mode, or wherever
  Bash runs without asking, turn on Claude Code's sandbox (`/sandbox`) and list the same three folders under
  `sandbox.filesystem.denyRead`; its Config tab shows what applies. Claude Code's documentation does not say whether
  the sandbox stays on under `--dangerously-skip-permissions`: check it with
  `test -r ~/.config/pulsar/secret && echo readable` in such a session (it prints nothing when the sandbox holds).
- **Writes.** Every POST must carry `X-Pulsar: 1`. A page on another site cannot send a custom header without the
  browser asking this server first (CORS preflight), and this server never says yes.
- **The page itself** is served with a strict Content Security Policy: scripts only from Pulsar
  (`script-src 'self'`), no inline scripts, nothing loaded from the internet.
- **Secrets** go to the macOS login keychain, service `pulsar`: account `productive` (your API token) and account
  `google-calendar` (the Google refresh token). Elsewhere they go in files only their owner can read. They never go
  in `settings.json`, never reach the page, and are cut out of the error log (`logbook.scrub`: tokens, keys and codes
  by their labels and shapes, fields named as keys or secrets, bare lowercase hex of 32 characters or more except a
  40-character git commit id, UUIDs standing alone, long opaque strings; paths and file names stay).
- **The Google sign-in callback** (`/oauth/google`) is the one exception to the rules above, because Google sends the
  browser back with no Pulsar session. It must still come to 127.0.0.1, as a plain page load. It does nothing without
  a pending `state` that a signed-in Settings click created (random, used once, ten minutes), and it can only finish
  that one sign-in.
- **Writing to Productive** happens in four functions: `productive.create_entry` (from `server.api_log`: the log
  dialog's button and the confirm step of Log the day through `POST /api/log`, section 5.4, and `pulsar_log` in Claude
  Code through `POST /api/mcp/pulsar_log`, section 5.6), and
  `update_entry` and `delete_entry` (from `POST /api/entry/update` and `/api/entry/delete`: Edit, Delete, a dragged
  edge and Log the day's Undo, section 5.2b, all from the page only), and `create_task` (from `POST /api/task/create`:
  **Create in Productive** in the log dialog's new-task form, 5.1, from the page only). With `PULSAR_READ_ONLY=1` (any value but
  `preview`) in the environment, every write is refused at the lowest level (`productive.https`), and
  `POST /api/log` refuses it before the Log the day journal records anything (`POST /api/task/create` before anything
  is sent too). Test servers always run with it.
- **Git in other people's repositories.** Notes from commits (`commits.py`) read any repository a session touched,
  an unpacked client archive too, so its `.git/config` is not trusted: `git log` runs with `--no-show-signature`,
  `-c log.showSignature=false`, `-c core.fsmonitor=false`, `-c core.hooksPath=/dev/null` and `--no-pager`, no
  inherited `GIT_*` variables, and `GIT_ALLOW_PROTOCOL=` empty, so a signature check's program, a hook or a fetch of
  a missing object (through the repository's own remote and `core.sshCommand`) never runs. Nor does it decide whose
  commits are yours: a commit counts only when its author and its committer both carry an email of your own git
  settings (`git config --show-scope`: global or system, so `~/.gitconfig` and what it includes), or, for a person
  who sets `user.email` per repository, an email that another repository of the day's sessions sets too. A
  repository that names its own author as `user.email` cannot turn that author's commit subjects into your note or
  into Ask Claude's prompt.
- **Claude Code's file tools and Pulsar's folders.** The Claude Code switch (5.7) adds a Read and an Edit deny rule
  to Claude Code's user settings for each of Pulsar's folders (`claudelink.deny_rules`): `~/.config/pulsar` and
  `~/.config/pulsar-preview` (the key, the sign-in code, settings, tokens); `~/Library/Application Support/Pulsar*`
  (the installed code: an edit there would take out Pulsar's own checks at the next restart);
  `~/Library/WebKit/com.fosterinteractive.pulsar*` (the window's data, its sign-in token among it); `~/.cache/pulsar*`
  (copies of days with their notes, Ask Claude's answers, Logs); and `~/.config/pulsar-release` (the release key)
  only on a Mac that has that folder. Switching off takes back only the ones Pulsar added (`claude_denies_added`); a
  copy switched on before a rule existed says in Settings that rules are missing and offers to add them again.
  Claude Code applies a Read or Edit deny rule to its own file tools (Read, Grep, Glob, Edit, Write) and to the file
  commands it recognizes in a shell command (`cat`, `head`, `tail`, `sed`, `tee`, a redirection), in every mode,
  bypass included. **What they do not stop:**
  - a script that opens the file itself (`python3 -c "open(…)"`, `node -e`, a script on disk);
  - a command that reaches Pulsar without naming its files: `pulsar.py --mcp` or `--signin-url`, `curl` to its port;
  - a command that reads files without naming them, and any path Claude Code does not recognize as the same folder
    (a symbolic link into it, for one);
  - any program that is not Claude Code (an editor extension, an npm install script);
  - a session in bypass mode that first removes the rules from `~/.claude/settings.json` (Claude Code re-reads it
    while running).

  With Claude Code's sandbox on, the same paths bind every shell command (its `sandbox.filesystem.denyRead` applies
  only while `sandbox.enabled` is on, and no setting applies it to shell commands without the sandbox). Pulsar does
  not turn the sandbox on; Settings links its documentation. So the rules stop a session's casual or injected
  `cat ~/.config/pulsar/secret` or an edit of Pulsar's code by Claude's own Edit tool, not a program set on it.
- **Claude Code runs Pulsar starts** (Ask Claude, the session note) get a short list of Pulsar's environment
  (`askclaude.ENV_KEEP`: `HOME`, `PATH`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `TZ`,
  `CLAUDE_CONFIG_DIR`, a sign-in key or token, a proxy and its certificates), never all of it: Pulsar started from a
  Claude Code session carries that session's `CLAUDECODE`, session id, messaging socket and token, and any
  `ANTHROPIC_BASE_URL`. They also get `ENABLE_CLAUDEAI_MCP_SERVERS=false` (no claude.ai connectors) and
  `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Checked with Claude Code 2.1.301: the sign-in still works with only that list.
- **Updates** are signed, and so is the code the app carries. See section 9 and step 2 of section 2.
- **What the release key does not cover.** The window and the launcher of a Pulsar.zip are Apple ad-hoc signed only,
  which says nothing about who built them (section 13). A first install made with README.md's line is protected by
  `Pulsar.zip.sig`: the line checks Pulsar.zip, window and launcher included, with `/usr/bin/openssl` and the release
  key's public half written in the line, before it unpacks anything, so a Pulsar.zip published by someone without the
  key is refused. That trusts the line itself, so it is copied from README.md on GitHub or the onboarding document.
  Downloaded in a browser, nothing checks Pulsar.zip: only the launcher's check of the code it carries, which belongs
  to the app being installed (it stops a Pulsar.zip whose code was swapped or built without the key, not an app
  whose launcher was replaced too). So who may publish a release on the repository still matters (section 9, "What
  the maintainer must do").

## 4. Where the day comes from

The day view asks the server for five things at once, each on its own, so one slow source never holds the others:

| Request | Column | Source |
|---|---|---|
| `GET /api/activity?date=&source=` | Activity | ActivityWatch or MemTime |
| `GET /api/proposal?date=` | Likely | Claude Code sessions and the tracker |
| `GET /api/entries?date=` | Logged | Productive |
| `GET /api/calendar?date=` | Meetings | Google Calendar |
| `GET /api/repeats?date=&source=` | Logged (dashed cards) | Productive history and the tracker |

Times travel as **minutes from the day's midnight** (`s`, `e`). On the two days a year the clocks change, the server
converts elapsed minutes to wall-clock minutes (`server.clock`), so 09:00 is drawn at 09:00.

### 4.1 Activity (`pulsar/trackers.py`)

Both readers return the same items: `{s, e, app, title, host, url, cat, icon}`, where `cat` is `work` or `personal`.

- **ActivityWatch** (`http://localhost:5600`, read only). Pulsar reads the window bucket and the AFK bucket of every
  host name the Mac has had (a renamed Mac gets new buckets). A window counts only while the AFK watcher says someone
  was at the keys (`clip_to_present`). A pause the AFK watcher wrote that is shorter than Settings' **Away after**
  (`config.away_after`: 10, 15, 20 or 30 minutes, 15 by default), between two stretches of input, counts as being
  there (`present_spans`), as in the fi-time-from-activity skill: the window in front then counts as work. When the AFK
  watcher recorded nothing at all, Pulsar says so instead of showing a day with no breaks.
- **MemTime.** MemTime keeps its database open and writing, so Pulsar reads a private copy
  (`$TMPDIR/pulsar-memtime-*`, tens of MB), made at most once a minute and deleted when the next one is made, before
  a restart into an update, and on exit. A Pulsar that is killed runs nothing at exit, so every ten minutes at most
  (and at start-up) any copy older than ten minutes goes, whichever Pulsar made it: a copy serves queries for a minute
  only, so none that old is still read. Rows of type `offline` are left out. App names come from MemTime's own table of bundle
  ids. Icons are the ones MemTime already saved; nothing is fetched from the internet.
- **Auto** picks ActivityWatch, the main tracker, whenever it saw the day; MemTime only for a day it saw nothing of.
  MemTime counts the window in front even while nobody is there, so its items are cut where ActivityWatch recorded
  the person away (`trackers.aw_away`, `cut_out`). A gap from Away after up to an hour is a **pause**, an hour or
  more is **away** (`server.AWAY_LONG`), three hours or more is **off**; a gap still going on today shows too.
  The Activity header can switch.
- **Personal windows**: a window whose app, title or site contains one of the words in Settings is `personal`. It is
  drawn faded and never counts as work, in the totals, the gaps or Likely.
- **Cleaning**: touching pieces of the same window are joined (`compact`), so a day is hundreds of items, not
  thousands. Editor titles lose the workspace part ("Tab — repo (Workspace)" becomes "Tab"; the workspace is kept for
  rules). URLs lose their query string, which can carry tokens.
- **Away** (`server.away`): every gap of Away after or more between the first and the last activity of the day. Gaps of
  3 hours or more are "Off" (a night, an afternoon off): drawn, never added to the total. Away after also ends a
  sitting in Likely (`sessions.propose`) and in Log the day (`dayplan.opts_for`, `sitting_gap`). The one setting drives all
  four; `dayplan.MERGE_GAP`, `CALL_GAP` and `OFF_MIN` are not breaks and stay fixed.
- **ActivityWatch's own settings** (`pulsar/awsetup.py`). Its AFK watcher writes "afk" only after its own timeout
  (`~/Library/Application Support/activitywatch/aw-watcher-afk/aw-watcher-afk.toml`, section `[aw-watcher-afk]`, key
  `timeout` in seconds; 180 without the file or the key), backdated to the last input, so a pause shorter than that
  timeout is not in its data and no threshold in Pulsar can see it. Settings → Time and activity → **Away** reads that
  one key, line by line with the standard library (`scan`; `[aw-watcher-afk-testing]` and every other section are
  never read or changed; a form it cannot read for sure, such as a dotted key, the key twice or a value that is not a
  number, is said and left alone). A timeout longer than Away after offers **Fix**: it keeps a copy of the file next
  to it (`aw-watcher-afk.toml.pulsar-<date-time>.bak`, never over another file), changes that one line, or adds the
  key in that section, writes through a temporary file and a rename with the file's own permissions, reads it back
  (and puts the file back if it does not say the new value), saves Away after, and restarts ActivityWatch: aw-qt
  ignores an AppleScript quit, so `pkill -f "ActivityWatch.app/Contents/MacOS/aw-qt"`, then a wait (15 seconds at
  most) until no process of the app is left, `open -a` the app, and a wait (30 seconds at most each) for
  `/api/0/info` and for the AFK watcher (its process, or a heartbeat in its bucket since the restart). The page says
  before the click that the new timeout applies to new data only. A timeout the same or shorter is never touched:
  `present_spans` counts the shorter pauses as work. **Login Items**: ActivityWatch not in them records nothing after
  a restart of the Mac until someone opens it. Only on a click, Pulsar asks System
  Events for the login items' names and, on a second click, adds the app where it really is (the copy that runs,
  else `/Applications` or `~/Applications`, else Spotlight by bundle id, each checked by its `Info.plist`) as a hidden
  login item, then lists them again: System Events answers "login item UNKNOWN" even when it worked. The page says
  first that macOS asks once whether Pulsar may control System Events (Automation); a no (-1743) says where to allow
  it, or how to add ActivityWatch by hand. Settings shows the last answer while Pulsar runs; nothing is asked by
  itself. The two routes (`POST /api/activitywatch/fix`, `/login`) need the page's session (the key alone is
  refused, `server.key_refusal`) and refuse in a read-only run (`PULSAR_READ_ONLY`) and in tests (`awsetup.blocked`);
  behind that, `awsetup._run` and `_fetch` refuse on their own, and the tests cannot start `pkill`, `osascript` or
  `open` at all.

The column groups items into blocks per zoom slot (1 to 60 minutes): each slot shows the window with most time in it,
and equal neighbours join (`activityBlocks` in `day.js`). Away time is drawn as a hatched rail on the column's edge,
above the blocks, because a 30-minute block can hide a 20-minute break, and named (Pause, Away, Off) with its length
at the first place in it no block covers.

### 4.2 Logged (`pulsar/productive.py`)

Two connections give the same rows: `{id, date, start, minutes, note, approved, task, taskId, service, serviceId,
project, budget, billable, billing}`. A card in the Logged column leads with its task (what was logged), with the
project as its colour stripe and, where the card is tall enough, its second line.

- **API token** (Settings): Productive's REST API (`api.productive.io/api/v2`), with the organization id and your
  person id. Read/Write to log time.
- **Claude Code's connection**: the Productive MCP server (`mcp.productive.io/mcp`) with the token Claude Code keeps
  in the keychain (`Claude Code-credentials`). It lasts an hour. When it has expired, Pulsar runs
  `claude mcp get productive` (the `claude` command from the PATH, the usual install places, or the VS Code / Cursor
  extension), which makes Claude Code renew it, then reads the keychain again; at most once every 5 minutes. MCP
  answers are text written for an AI; `parse_mcp` reads the JSON block in them, and `_mcp_rows` pages with offsets
  (an offset must be a multiple of the page size).
- **Auto** uses the token when there is one, else Claude Code's connection.
- **Who you are** is found once from the connection (`identify`) and kept in Settings. A failure is not retried for 5
  minutes.
- **Names** of projects, budgets, services and tasks are cached in `~/.cache/pulsar/names.json`, with when each was
  read. A name or billing type is read again once it is a week old (a billing type the role could not see, after a
  day); until then the old one is shown. A failed re-read keeps the old one and tries again a day later. The same
  file keeps how many of a project's recent tasks have an estimate, for the ticket flags (5.3), read again after a day.
- **Billable**: Productive hides the billing fields from some roles but still filters on them. So Pulsar lists each
  deal's services once per billing type and remembers which type each service is. Billable means Fixed price, Time
  and materials or Percentage. Unknown stays unknown (grey on the page), never guessed.
- **Every successful read is kept** per person and day in `~/.cache/pulsar/days/<person>/<date>.json`. When
  Productive cannot be reached, the day and the report show those copies and say from when.
- **Network**: one pooled HTTPS connection per host, redirects never followed (a token only ever goes where it was
  meant). Reads are tried up to 3 times when the server hangs up; writes are never repeated. Claude Code's
  connection takes four requests at a time; the 60-day history and the Tasks box's estimates share two of them, so
  the day's own entries and the log dialog never wait behind them.
- **History**: the repeats, the meetings' tasks and the learned tasks read the 60 days before a date once, kept an
  hour. A column that asks while it is being read waits for that read rather than starting another.
- **The keychain**: when it does not answer (locked, or a dialog nobody sees), the page says so and shows the saved
  copy, rather than asking for a token.
- **Creating a task** (`create_task`, only from **Create in Productive**, 5.1). Productive needs a title (at most 140
  characters), a project, and a task list or none: `task_list` null puts the task straight in the project, and is also
  how a subtask is made (`parent_task`). A subtask cannot have subtasks ("invalid level of subtasks"). Pulsar sends the
  title, the description, the project, the parent or the list, the person as assignee, and the status and estimate
  picked in the form (`workflow_status`; `initial_estimate`, minutes through the token, "90 min" through Claude Code's
  connection). With no status, Productive puts the task in its workflow's first. It never sends a service: a
  developer's role does not set a task's service, a PM does (fi-productive-process, and below). A time entry takes its
  own service anyway, from the project's budgets (5.1). Through Claude Code's connection it is `create_resource` with bare ids, as
  Claude Code sessions create tasks; only Productive's sentence "task created (ID: n)" or "Created 1 task (ID: n)"
  counts as created, "Failed to create task …" is a refusal said in plain words, followed by Productive's own words
  (the page and Logs keep them: a read-only token, a member missing from the project), unless it names a server error,
  a timeout or a dropped connection behind the MCP: that, and any other answer, may have created it (said so, never
  tried again). Through the token it is a JSON:API `POST /tasks` with relationships, as
  Productive's API reference shows it (not yet tried against Productive). The role: one person's Staff role created 43
  tasks from 22 September to 4 November 2026, in 12 projects (10 of clients), 29 of them as subtasks, through Claude
  Code; Productive's own permission is "Add, edit, and delete tasks", and a role without it in a project gets "your role
  may not add tasks to this project".
- **Who sets what on a new task**, read from Productive on 7 November 2026 (tasks created since July, their activity
  feeds, the time entries on them): of 73 tasks created with the Staff role (44 by one person, 29 by three other Staff
  accounts), none carried a service when created. All 28 service changes on the first person's tasks were made by the
  two people with the PM role (Profitability Manager), and the other Staff accounts' 140 changes since 15 September
  include no service. The Staff role's permissions, as Productive lists them, include adding and editing tasks but not
  seeing budgets or service types. So the service is never sent. The first person set 10 estimates and 9 statuses on
  tasks they had created, and 3 tasks had an estimate when created: a developer sets both.
- **The project's statuses** (`workflow_statuses`): the project's workflow, then its statuses, two reads kept 10
  minutes; the open ones (not started, then started) are offered, in the workflow's order.
- **Where a new task can go** (`project_places`, `newtask.places`): the project's open top-level tasks and its active
  task lists, two reads. Offered first: the period's contingency task, as the parent, when the project has one (FI's
  maintenance projects put each issue under it, `newtask.PERIOD_TASK`; the one named for the entry's month first, else
  the newest); else the task list of your newest open task in the project; else its only list; else straight in the
  project; with several lists and none of yours, the form asks. On four projects checked on 5 November 2026, this was
  where the person's own last tasks there had gone.
- **Projects for a new task** (`active_projects`): the active projects you can see, one read kept 10 minutes.

### 4.3 Meetings (`pulsar/calendar.py`)

- **Connecting** is OAuth 2.0 for installed apps with PKCE. **Connect** makes a `state` and a PKCE pair and opens
  Google's sign-in in the default browser. Google sends that browser back to `http://127.0.0.1:<port>/oauth/google`.
  The server trades the code and the PKCE verifier for tokens at `oauth2.googleapis.com/token`. The refresh token
  goes to the keychain; the access token stays in memory and is renewed when it runs out.
- **Scope**: `calendar.events.readonly` only. The account shown is the primary calendar's id, which Google sets to its
  owner's email, so no extra scope is needed.
- **The client**: a "Desktop app" client in the Google Cloud project `pulsar-fi`, audience Internal (only
  fosterinteractive.com accounts can sign in). Its JSON is not in git (`pulsar/google_client.json` holds
  placeholders). Whoever releases keeps the real one at `~/.config/pulsar/google_client.json`; `release.sh` and
  `build_app.sh` put it into the release and the app. Google treats a desktop client's secret as public; Internal is
  what limits who can use it.
- **A day**: one `events.list` on the primary calendar, repeats expanded by Google (`singleEvents=true`), kept 5
  minutes; ↻ asks again at once. Working-location, out-of-office and birthday entries are left out. A declined
  meeting is marked. The join link comes from Meet, the conference data, or a Zoom/Teams/Webex address in the
  location. Of each event Pulsar asks only for its title, times, status and type, the links, the person's own answer
  (`attendees(self,responseStatus)`) and the organizer's address (`organizer(email,self)`); never its description or
  the other guests (`calendar.FIELDS`). The organizer's address stays in the server: the page gets only `external`,
  true when neither the person nor someone of their own company (their Google account's domain or a subdomain of it;
  an account at a public mail service names none) organized it (`calendar.outside`), and `note`, the title a log entry
  may take as its note (below).
- **Disconnect** revokes the token at Google, then forgets it here whatever Google answered. Pulsar Preview only
  forgets it here: it signs in with Pulsar's own Google client, and a revoke withdraws that client's access for the
  account, Pulsar's too.

### 4.4 Likely (`pulsar/likely.py`, `pulsar/sessions.py`)

Which Claude Code session, and so which project, each minute of work most likely went to.

- **Source**: Claude Code keeps every session as JSON lines in `~/.claude/projects/<folder>/<id>.jsonl`: what you
  typed, what the agent did (its tool calls name the files it touched) and the session's title. A line whose every
  UTC time (`"timestamp":"…Z"`) is outside the day (with six hours before and one after) is skipped before it is
  parsed (`sessions._outside`): parsing every line of long sessions was most of a day's cost. A day reads each session
  once: commits and Ask Claude's steps take the read Likely made (`sessions.read_day`). On five past days the rows,
  commits and steps came out the same, in 4.3 to 5.6 seconds of CPU instead of 8.1 to 9.6.
- **A turn's project** is the git repository its tool calls worked in: the folder that holds `.git`, named after its
  `origin` address (a clone in a folder called `repo` is still `fi-drupal-qa`). Names that say nothing
  (`repository.git`, `repo`, `src`, `app`…) fall back to the folder name. Each call weighs by what it did there: a file
  changed (Edit, Write) 3, a command run in a folder of it (Bash `cd`, `git -C`, a path that exists) 2, a file read or
  searched (Read, Grep, Glob) 1, a path you typed 1; a word that only names a project (a search for it, a commit
  message, an instruction to a subagent) counts nothing. A subagent's calls (`<session>/subagents/*.jsonl`) count for
  the turn that started it. So one session that works in a client's site, then in an internal tool, splits into one
  row per repository, turn by turn. A turn that touched no files takes its neighbours' project, then the session's
  title, then the folder the session ran in. `~/.claude` itself is tooling, never a project.
- **Each minute** the tracker saw work goes to one session: the one whose title the editor window showed; else one
  whose agent was running; else the one last used in that sitting (a sitting ends after Away after with no sign of
  work, 4.1). In a
  browser (or Slack, or any other work window), a page whose title or address names a project goes to that project's
  session; else the session whose tab was in front last in that sitting, the work the person came from (1.6.8: it
  went to the session used last, often another one whose agent was running in the background; on 19 past days the
  minutes per task were right for 43.6% instead of 42.6%, the dialog's first task for 34.4% instead of 33.8%); else
  the one used last. Meetings, personal
  windows and time logging (a minute that went mostly to Pulsar or Productive) go to none. Except Pulsar's own
  window while a session in Pulsar's own repository (`pulsar`, `pulsar-preview`) is at work, a message typed or its
  agent running within the away time: that is testing what was just built, so the minute is that session's
  (`sessions.OWN_REPO`, as `dayplan.OWN_REPO` for Log the day). Productive stays logging.
- **Without a tracker**, the time an agent ran on a request, and answers typed within Away after, count as work.
- **Rows**: neighbouring minutes of one project join into rows `{s, e, minutes, project, titles, messages,
  first_message, sessions, single, repoLinks}`, at the real time of the work. `single`: each of the row's sessions was
  in this repository all day so far (learning, 5.5). `repoLinks`: the task links typed in a turn of this repository.
- **The background** (`background`): another session's agent that ran meanwhile, in another repository, at its real
  time, as rows of their own (`kind: "background"`), never in the column's minutes. A stretch is attended
  (`qualifies`) while its agent runs on a chain started by a message you typed (a background task reporting back
  carries it on), with no silence of the agent longer than 10 minutes (a silence up to 10 minutes counts, a longer one
  ends the chain until you type again), in a turn whose calls did work in that repository (weight 2 or more). Log the
  day uses it (5.4); the column draws it as a thin dashed rail on its right edge, and the block's popover lists it.
- **The fi-time-from-activity skill**: where `~/.claude/skills/fi-time-from-activity/scripts/activity_blocks.py`
  exists, its project names are used (`likely.skill_names`: its `projects_in` rules, and its `LOGGING` name: a turn
  it names "time logging", such as a Productive call on time entries, goes to no session), and each turn is named as
  it names it (`likely.skill_turns`, `sessions._named_turns`): every string of the turn's tool calls and what was
  typed, counted by `projects_in`, words included, with its own last resorts (the title without a leading
  "IMPORTANT!", the folder, "unknown"); the turn's hard calls count for that project at their highest weight. On one
  person's 52 days this named the client logged more often than the weights above (right task at the right time 33.9%
  against 30.4%). Without the script, or when it cannot be read, Pulsar's own names and weights. Pulsar reads the sessions
  itself; the script's own rows are not used: they laid each sitting's minutes out one project after another (on one
  person's days, only 57% of the minutes in front had the same project in the script's row) and moved the agents'
  parallel time into free time. It is never part of the repository.
- **Caching**: a past day is worked out once (about 3 to 6 seconds), today again after 3 minutes or on ↻. Learning's
  background run (`learn.warm`), which works out each past day anyway, also keeps the last 31 days for the day view
  (`likely.keep_day`, with their commits), so yesterday opens at once the first time after midnight; the page says
  "reading Claude sessions…" only after 0.4 seconds, so a day read from disk never flashes it. Copies kept
  in the older shape (`likely-full` format 1, `likely-days` format 2) are worked out again. A past day
  worked out while no tracker answered (ActivityWatch not up yet at login) is not final: it is reused for 3 minutes, as
  today is, never kept on disk (`likely-full`), and learning never takes it from memory, so it is worked out again
  once a tracker answers.

## 5. Logging time

### 5.1 The dialog (`web/js/log.js`)

It opens from many places, each with the time already filled in:

| From | Start and length | First group in the task list |
|---|---|---|
| Dragging in the Logged column | the dragged stretch; note as for a stretch (below) | as for a stretch |
| The yellow rail (active, not logged) | the stretch; note as for a stretch | as for a stretch |
| An Activity block, **Log this time** | its active minutes; note as for a stretch | as for a stretch |
| A Likely block, **Log this time** | the block; note = Claude's draft, if it wrote one | "For this work": Claude's pick, task links, learned tasks |
| A meeting, **Log this time** | the meeting; note = its title | "For this meeting" |
| A dashed card (a repeat) | the call as seen; note = the usual note | "Usually at this time" |
| **+ Log** | the first yellow-rail stretch, as for a stretch; else after the last entry | as for a stretch; else the rules |

**A stretch** (`day.js` `logStretch`; also the stretch **Log and next** opens) is read from the Likely rows inside it,
not from the blocks, which change with the zoom: the project with most minutes there is its work, when it has half
the stretch, or less when no meeting covers half of it and no rule has more of it. The first group is that project's
"From the project sessions": Claude's pick, task links and learned tasks of those rows, as for its Likely block. Else a
meeting that covers half of it ("For
“title”"), else the rules. The note starts from the same rows as for a Likely block: Claude's pick's note, else
the commit draft; then range.js (below) leads with the task of the work that holds most of the stretch, takes its
note, and, with the Claude Code switch on, asks for its session note (5.7). A stretch with no Likely row in it
gets neither. Earlier versions drafted no note for a stretch, and took its first group from the Likely block that
covered half of it at the zoom on screen: it changed with the zoom and could name a project with no row in it.

After the first group come **From your rules**, **You logged to these lately** (tasks of your last 30 days, with the
service you used last) and **Assigned to you** (open tasks in active projects, the urgent ones first: overdue, due
today, blocked or in progress, then the rest by due date). The search box searches every task you can see, yours
first (`server.rank_search`): tasks you logged to in the 30 days before the day being logged (newest first), then open
tasks assigned to you, then everyone else's in Productive's order, closed tasks last; each of yours says "yours, logged
lately" or "assigned to you", and one of yours whose title holds the words is added when Productive's first page left
it out. In a company where everyone has a "Scrum - Name" task, yours is then first, not twelfth. The first
task of the first group is picked for you; nothing is logged until you click.

The service list is the task's project's services that take time on that date (`bookable_services`); the one you
used last on that task is picked, else one with the same name in a newer budget, else the task's own. A blocked task
says so before you log.

The start is a 24-hour `HH:MM` text field, like every time in Pulsar (`9:30`, `930` and `9.30` become `09:30`; a
time that is not one is refused before anything is sent). **End** (`flow.js`), the same kind of field, sits between
Start and Minutes and is Start plus Minutes; every way the dialog opens fills it. The three stay in step as you type:
a new Start keeps Minutes and moves End (what is logged stays the same, as before End existed and as calendars do), a
new End changes Minutes, new Minutes move End. Only the start and minutes are sent; the API is unchanged. An entry
stays in its day, so End is `24:00` at the latest (typed `24:00` or `00:00`). A Start or Minutes that would run past
midnight shows End `24:00` and a line saying so while typing; leaving the field cuts Minutes to midnight, and the next
Log stops once to show the cut. An End before Start (or equal to it, or not a time) is said under the times, and Log
sends nothing until it is fixed. Minutes are real minutes and End a time on the clock: the two nights a year the
clocks change they differ (`core.js` `realMinutes`, `clockAfter`), so 01:30 to 03:30 the night they go forward is 60
minutes, and 00:30 to 02:30 the night they go back is 180, as the server counts an entry. A drag, a dragged edge and
the split count the same way. A note Pulsar filled in, a meeting's title or a repeat's
usual note, says "Filled in for you. Clients may see notes." until it is edited. On a Likely block with commits of
yours, the note is drafted from them (below), with "From your commits. Clients may see notes."; a note Ask Claude
drafted comes first. Every note hint has that form: where the note came from, then "Clients may see notes". The picked task has an **Open in Productive** link (see
5.2b for where its address comes from), and under the task list its ticket flags, if it has any (5.3).

The minutes of a block (a stretch of the rail, an Activity, Likely or meeting block; not a drag in the Logged column,
which keeps the minutes dragged) are rounded to Settings'
**Round entries to** (5, 10 or 15 minutes): to the nearest step, and at least one step. Next to an entry already
logged that day the block runs up to that entry instead, whenever rounding would run into it or leave less than a step
before it: a 14-minute stretch ending at an entry is logged as 14 minutes, not 10 with a 4-minute crumb left "not
logged" (`roundMinutes`). A block that starts inside an entry already logged (a Likely block, a meeting) starts
where that entry ends instead, less those minutes (`freeStart`), so **Log this time** never opens on logged time.
Minutes typed in the dialog (or worked out from a typed End), a drag in the Logged
column (in 1-minute steps: the person picked those minutes) and an entry being changed are never rounded.

**Around time already logged.** When the overlap check (5.2) finds entries inside the time asked for, the answer also
has the pieces around them (`server.pieces_around`): the time asked for less the time logged, never more and never
moved. A boundary at an entry goes to Settings' grid, away from the entry; the start and end in the dialog stay; a
piece shorter than Log the day's shortest entry (`dayplan.opts_for`: 10 minutes, 15 with quarter hours) is left out,
and its minutes are said. The message lists them ("Around it: 00:00–02:30 (2 h 30 min)"), the main button becomes
**Log 2 h 30 min around it** or **Log 2 entries around it**, and **Log anyway** stays, second. Any change to the date,
start, minutes, task or service asks again. The pieces go in one request, each one an entry with the same task,
service and note (5.2); Log and next goes on after the last one. Like any entry the dialog logs, they are not a Log
the day run: no journal and no Undo (delete one from its card). The case that asked for it, 5 Nov: a Likely block at
00:00 for 160 minutes, with 16 minutes of another task logged at 02:30, becomes one entry, 00:00–02:30. On one
person's 34 fully logged days (21 September to 4 November 2026, zoom 30), up to 38% of Likely blocks (114 of 301) and
37% of Activity blocks (177 of 473) ran into another task's entry logged that day ("up to": some of those entries
were logged after the block). Around it, one piece of 10 minutes or more was left for 71% and 84% of them, two or
three for 17% and 11%, none for 12% and 6%.

**Notes from commits** (`commits.py`). For each Likely row the page shows (not the 60 past days learning reads), the
repositories its Claude sessions worked in during the row (the session's folder and the paths its tools named), kept
when their name matches the row's project ("Acme" and acme-web, "claude-rules / skills" and claude-rules).
In each, `git log --all --no-merges` since the day before, keeping your commits (author and committer by your own git settings' email: section 3) from
five minutes before the row to ten minutes after it; a commit goes to one row only, the one it was made in, else the
nearest. Merges, version bumps, changelog, WIP and fixup commits are skipped, and so is any subject `guard_note`
refuses (a path, a web address, code, a tool's name). The draft is the first three subjects joined with "; ", a shared
"Area: " said once, stopping at 160 characters. Only `git config` and `git log` run, read only; none at all without
git, or with only Apple's stand-in that would ask to install the developer tools. Session files are read once per
change, so a refresh costs about 0.1 s.

**A stretch picked in the Day view** (`web/js/range.js`, `GET /api/range`, `pulsar/ranges.py`). A drag in the Logged
column, a rail, an Activity block, **+ Log** and Log and next open the dialog at once, as before, then ask what the
stretch holds. The server works the day out as Log the day does (`dayplan.gather`; about a second once the day has
been read) and keeps, for each minute, the claim that has it (`plan(..., keep=)`), before the layout joins and moves
the pieces. Within the stretch, the minutes make parts by kind: Claude work by repository, a meeting, a call, work
outside Claude, a repository you never log, already logged, away, no work seen. Each work part has Log the day's task
for it (with its confidence and why) and a note source: its commits, Claude's draft for that task, a meeting's title
or a repeat's usual note. Parts with the same task are one group, and the group with most minutes leads:

- Its task comes first, in a group "For this time: fi-drupal-qa, 50 min of 1 h", with the other groups' tasks
  and its alternatives after it; the dialog's other groups follow. It is picked unless a task was picked by hand or
  just created there (Create a task), and not at all once the day or time was changed meanwhile. Not
  when the main work is work outside Claude: its task is only the neighbour's, and on the replay the dialog's own
  first task was right more often there (29% of those minutes against 22%). Everywhere else Log the day's task was the
  better first pick: for entries logged as one, the logged task came first for 47% of the minutes instead of 44%; for
  runs of two or three entries back to back, for 45% instead of 33%.
- Its note fills an empty note: the commit draft or Claude's draft at once, with the line that says where it came
  from; then, with the Claude Code switch on and the main work in a Claude session, Claude's note from the session
  (`POST /api/likely/note` for that repository and the stretch, 5.7), unless Claude's pick already brought one. Never
  once the person has typed in the note, and only in the dialog that asked.

**A stretch that mixes work.** When the biggest group holds less than three quarters of the work minutes, or two
groups have 20 minutes or more each, one line under the note says so: "Mixed work: fi-drupal-qa 50 min, a meeting
30 min, client-site 20 min. Split into 3 entries?". The groups are counted as the split
would make them: minutes that are not work (away, no activity, time spent logging) go with the work before them, time
already logged stays out, a piece with under 10 minutes of work (the shortest entry, or Settings' **Round entries
to**) joins its neighbour, or is left out when it has none, and boundaries inside the stretch go on the grid. The
threshold comes from the replay of 34 days (21 September to 5 November 2026, each day planned from the days before it
as if nothing were logged; `tools/replay/range_hint.py`): the line shows for 8.8% of the 487 entries logged as one (one task) and for 53.0% of the
436 runs of two or three entries back to back with different tasks. Log the day tells apart the work of 258 of those
runs (in the rest one session moved between tasks), and the line shows for 89.5% of them. With 0.6 it was 6.2% and
44.7%, with 0.7 8.4% and 48.9%, with 0.8 10.3% and 55.5%. On the runs it shows for, the split has the logged task for
46% of the logged minutes, one entry with the main task for 37%.

The question lists the entries, each with its time, task, project and service (the one Log the day picks: last used
on the task, checked against the date) and its note, which can be changed there. As in Log the day, an entry whose
task Claude found by searching Productive, or whose note Claude drafted, starts unticked with the reason beside it:
it is sent only once ticked (editing the note ticks nothing; it only removes the draft's mark). **Log these N
entries** sends the ticked ones one at a time through `POST /api/log`, with the dialog's checks: an overlap or a refusal stops the rest and says why,
and nothing is forced. An entry with no task (a call with no calendar entry) is listed and left to log on its own.
**Log in Productive** still logs the stretch as one entry. This is a minimal way to log several entries from the
dialog; the dialog's own (around entries already logged) should take its place once both are in.

**Log and next** (the button beside Log, or ⌘⇧↩; ⌘↩ logs and closes) logs the entry and opens the dialog again on
the next yellow-rail stretch after it, else the first one left in the day, with that stretch's suggestions
(`leadFor`, as the rail itself) and their first task picked. A line at the top says what was just logged and how
many stretches are left. Stretches logged from the page in the last ten minutes are left out even before Productive
shows them. **Skip**, on a dialog opened on a stretch while others are left, leaves that stretch unlogged and opens
the next one the same way (skipped stretches are left out of the next ones for ten minutes; the rails stay). It is offered for new entries in the day on screen only. Clicks to log a whole day again, entry by entry
(Nick's own entries, on a simulated empty day): 4 Nov 2026, 21 entries, 57 clicks before and 38 with it; 3 Nov,
38 entries, 108 and 71 (with ⌘⇧↩, 17 and 33). Entries away from the computer (calls) still need their start typed.

**Create a task** (`web/js/newtask.js`, `pulsar/newtask.py`). Under the task list a small link, **No task fits this
work? Create one in Productive**, is there for new time, never while an entry is changed. It is always there rather
than only when Pulsar is unsure: the lists' first task was the logged one for under half of the minutes (5.5), so
whether one fits is the person's call. It opens a small form over the dialog:
- **Project**: the project of the task picked in the dialog (the work's own, a rule's or the person's), else the one
  the work's repository names, loosely (`newtask.guess_project`: "acme-web" and "Acme-OS …", "globex-r" and "GX-OS
  Globex …"), else none. Projects logged to in the last 30 days come first, with their minutes.
- **Where it goes**: under the period's contingency task, in a task list, or straight in the project (4.2).
- **Title and description**, drafted from the Likely rows of the project with most minutes in the dialog's time, whole
  rows, as the session note takes them. At once, on this Mac (`local_draft`): the first commit subject made then is
  the title, and all of them a list. With the Claude Code switch on, Claude then drafts both (`claude_draft`, with the
  model chosen for "New task", Haiku by default: 5.7) from the same rows' steps (what was typed, Claude's replies,
  `askclaude.steps`), commits and session titles, with up to 8 titles of the person's own tasks of the last 30 days
  (their own time entries; the same project's first) as examples of the style: a task's name is anyone's words in
  Productive, so no other task's title goes to Claude. It runs as the session note
  does (5.7): no tools, no MCP server, none of the person's settings, auto memory off, a JSON schema. A draft replaces
  only a field the person has not typed in, and **Create in Productive** is off while Claude drafts and for a second
  after its draft fills the form, so a click never sends text that changed under it. A meeting's title is the draft
  when the time was a meeting and no work was seen in it; Claude is not asked then. The title goes through the note's
  checks (`guard_title`: no path, web address, token, code, tool's name, sum of money, look-alike or invisible
  character, or remark on the time, one sentence). The description is checked line by line the same way, cut at a
  sentence to 700 characters, and a sentence about the time itself ("the review time is logged as billable") is left
  out. Claude's title or description that names another client's project, or the person, is left out
  (`askclaude.foreign_name`, as for a note).
- **Status, estimate and service**, proposed for the place picked (`GET /api/task/fields`, `newtask.propose`), each
  with a line saying why, and editable. The form opens at once; they fill in when they arrive, and a status or service
  the person picked, or an estimate they typed, stays when the place changes (while the new place offers it). The
  rules come from what Productive showed on 7 November 2026 (4.2), on tasks created since July:
  - **Status**: a subtask starts in the workflow's first status (Backlog), a task in a task list or the project in
    "To Do". Of 36 subtasks created by people with the Staff role, 33 started in Backlog; of 26 top-level tasks, 18
    started in To Do and 8 in Backlog. (A shared QA account's 11 subtasks, made in QA or Client QA by a bot, are left
    out.) Closed statuses are not offered.
  - **Estimate**: empty when the parent carries one, since a subtask may then have none (fi-productive-process); else
    the median of the project's recent tasks of the same kind that have one (its subtasks for a subtask; its
    top-level tasks, containers such as core updates left out, otherwise), to the nearest 5 minutes, from the same
    read as the "no estimate" flag (`project_estimates`, kept a day); with fewer than 2 to go by, none, and none
    when that median is over 8 hours (`ISSUE_MAX`: a project whose tasks are phases of 10 to 100 hours would offer 60 h
    for one small fix). Typed as
    minutes ("90") or hours ("1h 30m", "1.5 h"), up to 500 hours; empty is allowed. Of the 200 subtasks created most
    recently in the organization (of 1,226 since 15 September), 13 of their 73 parents carried an estimate, and 160
    had none anywhere in the chain, so an empty estimate under a parent with none is allowed but said in the line.
    The budget's hours minus one for PM work (fi-productive-process) cannot be read: a Staff role sees no budget
    fields (4.2, flags).
  - **Service for this time**: the parent task's service when it takes time on that date; else, under a period's
    task, the budget's contingency line on that date ("Contingency", then "Service request" or "Support request",
    never "Overage" or "Rollover"; with two such budgets open, the one named for the month); else the only service
    that takes time then; else none. It is not sent with the task: once created, the log dialog picks it for the
    time. Of 64 entries one person logged to the 28 subtasks they had created, 57 went to "Service Request /
    Contingency", 3 to Rollover, 2 to Maintenance Updates, 1 to an upgrade's own line and 1 to an overage line; of
    the 48 recent subtasks whose parent had a service, 34 had the same one. The service a PM later set on the task
    itself differed from the entries' in 37 of the 47 entries where both are known, so the form says this one is for
    the time only.
- One line says what the form does not show: "Assigned to you." (and when the estimate typed is not a duration). The
  service's line ends "Used only for this time; a PM sets the task's own."

**Create in Productive** sends it (`POST /api/task/create`). Read-only and Preview refuse it before anything is sent,
and the form says so when it opens. The same title in the same project within a minute is refused: a second click
would create a second task. After an answer that is not clear (a timeout, a server error, Productive's MCP passing on
a failure behind it, a reply that is not its success sentence) the task may exist: the button stays off until the form
opens again, the form says to search for it first, and the server refuses the same title in that project for ten
minutes. A refusal is said in plain words and the form stays open. Once created, the form closes,
and the task is first in the dialog's list, under "Just created", and picked; its services load as for any task,
with the form's service picked.

**Local or Claude?** Measured on 14 ranges of one person's work (22 September to 2 November 2026) where a task was
really created from the session doing that work, each draft's title read against the title created. On the whole
Likely rows around it, as the dialog drafts: the commit subject gave a usable title for 3 of 14 (nothing for 9, a
wrong one for 2); Haiku for 7 (3 right, 4 close enough to edit; nothing for 2, vague or wrong for 5). The real title's
words found in the draft: 11% and 21%. Cut at the moment the task was created: 2 and 6 usable. In 4 ranges the work
in that time was not the task's work, so no draft could be right. The session's title as a fallback named the task
in none of 10 tries (a long session's first topic, or in another language), so it is never used. Haiku: $0.0008 to
$0.0032 a draft, 6 to 8 seconds. Hence the local draft at once and Haiku's after it, only with the switch on.

### 5.2 Writing (`POST /api/log`)

1. The day is read again from Productive: someone (or another tab) may have logged since.
2. An entry that would overlap another one is not written; the dialog shows the overlap, and the pieces around it
   when there are any (5.1), else turns the button into **Log anyway**. With `"pieces"` (the dialog's pieces, sent
   with the `start` and `minutes` they came from), the server reads the day again and writes nothing unless that read
   gives the same pieces: when the day changed since, the new overlap and pieces come back (`"changed": true`). Then
   each piece goes through steps 1 to 4 as its own entry, never forced; when one is refused after others were
   written, the answer lists what was logged and what was not (`notLogged`, `why`), and the next check offers only
   what is still free, so a piece is never logged twice. Pieces are never forced, never part of a Log the day run,
   and never sent with the key alone (section 3).
3. One write at a time (a lock), and the same entry sent twice within a minute is refused.
4. `create_entry` writes it as the person in Settings, with its start time. `"dry": true` does everything except the
   write (tests). Only an answer that names the new entry counts as logged: Productive's success sentence through
   Claude Code's connection, a JSON body with the new id through the token. Anything else after the request went out
   is "may or may not exist" (`ProductiveUnclear`), never "not logged": a timeout, an HTTP 5xx, a created answer
   without the entry in it, a failure Productive's MCP server passes on from behind it (a JSON-RPC error, or an error
   naming a 5xx, a timeout or a dropped connection), or a fault in Pulsar after sending.

### 5.2b Changing a logged entry

A logged entry's popover has **Edit** (the log dialog, in its own day: task, service, start, minutes, note),
**Delete** (asked first; Productive keeps it in its Recycle Bin) and **Open in Productive** (the task's page:
`https://app.productive.io/<org id>-<name>/tasks/<id>`). Productive's API gives the organization's id but not the
name part of that address, so it is taken from links in the person's own Claude sessions
(`sessions.productive_address`, the newest first, only links whose id is the organization's, only while the Likely
column may read the sessions); with none seen, there is no link rather than a guessed one. Its top and bottom edges can be dragged in 1-minute
steps (a drag in the empty column picks a stretch in 1-minute steps too); on release Pulsar asks "Change this entry?" before writing.
After a delete or an edge drag, the note at the bottom has **Undo** for 10 seconds: a drag goes back to its start and
length (`POST /api/entry/update`, with the entry as just changed as `seen`, and the overlap check); a delete logs the
entry again as it was (`POST /api/log`: a new entry, the deleted one stays in the Recycle Bin), except an entry with
no start or with a formatted note, which the page cannot write back as it was. `POST /api/entry/update` and `/api/entry/delete`
read the day afresh and refuse an entry that is no longer there. They show overlaps first (Change anyway), take the
same write lock as logging, and accept `"dry": true`. Both need `seen`, the entry as the page showed it with every
field (start, minutes, task, service, note): one left out would never be compared. A new start in the hour the clocks
repeat keeps the one of the two hours the entry is in; a start the clocks skip (02:30 the night they go forward) is
refused, here and in `POST /api/log`, since Productive would store it an hour later. "Approved" is not refused by Pulsar: where a budget needs no
approval, Productive approves every entry at once and its author can still change it; where an approver has approved
it, Productive refuses and the page shows why. With
`PULSAR_READ_ONLY` (any value but `preview`, which leaves it to Preview's setting) every change is refused at the
lowest level, like any write.

### 5.3 Ticket flags (`pulsar/flags.py`)

The log dialog (for the picked task, under the task list) and Log the day (under each row, and their number among the
chips of the summary) show one quiet line when Productive says something about a task that someone should fix there:

- **Closed in Productive**: the task is closed (its `closed_at`, read with the task).
- **No service takes time on 6 Nov: no budget for it**: no service of the task's project takes time on that date
  (`bookable_services` is empty).
- **No estimate, while 19 of the project's 55 recent tasks have one**: the task has none (or 0), and a quarter or more,
  and at least two, of the project's 100 most recently active tasks have one. Estimates are rare in most projects (on
  one person's 14 projects, 0 to 35% of the recent tasks had one), so flagging every task without one would put a flag
  on nearly every row.
- **+1 h 30 min over the estimate (5 h 30 min of 4 h)**: the time worked on the task by everyone (`worked_time`),
  plus the minutes about to be logged, is over its estimate (`initial_estimate`). The log dialog works it out itself
  from the task's numbers and follows the length as it is typed (an entry changed on its own task is in the time
  worked already); Log the day adds, per row, the plan's earlier rows on the same task (`flags.over_estimate`;
  `GET /api/services` takes `minutes=N`). A write drops the task from the 5-minute cache, so the next check reads
  the new time worked.

Each line links to the task in Productive (the address of 5.2b). Pulsar only reads: it never closes, estimates or
budgets anything, and a flag never unticks a row of Log the day. The closed state comes with the task Pulsar already
reads (`task_details`); the project's share is one more read per project (`productive.project_estimates`), kept in
`names.json` as `estimates:projects:<id>` and read again after a day. A failed read keeps the old answer and asks
again an hour later; with no answer kept, there is no flag. `GET /api/services` adds the flags only with `flags=1`
(the page): Claude Code's tools never pay for that read. Log the day reads each project once, before its rows.

**No flag for a budget nearly used up.** Productive gives a budget's totals and usage only to roles with "View
budgets where a user is a member" (`budgets_view_restricted`). For a Staff role, checked in November 2026 through
Pulsar's own read functions: budgets cannot be loaded at all, the budget and service reports are refused, and a
service's `budget_total`, `budget_used`, `budgeted_time`, `worked_time` and `quantity` come back empty.

### 5.4 Log the day (`pulsar/dayplan.py`, `web/js/logday.js`)

**Log the day** in the Logged column proposes the whole day at once and opens a review: the Likely column steps
aside (each row says why instead), the Logged column shows the proposed entries as dashed cards, and the side panel
becomes the list to review. Nothing is written until the person confirms.

**The plan** (`GET /api/dayplan?date=&source=`, `dayplan.gather` then the pure `dayplan.plan`). It reads the Likely
rows with their task links, learned tasks and any Ask Claude answer, the activity and away time, the calendar day and
its history, the repeats, the rules, 60 days of history, and the day's entries. Each minute is placed once, first
claim wins:

1. entries already logged;
2. meetings attended: a repeat whose call the tracker saw, a calendar meeting with a call seen, a call with no
   calendar entry. A repeat and a calendar meeting on the same call are one entry (when the repeat covers half of the
   meeting, so a repeat that runs on into the next meeting never swallows it); a Likely row inside a meeting gives
   it its minutes. A call that started early or ran over is that meeting: its free minutes right before and after
   go to it, up to the meeting's own length on each side ("the call ran 25 min over" in its why); the next
   meeting's minutes are not free, so meetings back to back each keep their own, and what is left of a longer call
   is a call of its own;
3. Likely rows at their own time, never on away minutes or time spent logging (Pulsar, Productive), in this order
   (parallel work, below): a client's work in front; then a client's agent that ran attended while internal work was
   in front (a **shared** row); then the rest of the work in front. The agents' other background time is noted, never
   placed ("Left out: pulsar 48 min (ran in parallel)");
4. on free active minutes only: calendar meetings with no call seen, and work outside Claude, an editor or terminal
   with no session row included (it takes the task of the work next to it, never a meeting's, and starts unticked).

**Parallel work.** Each stretch of work has a billing class (`attribution.segment_class`): from a rule, a task link or
a ticket key in the work itself, the class of that task; else the repository's 60 days of logged minutes (75% or more
billable and an hour or more: client; 75% or more not billable: internal; else mixed); else the class of the project
the repository's name points to; else unknown. Internal is a non-billable service or a project of the agency's own
(`FI-…`); fixed-price ongoing support is billable, so client. The task picked for the work never sets its class. A
billable minute needs evidence: the person there (a work window, not away, not personal, not logging) and either its
own work in front, or its agent attended (above) with internal work in front: then the client gets the minute first,
as a shared row, when the agent's task is a known client's task and the stretch runs 10 minutes or more (a shorter one
stays with the work in front; the client's own work in front between internal turns does not cut the stretch, so
internal work in short turns with it adds up; internal means the repository's class, or the task picked for a row of a
"mixed" repository). Mixed and unknown work gets no priority, and a client's work in front is never given to
another client's agent. Priority never adds a minute, never makes a task or service billable, never touches away
time. A shared row stays at its real time: it never joins other pieces, and nothing joins across it. Under the cap,
internal work is cut first, then mixed and unknown, a client's last. The **evidence cap**: a task that may be billable
(any not known to be internal) never gets more minutes than the person was seen at work on it, whatever the grid and
the joins did, and a client's entry is never joined across time away. Agent time moved into free slots (the skill
script's rows) is not used at all. Replayed on one person's 52 days (14 September to 4 November 2026), each planned as
if nothing were logged from the days before it only, against what was logged and against the planner before this:
billable minutes with no evidence 0 (464 before), over-billed minutes 3038 (3792), billable proposed on minutes not
logged 2457 (2987), wrong client 3711 minutes (4273), the right task at the right time 33.9% (35.0%), with each turn
named as the fi-time-from-activity script names it (4.4). With Pulsar's own weights instead it was 30.4%: QA hub
turns that run commands in clients' checkouts read as those clients' work. Shared rows were rare (45 minutes in the 52
days): most work in front had no settled class yet, or the client's agent was not attended.

Pieces of one task in one sitting (it ends after Away after with no activity) join, also across another task's pieces
when every one of them is under 10 minutes (two works by turns, a few minutes of each: one entry each, one after the
other, each its own minutes, within the time they took; but a turn of internal work under 10 minutes between two turns
of one client's work, in one sitting and within 30 minutes, is that client's, `3b'` in `_rows`, as the Likely column
shows the stretch (on 19 past days 65 more minutes had a task, the minutes per task right for 44.2% instead of 44.0%,
the right task at the right minute 48.0% instead of 48.8%: some of those turns had been logged on their own); before 1.6.8 the short turns folded into one entry that the
evidence cap cut, and on 9 November 65 of 114 minutes showed as not logged; on 19 past days 145 more minutes had a
task, the minutes per task were right for 42.6% instead of 41.3%, and the right task at the right minute 48.1%
instead of 49.3%, as turns laid one after the other move their minutes), pieces under 10 minutes join a
neighbour within 30 minutes in the same sitting
(never one hours away, which would move it there, never across an entry already logged; a meeting someone attended
keeps its own time, so nothing before it joins it), boundaries go on a 5-minute grid except right against an entry
already logged (an entry starts where that one ends, or stops where it starts, taking in the minutes up to the grid
instead of leaving them as a crumb; its other end stays on the grid), a personal window's minutes are
never in an entry, nothing overlaps an entry already logged, and **proposed plus logged never exceeds active time plus
attended meetings**: the rest is listed as not placed, never placed (what the cap leaves under 10 minutes goes with
it). The grid and the shortest entry follow Settings' **Round entries to** (`dayplan.opts_for`): 5 minutes keeps the
5-minute grid and the 10-minute shortest entry, 10 gives 10 and 10, 15 gives quarter hours and 15. Each row shows its
ticket flags (5.3). "Not covered" lists only stretches of a step or more (`uncovered`, `totals.notCovered`). A
row's task comes from, in this order: a rule, a task link pasted in the session (in a turn of that repository), a
ticket key in the title, the tasks logged in the same session in the same repository, the task of the repository's
project that the work names (`learn.work_match`, below; unticked, and never replaced by a new task), then the same
repository
(votes from the last 60 days, older days counting half every 7 days), the same session earlier that day, and task
names that share words with the work (your own name and common words such as "not" and "the" do not count). A
session's history applies per repository (`attribution.session_votes`): its votes from another repository count only
for a task this repository is logged to (a quarter of its votes or more, once it has an hour of them, `dayplan.fits`),
or when the session has been in this repository all day (`single`) and four fifths of its votes are one project's
(`attribution.one_project`); never in a repository with no history otherwise (2026-11-06: a session's client task
went to hours of work in a repository of the agency's own; 2026-11-09: a session that upgraded four clients'
databases in turn offered the first client's task on the fifth site). A path a command writes as `~/Sites/x` or
`$HOME/Sites/x` is that repository, as its full path is (`sessions.find_paths`). On internal work a
client's task is the pick only from a rule, a link or a ticket key in that work; on a client's repository, another
client's task from the session's history is never the pick; with no task the class admits, no task rather than a
wrong billable one (`dayplan.admissible`; such tasks stay among the alternatives). So QA hub work for a client goes to
that client when the work names the client's task. A task you never logged to that the work
names (5.5) is the first alternative, and the pick where the answer is unsure and not one you gave (a rule, a link, a
ticket key): a ticket key it names, or words it shares strongly (cosine 0.5). A repository you work in for an hour or
more and log under a tenth of is set aside as one you never log; days with entries that have no start time do not
count, since they cannot be matched to the work. Confidence: high, medium or low, in words and a three-bar meter; the
same session's votes are high from 60% (and 30 minutes), else low. **Unticked at the start**: low confidence, no task,
no service, meetings with no call seen, **Claude's pick of a task you did not log to in the last 30
days** (one it found by searching Productive), **a note Claude drafted**, and a task that already has time logged with
**no start time** that day: such an entry is on no timeline, so no overlap check sees it, and it may be the same work. The review
lists it ("no start"), the confirm step names it, and under the cap that task's proposals give way first, up to its
minutes, before the least sure rows. **Notes** are never written from session text: only a meeting's title, your usual
note for a repeat, a note Ask Claude drafted for that task (from the session's messages, and unticked), or one from your commits in the work, each with "clients
may see notes". **What goes into notes from the calendar**: anyone can send an invitation that Google puts on the
calendar by itself, and its organizer can rename it at any time. So a meeting's title becomes its note only when the
person or someone of their own company organized it (`calendar.outside`), and only through the note's checks
(`askclaude.guard_note`, read folded: no tool's name, sum of money, web address, look-alike or invisible character;
`calendar.note_of`). A title the checks refuse gives no note and leaves the row unticked ("The meeting's title was
not used as the note"), and so does a series whose title is not the one it had the last time you logged it
(`server.meeting_tasks` keeps that title as `lastTitle`; "This meeting's title changed since you last logged it"). An
event organized outside gives no note and keeps its tick: its task comes from what you logged during it before. The
log dialog's **Log this time** on a meeting fills the note in from the same `note`. The **service** is the one last used on the task, checked against the services that take time on that
date (`bookable_services`). With Productive unreadable, nothing is proposed. About 6 seconds for a day the first time,
under a second after.

**Ask Claude** (only when it is on in Settings: from **Check my day with Claude** in Log the day, which asks for
picks when the day has none yet and rows are unsure, then runs the review, 5.4c; its tooltip names each model and the
most a run may cost, `sgCheck` in `ldreview.js`; and from **Ask Claude** in the Likely column's header again since
1.6.8, picks only, shown on the blocks and first in the log dialog; nothing runs on opening; the same blocks asked
about again, also after a restart, give the kept answer and Claude is not run, `askclaude.kept` with the blocks it was
asked about; each block also carries Pulsar's own guesses, its first three learned tasks with why, `pulsar_guess`,
which Claude weighs before its 30 days of tasks and before searching Productive: on 9 November, without them, it took
the first task with the site's name in it for two blocks of other work): the review shows the plan at once, then sends the day to Claude
(every row of ten minutes or more) and fills in the answer as it arrives. Claude's pick replaces only rows the planner
is not sure of (medium, low), with the planner's guess kept as the first alternative; a row Claude found no task for
keeps the planner's guess and says so. Claude's pick starts ticked only when it is one of the tasks you logged to in
the last 30 days and its row carries no note of Claude's: what Claude reads (the session text, commit subjects) could
carry a prompt injection that steers it to another task it can find in Productive, or into the note, so such a row
waits for the person (the review says why: "Claude found this task by searching Productive…", "Claude drafted the
note…"; picking another task clears the first, editing the note the second's message; ticking it is the person's). On the two days with kept answers (5 and 6 November
2026), 1 of Claude's 30 picks with a task was one not logged to in the 30 days before, and 25 carried a note. An empty answer is asked once more. Rows the person changed meanwhile are
left alone. An answer that arrives while the confirm step is open waits until it closes (Back to the review); once a
run has started it is only mentioned. Haiku did as well as Sonnet here, at a small part of the cost.

**Kept while the page is open.** Closing the panel or going to another day keeps what the person did there, per
day (`ldkeep.js`): ticks, tasks, services, times, notes, the rows opened and the rows deleted from the proposal, and
the review's suggestions with their state. Opening the day again puts them back on a fresh plan, row by row by what
each row was proposed for (the day, its start as proposed, its repository or meeting, its task as proposed), never by
its place in the list. A row's work is dropped when that row is no longer proposed or its time is logged in Productive
now; a row a run wrote stays as the journal says. **Start over** forgets it all for that day. It lives in the page
only: a reload starts again. The review itself is kept on the server too (8): opening the panel asks for it with
`kept_only` (never a new run), and **Check again** asks Claude anew (`again`). A row's note is drafted by Claude only
when its empty note field gets the focus, never by opening the row; the server keeps each draft (8), so the same work
is not drafted twice, after a restart either.

**Reviewing.** The list shows, by default (**To review**), the rows still to log and the time not covered; what is
logged, in Productive or by this run, is one line at the top ("6 h 1 min logged, 17 entries"), and **All** shows each
entry in its place (the choice is kept in this window's storage). The timeline shows everything either way. A row needs a look when it is unsure ("check"), has no task or service, or carries something Claude did
(a task it found by searching, a note it drafted, no task found). The "N to check" chip counts those not yet looked
at: a row counts as checked once the person has opened it and it is ticked (one with no task or service cannot be
ticked, so it stays until it has them). The chip opens the next such row after the open one, in time order, and reads
"All checked" when none is left (`ldNeedsLook`, `ldCheck` in `logday.js`). **Tick all N with a task** ticks every row
that has a task and a service, except one whose task Claude found by searching Productive (not one you logged to in the
last 30 days) that nobody opened: that rule stays, for the reason above, and the panel says how many it held back.
Enter on the open row (its line or its tick box) ticks it and opens the next row still to log; on a row with no task
or service it opens the task picker. The confirm step is the check for all of them: it lists every entry with its
note, marks a note Claude drafted ("Claude's note:"), a task Claude found by searching, and a shared row ("Shared: its
agent ran while you did internal work."). **Shared rows** start ticked, with a short note ("Client work went first:
its agent ran while you did internal work. Check it.") and a choice: **Client** (as proposed), **Half each** (the first
half the client's, the second half the internal work's, both at their real time) or **Internal** (the stretch goes to
the work that was in front, `sharedWith`, with its service). The choice is kept with the day's work (`ldkeep.js`
`ldChoice`). A **Parallel** chip says how long agents ran while you did other work; its tooltip, how much of it went
to a client first and how much was left out.

**Writing.** The confirm step shows the totals, overlaps, unopened rows marked "check", and active time left
unlogged; **Dry run** sends every row with `"dry": true` and shows exactly what would be sent. The confirm step keeps
the exact request of each row it lists, and **Log** sends those, not the rows as they may be a moment later; while a
run writes, the rows cannot be opened, ticked or deleted. Then the rows go through `POST /api/log`, the same path as the log dialog (a fresh read of the day, the overlap check, the write lock),
strictly one at a time, in time order, each with the run's name (`ld-<date>-<random>`) and the row's. The page stops
at the first answer that is not "logged" (refused, with the reason, or unknown) and offers to try the rest again.

**The journal** (`~/.cache/pulsar/logday-journal.json`, owner-only). Before each write the server records the row as
`sending`, then `logged` with the new entry's id, `refused` with the reason, or `unknown` when no clear answer came
(`productive.ProductiveUnclear`: a network error, an HTTP 5xx or an answer without the new entry after the write was
sent, 5.2; the answer then says
`"unclear": true`, and so does any unexpected error, so neither is read as "not logged"). A row that a run of that day
already sent is refused when it is `sending` or `unknown`, and when it is `logged` while its entry is still among the
day's entries just read: one deleted by hand, or moved to another day, can be logged again (a read that failed counts
as still there). So a restart, a second tab or a retry never writes it twice. An entry created whose `logged` the
journal could not take is still answered as created, with a warning; its row stays `sending`. A journal that is there
but cannot be read is never taken for an empty one: nothing is proposed or written from Log the day or Claude Code,
and Logs says which file to look at. **Undo this run** (asked first) deletes, one at a time through
`POST /api/entry/delete` with the run's name, only the entries that run created, and only if each is still as it
wrote it. It tries every one: an entry deleted or moved by hand is marked `gone`, one changed by hand `changed`
(neither is offered again), one Productive refuses stays offered, and the toast says how many were deleted and why
the rest were left. **Start over** clears the last run's results and Undo. Runs that started more than 31 days ago
are dropped (counted from today, never from an entry's date).

Replayed on one person's 34 fully logged days (21 September to 4 November 2026, `tools/replay/`), each day planned
as if nothing were logged, from the days before it only, and compared with what was logged: 45% of the minutes
needed no change; the right task in the right amount for 49% of the logged minutes; the right task at the right time
for 38% of them (37% of the proposed minutes). By confidence, right at that time and right somewhere that day: high
67% and 95%, medium 29% and 72%, low 24% and 54%. The rows ticked at the start were right that day for 94% of their
minutes. Repeats were the surest source (78% at the right time), calendar meetings the weakest (16%), so most start
unticked. Most of the rest is tasks new that day (14% of the logged minutes), sessions that moved between tasks, and
time logged when the tracker saw nothing (8%).

### 5.4b Catching up on earlier days (`web/js/catchup.js`)

**Earlier days…** in Log the day's title row lists the last 7 workdays before today that are not done (6: marked
done, or their logged time reached the target and nobody said not done), less the time marked "not work" (Monday to Friday; **Show 7
earlier days** gives 14), each with the active time no entry covers, and its logged and active time, from
`/api/report` (the Report's own numbers). Time logged with no start time is on no timeline, so it counts against the
active time not covered (as Log the day caps a day): a day logged in full from Productive's timesheet reads "nothing
left". Days with 30 minutes or more left start ticked; a day with under 5 cannot be ticked. When no tracker gave any
activity for those days, the list says why (the Report's `trackerWarning`), and its days read "no activity read",
not "nothing left". **Review N days** opens the oldest first: the Day view moves to it and Log the day opens on it exactly as if
opened there by hand, with its own plan, review, confirm step, journal and Undo. A line under the summary says which
day of how many it is and how much this catch-up has logged so far (what each day's runs logged, less what Undo took
back), with **Next day** (**Finish** on the last). The catch-up writes nothing itself: each day is written only from
its own confirm step, one entry at a time, and moving on writes nothing. Closing Log the day, or going to another
day, ends it.

### 5.4c Log the day with Claude (`pulsar/dayreview.py`, `web/js/ldreview.js`, experimental)

A second look at Log the day's plan, the way a Claude Code session looks at a day when it is asked to log it, with
FI's rules for where time goes. Everything here is a suggestion under a row (or, for the whole day, above the list),
with **Accept** and **Reject**. Accepting changes the row in the review as a change by hand would; nothing is written
until Log the day's own confirm step, one entry at a time, journalled, with Undo (5.4).

**What the sessions did that Log the day did not.** 104 logging requests in Claude Code sessions (1 October to 6
November 2026, read only from the transcripts), compared with the replay of Log the day on the same days:

| Decision | How often | What it changed |
|---|---|---|
| Asked a question before writing | 59 requests | after 36 the person changed something; the questions: which task 18, agent time to add 13, minutes 6, a closed task or a new period 4 |
| Searched Productive for the task | 46 | the task; 11.7% of the logged minutes went to tasks created that day, 3.2% to tasks never logged to before |
| Created a task, mostly a subtask of the period's contingency task | 12 (21 tasks) | always: the time went there |
| Chose between period tasks (Aug/Sep, Oct/Nov), noticed a closed task | 15 and 7 | the task; the plan picked a task closed before the day for 3.7% of the logged minutes |
| Read services or budgets | 24 | Productive refused 7 writes (a closed or ended budget, no access); 89% of the minutes kept the service used last on the task, 9.6% were on a task with none yet |
| Estimates to the estimations task, internal work to internal tasks | 15 | 4.2% of the logged minutes were another internal task in the plan |
| Split one block across several tasks or sites | 8 | the minutes per task |
| Said the day's total against the time worked | 34 | nothing directly; Log the day shows it already |
| Split a meeting by topic | 2 | rare |

**FI's rules in code** (`fi_rules`, with the plan: `GET /api/dayplan` returns `suggestions`). On this Mac, no
Claude, read only:
- A task whose title names a period (a month, two months, a quarter: "Oct/Nov 2026", "2026-09/10", "2026/Q4") used
  on a day outside it: the same task of the day's period (same project, same title but for the period and its
  ticket key), open on the day, starts as a sure suggestion; with none, and when it is a period's container
  (contingency, core updates, ongoing support, PM), a task to create for the day's period, in the same form
  ("Aug/Sep" then "Oct/Nov"): FI creates each period's task by hand, and a new period's is often missing.
- A task closed before the day (by its closing date in the recent tasks, never by "closed now"): the open task of the
  same name in the project when it was made again, else a question. Not the next alternative: on the replay those
  were right for 75 of 595 minutes.
- `service_by_kind`: for a task with no service used before, the line its kind goes to (contingency to "Service
  Request / Contingency", core updates to the maintenance line, PM, meetings), never an overage or rollover line
  while another fits. A subtask's parent is not read, so this only knows the containers by their own names.

On 36 replayed days (1 October to 5 November 2026) the period rule never fired: the plan did not pick a past
period's task. The closed-task rule asked about 15 rows (650 minutes), and the plan's task was wrong for all of them.

**The digest** (`digest`): what Pulsar knows of the day, as compact JSON for Claude, at most about 15,000 tokens
(4 characters a token): the totals (active, meetings, logged, proposed, not covered, over the day, the target), away
stretches of 10 minutes or more, activity by hour by kind (editor or terminal, browser, chat, call, logging, other,
personal counted) with up to three app names per hour, the plan's rows (time, task, project, service, budget,
billable, confidence, why, flags, ticket flags, three alternatives with why, the project's services, the note and
where it came from), what Pulsar could not place, the Likely work (time, repository, session titles, up to eight
steps of what was typed and Claude's reply, `askclaude.steps`, up to eight commit subjects, learned tasks with why,
linked tasks), meetings (time, title, declined, their tasks), entries already logged (time, task, service, note),
repeats, the tasks logged to in the last 30 days (with the service used last, up to 60) and the period tasks open
on the day in those projects (up to 30). Never in it: window titles, sites, addresses, the names of personal
windows, the rule words. Steps about logging time are left out: they say nothing about the work and on a past day
they would give the answer away. Over the budget, in this order: steps cut to 8, 4, then 2 per block (the newest
kept), replies to 200 characters, the tasks list to 30 and the apps per hour left out, then only what was typed.
Measured on the 30 replayed days with work: 4,600 to 25,400 tokens uncut, 4,600 to 15,000 after the cut; the steps
are most of it (up to 17,000 tokens), the tasks lists about 3,700.
**What goes to Anthropic** is the prompt: FI's rules and the digest. Unlike Ask Claude's prompt it holds the
meetings' titles, the apps' names by hour and the notes of the entries already logged that day; like it, never a
window's title, a site or an address.

**The review** (`review`, `POST /api/dayplan/review`, the page's session only; the key alone is refused). It runs
the person's own Claude Code with Ask Claude's isolation (5.5): `claude -p --no-session-persistence --tools ""
--permission-mode dontAsk --strict-mcp-config --setting-sources "" --json-schema`, Productive's read tools only,
auto memory off, its own model and cap in Settings > Claude (`review`, Haiku by default: nearly as good as Sonnet
on the replay below, at an eighth of the cost). The prompt is FI's rules (from the
fi-productive-process and fi-time-from-activity skills, written without names), what each kind of suggestion means
and the digest, which it is told is data. Kinds: another task, another service (by name, from the row's list),
minutes, a split (up to four parts that add up to the row), a task to create (title, project, parent, one-line
description), a question, leave the row out. Every suggestion is checked here before the page sees it
(`validate`), and one that fails is dropped, never repaired: its row must be one of the plan's, a task must be one
the digest named or one Productive names now (`task_details`), the page shows Pulsar's name for it, never Claude's
words; minutes inside the day, a split that adds up, a service on the row's list; a reason or a question is plain
text with no link and no invisible character (the page shows it as text), a new task's title and description pass
the note's checks (`guard_note`), since they would go to Productive. What was dropped is said in Logs. The page
matches each suggestion to its row by the row's start and task, so a row changed since shows its suggestion as
"the row changed since".

**Measured** on five replayed days (8, 15, 28 and 29 October, 3 November 2026; 2,839 logged minutes with a start
time), each planned as if nothing were logged, Claude with no tools at all (it could name only tasks the digest
names), every suggestion of the kinds counted taken as accepted (`tools/replay/review_replay.py`):

| | Right task at the right time | Right task, right amount | Cost a day | Time |
|---|---|---|---|---|
| Log the day | 23.5% | 34.4% | | |
| Plan only (no digest), Haiku, tasks and services | 27.6% | 39.6% | $0.009 | |
| Plan only, Sonnet | 27.6% | 39.2% | $0.071 | 10-15 s |
| Digest, Haiku, tasks and services | 33.7% | 50.2% | $0.018 | 70-100 s |
| Digest, Sonnet, tasks and services | 35.7% | 51.7% | $0.154 | 30-40 s |
| Digest, Sonnet, all suggestions (minutes and leave out too) | 30.0% | 45.4% | | |

Every task suggestion with the digest was on a row whose task was wrong (22 of 22 with Sonnet, 18 of 18 with
Haiku), and most questions were too (12 of 17, 17 of 24). Minutes suggestions cut the day: with all of Sonnet's,
2,227 minutes proposed against 2,839 logged; the person logs more than the time at the keys on days with agents
working, so they are suggestions to read, not to accept in a batch. The service was right for 94% to 95% of the
minutes with the right task, before and after. No task to create was suggested with the digest. Claude Code's own
prompt and the schema are about 15,000 tokens of each run's 28,000 to 32,000. Five days of one person: a direction,
not a verdict.

### 5.5 Where the suggestions come from

- **Rules** (Settings or the **Rule** button on an Activity window): words in a window's app, title, workspace, site
  or address that mean a task. They colour the Activity blocks and come first when logging that time.
- **Task links** (`sessions.task_links`): a Productive task address you typed or pasted into a Claude session
  (`app.productive.io/…/tasks/<id>`), in the three hours before a Likely row or during it. Only typed messages count,
  never what a tool returned. Older links were too often about other work.
- **Learned tasks** (`pulsar/learn.py`): for each Likely row, the task you logged while working in the same Claude
  session, then in the same repository, over the last 60 days. Each earlier row votes, with its minutes, for the task
  that took most of it. Older logging counts less: a day's votes halve every 7 days (`HALF_LIFE`, as in
  `dayplan.py`), which sets the order; the "why" shows the minutes actually logged. A session's history applies per
  repository, as in Log the day (5.4): its votes earned in this repository first; its other votes only for a task
  this repository is logged to, or when the session has been here all day. Each logged stretch teaches the unit
  whose repository is logged to that task's project, not always the one in front (`attribution.credits`): while
  internal work was on screen and a client's agent ran in the background, the person logged the client, and that
  teaches the client's repository and session, never the internal one (a repository of Pulsar's own had learned a
  client's task this way). With no history to tell them apart, the row in front learns, as before. The stored days
  keep the background rows for this. Repositories with two names are one (`attribution.REPO_ALIASES`: chronodeck,
  Pulsar's first name, and pulsar-preview are pulsar).
- **Tasks you never logged to** (`learn.new_tasks`, after Aidan's habit of looking up all the tickets): 14% of one
  person's logged minutes, and 41% of the client work, went to tasks with no entry in the 60 days before, which the
  votes cannot know. The pool is the tasks worked on in the last 30 days (`productive.recent_tasks`, one read kept ten
  minutes, `server.new_task_source`), of them the ones that are not closed before the day and not created after it,
  and are assigned to you or new in the last 30 days in a project you logged to in the last 30. The row's evidence is
  its sessions' titles, what you typed and Claude's last words of each step (`askclaude.steps`) and your commit
  subjects. A task whose ticket key (`UTFLM-271`) or number (`#728`) the work names comes first, unless the first is
  the same session's vote; else up to two whose title and project share words with the work (TF-IDF, cosine 0.5 or
  more) take the fifth place, before the task found by its notes. On the replay of 34 days (21 September to 4
  November 2026), the logged task was among the dialog's first five for 54.8% of the entries instead of 52.7%, and
  for 579 of the 3,746 minutes of new tasks instead of 364; Log the day had 378 of those minutes right instead of 289,
  the right task at the right time overall 37.8% instead of 37.3%, and no group of entries got worse by more than 0.1
  point. Below cosine 0.5, the first one found was the logged task for 1% of the minutes; at the second place they
  pushed out tasks you had logged before (3 points fewer in the first five). Most of the gain needs the task to exist
  when you log: counting only tasks created before the day, the first five held 389 minutes, not 579.
- **What you are doing, first** (`learn.work_match`, `learn.with_work`; a later row of the same session in the same
  repository that names nothing goes on with the task named earlier that day, rows under five minutes too, which are
  not read for words): the Productive projects of the row's
  repository (a quarter of its votes or more, once it has an hour of them; with less history, the projects of tasks
  whose names name the repository) narrow the tasks to that project's: the ones you logged to in the 60 days and the
  ones in the new-task pool, not closed. Of those, the task whose name shares most words with the work (its sessions'
  titles, what you typed, your commit subjects; TF-IDF over those names only, cosine 0.3 or more) goes first in the
  dialog's list, and one almost as good (90%) with it, yours (logged to or assigned) before the other. With fewer than
  three words in common it goes just after the same session's own vote instead. In Log the day it comes after the
  session's vote and before the repository's history. On the replay of 19 days (13 October to 6 November 2026) the
  dialog's first task was the logged one for 33.3% of the Likely blocks instead of 30.8%, and among the first three for
  47.2% instead of 44.6%; Log the day had 49.3% of the minutes right instead of 49.4%. Placed first everywhere, with
  any number of words, it was worse: 27.2% first and 44.0% of the minutes (one shared word such as "page" or "qa" in a
  project with many tasks).
- **A task found by its words** (`learn.text_match`): after the first four learned tasks, the fifth place goes to
  the task whose name, project and your own notes on it (the 60 days of entries already read) share most words with
  the row's work: its sessions' titles, what you typed in them during the row (`askclaude.steps`: up to 15 messages
  of 400 characters) and your commit subjects (Notes from commits). The words are compared as TF-IDF vectors
  (cosine); common English and Ukrainian words and your own name do not count; a task's score halves every 7 days
  since you last logged it. With fewer than four votes, more tasks found by words fill the list. The why names up to
  three shared words. Nothing new is kept on disk: the notes are in memory with the history, and the session text is
  read when a day is shown (about 3 seconds the first time, under 0.1 s after; a refresh re-reads only the session
  files that changed, about 1 s for a 300 MB one).
  Past days' rows are worked out once in the background and kept in
  `~/.cache/pulsar/likely-days/<date>.json`: times, repository names and a hash of each session title, never the
  titles or anything typed. Only a day that is over is kept, worked out after it ended: a proposal the page saw
  while its day was still today is worked out again the next time it is asked for, in memory and for learning
  (format 2 of these files; a format-1 file could hold a day cut off at the hour it was seen, so they are redone once).
  A day worked out while no tracker answered (ActivityWatch not up yet at login) is marked `blind` and worked out
  again once ActivityWatch answers or MemTime's database is there. Until all 60 days are there, the page says "Learning your tasks: N of 60". The rows do
  not wait for the 60 days of Productive history: while it is read, they come without learned tasks and with
  `learnPending`, and the page asks once more (`wait=1`) a few seconds later. Nor do they wait for their hints, the
  tasks linked in their sessions and the commits made during them (every session of the day read and git run: 1.5
  to 4.5 seconds, measured on two days): the page's first ask (`quick=1`) gets the rows with `hintsPending` while
  they are worked out in the background, and its second ask gets them. Every other caller (Ask Claude, Log the day,
  `pulsar --mcp`) waits for them.
- **The clock**: the fi-time-from-activity script writes wall-clock times; they are kept as minutes elapsed since
  midnight, like the built-in rows, and drawn by the clock. On the two days a year the clocks change, the page gets
  everything by the clock (`server.clock_spans`): a stretch of activity or away time across the change is cut there
  and each piece keeps its real length (the autumn hour no longer gives negative minutes, the spring one no longer an
  hour too many), and a Likely row keeps its start by the clock and its real length (`page_span`). Learning and Ask
  Claude work on the elapsed rows, as entries are; Ask Claude's picks go back to the page keyed by the start it shows,
  the times it gives Claude are clock times, and the session note finds its rows by the page's own times.
- **Ask Claude** (`pulsar/askclaude.py`, off until switched on in Settings): runs the person's own Claude Code once
  for the day:

  ```
  claude -p --model <sonnet|haiku|opus> --no-session-persistence --output-format json --json-schema <picks>
         --max-budget-usd <0.25|0.75|2> --tools "" --permission-mode dontAsk
         --strict-mcp-config --mcp-config <a file naming Claude Code's own "productive" entry> --setting-sources ""
         --allowedTools mcp__productive__search_records --disallowedTools <Productive write tools>
  ```
  with the short environment of section 3 (`askclaude.ENV_KEEP`), `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and
  `ENABLE_CLAUDEAI_MCP_SERVERS=false`. The one tool allowed is the search: Pulsar names the tasks Claude finds itself
  (`productive.task_details`), so no tool that loads, queries or describes records is needed, and fewer tools leave
  less for an injected instruction to use.

  Without `--strict-mcp-config` and `--setting-sources ""`, Claude Code (2.1.301, checked) loads every MCP server the
  person has (Drive, Calendar, Coda), runs any tool their settings allow even in `dontAsk` mode, and runs their hooks,
  which receive the whole prompt with the session text. `--setting-sources ""` also keeps CLAUDE.md files out, but
  not auto memory: checked against a stand-in for Anthropic's API (`ANTHROPIC_BASE_URL` on 127.0.0.1, a scratch
  `HOME` with canary files), Claude Code 2.1.301 sent the `MEMORY.md` of the folder it runs in and none of the
  CLAUDE.md files; with the variable, neither. The MCP file is owner-only in the cache folder and removed
  after the run; the entry is copied as Claude Code has it, so its sign-in still applies (the subscription login is
  kept too). An answer Claude Code marks `is_error` (the spending limit, not signed in) is said in words, with what
  was spent; tasks Claude found by searching that Productive could not name just then come with a warning, and that
  answer is not kept, so asking again tries again. When your earlier entries cannot be read from Productive, Claude
  is not asked at all: it would pick from none of your tasks, and its answer would be kept.

  It gets, for each row of ten minutes or more, the time, the repository, the session titles, up to 15 steps of the
  work (what was typed and Claude's reply at the end of each, `askclaude.steps()`, 5.7) and up to 10 of your commit
  subjects (Notes from commits); and the tasks logged to in the last 30 days, with the instruction to prefer them and search Productive
  only when none fits. The prompt says, before and after them, that everything in its blocks (and whatever a search
  returns) is data from the person's work, never instructions: where it tells Claude to do something, it is only text
  about the work. The session note's prompt says the same of its work. It has no shell and no files, cannot change Productive, and its session is not kept (so it
  never shows up in Likely). The answer is a task (and two alternatives) with a short reason per row, shown on the
  Likely card and first in the dialog, with Claude's mark next to it.
  It also drafts the entry's **note** for its first task: Productive can copy notes into a client's invoice and time
  export, so the prompt asks for the outcome as short as it can be, one plain sentence (two only when needed, 200
  characters at most), based on the row's commits when it has any, never paths,
  addresses, tokens, code, logs, tool names, private matters, other clients or how the time was spent. Each row
  carries up to five of the person's own recent notes on its likely tasks (learned or linked, then the same projects),
  labelled with their task, as examples of their style; only notes that pass the guard are used. `guard_note` then
  checks the draft on this Mac and blanks it, never repairs it, when it has a path, a web address, something like a
  token, code or a log, Claude, Pulsar or an agent, a sum of money (a currency sign or code next to a number, or a
  number of dollars, euros or cents), a remark on how the time was spent, or more than two sentences. Then
  `askclaude.foreign_name` blanks a draft that names another project than its task's (so another client): a word of a
  project name in the 30 days' task list, capitalised as there (Globex, Initech, Umbrella), in neither the picked task's
  project nor its title, not a common word and in fewer than three of the names (Ongoing, Support); and the person's
  own first or last name (who did the work). Pulsar has no list of Productive's people, so other people's names are
  only asked against, in the prompt. For the session note, which has no task yet, the projects of the block's likely
  tasks and those named like its repository are its own, and the projects of the person's history are the others.
  A note written for a task that could not be named, and any note from an older Claude Code without
  `structured_output`, is dropped. The log dialog fills the note in with "Drafted by Claude. Clients may see notes." under
  it; the line goes when the note is edited, and names the task the note was written
  for when another task is picked. The model is chosen in Settings (Sonnet by
  default; 5.7); each name is Claude Code's alias for the latest model of that kind. About 15 to 60 seconds and $0.10 to
  $0.25 of the person's usage per day with Sonnet. `--max-budget-usd` caps each run (not each day) at $0.25 for
  Haiku, $0.75 for Sonnet and $2 for Opus; an empty answer runs Claude once more, so one ask may cost twice the cap,
  and the cost shown is what both runs cost together. Log the day asks by itself when the switch is on (5.4). Answers are kept in
  memory (the last 30), and each day's last answer also on disk (`askclaude.keep`: `~/.cache/pulsar/claude-picks/`,
  owner-only, 60 days: task ids, reasons and the drafted notes, which are made from session text), so the day shows
  its picks again after a restart and Log the day can use them; with the switch off, neither the page nor Log the
  day reads them. Asking again after a restart, or once the day's rows changed (a new row, more commits), runs Claude
  again.
  **What goes to Anthropic** is exactly the prompt: for each row of ten minutes or more its time (by the clock),
  repository, up to 3 session titles, up to 15 steps (typed 400, reply 900 characters), up to 10 commit subjects and
  up to 5 of the person's earlier notes; and up to 150 tasks logged to in the last 30 days (id, title, project,
  minutes, last day). Plus what Claude reads from Productive while it searches. No window titles, apps, sites or
  meetings.
- **A meeting's task** (`server.meeting_tasks`): first, the task you logged during the earlier meetings of the same
  series (Google's `recurringEventId`) or the same title, each earlier meeting voting for the task that took most of
  it; then your tasks whose names share a word with the meeting's title. Words every meeting has ("sync", "weekly",
  "meeting"…) and your own name do not count.
- **Repeats** (`server.repeating`, `server.repeat_suggestions`): a task you logged at the same time of day (within 5
  minutes) on at least 3 days of the last 28, and on at least half of the days you logged it at all, still seen in
  the last 10 days. On a weekday it happened before, when the tracker sees a call (a Zoom meeting window, a Meet or
  Teams call, a Slack huddle, Webex) within 75 minutes of the usual time, the Logged column shows a dashed card.
  The call runs from its first window to its last, windows up to 20 minutes apart, at most one and a half times the
  usual length. A start within 10 minutes of the usual time, or of an hour either side (clock changes), is snapped
  to it. The card disappears once that task has an entry that day.

How well they guess, on 30 days of one person's logging (337 Likely rows, 1 October to 5 November 2026), each day
learned from the 60 days before it only, the right task being the one logged over most of the row: learned tasks had
it first for 43% of the minutes and in their first five for 57% (39% and 52% before older days counted less). For
rows whose session worked in two or more repositories that day, 51% and 63% (51% and 60% before). The "lately" list
alone: 29% and 56%.

With the task found by its words in the fifth place, tuned on the first 15 of those days and checked on the other
15: the right task was in the first five for 59% of the checked days' minutes instead of 52%. It was first for 38%,
the same as before, and no group of rows did worse. For rows whose session had no votes, it was in the first five
for 20% of the minutes instead of 3%. Over all 30 days: 45% first and 66% in the first five. A weighted vote that let
words outrank votes did better on the 15 days it was tuned on (60% first) but worse on the other 15 (32% first), so
votes keep the first four places. Claude's replies, git branch names, ticket numbers (ABC-123, #56) and client names
matched to project names were measured as well. None of them added anything to the words above. A fifth of the
minutes went to tasks logged for the first time that day: no learning from past days can name those.

### 5.6 Pulsar in Claude Code (`pulsar/mcp.py`, `pulsar/mcptools.py`, experimental)

`pulsar.py --mcp [--port 5610]` is an MCP server on stdio (JSON-RPC, standard library) that a Claude Code session
starts: `claude mcp add --scope user pulsar -- /usr/bin/python3 -E -s -B <path>/pulsar.py --mcp --port 5610` (the
switch in 5.7 does this). It is a thin proxy: it computes nothing from the person's data. Each tool call goes to
`POST /api/mcp/<tool>` on the running Pulsar with the key, and the server (`pulsar/mcptools.py`) builds the answer,
keeps the preview's code and summary, and writes. The proxy holds the JSON-RPC protocol and the tools' definitions,
says which app is asking (pulsar_log works only from Claude Code), and passes on the calling session's id
(`CLAUDE_CODE_SESSION_ID`) and folder (`CLAUDE_PROJECT_DIR`). Before every call it checks `/api/whoami` for its own
port (section 3), so a program on the port, or one passing the question on to a Pulsar on another port, never sees the
key. Pulsar must be open: when it is not, every tool fails with "Pulsar is not answering ... Open Pulsar, then ask
again", except `pulsar_status`. `pulsar_log` never says that once it reached Pulsar: it waits up to five minutes
(`mcp.LOG_WAIT`), and an answer that does not come, or a fault in Pulsar, is "Not known: Pulsar may have written some
of these entries, or still be writing them; read pulsar_day before logging them again". An older Pulsar (no `/api/mcp/` routes) gets "older than them": quit and open it again.
A Claude Code session started before an update keeps the older proxy, whose reads the key no longer opens: start a
new session.

| Tool | What it gives |
|---|---|
| `pulsar_status` | never fails: whether Pulsar answers, its version and the MCP's, whether writes are blocked and why, whether `pulsar_log` works from this app, today's date, time and time zone |
| `pulsar_day` | the day in numbers: logged, active stretches with no entry, Likely by project, meetings (times only), repeats |
| `pulsar_propose` | Log the day's proposal (5.4): start, minutes, task, service, confidence, why, alternatives; a shared row with `shared`, `sharedWith` (the work that was in front: task, service, repository) and `choice`; `leftOut` (agent time that ran in parallel, by repository) and `totals.parallel`, `shared`, `leftOut`. `prefer` (client, half, internal) re-plans every shared stretch that way in this answer only (read only) |
| `pulsar_session` | the calling session's own minutes (Claude Code gives every MCP server `CLAUDE_CODE_SESSION_ID`; Likely rows carry their sessions' ids), by repository, with a task for each and what is not logged yet; `givenAway`: a row's minutes another session's client agent got as a shared row (never in `notLoggedYet`); a background row (`shared`) for the minutes this session's agent got that way; stretches another session also had are kept apart, and so are stretches whose task already has time logged that day with no start time (`maybeLoggedWithoutStart`, with a warning: no overlap check sees such time, and it may be this work) |
| `pulsar_suggest` | the log dialog's task list for one stretch |
| `pulsar_tasks` | tasks logged to lately (with the service used last), assigned ones, and a Productive search |
| `pulsar_preview` | read only: services resolved, notes checked (`guard_note`), every entry through `server.api_log` with `dry` (overlaps, the journal); with no problem, a code of 16 hex characters (once, 10 minutes) holding exactly those entries, and `summary`, Pulsar's own text of what would be written, one line per entry: `Wed 4 Nov 2026 09:00-10:00 (60 min): Build (Acme), Dev in B, billable. Note: "Search fixed."` (an entry over a shared row of the day's plan says so before the note: "Its agent ran while you did internal work."; when the task already has time logged that day with no start time, the line ends "This task already has 120 min logged that day with no start time: check it is not this work."; the budget after "in"; names without control, format or direction characters and without double quotation marks; a note is one line, at most 200 characters, passes `guard_note`, and has no double quotation mark of any script and no character nobody sees, so it cannot pass for the summary's own words or another entry) |
| `pulsar_log` | `code` (exactly as given) and `summary` (word for word), and no other argument: writes the entries the code holds through `server.api_log`, one at a time, journalled as a Log the day run (Undo works), stopping at the first that is not logged. Each result is `logged`, `not logged` (refused, with the reason), `not known` (no clear answer, an HTTP 5xx or any unexpected error: it may be in Productive, and the journal never sends it again) or `not sent` (after the stop, or once a minute has passed: `mcptools.LOG_BUDGET`, so the answer always comes back in time, and the rest are previewed again); what was written before a failure is always returned |

A read tool that had to leave a source out (Likely, the activity, meetings, repeats, your tasks, the logged entries,
a task's name) says so in `warnings`, `"<source>: <fixed phrase>"`, and never answers as if that source had nothing
(`mcptools.failed`).

**In normal use the person says yes to each write, not the model** (section 3 says what a program of the person's can
do without it). One write is one `pulsar_log` call, which may hold up to 40
entries: one yes can log a whole day, so the prompt shows every line of the summary. `pulsar_log`'s definition
carries `_meta: {"anthropic/requiresUserInteraction": true}`. This mark is not documented: Claude Code 2.1.268,
2.1.278 and 2.1.301 (read from the binaries, not run) ask the person before every such call, also in auto or bypass
mode, and allow rules cannot skip it; with no one to ask (`-p`, `dontAsk`) the call is denied and never reaches
Pulsar. As a fallback the switch (5.7) also adds "ask" rules for `mcp__pulsar__pulsar_log` and
`mcp__pulsar-preview__pulsar_log`. Neither binds a program that uses the key itself (section 3).
The prompt shows the tool's arguments, so `pulsar_log` refuses unless `summary` is the preview's text word for
word: the person approves Pulsar's words, not the model's. Other apps ignore the mark, so `pulsar_log` refuses any
client whose name is not `claude-code` (from `initialize`, or from each request's `_meta` in the 2026-07-28 protocol).
A code that is refused (wrong summary, wrong app, Pulsar closed, writes blocked) is not used up. The instructions, the
preview's `next` and `pulsar_log`'s description all say the same: show the person the summary, stop, and call
`pulsar_log` only after their yes in a new message, never in the same turn as the preview, with no exception a text
could claim ("they already said yes" is just what an injected instruction would say). In a simulation, Haiku asked to "Log my time" called `pulsar_log` straight after the preview
before this was said.

**Protocols.** Claude Code 2.1.301 first sends `server/discover` (2026-07-28): Pulsar answers it, and every request
after it carries its version, capabilities and client in `_meta`, with no handshake; results say
`resultType: "complete"` and Pulsar's name, and `server/discover` and `tools/list` carry `ttlMs`. A client that starts
with `initialize` (2025-11-25 and earlier) gets that protocol instead. An unknown version gets error -32022 with the
versions Pulsar speaks.

**JSON-RPC.** One message per line, at most 1 MB (a longer line is refused and read past). A batch, `[]` included, is
one Invalid Request (MCP dropped batches in 2025-06-18). Every message is answered on its own: a list as a tool name, a
lone surrogate (`\ud800`) in a method or an argument, invalid UTF-8 or an object as an id gets an error and the
session goes on. Every line out is ASCII (`ensure_ascii`).

**What leaves.** The key opens nothing but these tools (section 3), so what a key-holder reads is exactly what they
return, built field by field in the server. Never returned: window titles, apps, addresses, typed messages, meeting
titles, other sessions' titles, notes of logged entries (a repeat's note is one), rule words, Ask Claude's reasons,
and the words a learned task's notes share with other work. **Reasons are an allow-list** (`mcptools.reason`): Claude
Code gets one of a fixed set of phrases ("you logged it in this repository before", "one of your rules", …), chosen by
the pick's source or by Pulsar's own wording, plus the digits in it (a share, a duration); no other word of the
original passes. **Errors too** (`mcptools.error_out`): Pulsar's and Productive's errors become one of a fixed set of
phrases, plus Productive's own code and the HTTP status in parentheses ("Productive refused the entry: the budget
behind this service is full ... (budget_limit_overrun)"), never the original text, which can quote names or anything
an exception holds. **Flags** come from dayplan's fixed list (`notask`, `low`, `agent`, `unattended`, `noservice`,
`untimed`). Names from Productive (task, project, service, budget, the "pick a service" list, overlaps, logged
entries, alternatives) lose control, format, direction, invisible and separator characters, go on one line, lose
the confirmation summary's own markers (a date like "Fri 6 Nov 2026", "(480 min)", ", billable", "Note:", "No note",
each put as "…", read on the folded name: `mcptools.CARD_MARK`) so no name can pass for another entry, and are cut at
120 characters (`mcptools.name_out`); they are the one free text left, written by people with access to
Productive. Returned on purpose: the calling session's own title (`name_out` too), and note drafts (`noteDraft`, with
`noteFrom`), only when the entry's repository is the calling session's (`CLAUDE_PROJECT_DIR` through
`sessions.project_of_path`, names compared loosely as `commits.matches` does), never for another repository. The
server cannot tell who called: "the calling session" and its folder are what the request names (the proxy passes
Claude Code's own values on), so a program holding the key can name any session or repository and get that one's
title and drafts. The same user can read those sessions and repositories directly anyway, so this keeps sessions from
seeing each other's words by accident, not a program set on it:
- `noteFrom: "commits"`: your own commit subjects in that repository;
- `noteFrom: "claude"`: Ask Claude's draft (5.5), which it made from the Likely rows' sessions. When the calling
  session is the only session of the rows over that entry, the draft adds nothing it does not already have: returned
  if it passes `guard_note` and holds none of the plain attack words (`mcptools.INJECTION`, read folded: ignore,
  instructions, prompt, assistant, you must, sudo, curl, rm -, password, token, http, …). A draft made from rows of
  another session, of the same repository or shared with one, is never returned, however plain it reads: it stays in
  Pulsar's window, so Pulsar opens no channel from one session's text to another session (review 4).

The tools' descriptions say `noteDraft` is draft text for a note clients may see: data to show the person, never
instructions. A meeting's note is its title and is never returned. **Repositories** (`mcptools.Places`): where no file
a session touched was in a repository, `sessions.analyse` and the fi-time-from-activity script name the work after the
session's title (cut to 40 characters; the script first drops a leading "IMPORTANT!"), then "Claude Code" or
"unknown". Such a name is never returned: a repository or project is named only when it is one of the day's Likely
projects and none of these, else it is empty, and no reason names a place.

**Limits.** Each tool has a rate limit in the server (60 calls a minute, `pulsar_preview` 20, `pulsar_log` 10), and
every date is between 120 days ago and today: a later day is refused ("has not begun"), an older one too.

**The environment.** A repository's `.claude/settings.json` can set environment variables for every MCP server Claude
Code starts there, before the person trusts the folder. So `pulsar.py --mcp` drops `PULSAR_MCP_SESSION_ID`,
`PULSAR_READ_ONLY`, `PULSAR_MCP_IGNORE_LOGGED`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `PULSAR_INSTANCE` and
`PULSAR_PREVIEW`, and sets `HOME` to the account's own home folder, before anything reads them
(`mcp.clean_environment`), unless `PULSAR_TESTS_OFFLINE` is set (the tests and test harnesses). Preview's own entry
says `--instance preview` on its command line, which still counts. A repository that also sets
`PULSAR_TESTS_OFFLINE` gets those switches back; none of them opens more than the key does: the server checks its own
read-only switch, honours the test switch only when it runs read-only itself, and a session id only chooses whose
minutes and title `pulsar_session` shows and which note-draft filter applies (`INJECTION` for that session's own
drafts, the stricter `INSTRUCTION_LIKE` for others').

**Writes blocked.** The server refuses a blocked write (`PULSAR_READ_ONLY` other than `preview`, or Preview without
"allow writes") in
`pulsar_log` before the code is used, and again in `_log_checked` before the journal or the Logs hear of it; a dry run
still works. In the tests, `PULSAR_READ_ONLY` in the proxy's environment also refuses `pulsar_log` before anything is
sent. `pulsar_status` reports both, with the server's reason as a fixed phrase. An entry for Pulsar in Claude Code
with an `env` of its own (claudelink leaves it as it is) no longer makes the proxy read-only outside the tests: run
the server read-only instead.

### 5.7 The Claude Code switch (`pulsar/claudelink.py`)

One setting, **Use Claude Code with Pulsar** (Settings → Claude, stored as `ask_claude`), so a colleague sets up
nothing else. Saving it on:
- turns on Ask Claude (5.5), the session note in the log dialog (below) and Claude's draft of a new task (5.1);
- adds Pulsar to Claude Code: `claude mcp remove` (Pulsar's older entry, another path or port), then
  `claude mcp add --scope user pulsar -- /usr/bin/python3 -E -s -B <dir>/pulsar.py --mcp --port <port>`. An entry
  named `pulsar` that Pulsar did not add (no `pulsar.py --mcp`, or an `env` of its own, such as `PULSAR_READ_ONLY`)
  is left as it is, and the switch says so. An
  installed Pulsar is started through `~/Library/Application Support/Pulsar/current`, so updates keep it working;
  Pulsar Preview adds itself as `pulsar-preview` on its own port, with `--instance preview` so the server reads
  Preview's key;
- adds rules to Claude Code's user settings (`~/.claude/settings.json`, or `$CLAUDE_CONFIG_DIR/settings.json`): an
  "ask" rule for `pulsar_log` of both Pulsars (5.6), and the "deny" rules of section 3 that keep Claude Code's file
  tools out of Pulsar's folders. The file is read, merged and renamed into place (a linked file stays linked, and
  keeps its permissions), with a one-time backup `settings.json.pulsar-backup`; a file that is not valid JSON is left
  alone and the switch says so. Rules already there are not counted as added, so they are never Pulsar's to take back.

**What Pulsar asks Claude, and with which model** (Settings → Claude, `pulsar/claudeuses.py`). Settings lists every
use, one row each, from `claudeuses.USES`: its name, what it does, what it sends to Anthropic (folded under "What it
sends"), and a model to choose (Haiku, Sonnet or Opus, the default marked) where Pulsar runs Claude itself:

| Use (id) | What it does | What it sends | Model by default | Most one run may cost (Haiku, Sonnet, Opus) |
|---|---|---|---|---|
| Check my day: task picks (`ask`) | A task and a note for each row Log the day is unsure of, before the review; also shown on the Likely cards (5.4, 5.5) | Each block's time, repository, session titles, steps (typed and Claude's replies), commit subjects, up to 5 of your notes; your tasks of the last 30 days. May search Productive, read only | Sonnet | $0.25, $0.75, $2 |
| Note (`note`) | The note for a block or a stretch (below), and for a row of Log the day whose empty note is clicked | That work's time, repository, steps, commit subjects, up to 5 of your notes | Haiku | $0.10, $0.30, $0.80 |
| New task (`task`) | A new task's title and description, from the log dialog or a row of Log the day (5.1) | That work's time, repository, session titles, steps, commit subjects, up to 8 titles of your recent tasks | Haiku | $0.10, $0.30, $0.80 |
| Check my day: review (`review`) | Reviews the day's plan with FI's rules: suggestions to accept or reject (5.4c) | The day's digest (5.4c): the plan, steps, session titles, commit subjects, meeting titles, app names by hour, notes of logged entries, your tasks of the last 30 days; may search Productive, read only | Haiku | $0.25, $0.75, $2 |
| Pulsar in Claude Code (`session`) | A session reads the day and logs through Pulsar (5.6) | What the session reads from Pulsar, as part of that session | the session's own | none of Pulsar's |

Each `claude -p` call takes its model and cap from there and nothing else changes: its isolation (`--tools ""`,
`--strict-mcp-config`, `--setting-sources ""`, `dontAsk`, the environment of section 3, the JSON schema) and the
guards on what comes back stay at the call. The server picks the model with `claudeuses.model(use, settings)`: the
person's choice in `claude_models` (`{use: model}`), then, for Ask Claude only, `ask_claude_model` (its one setting
before there was a list), then the default. Models are Claude Code's aliases only, never a pinned model id. A run's
cap (`--max-budget-usd`, `claudeuses.cap`) is the use's Haiku cap times 1, 3 or 8 (`SCALE`), so a larger model is not
stopped half way. Saving keeps only choices that differ from the default (a default changed later applies), drops an
unknown use or name, and keeps `ask_claude_model` in step for an older Pulsar. Pulsar Preview starts with its own
choices. Each row also shows the runs of that use since Pulsar started and what they cost, as Claude Code said
(`total_cost_usd`, counted in `askclaude._answer`, in memory only). Pulsar's other runs of the `claude` command ask no
model: `claude mcp add`, `remove` and `get` (the switch, below) and `claude mcp get productive` (renewing Claude
Code's Productive sign-in, 4.2). A new use is one entry in `USES`; the page lists it by itself.

**Ask before any Claude Code session changes Productive** (Settings → Claude, `claude_ask_productive`, off by default)
adds "ask" rules for the `create_resource`, `update_resource`, `delete_resource` and `perform_action` tools of the
Productive server named `productive`. They bind every Claude Code session on the Mac, not only Pulsar's, so every
session asks before each such change: the person's choice, said so under the box. Pulsar 1.3 and 1.4 added them
with the switch; `claudelink.tidy`, when Pulsar starts and whenever Settings are saved, takes back the ones this copy's own
list says it added unless the box is on, and updates the list (Pulsar and Preview each tidy their own). A rule
Pulsar did not add is never removed, and a later Save with the switch on does not put them back. The box does nothing
while the switch is off: it cannot be ticked then, and one ticked earlier says it waits for the switch.

Saving the switch off removes Pulsar from Claude Code and only the rules Pulsar added (`claude_rules_added` and
`claude_denies_added` in Pulsar's settings). The `pulsar_log` and deny rules are one set for Pulsar and Preview: while
the other is still in Claude Code they stay, and this copy keeps them on its list to take back later; its Productive
rules go. When Claude Code's `.claude.json` cannot be read, whether the other is still there is not known: Pulsar asks
Claude Code to remove itself, keeps the rules and says so. Settings shows where things stand from `.claude.json` and
the settings file, read only, and offers **Add Pulsar to Claude Code again** when the entry is missing, starts another
copy, or rules are missing (a copy switched on before the deny rules existed shows this once). A read-only run
(`PULSAR_READ_ONLY`, any value but `preview`) never changes Claude Code.

**What keeps it from applying**, said in Settings (`claudelink.status`, read only): an organization's managed
settings (`/Library/Application Support/ClaudeCode/managed-settings.json` and its `managed-settings.d`, the
`com.anthropic.claudecode` profiles in `/Library/Managed Preferences`, and the server's copy `remote-settings.json`
in Claude Code's folder) with `allowManagedPermissionRulesOnly` (Pulsar's rules are ignored) or
`allowManagedMcpServersOnly` (Pulsar's server is not started); and `CLAUDE_CONFIG_DIR` when Pulsar's own environment
has it (Pulsar then reads and writes that folder; a Claude Code started without it does not see them). **The
account**: which Claude account Claude Code is signed in with, and its plan, from `oauthAccount` in `.claude.json`
(no keychain): for Pro and Max, consumer plans, Settings says Anthropic may use these chats for training if that
setting is on for the account, and links to Claude Code's data-usage page; Team, Enterprise and an API key are
commercial.

**The session note.** With the switch on, **Log this time** on a Likely block, or a stretch (the yellow rail, a drag,
**+ Log**, **Log and next**, an Activity block; 5.1), shows the commit draft at once, then asks `POST /api/likely/note`
with its start, end and project (for a stretch, the project with most minutes in it). The server takes that
project's Likely rows that overlap the time, whole rows. `askclaude.steps()` reads their sessions (matched by title)
for each step's typed message and Claude's last words before the next message (its summary of what it did; no side
chains, tool calls or tool output; at most 15 steps, 400 and 900 characters), with the commits and up to five of the
person's own notes as examples. `claude -p --model <haiku unless chosen> --tools "" --strict-mcp-config --setting-sources "" --disallowedTools "mcp__*" --json-schema`, with the environment of section 3, drafts the note
(English unless their notes are in another language); `guard_note` checks it. It replaces the note only while the
person has not typed in it, and only in the dialog that asked, with "Drafted by Claude from this session and your
commits"; for a stretch "from the fi-drupal-qa session", adding "(the most minutes here; also pulsar)" when other
projects worked in it too. Measured: 6 to 11 seconds, $0.001 to $0.004 a note. Asking a copy of the session itself
(`claude -p --resume <id> --fork-session`) also works but reads the whole session each time ($0.16 to $0.55 a
question) and cannot tell when inside it the work happened.

## 6. The rest of the day view

- **Where a day opens**: today with the now line in the lower third of the view; another day at the start of its
  first long run of work (activity, entries and meetings less than 15 minutes apart, 30 minutes or more of them), so
  a stray minute at 00:30 does not decide it.
- **The clock**: the now line moves every minute. At midnight, or when the window comes back on a new day (a Mac
  that slept), a person who was on today goes to the new today; another day stays where it is. Back in the window
  after five minutes or more, today is read again. A move waits while a dialog is open or Log the day is writing
  (`tickClock` in `day.js`). Besides that, today's activity is read again every minute (`refreshToday`, `ACT_EVERY`), also
while the window is hidden, and drawn then too, so the time not logged, the Dock badge and the menu bar line keep up
(2026-11-09: at 14:20 the column still ended at 14:01, the last read); while hidden, today's entries every 10 minutes;
and the whole day every hour while the page shows. The app's window sends `pulsar:wake`
  after a sleep or at midnight; the page takes it (`preventDefault`) and does the same. `window.pulsarUnsaved()`
  says in a sentence what quitting would lose (Settings not saved, a note typed in the log dialog, Log the day
  writing), for the window to ask before ⌘Q. The zoom menu keeps hidden `#zoom button[data-v]` for the window's
  View menu.
- **The week** (under the day's name): "This week: X h logged of Y h active", Monday to Friday up to today, from
  `/api/report` for that week (kept 15 minutes) with the day on screen as its Logged column shows it now. A click
  lists each day; a day opens it. Where the header is narrow it reads "Week: X h of Y h".
- **The next step**: while active time is not logged, **Log the day** is the Logged column's main (filled) button,
  and "Active, not logged" in Work time is a link that opens it. The header keeps the day's controls: one zoom menu
  (the time each row shows), one ↻ for everything; the error log is in Settings → Updates, with a small **!** in
  the header only while there are errors of the last 24 hours that came after **Mark as seen** in the log
  (`logs_seen` in the settings; the log itself keeps every one). The Logged column's title shortens before its
  buttons ever cover it.
- **Day done, and time that was not work** (`pulsar/daymarks.py`, `~/.cache/pulsar/day-marks.json`, on this Mac
  only, never sent to Productive, 120 days). A day is done when its logged time reaches the daily target, unless the
  person says **Not done**, or when they say **Mark the day done** (Work time). A done day's yellow rails are grey,
  "Active, not logged" is quiet, Log the day is not the main button, and Earlier days leaves it out. **Not work**, in
  the log dialog opened on a stretch or on a Log the day row, marks that time as not work: it counts nowhere (no rail,
  not in "Active, not logged", not proposed by Log the day, less in Earlier days) and shows as a quiet hatched rail; a
  click on it, or Undo in the note at the bottom, counts it as work again. `GET /api/daymarks?date=` (or `from=&to=`),
  `POST /api/daymarks` `{date, done | undone | add: [a, b] | remove: [a, b] | nolearn: id | learn: id}`, the page's
  session only.
- **Not an example** (the same file, `noLearn`: entry ids per day): a logged entry's popover marks it so Pulsar never
  learns from it, for time moved by hand (internal work put on billable hours, or the other way) or logged to the
  right task at the wrong time. `server.history_rows` leaves such entries out, so the repeats, the meetings' tasks,
  Log the day and the Likely rows (`learn.annotate`, which also drops the day's own) all skip them. The entry shows a
  ⊘; **Learn from it again**, or Undo in the note, takes the mark back. The entry itself stays as it is in Productive.
- **In Pulsar's own window** (1.6 and later sets `window.PULSAR_APP`; `theme-early.js` then marks `<html>` with
  `in-app`): Settings → Time and activity has **This Mac**, the window's own choices, applied at once through
  `pulsarNative.setNativePrefs` and shown again on each `pulsar:prefs` event: the Dock badge, the end-of-day reminder
  and its time, and Open at Login (when macOS needs approval, a button opens Login Items; on macOS 12, the steps to
  do it by hand). The page leaves out what the window does itself: the wordmark. The theme button stays (1.6.0 hid it,
  and a theme chosen before then could be changed only from View > Appearance, which was hard to find); Settings >
  Appearance sets the theme too. The menu bar line takes today's numbers from the day view each time it draws today
  (`native.js`), and reads `/api/report` itself every 10 minutes only while another day or the Report is on screen. Every dialog
  keeps its ✕ (Escape, ⌘W and a click on the backdrop close them too, as the ✕ does; the sign-in notice stays); buttons keep the arrow cursor, and the main button and focus rings take the Mac's
  accent colour (`--mac-accent`). In a browser none of this shows. Everywhere: a Delete asks with Cancel first and a
  red Delete; the logo pulses only while the day is being read (`html.loading`); ↻ plays one short warp while it reloads;
  `prefers-contrast: more` strengthens secondary text and lines; segmented controls say which is on (`aria-pressed`).
  The Mac's own formats, from `PULSAR_APP.prefs`: 12-hour times on screen (`hm` in `core.js`; the axis reads "10 PM"
  and "10:30"), while every request and every time field keeps 24-hour HH:MM (`hm24`), and the fields also take
  "2:30 pm" (`startHHMM`); dates in English in the order of the Mac's region (`DATE_LOCALE`: en-US writes "Friday,
  November 6"; British English for a region whose language is not English); the date picker's weeks from the Mac's
  first weekday (`datepick.js`). Times the server writes into messages (an overlap's start) stay 24-hour.
- **First run**: when Productive is not connected (or does not know who you are), no tracker answers, or today has
  no activity yet, a short list at the top of the board says what is missing and gives the one action that fixes
  it (Connect Productive, Get or Open ActivityWatch).
- **Yellow rail**: stretches of at least one rounding step (Settings' **Round entries to**, 5 minutes by default),
  mostly active (personal time excluded), with no entry; a shorter stretch is rounding, not work to log, and counts
  nowhere. The active share of the rails' minutes is "Active, not logged" (the Report still counts every uncovered
  active minute). It is an amber line on the Logged column's left edge, so it runs beside the Likely column's
  right edge. A click, or Enter, opens the log dialog on the stretch with its Likely tasks and note (5.1, "A
  stretch"). Closing the dialog gives the rail its focus back; closed from the keyboard (Escape, ⌘↩), the rail shows
  the focus ring, a blue outline around the amber line, until something else is clicked.
- **Hatched rail**: a pause, away or off (under an hour, up to three hours, longer), on the Activity column's left
  edge, named with its length where no card covers it. A click only says what it is: away time is never proposed
  for logging; an entry for it is added by hand.
- **Dragging in the Logged column**: from an empty spot, up or down, in 1-minute steps (across entries too); letting
  go opens the log dialog on that stretch with those minutes, never rounded, which says what the stretch holds and
  offers a split when it mixes work
  (5.1).
- **Work time**: logged, billable, not billable, billing unknown, active, personal, active not logged, away (breaks),
  against the daily target (Settings, else the organization's working day in Productive, else 8 h).
- **Total project time**: per project, then budget and service.
- **Tasks against estimates**: each task logged today, with everyone's time on it against its estimate
  (`task_details`, cached 5 minutes).
- **Refreshing**: ↻ reloads everything and asks Google and the Likely proposal afresh. Today's activity refreshes by itself every minute (also while the window is hidden), the rest once an hour while the page is visible.
- **The warp**: an update plays a short gravitational-lens animation. In the window it is native
  (a snapshot of the page bent by Core Image's hole distortion, animated by Core Animation on the graphics card, over
  the page's own background colour, so the live page never shows round the shrunken picture or through its hole:
  during an update that page is already the new version, loading); in a
  browser it is a CSS version. It is off with Reduce motion, or in Settings. Taking the snapshot comes first (50 to 70
  ms), so the page waits that much more before it lets go, and the window lets go only once the pull is all the way
  in (it used to let go at about 60% of it). The data that comes back while the warp pulls in is drawn once, when it
  is all the way in, so the page it lets go of is the new one.

## 7. The report (`pulsar/report.py`, `web/js/report.js`)

Any range up to 93 days. Productive and the trackers are read at the same time. For each day, the tracker that saw
more of it is used. It shows logged against active time by day (billable, not billable and unknown stacked), time by
project, top apps and sites, projects by day, when in the week you work (a heatmap by hour), every task against its
estimate, and a table of every day. A day's bar and its row in the tables open that day in the Day view.
**Download** gives the days or the entries as CSV (values starting with `=`,
`+`, `-` or `@` are escaped, so a spreadsheet never runs them) or a print view that saves as PDF.

## 8. What is kept, and where

| Where | What |
|---|---|
| `~/.config/pulsar/settings.json` | Settings and rules. Each field is checked when read; a damaged file is kept aside as `settings.broken-<hash>.json` and replaced by defaults. |
| `~/.config/pulsar/secret` | This user's key: proves the launcher, signs `/api/whoami`. |
| (memory only) | SHA-256 of each signed-in browser's token, for as long as that server runs. Older versions kept them in `~/.config/pulsar/sessions.json`, which start-up now removes. |
| `~/.config/pulsar/signin-code` | The one-time code (two minutes). |
| `~/.config/pulsar/google_client.json` | Only on the releaser's Mac: the real Google client. |
| Keychain, service `pulsar` | `productive` (API token), `google-calendar` (refresh token). |
| `~/.cache/pulsar/days/<person>/` | The last copy of each day's entries (120 days). |
| `~/.cache/pulsar/names.json` | Names of projects, budgets, services, tasks; billing types; how many of a project's recent tasks have an estimate (5.3); when each was read. |
| `~/.cache/pulsar/likely-days/` | Past days' Likely rows for learning: times, repository, title hashes. |
| `~/.cache/pulsar/likely-full/` | Past days' Likely columns as the page shows them (rows with session titles and first messages), so a day opened again, or after a restart, shows at once; 31 days; ↻ works a day out again. Never a day worked out while no tracker answered. |
| `~/.cache/pulsar/claude-picks/` | Ask Claude's last answer per day (`<date>.json`: task ids, reasons, drafted notes), shown again when the day is opened, never asked again by itself; the notes Claude drafted for a stretch of work (`<date>.notes.json`, under the stretch, its repository and what its session and commits held, so the same work is never drafted twice); the day's last review (`<date>.review.json`, under the plan it reviewed, `server.review_key`, given back while the plan is the same). 60 days. |
| (the page, while it is open) | Log the day's work per day: ticks, tasks, services, times, notes, the rows opened and deleted, the review's suggestions with their state (`ldkeep.js`). |
| `~/.cache/pulsar/day-marks.json` | Days marked done (or not done) and time marked "not work", in minutes of the day (6); 120 days. |
| `~/.cache/pulsar/logday-journal.json` | Log the day: each row a run sent, before it was sent, and what came of it (31 days). |
| `~/.cache/pulsar/logs/` | The error log, one JSON line per event, 31 days; tokens, keys, OAuth codes, signed addresses, fields named as keys or secrets, bare hex secrets, UUIDs and long opaque strings removed. |
| `~/Library/Application Support/Pulsar/` | The installed code: `versions/`, `current`, `previous`, `bad-versions`, and `seen-release` (the newest signed `latest.json` believed: its version and expiry). Only code a release signed gets here (section 2). |
| `~/Library/Logs/Pulsar.log` | What the launcher and the server printed. |
| `~/Library/Application Support/activitywatch/aw-watcher-afk/aw-watcher-afk.toml.pulsar-<date-time>.bak` | ActivityWatch's file as it was before each Fix (4.1). Pulsar never removes it. |

Every file Pulsar writes is readable by its owner only. Settings and caches are written atomically (a temporary file, then a rename), so a reader never sees half a file; the error log is appended to.

## 9. Updates

```mermaid
sequenceDiagram
  participant S as Server (installed copy)
  participant GH as GitHub releases
  S->>GH: latest.json and latest.json.sig (at start, every hour, on wake)
  S->>S: verify the signature against PUBLIC_KEYS, and that it has not expired
  S->>S: versions list → how many releases behind
  Note over S: the page shows the version in blue, at 2 or more behind, a reminder every 2 days
  S->>GH: on Update: pulsar-X.Y.Z.zip (at most 30 MB)
  S->>S: SHA-256 = latest.json, unpack (at most 60 MB) into versions/X.Y.Z
  S->>S: run its self-test (60 s)
  S->>S: current → X.Y.Z, previous → the running one, restart in place
```

- **Only signed releases.** `latest.json` (`{version, url, sha256, notes, published, expires, versions, app}`) is signed
  with an ECDSA P-256 key that only the maintainer holds, and checked with the Mac's own `/usr/bin/openssl` against
  `PUBLIC_KEYS` in `pulsar/release_sig.py`. A manifest that does not verify is ignored, so even someone with write
  access to the repository cannot ship code to installed copies. The list has room for a reserve key.
- **Only fresh ones.** `expires` is 60 days after the release (`MANIFEST_DAYS`), signed with the rest. A
  `latest.json` past it, or without one, is ignored, and Settings says so ("The latest release's description
  expired on …"; a warning in the error log once a day). Without it, an old signed `latest.json` served again in
  place of a newer one would hide every later release for good. The cost: with no release for 60 days, installed
  copies stop offering updates until the next one, so release at least every 60 days.
- **Never an older one.** Within those 60 days an installed copy also refuses a signed `latest.json` older than the
  newest it has already believed (its version, then its `expires`; kept in `seen-release` in the install folder) or
  than its own version (it came from a release at least that new), and Settings says so ("The update server sent an
  older release description…"; a warning in the error log once a day). So an old manifest served again cannot hide
  a newer release, nor offer a downgrade, which is never installed anyway. A copy run from source keeps the record in
  memory only.
- **Never automatic.** A check only looks; installing needs the **Update** button. A stolen release key alone cannot
  reach everyone's Mac the same hour.
- **Downloads** come only from `github.com/fosterinteractive/pulsar/releases/download/` (the repository must stay
  public). The zip must match the signed SHA-256, have one top folder, and no path may leave it.
- **A version that fails** its self-test twice in a row is marked bad and never tried again; the running copy stays.
  A self-test that could not finish (a timeout on a busy or sleeping Mac, its own or one of its requests to the
  page, exit status 75) marks nothing: the next Update tries again.
- **The restart** keeps the same arguments and port. The page polls `/api/version` and notices the new version. It
  does not reload itself (section 3): it drops its token, and in the app the window runs the launcher, which checks
  the new server holds this user's key and signs the page in again. The warp holds over the page meanwhile and lets
  go once the new data is in. In a browser the page asks to be opened again from the app icon.
- **The window shell** (Pulsar.app itself, the window and the launcher) does not update this way. A release that
  needs a newer one says so in its signed `latest.json`: `app: {version, min, url, sha256}`, where `min` is
  `MIN_APP` in `pulsar/appupdate.py` and `sha256` that of the release's Pulsar.zip. The page compares `min` with the
  version the window injects (`window.PULSAR_APP`; a window of 1.5 or older injects none, and counts as older) and
  says so on Settings' own Update button (`web/js/native.js`; once a day also in a toast): **Update to X and
  restart** when a newer release is there too, **Update and restart** when only the app is behind, **Restart
  Pulsar** once the new app waits in Applications (a window of 1.5 or older reads **Update the app** and asks for
  Cmd+Q). After a click on Update the page remembers it (`sessionStorage` `pulsar-update-app`) and, once the new code
  has loaded, does the app as well by itself: one click, one more restart. Only after such a click,
  `POST /api/update/app` (page session only; `pulsar/appupdate.py`):
  1. refuses in Preview, from source, for an app that is not `com.fosterinteractive.pulsar`, for one macOS runs
     translocated, and for a version that did not start here before;
  2. when this account cannot change the app or its folder, downloads nothing and answers README.md's install line
     for the page to show;
  3. downloads Pulsar.zip from the release only (`releases/download/v<version>/Pulsar.zip`, at most 30 MB), checks
     its SHA-256 against the signed `latest.json` and its own signature (`Pulsar.zip.sig`, the release key, as the
     install line checks it);
  4. unpacks it with `ditto` into a hidden folder beside the app (same disk), and requires one `Pulsar.app` of
     Pulsar's bundle id and the version named, with its program, that passes `codesign --verify --deep --strict`,
     and whose code a release signed (`release_sig.check_bundle` with this copy's keys);
  5. swaps the two apps in one step (`renamex_np`, `RENAME_SWAP`): the new one at the app's path, the old one kept in
     `.Pulsar-previous` beside it; the state goes in `app-reinstall.json` in the install folder;
  6. starts a helper on its own (`appupdate.finish`), which waits for the running window to quit (the new window of
     1.6.0 on quits by itself after the click; an older one asks the person to press Cmd+Q), opens the new app, and
     waits two minutes for its page to say it started (`POST /api/app/started` with its version). Then the old copy
     goes. Otherwise the new app is quit, the old one swapped back and opened, and that version is not offered again.

  Nothing in it is automatic, and nothing changes before every check has passed; a failure leaves no folder behind.
- **Test switches** (`PULSAR_UPDATE_URL`, `PULSAR_UPDATE_PUBKEY`) are ignored by an installed copy.

### Releasing (`macos/release.sh`)

1. It signs only `main` as it is on GitHub: HEAD must be `origin/main` (fetched) and the tree clean, at the start
   and again right before the first signature (where only the version it set may differ), so what protects `main` on
   GitHub also decides what the release key signs. It refuses a version that is not newer, a version with no
   `CHANGELOG.md` entry, and failing tests.
2. It sets `__version__`, zips the tracked files (`pulsar.py`, `README.md`, `CHANGELOG.md`, `pulsar/`, `web/`), with
   the real Google client in place of the placeholder.
3. It writes `latest.json` with every released version (from the git tags) and `expires` 60 days ahead, signs it,
   and checks the signature and the date the way installed copies will.
4. It builds Pulsar.app and Pulsar.zip (`build_app.sh`: the Swift window for Apple Silicon and Intel, ad-hoc signed),
   with `PULSAR_BUNDLE_SIGNING_KEY` set to the same key: the app's `app.manifest.json` (the code's version and each
   file's SHA-256) is signed and checked with the launcher's own copy of `release_sig.py` before the app is sealed. An
   app built without the key installs nothing (section 2). With an encrypted key, openssl asks for the passphrase
   twice: once for `latest.json`, once for the app.
5. It signs Pulsar.zip itself (`Pulsar.zip.sig`, the same key) and checks it the way README.md's install line will:
   the public key written in that line must be one of `PUBLIC_KEYS` and must verify it.
6. The release is published as `vX.Y.Z` with five files: the code zip, `latest.json` and its signature, Pulsar.zip
   and its signature.
   Releases are immutable once published. The title carries the minor version's pulsar name (`pulsar/names.py`).

### What the maintainer must do (by hand, not in code)

The signatures above hold only as long as the key and the repository do. These steps are for Nick, or whoever
publishes; none of them is done yet unless noted in section 13.

1. **Protect `main`** on GitHub: a branch protection rule (or ruleset) that requires a pull request and a review, and
   blocks force pushes and deletion. Releases are built from `main`, so a change pushed there straight would ship.
2. **Limit who can release.** Give write access to the repository only to people who publish; others get read or
   triage. Restrict tag creation for `v*` (a tag ruleset) to maintainers, and keep "immutable releases" switched on.
   Anyone with write access can otherwise publish a release with a Pulsar.zip of their own: installed copies ignore
   it, and README.md's install line refuses it (no valid `Pulsar.zip.sig`), but a colleague who downloads Pulsar.zip
   in a browser would install it.
3. **Create the reserve key** (`macos/README.md`, "The release keys"), add its public half to `PUBLIC_KEYS` in
   `pulsar/release_sig.py`, and ship a release with it, before more people install. A key can only be added by a
   release signed with a key already listed. **Done 2026-11-07**: kept encrypted in the publisher's LastPass, listed
   from 1.5.1.
4. **Move the release key to Touch ID** (`macos/README.md`, "Moving to a Touch ID key"): `macos/sign.sh keygen`
   makes it in the Mac's Secure Enclave, where no program can copy it, and each signature then needs a fingerprint.
   **Under way**: the key was made on 2026-11-07 and is listed from 1.5.1 (signed with the old key file); the next
   release is signed with it, and the release after drops the old key, whose file is then deleted.
5. **Release at least every 60 days**, or installed copies stop seeing updates until the next release (`expires`).

## 10. The API

All JSON. GET reads; POST needs `X-Pulsar: 1`. Everything but the public routes needs the page's session; the key alone opens only `/api/quit` and `/api/mcp/<tool>` (section 3).

| Route | What |
|---|---|
| `GET /api/config` | Settings for the page, the connection's state (with `productive.web`, the organization's address for task links, or ''), update state, calendar state. Never a token. |
| `GET /api/activity?date=&source=` | Tracker items and away time. |
| `GET /api/entries?date=` | Productive entries; `fromClaudeCode` on those Pulsar in Claude Code wrote. |
| `GET /api/proposal?date=[&fresh=1][&quick=1][&wait=1]` | Likely rows, with task links, commits and learned tasks (`quick=1`: `hintsPending` while the links and commits are worked out; `learnPending` while the history is read; `wait=1` waits for both). |
| `GET /api/calendar?date=[&fresh=1]` | Meetings. `GET /api/calendar/status` while signing in. |
| `GET /api/repeats?date=&source=` | Repeats seen today. |
| `GET /api/meeting-tasks?date=&uid=&title=` | A meeting's tasks. |
| `GET /api/tasks/suggest?date=` | Recent and assigned tasks. `/api/tasks/search?q=&date=` (yours first), `/api/tasks/estimates?ids=`. |
| `GET /api/services?task=&date=[&flags=1]` | Services that take time on that date; with `flags=1`, the task's ticket flags (5.3). |
| `GET /api/report?from=&to=` | A range in numbers. |
| `GET /api/logs?days=`, `/api/logs/download` | The error log. |
| `GET /api/entries?date=&copy=1` | The copy of the day's entries kept from the last read (`days/<person>/<date>.json`), at once and without asking Productive: `{entries, copy: true, fetched}`, or `{entries: [], copy: false}`. The page draws it while it asks Productive itself, then draws Productive's answer over it (`setHTML` leaves unchanged columns alone). Not on ↻ or a refresh after a write. |
| `GET /api/changelog`, `/api/version`, `/api/whoami?nonce=` | Changelog; version (public); proof of the key, bound to the port (`proof1`), and the older form (`proof`) (public). |
| `POST /api/mcp/<tool>` | `{args, client, session, sessionFrom, projectDir, ignoreLogged}`: one of Pulsar in Claude Code's tools, answered in the server (5.6). The key opens it. |
| `POST /api/session` | Trade a sign-in code for a session token. `POST /api/session/end` signs out. |
| `POST /api/settings` | Save settings (each field checked). |
| `POST /api/activitywatch/fix` | `{minutes}`: ActivityWatch's AFK timeout set to Away after (saved too), with a copy of its file, then ActivityWatch restarted (4.1). The page's session only; refused read-only and in tests. |
| `POST /api/activitywatch/login` | `{add}`: whether ActivityWatch is in Login Items, or adding it, through System Events (4.1). The page's session only; refused read-only and in tests. |
| `POST /api/log` | Log one entry. With `run` and `row`: one row of Log the day, journalled. |
| `GET /api/dayplan?date=&source=` | Log the day: the day proposed for review. Nothing is written. |
| `GET /api/dayplan/journal?date=` | Log the day: what each run of that day sent, and what Undo would delete. |
| `GET /api/range?date=&s=&e=[&source=]` | What a stretch of the day (minutes `s` to `e`) holds, by Log the day's minutes: parts, task groups, the one that leads, `mixed` and the `split` (5.1). Nothing is written. `ignore_logged=1` as for `/api/dayplan`. |
| `POST /api/entry/update`, `/api/entry/delete` | Change or delete one of your entries. Delete with `run`: only what that Log the day run created. |
| `POST /api/likely/ask` | Ask Claude (only when switched on). |
| `POST /api/likely/note` | `{date, s, e, project}`: the log dialog's note for a Likely block, from its session and commits (Haiku unless another model is chosen; only with the Claude Code switch on, 5.7). |
| `GET /api/task/projects?date=[&hint=][&repo=]` | Active projects for a new task, those logged to lately first, and the likeliest (5.1). |
| `GET /api/task/places?project=&date=` | Where in a project a new task can go, and which first (4.2). |
| `GET /api/task/fields?project=&date=&place=[&title=]` | For a new task at `place` (`parent:<id>`, `list:<id>` or `none`): the project's open statuses and the services that take time on the date, and the status, estimate and service proposed, each with why (5.1). Read only. |
| `POST /api/task/draft` | `{date, s, e[, meeting]}`: a new task's title and description from the work in that time; with `claude` and `projectName`, Claude's (Haiku unless another model is chosen; the switch on, 5.1). |
| `POST /api/task/create` | `{projectId, parentId or taskListId, title, description[, statusId][, estimate]}`: one task, assigned to you, never with a service (the page only). |
| `POST /api/calendar/connect`, `/disconnect` | Google Calendar. |
| `POST /api/update/check`, `/install`, `/apply` | Updates. |
| `POST /api/update/app` | Reinstall Pulsar: the newer app the signed `latest.json` describes, in place of this one (section 9). `{app: {ok, version, pending}}`, or `{error[, installLine]}`. |
| `POST /api/app/started` | `{version}`: the page of a window of 1.6.0 on says it started; ends a reinstall of that version (section 9). |
| `POST /api/productive/test`, `/api/client-error`, `/api/quit` | Test the connection; log a page error; stop (launcher). |

## 11. The page (`web/`)

- **`core.js`**: the shared state (`state`: date, zoom, tracker, and each column's data), the session token and
  requests (`getJSON`, `postJSON` add the headers; a 401 shows **Sign in**), formatting, the theme, the toast, the
  warp, and `todayLocal()` built from date parts (a locale's date format broke macOS 14 once). Every button, link
  and summary with no tabindex of its own gets `tabindex="0"`, now and as the page draws new ones (`makeTabbable`):
  WebKit, the app's window, skips them on Tab unless macOS's keyboard navigation is on. The chrome (header, column
  heads, the timeline, buttons, dialog bars) is not selectable text (`user-select: none`), so a drag on the timeline
  selects nothing; notes, popovers, the error log and every field are.
- **`day.js`**: `load()` asks for the columns, `render()` draws everything from `state` (the axis, the four columns,
  the rails, the side panels), and one click handler opens popovers and the log dialog. The answers draw through
  `drawSoon()` (core.js): the ones of the same frame once, none while the warp pulls in, and `setHTML` leaves a part
  whose HTML did not change as it is, so its cards and icons are not rebuilt. Cards are keyboard
  reachable (Enter, Space, Escape back to the card).
- **`log.js`**: the log dialog, the task picker (shared with the rule dialog and Log the day) and rules.
- **`flow.js`** (and `flow.css`): Log and next, Start and End as `HH:MM` in step with Minutes, links to Productive,
  the first-run checklist, the week in the header, report days that open the Day view, and where a day opens.
  `day.js` and `log.js` call it at a few places marked "flow.js".
- **`logday.js`** (and `logday.css`): Log the day, its review panel, the confirm step, the run and Undo, and the
  ticket flags of each row.
- **`range.js`** (and `range.css`): a stretch picked in the Day view (5.1): the task and note of its main work in the log
  dialog, the line that says it mixes work, and the split. `log.js` calls it at two places (`rangeOpen`, `rangeLead`),
  and the places that open the dialog on a stretch pass `range: true`.
- **`catchup.js`**: Earlier days in Log the day (5.4b), its list of workdays and the line that moves on to the next
  day. `logday.js` calls it at four places (`cuRender`, `cuButton`, `cuStrip`, `cuClosed`).
- **`newtask.js`** (and `newtask.css`): Create a task in the log dialog (5.1). `log.js` has three small hooks marked
  "newtask.js": the picker's `pick`, the link hidden while an entry is changed, and the service the form picked.
- **`ask.js`** (and `ask.css`): Ask Claude while it works. The button turns into a small pulsar (beams sweep its
  edge, a glow breathes, Claude's mark flashes), the Likely header says what is going on by the time only, and the
  blocks that got a task are lit once. `day.js` calls `askStart()` and `askEnd()` around the request. CSS on transform
  and opacity; a still glow with Reduce motion; paused while the window is hidden.
- **`meetings.js`**: the Meetings column and the Google part of Settings.
- **`settings.js`**: Settings (a sidebar of categories, each with its own icon, and a search that shows the matching
  sections of every category; the last category open opens again), updates and the reminder, the changelog. Saving
  asks Productive again only when the connection changed. Closing it (✕, Close, Escape, the error log) with a change
  not saved asks first: Save, Discard or Keep editing. Settings → Updates also opens the error log. Time and
  activity → **Away** holds Away after and, where ActivityWatch is installed, its AFK timeout against it (repainted
  as the choice changes) with **Fix**, and Login Items with **Check Login Items** and **Open ActivityWatch at login**
  (4.1); each says what it does before the click.
- **Keys** (`day.js`, the `?` list in `#keysdlg`): L Log the day, N + Log, ← → another day, T today, Enter on a block
  or a rail opens it; in the log dialog ⌘↩ logs (shown on the button) and ⌘⇧↩ is Log and next.
- **`report.js`**: the report, its charts (SVG, drawn here) and downloads.
- **`datepick.js`**: the calendar behind every date field (the native fields stay underneath, hidden, so the rest of
  the code still reads `YYYY-MM-DD`).
- **`main.js`**: start-up and switching between Day and Report; `logs.js`: the Logs dialog.
- **`native.js`**: the page's side of Pulsar.app's window (macos/README.md, "How the window works"): what the page
  shows, posted to the window for its menus, title and title bar; the commands its menus run; Cmd+W's "close what is
  open"; what Cmd+Q would lose; the right-click menus of entries, blocks and gaps; the Dock badge and the reminder's
  "time not logged today"; the day after a sleep; an automatic new sign-in; and Reinstall Pulsar in Settings,
  Updates. It wraps three of the page's own functions (`syncURL`, `paintUpdate`, `renderAside`) rather than changing
  them, and does nothing in a browser.
- **Colours**: tokens on `:root`, a dark set under `prefers-color-scheme` and `data-theme`. Billable blue
  (`#2a78d6`), not billable orange (`#eb6834`), unknown grey (`#7d7c76`), checked for colour-blind readers in both
  themes.

## 12. Tests and test servers

- `python3 -E -s -B -m unittest discover -s tests`: the Python logic on made-up data, the same with and without
  `PULSAR_READ_ONLY=1`. Every test module imports `tests/isolation.py` first: one scratch folder per run holds `HOME`,
  `TMPDIR`, `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` (so `~/.claude`, `~/.config/pulsar`, MemTime's database and the
  installed copy are out of reach) and goes when the run ends, leaving nothing behind; `PULSAR_TESTS_OFFLINE` makes
  Pulsar itself skip the keychain, Claude Code and the network. Every `GIT_*` variable is dropped and git's global
  settings file is one in the scratch folder (`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM`): git exports `GIT_DIR` into
  hooks, so a hook that ran the tests would otherwise point them at the real repository. Behind that, `security`,
  `claude`, `osascript`, `open` and `pkill` cannot start, directly or through `env`, `sh -c`, `shell=True`, `os.system` or
  `os.posix_spawn`, nor `os.exec*` at all; no connection or UDP datagram leaves the computer, no name is looked up but
  its own, none reaches a local port the run did not open (not the real Pulsar, a Preview or ActivityWatch), and a
  Unix socket only inside the scratch folder. Not covered: a call into libc through ctypes, and a child Python, which
  inherits the environment but not these checks. A test that tries fails with `RealWorld`, which `except
  Exception` cannot swallow, and the run exits 1 listing what was tried (`tests/test_isolation.py`). McpTest refuses
  the same way a write to Productive it did not fake. Tests that write
  fake Productive, so they pass the same under `PULSAR_READ_ONLY=1`. `test_isolation.py` also fails when a test
  module does anything before importing `isolation`, so each one is guarded when run alone too. The writes are
  checked by the whole request that reaches Productive's fake wire, REST and MCP, from `POST /api/log`, Undo and
  `pulsar_log` down (`tests/test_payloads.py`). `tests/test_appupdate.py` puts stand-in apps (signed ad hoc, their code
  signed with a throwaway key) through Reinstall Pulsar end to end in temporary folders, the helper's wait, open,
  confirmation and rollback included, and talks to the launcher as the window does (dialogs, progress, the
  developer tools' wait, the pipe no server may hold). `tests/test_release.py` signs test apps with a throwaway key and runs
  the real `launcher.sh` against them, with a home of its own and dialogs written to a file (`PULSAR_TEST_DIALOGS`):
  signed, tampered, unsigned, foreign and higher-than-released code, and a port taken at start.
- `tools/replay/` (Log the day replayed on real days) refuses to run without `PULSAR_REPLAY_DIR`, a private folder
  outside any git checkout: it keeps real entries, window titles, meetings and typed text there. `.gitignore`
  covers `tools/replay/data/` and its results too.
- A test server against real data: `PULSAR_READ_ONLY=1 python3 -E -s -B pulsar.py --port 5698 --no-open`, then
  `python3 pulsar.py --signin-url --port 5698` prints a one-time address for a browser. Block `**/api/log` and
  `**/api/entry/**` in a scripted browser as well. On such a server only, `?ignore_logged=1` in the page's address
  makes Log the day plan a day as if nothing were logged, to see a whole proposal on a day already logged, and so are
  the stretches picked for the log dialog (5.1). Give it
  `XDG_CACHE_HOME` pointing at a copy of `~/.cache/pulsar`, so its journal and Logs are not the real ones.
- `PULSAR_MCP_IGNORE_LOGGED=1` in the MCP's environment, with `PULSAR_READ_ONLY=1` there and on the server (else
  every tool refuses), and `PULSAR_TESTS_OFFLINE=1` there too: without it `pulsar.py --mcp` drops both before anything
  reads them (`mcp.clean_environment`), since a repository's settings could set them. Every tool then sees the day as
  if nothing were logged. Log the day plans with `ignore_logged`, the
  Likely rows learn their tasks from the days before only (`/api/proposal?ignore_logged=1`, refused by a server that
  is not read-only), "logged lately" stops the day before, and the preview's dry run does not report overlaps with the
  real entries. Used to measure proposals against days already logged.
- The window has test hooks when built with `PULSAR_SWIFT_FLAGS="-D PULSAR_TEST"`: with `PULSAR_TEST_DIR` it saves a
  picture of the page, a download and a PDF, then quits; with `PULSAR_TEST_WARP` too, three pictures of the native
  warp; with `PULSAR_TEST_SCRIPT=<file>`, it runs that file in the page once the day has loaded (the body of an async
  function returning JSON) and saves its answer as `ui.json`. `macos/ui_smoke.sh` builds that window and runs
  `tests/ui/smoke.js`: a click-through of the page that writes nothing (dialogs and their ✕, a click outside them,
  breaks named and never offered for logging, Not an example, the over-estimate line, Update's "and restart", the
  day's kept copy, no page error), each check printed, exit 1 on a failure.

## 13. Known limits

- The REST token path has not been tried against real Productive yet; everyone so far uses Claude Code's connection.
  Its task filters are checked again on what comes back (open tasks only where asked, the words of a search), so a
  filter Productive ignored costs suggestions, not wrong ones. The daily target from the organization is read only
  through Claude Code's connection; with a token it is 8 h unless set in Settings. Through a token, the "no estimate"
  flag counts the project's first 100 tasks as Productive lists them, not the most recently active.
- No ticket flag for a budget nearly used up: Productive does not show budgets to a Staff role (5.3).
- The app is ad-hoc signed, not with an Apple Developer ID: macOS asks for confirmation on first open (Open Anyway).
  Whether macOS lets such an app send notifications was not checked on a real Mac: the end-of-day reminder falls
  back to a bounce of the Dock icon and a sheet on the window when it may not. Open at Login (`SMAppService`) was
  not tried on a real Mac either.
- Reinstall Pulsar reaches a window of 1.5 or older too (it is the page that offers it), but such a window cannot
  quit by itself: it asks the person to press Cmd+Q, and the helper opens the new app once it has quit (it waits up
  to a day). Its right-click menu still has WebKit's Reload, which skips the launcher's check, until it is
  reinstalled.
- `main` is not protected and who may publish a release is not limited yet; the old release key file still exists,
  unencrypted, until the move to the Touch ID key is finished (section 9, "What the maintainer must do"). The reserve
  key and the Touch ID key were made on 2026-11-07.
- The app around the code, its window and launcher, is not covered by the release key: Apple's ad-hoc signature does
  not say who built it, and the launcher's check of the code belongs to the very app it checks. A Pulsar.zip built
  from scratch by someone who can publish on the repository installs whatever it likes when it is downloaded in a
  browser; README.md's install line refuses it (`Pulsar.zip.sig`). Only a Developer ID signature
  and notarization would change that (`macos/README.md`, "Removing the first-open warning").
- A copy installed before the launcher checked signatures stays as it is: a version higher than every release put in
  place that way still has to be removed by hand (`~/Library/Application Support/Pulsar/`).
- In a browser (not the window) the page cannot check who answers on the port: after a restart it asks to be opened
  from the app icon, but a page loaded there by hand loads whatever holds the port. Its token is worth nothing there.
- ActivityWatch's AFK timeout decides which breaks are in its data: one longer than Away after hides the shorter
  breaks until Fix is clicked, and Fix changes new data only. Pulsar checks neither MemTime's idle time nor whether
  MemTime opens at login; a MemTime pause shorter than Away after is neither away nor work (its window is not known).
- Away after is read when a day is worked out: Likely's past days kept on disk (`likely-full/`) keep the sittings of
  the setting they were worked out with until ↻ works them out again. Where the fi-time-from-activity skill makes the
  Likely rows, its own away time applies to them.
- The night the clocks go back, the repeated hour (01:00 to 02:00 in Toronto, 03:00 to 04:00 in Kyiv) is one hour on
  the clock: Log the day folds the two hours together, and an entry there is sent as the first of them. The overlap
  check of `POST /api/log` still refuses one that would clash with an entry in the second.
- When the service last used on a task no longer takes time, Pulsar picks one of the same name, and with several
  budgets that have it, the first. The confirm step and `pulsar_preview`'s summary name the budget, so it is seen
  before anything is written.
- Learned tasks and Ask Claude guess; on one person's days they were right often but not always. They only ever
  suggest.
- The key in `~/.config/pulsar/secret` is readable by every program of the person's, so a session in bypass mode can
  log new entries without the `pulsar_log` prompt, and a program set on it can sign a window in itself (section 3).
- `/api/whoami` still answers the older proof, the HMAC of the nonce alone, for the launcher inside a Pulsar.app of
  1.4.0 or earlier; that launcher accepts it only from this user's `pulsar.py` (section 3). Pulsar in Claude Code and
  newer launchers check `proof1`, bound to the port.
- The Productive ask rules (the opt-in of 5.7) cover the server named `productive` only; a claude.ai connector
  (`mcp__claude_ai_<name>__…`) or a server under another name gets none. With the box on in one copy and off in the
  other, the other's tidy can take back a rule the first found already there (so not on its own list): Settings then
  says rules are missing and offers to add them again. Switching off also removes a rule the person added by hand
  after Pulsar added the same one. Its change to the settings file is a read, a merge and a rename: a change Claude
  Code makes in the same instant is lost.
- The deny rules stop Claude Code's file tools and the file commands it recognizes in a shell; a script that opens
  Pulsar's files itself is stopped only with Claude Code's sandbox on (section 3).
- `CLAUDE_CONFIG_DIR` set only in a shell is not in the environment of a Pulsar started from the Dock: Pulsar then
  adds itself and its rules to `~/.claude`, which that Claude Code does not read, and cannot tell.
- A drafted note naming another client is caught only by the words of the task list's project names, as written
  ("Globex", not "globex"); Pulsar has no list of Productive's people, so other people's names are only asked
  against in the prompt.
- Commits by you: with no email in your own git settings, a per-repository email counts when two repositories of the
  day set it; two repositories from the same outside source that both name the same author would pass.
- When your last 30 days cannot be read, the log dialog, Log the day and `pulsar_tasks` say so, and the failed read
  is kept a minute, not ten or sixty. `pulsar_preview` then still picks the task's own service, as for a task never
  logged before: its summary names the service and budget the person approves.
- A self-test whose own requests time out exits 75, which this updater does not count against a version. An
  installed 1.2.x updater still counts it as a failure, so updating from those versions keeps the old risk.
