# Pulsar.app (macOS)

A double-clickable Pulsar for colleagues who never open Terminal. The app is a window (`PulsarWindow.swift`,
about 1,300 lines, the Mac's own WebKit, with `web/js/native.js` on the page's side) and a bash launcher: the launcher installs the code the app carries and
starts it with the Mac's own Python, and the window shows the page. It needs macOS 12 Monterey or newer, Apple
Silicon or Intel (macOS 11's developer tools come with Python 3.8, and Pulsar needs 3.9), and Safari 16.2 or
newer for the window (older: the window hands the page to the default browser).

| File | What it is |
|---|---|
| `release.sh` | builds a release and, with `--publish`, puts it on GitHub |
| `build_app.sh` | builds `dist/Pulsar.app` and `dist/Pulsar.zip` |
| `PulsarWindow.swift` | the window; compiled to `Pulsar.app/Contents/MacOS/Pulsar` for arm64 and x86_64 |
| `launcher.sh` | the launcher; becomes `Pulsar.app/Contents/Resources/launcher.sh` |
| `AppIcon.svg` | the icon source |
| `AppIcon.icns` | the icon, rendered from the SVG and committed, so a build needs no Node |
| `AppIcon-1024.png` | the 1024 px render, for previews |
| `render_icon.mjs` | renders the SVG into an `.iconset` (Node + playwright-core + Chrome) |

## Releasing an update

```bash
macos/release.sh 1.1.0 --notes "Adds the week view. Fixes the totals on days without entries." --publish
```

Run it from a clean `main` with the changes you want to ship committed, and their entry in `CHANGELOG.md`
(`## 1.1.0 (2026-11-20)` and a `- ` line, a few words per change). The Google sign-in client for Google Calendar
is not in git: keep the JSON Google Cloud gave for the "Pulsar" Desktop client at `~/.config/pulsar/google_client.json`
(or point `PULSAR_GOOGLE_CLIENT` at it); the release and the app carry it in place of the repo's placeholder. It
signs only `main` exactly as it is on GitHub: it refuses when HEAD is not `origin/main` (another branch, a commit not
pushed) or the tree has changes, at the start and again right before the first signature, so what protects `main`
on GitHub also decides what the release key signs. It refuses as well a version
that is not newer than `__version__` in `pulsar/__init__.py`, a version with no `CHANGELOG.md` entry, and failing
tests. Then it sets the version, builds into `dist/`, shows what it will do and asks before it:

1. commits the version change as "Pulsar 1.1.0" on top of `origin/main` and pushes it to `main`;
2. creates the public release `v1.1.0` on `fosterinteractive/pulsar` at that commit, with five files:
   - `pulsar-1.1.0.zip`: the code git tracks, in one folder `pulsar-1.1.0/` (what installed copies download);
   - `latest.json`: version, download address, SHA-256 of the zip, the notes, the date, an expiry date 60 days
     ahead (what they read; past that date they ignore it, so release at least every 60 days), and the app:
     Pulsar.zip's address and SHA-256 and `MIN_APP` (Reinstall Pulsar, below);
   - `latest.json.sig`: the release key's signature of `latest.json` (see below);
   - `Pulsar.zip`: the whole app, for new installs, with the code it carries signed by the same key
     (`Contents/Resources/app.manifest.json.sig`): its launcher installs that code only as signed. Share the
     install line in README.md (it downloads
     `https://github.com/fosterinteractive/pulsar/releases/latest/download/Pulsar.zip`) with new colleagues;
   - `Pulsar.zip.sig`: the release key's signature of `Pulsar.zip` itself. README.md's install line checks it with
     `/usr/bin/openssl` and the public key written in the line before it unpacks anything; `release.sh` refuses to
     finish when that key is not one of `PUBLIC_KEYS` or does not verify this signature, so after a key change the
     line in README.md (and in the onboarding document) must change with it.

Without `--publish` it only builds and prints the commands to publish by hand. It needs the GitHub CLI
(`gh auth login`) for `--publish`. Write the notes for colleagues: one or two plain sentences.

**What colleagues see.** Each Pulsar checks `releases/latest/download/latest.json` every few hours and shows a newer
version next to its own; it installs it only when the person clicks **Update** (versions up to 1.0.6 still installed
by themselves, so they all reach 1.0.7 that way). On the click, `latest.json` must carry a valid signature from a
release key, the download must match its SHA-256 and pass its self-test (`pulsar.py --self-test`, which checks every
file `index.html` loads, starts a server, fetches them and asks it for its version); then Pulsar restarts into it. A
self-test that fails is run once more; a version that fails twice goes into `bad-versions`, and one whose self-test
could not finish (a timeout) is left to try again. `latest.json` also lists every version released
so far (from the git tags): a copy two or more releases behind reminds its person once every two days, until they
update or skip that release. A copy run from a git checkout only says that a newer version exists.

**A new app (window and launcher).** Updates replace only the code, never Pulsar.app. Each release's `latest.json` also
describes its app (`app: {version, min, url, sha256}`: Pulsar.zip's address and SHA-256, and `min`, `MIN_APP` in
`pulsar/appupdate.py`). A window older than `min` (a window of 1.5 or older counts as older) is offered **Reinstall
Pulsar** in Settings, Updates: on the click, Pulsar downloads Pulsar.zip, checks it against that SHA-256, its own
signature (`Pulsar.zip.sig`), macOS's signature check and the code check, swaps it in for the running app, quits,
and the new app opens; the old one is kept beside it (`/Applications/.Pulsar-previous`) until the new one has started,
and put back if it does not (`docs/HOW-IT-WORKS.md`, section 9). So: **raise `MIN_APP` in a release whose window or
launcher changed in a way people should have** (it must not be newer than the release; `release.sh` checks). Where
the account cannot change the app in /Applications, the page shows the install line instead.

Click-only updates also limit the damage of a stolen release key: a forged release reaches only the people who
click Update, not everyone within six hours.

**Keep the repository public.** Installed copies and the Pulsar.zip link download without a GitHub sign-in. In a
private repository every download answers 404: Settings then says no release is published, and an installed
copy writes a warning to its error log once a day.

**The release keys.** Installed copies trust only releases signed with one of the ECDSA P-256 keys listed in
`PUBLIC_KEYS` in `pulsar/release_sig.py`. Since 1.5.1 there are three: the old key file (until the move below is
finished), the Touch ID key (made 2026-11-07 on the publisher's Mac) and the reserve key. The same keys sign the code inside
Pulsar.app, and the app's launcher checks it with its own copy of that file before it installs anything.

- **Main key**: best a Touch ID key, `~/.config/pulsar-release/update-signing-key.se` (below): its private half lives in
  the Mac's Secure Enclave and signs only with a fingerprint, so no program can copy it or sign with it unseen.
  Otherwise a key file, `~/.config/pulsar-release/update-signing-key.pem`, on the Mac of whoever publishes
  (owner-only, never in the repo). `release.sh` uses the `.se` key when there is one, else the `.pem`
  (`PULSAR_SIGNING_KEY` points elsewhere), signs through `macos/sign.sh`, and refuses a key that is not listed, or
  that the newest released version does not list (people would not get the release).
  A Touch ID key cannot be copied, so it is lost with its Mac: that is what the reserve is for. A key file, while
  one is used, is best encrypted (`openssl ec -aes256 -in key.pem -out key.enc.pem`); `release.sh` then asks for its
  passphrase for each signature, so publishing needs a person at a terminal.
- **Reserve key**: encrypted with its own passphrase, kept only in the publisher's password manager (a LastPass
  secure note, made 2026-11-07), never on a laptop. If the main key is lost or leaks, sign one release with
  the reserve (`PULSAR_SIGNING_KEY=<reserve file> macos/release.sh …`) whose `PUBLIC_KEYS` drops the old main key and
  adds a new one; every installed copy accepts it and from then on trusts the new list, with no reinstall.
  Create it on the Mac of whoever keeps it, so the private half is never seen anywhere else:

  ```bash
  /usr/bin/openssl ecparam -name prime256v1 -genkey -noout | /usr/bin/openssl ec -aes256 -out ~/Desktop/pulsar-reserve-key.pem
  /usr/bin/openssl ec -in ~/Desktop/pulsar-reserve-key.pem -pubout     # the public half: add it to PUBLIC_KEYS
  ```

  Put the file in the password manager (its passphrase best kept apart), delete the file from the Desktop, and
  publish a release that lists the public half. A key can only be added by a release signed with a key already listed, so the reserve has to be
  in place before people install.

**Moving to a Touch ID key** (a Mac with Touch ID or a keyboard with it, and Apple's Command Line Tools):

1. `macos/sign.sh keygen`: makes the key in this Mac's Secure Enclave, asks for nothing yet, and prints its public
   half. The file `~/.config/pulsar-release/update-signing-key.se` holds only a handle that this Mac's chip alone can
   use: copied anywhere, it signs nothing.
2. Add that public half to `PUBLIC_KEYS` in `pulsar/release_sig.py`, after the current main key.
3. Release that version with the old key: `PULSAR_SIGNING_KEY=~/.config/pulsar-release/update-signing-key.pem
   macos/release.sh …`. Installed copies accept it and from then on trust the new key too. (`release.sh` refuses to
   sign this release with the new key: the version people have does not list it yet.)
4. From the next release on, `macos/release.sh …` signs with the Touch ID key: a fingerprint for each signature
   (the update and the app's code), in a dialog that names what is signed and the start of its SHA-256. The terminal
   prints the full SHA-256 first: compare it before you touch. **But sign `latest.json` with the old key for a while
   longer** (`PULSAR_UPDATE_KEY=~/.config/pulsar-release/update-signing-key.pem macos/release.sh …`): a copy older
   than step 3's release knows only the old key, and every copy reads `latest.json` from the newest release only, so
   an update signed with the new key alone leaves those copies where they are ("The latest release is not signed with
   the release key"). 1.5.2 did that; 1.5.3 is the bridge. That first release also puts the Touch ID key's public
   half in README.md's install line (the `printf` lines), since Pulsar.zip is then signed with it: `release.sh`
   refuses a Pulsar.zip the line's key does not verify.
5. Once a release signed with the Touch ID key is out, drop the old main key from `PUBLIC_KEYS` in a release and
   delete the `.pem` file (and any copy of it).

Where this stands (2026-11-07): steps 1 to 3 done (1.5.1). Step 4: 1.5.2 signed everything with the Touch ID key,
so copies older than 1.5.1 ignored it; 1.5.3 signs `latest.json` with the old key again, the rest with Touch ID.
Step 5 waits until the copies people have are on 1.5.1 or later. Keep the reserve key: a new Mac, or a wiped one, cannot use the
   Touch ID key, and the reserve is then the only way to move on.

Any enrolled finger signs, so enrolling another (which needs the Mac's password) does not lose the key. What it does
not stop: a program already running as you while you release could change a file just before you sign it. The
SHA-256 in the dialog and the terminal is there to catch that.

Anyone with write access to the repository can publish a release, but without a listed key installed copies ignore
it, and releases are immutable once published (repository setting), so nobody can swap a file of a published
release. A Pulsar.zip in such a release still reaches whoever downloads it: its code would not install through
Pulsar's own launcher, but a whole app built from scratch carries a launcher of its own. Hence the steps in
`docs/HOW-IT-WORKS.md`, section 9, "What the maintainer must do": protect `main`, limit who can release, the reserve
key, the Touch ID key. For the first release of a version (for example 1.0.0 before any tag exists) `release.sh` accepts the
version already in `__init__.py`.

**Where it is installed.** `~/Library/Application Support/Pulsar/`:

```
versions/1.0.0/  versions/1.1.0/   full copies of the code (pulsar.py, pulsar/, web/, README.md)
current  -> versions/1.1.0         what runs
previous -> versions/1.0.0         what ran before
bad-versions                       versions that failed to start or their self-test twice, one per line; never used again
```

The links are relative and switched atomically. The updater keeps `current` and `previous` and removes older
copies. Settings, the token and caches live elsewhere (`~/.config/pulsar`, the keychain, `~/.cache/pulsar`)
and survive every update.

**Rollback.** Automatic: when the app is opened and `current` does not answer within 15 seconds, the launcher
writes that version into `bad-versions`, points `current` back at `previous` and starts it. A port that something
else holds is never the version's fault: the launcher tries again and marks nothing. A bad version is
never installed again, from the app or the updater, so the fix is a new release with a higher number. By
hand: point `current` at an older folder (`ln -sfh versions/1.0.0 current` in the install root), quit
Pulsar (Activity Monitor) and open it again.

## Build the app only

```bash
macos/build_app.sh
```

It runs from any directory and takes about a second. It writes:

- `dist/Pulsar.app`, version from `__version__` in `pulsar/__init__.py`;
- `dist/Pulsar.zip`, the whole app (about 1 MB).

Without `PULSAR_BUNDLE_SIGNING_KEY=<private key>` (which `release.sh` sets) the code inside is not signed, and such an
app installs nothing: it says so and opens the version already installed. That is on purpose: only a release may
put code in `versions/`. With a key that is not in `PUBLIC_KEYS`, the build stops.

`dist/` is build output and ignored by git.

Inside the app:

```
Pulsar.app/Contents/
  Info.plist           com.fosterinteractive.pulsar, macOS 12+, local networking allowed (127.0.0.1)
  MacOS/Pulsar         the window (PulsarWindow.swift), one binary for Apple Silicon and Intel
  Resources/launcher.sh
  Resources/release_sig.py       the launcher's copy of the release check and keys, outside the code it checks
  Resources/app.manifest.json    the code's version and each file's SHA-256; .sig: the release key's signature
  Resources/AppIcon.icns
  Resources/app/       pulsar.py, pulsar/ (no __pycache__), web/, README.md: the copy the app installs
```

The bundle is ad-hoc signed (`codesign -s -`), which is enough to run on Apple Silicon. It has no Developer ID
signature, so the first open needs the confirmation described in the main README (`spctl -a -t exec` says
"rejected"), and a company-managed Mac may refuse it outright. See "Removing the first-open warning" below.

## How the window works

It runs `launcher.sh --window`, which does everything below and prints `PULSAR_URL <address>?code=<one-time
code>` instead of opening a browser; the window loads it and the page signs in. While that runs it shows
"Starting Pulsar…" under a spinner, and the launcher's dialogs are sheets on the window (below); if the launcher
fails, its dialog says why and the app quits.

- Only `http://127.0.0.1:<port>` opens in the window. A link elsewhere that the person clicks goes to the default
  browser; anything else (another site, a frame, a new window) is refused.
- Downloads (the page's `<a download>` with a blob) go through `WKDownload` and a save panel. **File > Print** asks
  the page to print (it posts `{action: "print"}`; a WebView ignores `window.print()`).
- **View > Reload Pulsar** (Option-Cmd-R), **Start Pulsar**, a new sign-in and the page's move into a new version
  (`{action: "reload"}`) all run the launcher again for a fresh code; the window never reloads the page by itself,
  since a session lives only as long as its server and the launcher first checks the server on the port is this
  user's (`docs/HOW-IT-WORKS.md`, section 3). The page's view, day, rows, tracker and report range are carried into
  the new address, so nobody lands back on today. WebKit's own Reload is taken out of the right-click menu for the
  same reason (`PulsarWebView.willOpenMenu`), with the other items that belong to a browser (Open Link in New
  Window, Download Linked File, Search with Google, Share, Copy Link with Highlight; Writing Tools outside a text
  field). When the page's session has expired, `web/js/native.js` asks for a new sign-in by itself, once a minute
  at most.
- **The page and the window** talk through `web/js/native.js`. The window injects `window.PULSAR_APP` at the start of
  every load: its version, path and pid, the Mac's own choices (`prefs`: 12 or 24 hours, the first weekday, the
  locale and languages, the accent colour, also set as `--mac-accent` on `<html>`) and the app's own (`native`: badge,
  reminder, reminder time, Open at Login). The page posts `{action: "state", …}` whenever what it shows changes (view,
  day, rows, theme and background colour, title, open dialogs, unsaved work), and the window calls
  `pulsarNative.run(command, arg)`, `closeTop()`, `wake()`, `menuPick(id)`, `todayUnlogged()` and `prefs(…)`. A
  window of 1.5 or older sets no `PULSAR_APP`: the page then sends it none of this.
- **Menus.** File: New Entry (Cmd+N), Log the Day (Cmd+L), Close (Cmd+W), Export (days or entries, CSV), Print
  (Cmd+P). View: Day (Cmd+1), Report (Cmd+2), Rows, Refresh (Cmd+R), Reload Pulsar (Option-Cmd-R), Actual Size,
  Zoom In and Out (Cmd+0, Cmd+= or Cmd++, Cmd+-; the page zoom is kept between launches), Appearance, Enter Full
  Screen (Ctrl-Cmd-F). Go: Today (Cmd+T), Previous and Next Day (Cmd+Left, Cmd+Right). Pulsar: Check for Updates,
  Settings (Cmd+,), Services. Window: Pulsar (brings the window back), Bring All to Front. Help: Pulsar Help, How
  Pulsar Works (both on GitHub), Show Error Log; the Help menu also searches every menu. `validateMenuItem` greys out
  what does not apply (the day's commands in the report, Print and Export outside it, everything while one of the
  page's dialogs is open or before the page has loaded) and ticks the view, rows and appearance shown.
- **Cmd+W** closes the page's top dialog, popover, date picker, task picker or Log the day, as a sheet would close
  (`pulsarNative.closeTop()`); with nothing open it closes the window. **Closing the window keeps Pulsar running**:
  the Dock icon (or Window > Pulsar) brings it back. **Cmd+Q** asks first, as a sheet, when the page says something
  would be lost (the log dialog open, a task or rule being written, changes in Log the day, Log the day writing;
  the page may add its own with `window.pulsarUnsaved()`), then runs `launcher.sh --stop`: the server stops if this
  app started it and it holds this user's key. A server started from a checkout keeps running.
- **Right-click** on an entry: Edit Entry, Delete Entry, Open in Productive; on an activity block: Log This Time, Add
  a Rule for This Window; on a Likely block, a usual entry or a gap: Log This Time. The page posts the items
  (`{action: "menu", x, y, items}`) and the window shows a real menu at the pointer; Open in Productive opens in the
  browser (https only), the rest goes back to the page (`menuPick`). Anywhere else the menu is WebKit's, cut down.
- **The title bar** is transparent with no separator and no title of its own, in the page's background colour and
  theme (light, dark or the system's, `window.appearance`), so it reads as one band with the page's header. The
  window's title still names the day or the report's range, for the Window menu, Mission Control and VoiceOver.
- **A pinch** changes the day's rows (1 minute to 1 hour) instead of magnifying the window.
- **After a sleep, and at midnight**, the window calls `pulsarNative.wake()`: the page gets a `pulsar:wake` event, and
  unless a listener takes it (`preventDefault`), a day view that was showing today moves on to the new today, or
  refreshes.
- **The Dock badge** is today's active time with no entry ("45m", "3h"; nothing under 15 minutes), from the day view
  when it shows today, else from `/api/report` for today every half hour.
- **The reminder**: once a working day at 17:30 (the page can set another time, or switch it off), when half an hour
  or more of today has no entry, a notification "Time not logged today" with **Log the Day**. The app is signed ad
  hoc only, and whether macOS lets such an app notify was not checked on a real Mac: if the request for permission
  fails or is refused, the Dock icon bounces once and a sheet on the window says the same, with Log the Day.
- **Open at Login** (macOS 13 and later, `SMAppService.mainApp`): the page can switch it with
  `pulsarNative.setNativePrefs({login: true})`; its state ("on", "off", "approve", or "unsupported" on macOS 12) is in
  `PULSAR_APP.native.login`. Launched at login, the window opens without taking the keyboard.
- **The launcher's dialogs**: with `PULSAR_WINDOW_DIALOGS=1` (set by this window) and `--window`, the launcher writes
  `PULSAR_DIALOG <text> <buttons>` (each base64) on its own output and reads the button's title back on its input;
  the window shows each as a sheet, the last button the default. `PULSAR_STATUS <text>` lines replace the words under
  the spinner. The window gives up on a launcher that says nothing for five minutes. Without the variable (an older
  window, a test), the launcher shows its own osascript dialogs as before.
- **Reinstall Pulsar**: the page posts `{action: "quit"}` once the new app is in place (`pulsar/appupdate.py`), and the
  window quits without asking about unsaved work.
- The warp (an update, the refresh button): the window tells the page it can draw it (`window.PULSAR_NATIVE_WARP`),
  and the page then only posts `{action: "warp", to: 0..1, instant, dark}`. The window takes a picture of the page,
  bends it with Core Image's `CIHoleDistortion` and draws the core, the ring and the flash as Core Animation layers,
  all animated on the graphics card (`NativeWarp`). The picture sits over the page, so the warp holds through the
  reload into a new version; it lets go when the page asks, or after ten seconds. The test build checks it with
  `PULSAR_TEST_WARP=1`: it photographs its own window held and letting go, and says whether the overlay went away.
- Before loading, it asks WebKit for `color-mix()` and container queries (Safari 16.2). Without them it says so
  and opens the address in the default browser, and leaves the server running.

## How the launcher works

1. **Python.** It uses `/usr/bin/python3`, which works only once Apple's Command Line Tools are installed. It
   checks `xcode-select -p` first; without the tools it shows its own dialog and, on **Install**, runs
   `xcode-select --install` (macOS asks for an administrator password). Run by the window (`--window`), it then waits,
   saying so under the window's spinner every few seconds, and goes on by itself once the tools are there (an hour
   at most); run any other way, it exits and the person opens Pulsar again afterwards. With the tools but a Python
   older than 3.9, it points to Software Update.
2. **Install.** It compares the version the app carries (B, `Resources/app`) with the installed one (C,
   `current`). With nothing installed, or B newer than C and not in `bad-versions`, it copies B to a temporary
   folder, checks the copy against the signed `app.manifest.json` (`release_sig.py check-bundle`), moves it to
   `versions/B`, points `previous` at the old `current` and `current` at `versions/B`. Code that is not signed, or
   not as signed, is not installed: a dialog says why, and C opens (or, with nothing installed, Pulsar stops). When C
   is newer (the app has updated itself since this bundle was built), C stays. Versions compare numerically (1.10.0
   > 1.9.0).
3. **Already running?** If `/api/version` answers with C's version and the server proves it holds this user's
   key (`/api/whoami`, `is_ours`), or the server was not started by the app
   (for example `python3 pulsar.py` from a checkout, which is never stopped), it opens the page and exits. A
   server the app started with another version is asked to quit (`POST /api/quit`) and C starts instead. If
   another program holds the port, it says so and changes nothing.
4. **Start.** It runs `python3 -B <real path of current>/pulsar.py --no-open --port 5610` with `nohup`, in a
   new session, so the server outlives the launcher, with `PULSAR_APP_BUNDLE` set to this app's path (for Reinstall
   Pulsar) and none of the window's pipes. `-B` writes no `__pycache__`. Output is appended to
   `~/Library/Logs/Pulsar.log`, together with what the launcher decided.
5. **Check and roll back.** It waits up to 15 seconds for `/api/version` and `is_ours`, then opens the page. If the server
   exits or never answers and `previous` is another version, C goes into `bad-versions`, `current` goes back
   to `previous`, and that starts. Only if that fails too does a dialog point to the log. A server that exits 98
   (`pulsar.py`: the port is taken) is started again, five times a second apart; if the port stays taken, a dialog
   says so, nothing is marked and `previous` is not tried.

For tests, a throwaway `HOME` so the real install root is not touched, and `PULSAR_TEST_DIALOGS=<file>` so its
dialogs go to that file (each answered with its default button) instead of the screen. A build without the release
key installs nothing, so the launcher's install is tested by `tests/test_release.py`, which signs test apps with a
throwaway key and lists only that key in each test app's `Resources/release_sig.py`. The launcher alone (on an empty
`HOME`, a build without the key writes "This copy of Pulsar is not a signed release" to `dialogs.txt` and stops):

```bash
T=$(mktemp -d); HOME=$T PULSAR_PORT=5699 PULSAR_NO_BROWSER=1 PULSAR_TEST_DIALOGS=$T/dialogs.txt dist/Pulsar.app/Contents/Resources/launcher.sh
```

The window, with a test build that loads the page, saves `page.json` (signed in or not), `window.png`, the error
log through the download path and `report.pdf` through the print path into `PULSAR_TEST_DIR`, then quits and
stops its server. Build it in a copy of the checkout so `dist/` keeps the real app, and give it another bundle id:

```bash
PULSAR_SWIFT_FLAGS="-D PULSAR_TEST" macos/build_app.sh
plutil -replace CFBundleIdentifier -string com.fosterinteractive.pulsar.test dist/Pulsar.app/Contents/Info.plist
codesign --force --deep -s - dist/Pulsar.app
T=$(mktemp -d); env HOME=$T XDG_CONFIG_HOME=$T/.config XDG_CACHE_HOME=$T/.cache PULSAR_PORT=5699 \
  PULSAR_READ_ONLY=1 PULSAR_TEST_DIR=$T dist/Pulsar.app/Contents/MacOS/Pulsar
```

Do not test with `open dist/Pulsar.app` while a development server runs on 5610: the launcher would find it
and only open the page.

## Change the icon

1. Edit `AppIcon.svg`. The canvas is 1024 x 1024; the tile is 824 x 824 at (100, 100) with a 185 corner radius,
   the macOS 11+ grid. Keep the margin around the tile transparent.
2. Re-render and rebuild:

   ```bash
   npm install playwright-core            # once, anywhere; or set PLAYWRIGHT_CORE=/path/to/playwright-core
   macos/build_app.sh --icon
   ```

   This needs Node 18+ and Google Chrome (`CHROME=/path/to/chrome` for another one). It rewrites
   `AppIcon.icns` and `AppIcon-1024.png`; commit both.
3. Look at the small sizes as well as the large one: `iconutil -c iconset macos/AppIcon.icns` unpacks them, and
   the 16 and 32 px images are the ones that show in Finder lists.

Finder caches icons. If an old icon still shows, rename the app or log out and back in.

## Removing the first-open warning

macOS shows the "cannot be opened" warning because the app is not signed with a Developer ID and not
notarized by Apple, and company-managed Macs may block such an app completely until IT allows it. Signing
and notarizing removes both. These are the steps (not done):

1. Join the Apple Developer Program as an organization and create a **Developer ID Application**
   certificate (Xcode → Settings → Accounts → Manage Certificates, or developer.apple.com). Install it in the
   login keychain of the Mac that builds.
2. Sign with it instead of the ad-hoc signature, with the hardened runtime and a secure timestamp:

   ```bash
   codesign --force --deep --options runtime --timestamp \
     -s "Developer ID Application: Foster Interactive (TEAMID)" dist/Pulsar.app
   ```

3. Store notary credentials once (an app-specific password from appleid.apple.com):

   ```bash
   xcrun notarytool store-credentials pulsar-notary --apple-id you@example.com --team-id TEAMID
   ```

4. Zip, submit and wait:

   ```bash
   ditto -c -k --keepParent dist/Pulsar.app dist/Pulsar.zip
   xcrun notarytool submit dist/Pulsar.zip --keychain-profile pulsar-notary --wait
   ```

5. Staple the ticket to the app, then zip it again (stapling changes the app, and a zip cannot be stapled):

   ```bash
   xcrun stapler staple dist/Pulsar.app
   ditto -c -k --keepParent dist/Pulsar.app dist/Pulsar.zip
   ```

6. Check: `spctl -a -vv -t exec dist/Pulsar.app` should say `accepted` and `source=Notarized Developer ID`.

To make releases signed too, put steps 2 to 5 into `build_app.sh` before the zip is made. Updates do not need
a Developer ID: they are plain code run by Apple's own Python, and they are authenticated by the release key
signature on `latest.json` (see "The release key") plus the SHA-256 it carries.

The app's main executable is the compiled window; `launcher.sh` sits in Resources and is signed with the
bundle. Neither has been through notarization here.

## Preview: try a branch before it is released

**Pulsar Preview** is a second app that runs a git checkout's code as it is, beside the released Pulsar, so new work
can be tried on real data before anyone gets it. It uses its own port (5620), its own log
(`~/Library/Logs/Pulsar-Preview.log`), its own window storage, and its own settings, sign-in, sessions, caches and
keychain items (`~/.config/pulsar-preview`, `~/.cache/pulsar-preview`, keychain service `pulsar-preview`): its launcher
starts the code with `PULSAR_INSTANCE=preview`. "Remove token" in Preview never touches Pulsar's, and "Disconnect"
(Google Calendar) only forgets Preview's own access on this Mac: Google is not told, because Preview signs in with
Pulsar's Google client and a revoke would end Pulsar's access too. Neither app's saves drop the other's settings. It never installs or updates anything, and is never zipped or released.

- **It writes nothing to Productive** unless **Settings > Updates > Preview > Allow this preview to write** is on.
  The chip at the top always shows the branch and commit it runs; it turns red while writing is allowed. The launcher
  also sets `PULSAR_READ_ONLY=preview`: this code reads that as "a preview" and follows the setting, while a checkout
  older than Preview (`main` before it, 1.2.x) takes any value as read-only and writes nothing at all.
- **The first start** copies Pulsar's settings once (rules, time zone, who you are; not the Google account, the
  token source, update choices, write permission or the Claude Code switch, which starts off in Preview). Productive then comes through Claude Code's connection, with no
  setup; to use a token instead, paste one in Preview's Settings. Google Calendar needs **Connect** once in Preview's
  Settings (it finds the sign-in client in Pulsar's folder). An older checkout, which does not know
  `PULSAR_INSTANCE`, keeps using Pulsar's folders: the launcher asks the code where it keeps them.
- **Set up once:** a checkout for it, and the app:

  ```bash
  git worktree add ~/pulsar-preview -b preview main
  PULSAR_PREVIEW_DIR="$HOME/pulsar-preview" macos/build_app.sh
  ditto "dist/Pulsar Preview.app" "/Applications/Pulsar Preview.app"
  ```
- **Put new work in it:** merge the branches to try into `preview` (in `~/pulsar-preview`, so the main checkout
  is not touched), then quit Pulsar Preview (Cmd+Q) and open it again: quitting stops its server, and opening it runs the
  code as it is now. Page files (`web/`) also reload with ↻; Python changes need the reopen.
- **Release** only what was tried in Preview: merge it into `main` and run `release.sh` as below.
- **Remove it:** switch off Claude Code in its Settings first if it is on (that removes `pulsar-preview` from Claude
  Code), quit it, delete `/Applications/Pulsar Preview.app`, `~/.config/pulsar-preview`, `~/.cache/pulsar-preview`,
  `~/Library/Logs/Pulsar-Preview.log`, `~/Library/WebKit/com.fosterinteractive.pulsar.preview` and
  `~/Library/Caches/com.fosterinteractive.pulsar.preview`, run `defaults delete com.fosterinteractive.pulsar.preview`,
  and remove its keychain items (`security delete-generic-password -s pulsar-preview -a productive`, and
  `-a google-calendar`). Pulsar's own folders and items stay. A checkout older than `PULSAR_INSTANCE` used Pulsar's.
