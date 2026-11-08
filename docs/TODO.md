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
- **Moved time, told apart by itself.** "Not an example" is a mark by hand. To think through: whether Pulsar
  can tell such an entry itself (its task does not match the sessions under it) and leave it out of learning without
  a mark.
- **Working days** as a setting (Monday to Friday is fixed today: Earlier days, the week line, the reminder).
- **Advanced settings**, a small closed set (join gap, outside-Claude entry length, learning memory, daily meeting
  cards, split hint, the "No estimate" flag), and a developer override for calibration in read-only runs.
- **Security, to decide:** the window's sign-in through a pipe instead of a file, and writes made with the key
  confirmed in the window.
