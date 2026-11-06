# Pulsar.app (macOS)

A double-clickable Pulsar for colleagues who never open Terminal. The app is a window (`PulsarWindow.swift`,
about 400 lines, the Mac's own WebKit) and a bash launcher: the launcher installs the code the app carries and
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
(or point `PULSAR_GOOGLE_CLIENT` at it); the release and the app carry it in place of the repo's placeholder. It refuses a git tree with changes, a version
that is not newer than `__version__` in `pulsar/__init__.py`, a version with no `CHANGELOG.md` entry, and failing
tests. Then it sets the version, builds into `dist/`, shows what it will do and asks before it:

1. commits the version change as "Pulsar 1.1.0" and pushes the branch;
2. creates the public release `v1.1.0` on `fosterinteractive/pulsar` at that commit, with four files:
   - `pulsar-1.1.0.zip`: the code git tracks, in one folder `pulsar-1.1.0/` (what installed copies download);
   - `latest.json`: version, download address, SHA-256 of the zip, the notes and the date (what they read);
   - `latest.json.sig`: the release key's signature of `latest.json` (see below);
   - `Pulsar.zip`: the whole app, for new installs. Share
     `https://github.com/fosterinteractive/pulsar/releases/latest/download/Pulsar.zip` with new colleagues.

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

Click-only updates also limit the damage of a stolen release key: a forged release reaches only the people who
click Update, not everyone within six hours.

**Keep the repository public.** Installed copies and the Pulsar.zip link download without a GitHub sign-in. In a
private repository every download answers 404: Settings then says no release is published, and an installed
copy writes a warning to its error log once a day.

**The release keys.** Installed copies trust only releases signed with one of the ECDSA P-256 keys listed in
`PUBLIC_KEYS` in `pulsar/updater.py`: the main key, then a reserve key.

- **Main key**: `~/.config/pulsar-release/update-signing-key.pem` on the Mac of whoever publishes (owner-only, never in
  the repo; `PULSAR_SIGNING_KEY` points elsewhere). `release.sh` signs with it and refuses a key that is not listed.
  Keep a copy in the team's 1Password vault, so another maintainer can publish and a lost Mac loses nothing.
  Encrypted (`openssl ec -aes256 -in key.pem -out key.enc.pem`), `release.sh` asks for its passphrase, so publishing
  then needs a person at a terminal.
- **Reserve key**: kept only in 1Password, never on a laptop. If the main key is lost or leaks, sign one release with
  the reserve (`PULSAR_SIGNING_KEY=<reserve file> macos/release.sh …`) whose `PUBLIC_KEYS` drops the old main key and
  adds a new one; every installed copy accepts it and from then on trusts the new list, with no reinstall.
  Create it on the Mac of whoever keeps it, so the private half is never seen anywhere else:

  ```bash
  /usr/bin/openssl ecparam -name prime256v1 -genkey -noout | /usr/bin/openssl ec -aes256 -out ~/Desktop/pulsar-reserve-key.pem
  /usr/bin/openssl ec -in ~/Desktop/pulsar-reserve-key.pem -pubout     # the public half: add it to PUBLIC_KEYS
  ```

  Put the file and its passphrase in 1Password, delete the file from the Desktop, and publish a release that lists
  the public half. A key can only be added by a release signed with a key already listed, so the reserve has to be
  in place before people install.

Anyone with write access to the repository can publish a release, but without a listed key installed copies ignore
it, and releases are immutable once published (repository setting), so nobody can swap a file of a published
release. For the first release of a version (for example 1.0.0 before any tag exists) `release.sh` accepts the
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
writes that version into `bad-versions`, points `current` back at `previous` and starts it. A bad version is
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

`dist/` is build output and ignored by git.

Inside the app:

```
Pulsar.app/Contents/
  Info.plist           com.fosterinteractive.pulsar, macOS 12+, local networking allowed (127.0.0.1)
  MacOS/Pulsar         the window (PulsarWindow.swift), one binary for Apple Silicon and Intel
  Resources/launcher.sh
  Resources/AppIcon.icns
  Resources/app/       pulsar.py, pulsar/ (no __pycache__), web/, README.md: the copy the app installs
```

The bundle is ad-hoc signed (`codesign -s -`), which is enough to run on Apple Silicon. It has no Developer ID
signature, so the first open needs the confirmation described in the main README (`spctl -a -t exec` says
"rejected"), and a company-managed Mac may refuse it outright. See "Removing the first-open warning" below.

## How the window works

It runs `launcher.sh --window`, which does everything below and prints `PULSAR_URL <address>?code=<one-time
code>` instead of opening a browser; the window loads it and the page signs in. While that runs it shows
"Starting Pulsar…"; if the launcher fails, its own dialog says why and the app quits.

- Only `http://127.0.0.1:<port>` opens in the window. A link elsewhere that the person clicks goes to the default
  browser; anything else (another site, a frame, a new window) is refused.
- Downloads (the page's `<a download>` with a blob) go through `WKDownload` and a save panel. **Print or save as
  PDF** asks the window to print (the page posts `{action: "print"}`; a WebView ignores `window.print()`).
- **Sign in** on the "not signed in" notice, **View > Reload** when the server is gone, and **Start Pulsar** run
  the launcher again for a fresh code. Cmd+, opens Settings; Cmd+=, Cmd+- and Cmd+0 zoom.
- Closing the window quits the app, and quitting runs `launcher.sh --stop`: the server stops if this app started
  it and it holds this user's key. A server started from a checkout keeps running.
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
   `xcode-select --install` (macOS asks for an administrator password), then exits. The person opens Pulsar
   again afterwards. With the tools but a Python older than 3.9, it points to Software Update.
2. **Install.** It compares the version the app carries (B, `Resources/app`) with the installed one (C,
   `current`). With nothing installed, or B newer than C and not in `bad-versions`, it copies B to
   `versions/B`, points `previous` at the old `current` and `current` at `versions/B`. When C is newer (the
   app has updated itself since this bundle was built), C stays. Versions compare numerically (1.10.0 > 1.9.0).
3. **Already running?** If `/api/config` answers with C's version, or the server was not started by the app
   (for example `python3 pulsar.py` from a checkout, which is never stopped), it opens the page and exits. A
   server the app started with another version is asked to quit (`POST /api/quit`) and C starts instead. If
   another program holds the port, it says so and changes nothing.
4. **Start.** It runs `python3 -B <real path of current>/pulsar.py --no-open --port 5610` with `nohup`, in a
   new session, so the server outlives the launcher. `-B` writes no `__pycache__`. Output is appended to
   `~/Library/Logs/Pulsar.log`, together with what the launcher decided.
5. **Check and roll back.** It waits up to 15 seconds for `/api/config`, then opens the page. If the server
   exits or never answers and `previous` is another version, C goes into `bad-versions`, `current` goes back
   to `previous`, and that starts. Only if that fails too does a dialog point to the log.

For tests, a throwaway `HOME` so the real install root is not touched. The launcher alone:

```bash
HOME=$(mktemp -d) PULSAR_PORT=5699 PULSAR_NO_BROWSER=1 dist/Pulsar.app/Contents/Resources/launcher.sh
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
starts the code with `PULSAR_INSTANCE=preview`. "Remove token" or "Disconnect" in Preview never touch Pulsar's, and
neither app's saves drop the other's settings. It never installs or updates anything, and is never zipped or released.

- **It writes nothing to Productive** unless **Settings > Updates > Preview > Allow this preview to write** is on.
  The chip at the top always shows the branch and commit it runs; it turns red while writing is allowed. The launcher
  also sets `PULSAR_READ_ONLY=preview`: this code reads that as "a preview" and follows the setting, while a checkout
  older than Preview (`main` before it, 1.2.x) takes any value as read-only and writes nothing at all.
- **The first start** copies Pulsar's settings once (rules, time zone, who you are; not the Google account, the
  token source, update choices or write permission). Productive then comes through Claude Code's connection, with no
  setup; to use a token instead, paste one in Preview's Settings. Google Calendar needs **Connect** once in Preview's
  Settings (it finds the sign-in client in Pulsar's folder). An older checkout, which does not know
  `PULSAR_INSTANCE`, keeps using Pulsar's folders: the launcher asks the code where it keeps them.
- **Set up once:** a checkout for it, and the app:

  ```bash
  git worktree add ~/Sites/pulsar-preview -b preview main
  PULSAR_PREVIEW_DIR="$HOME/Sites/pulsar-preview" macos/build_app.sh
  ditto "dist/Pulsar Preview.app" "/Applications/Pulsar Preview.app"
  ```
- **Put new work in it:** merge the branches to try into `preview` (in `~/Sites/pulsar-preview`, so the main checkout
  is not touched), then quit and reopen Pulsar Preview: closing its window stops its server, and opening it runs the
  code as it is now. Page files (`web/`) also reload with ↻; Python changes need the reopen.
- **Release** only what was tried in Preview: merge it into `main` and run `release.sh` as below.
- **Remove it:** switch off Claude Code in its Settings first if it is on (that removes `pulsar-preview` from Claude
  Code), quit it, delete `/Applications/Pulsar Preview.app`, `~/.config/pulsar-preview`, `~/.cache/pulsar-preview`,
  `~/Library/Logs/Pulsar-Preview.log`, `~/Library/WebKit/com.fosterinteractive.pulsar.preview` and
  `~/Library/Caches/com.fosterinteractive.pulsar.preview`, run `defaults delete com.fosterinteractive.pulsar.preview`,
  and remove its keychain items (`security delete-generic-password -s pulsar-preview -a productive`, and
  `-a google-calendar`). Pulsar's own folders and items stay. A checkout older than `PULSAR_INSTANCE` used Pulsar's.
