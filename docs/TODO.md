# To do

Agreed next steps, newest first. Done items go to `CHANGELOG.md`.

- **A security check on every release, by Claude in a GitHub Action.** As the QA hub reviews pull requests: first
  look at how it runs, then build a light security checker for Pulsar's pull requests and releases (agreed
  2026-11-11, after the team demo; not started).
- **Parallel time, in Settings.** How time is split when two sessions run at once (two clients: half of the shared
  minutes to the one in the background, from 15 minutes; a client's agent over internal work: its minutes, from 10)
  is the same for everyone now. Asked at the team demo, agreed 2026-11-11: each person can change the share and the
  thresholds in Settings > Advanced.
- **Names from words.** A turn is named by the words in its commands (the fi-time-from-activity script's naming), so
  a client's name or "time logging" shows up in work on Pulsar itself when a command only mentions them. To fix.

- **Release keys: make the fingerprint count.** The old key (a file on the release Mac, no fingerprint) still signs
  `latest.json`, so copies older than 1.5.1, which know only that key, keep getting updates. But every copy still
  trusts it too, so whoever gets that file can sign an update all of them accept: until the old key is gone, the
  Touch ID key protects nothing. To sort out: how many copies are still older than 1.5.1 (downloads per release),
  then one release signed with Touch ID only that drops the old key from `PUBLIC_KEYS`; delete every copy of the
  file; anyone still on 1.5.0 or older runs the install line once. Then sign with one touch, not two.
- **The warp, smoother.** It is not smooth now, and on an update it bends oddly: the new build already shows behind
  it before the warp ends. To test together; maybe a nicer loading animation instead.
- **Moved time, told apart by itself.** "Not an example" is a mark by hand. Tried 2026-11-09: an entry over a
  repository's work, to a project under 5% of that repository's logged time and logged from another repository, taught
  nothing (`attribution.credits`). Replayed on 19 days it was slightly worse (top 3 48.7 to 47.7%, right minute 48.8 to
  48.2%), so it is not in. Another sign is needed, or the mark stays the way.
- **Security, to decide:** the window's sign-in through a pipe instead of a file, and writes made with the key
  confirmed in the window. Reviewed 2026-11-09: the pipe is not worth building; for writes, no confirm before them,
  but perhaps a toast with Undo after Claude Code writes (about 50 lines).
- **Productive through Claude Code's connection, to decide.** To take the Productive token, Pulsar reads the whole
  keychain item "Claude Code-credentials", which probably also holds the claude.ai sign-in: Anthropic's terms do not
  let other programs collect it. Pulsar never uses it, but it reads it. Options: tell people to use their own token;
  Automatic uses the token only, and Claude Code's connection only when chosen, with the reason shown; or drop the
  connection. A token never expires, but the daily target then comes from Settings, not Productive.
