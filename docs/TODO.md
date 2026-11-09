# To do

Agreed next steps, newest first. Done items go to `CHANGELOG.md`.

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
  confirmed in the window.
