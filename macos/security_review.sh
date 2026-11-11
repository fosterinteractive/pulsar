#!/bin/bash
# The security review before a release, on this Mac: Claude Code reads what changed since the last release with
# .github/security-review.md (Anthropic's /security-review method and Pulsar's own boundaries), read-only, and ends
# with a verdict. release.sh runs it and stops on "block". Once the repository is private the same review runs on
# GitHub too (.github/workflows/security.yml), where colleagues can see each one.
#
#   macos/security_review.sh [<base tag> [<commit>]]
#
# Prints the review and keeps it in dist/security-review.md; exits 0 on "VERDICT: pass", 1 on "block" or no verdict.
# The base is the last release tag before the commit (HEAD). Claude reads a copy of the commit (git archive) without
# the files that steer agents (CLAUDE.md, .claude, …) and the changes as files, with Read, Grep and Glob only: no
# shell, no network, no writes. It runs on this person's Claude Code subscription.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
die() { echo "security_review.sh: $*" >&2; exit 1; }

command -v claude >/dev/null || die "needs Claude Code (claude) on this Mac"
git fetch -q --tags origin 2>/dev/null || true
HEAD_SHA="$(git rev-parse --verify -q "${2:-HEAD}^{commit}")" || die "no commit ${2:-HEAD}"
BASE="${1:-$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' "$HEAD_SHA" 2>/dev/null || true)}"
[[ "$BASE" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "no release tag before ${HEAD_SHA:0:7}: give one, e.g. v1.6.17"
git rev-parse --verify -q "$BASE^{commit}" >/dev/null || die "no tag $BASE"
git merge-base --is-ancestor "$BASE" "$HEAD_SHA" || die "$BASE is not before ${HEAD_SHA:0:7}"
[ "$(git rev-parse "$BASE^{commit}")" != "$HEAD_SHA" ] || die "nothing changed since $BASE"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src" "$TMP/in"
git archive "$HEAD_SHA" | tar -x -C "$TMP/src"
[ -f "$TMP/src/.github/security-review.md" ] || die "${HEAD_SHA:0:7} has no .github/security-review.md"
rm -rf "$TMP/src/CLAUDE.md" "$TMP/src/AGENTS.md" "$TMP/src/.claude" "$TMP/src/.cursor" "$TMP/src/.mcp.json"
git log --format='%h %s' "$BASE..$HEAD_SHA" > "$TMP/in/commits.txt"
git diff --stat "$BASE" "$HEAD_SHA" > "$TMP/in/diffstat.txt"
git diff "$BASE" "$HEAD_SHA" -- . ':(exclude)*.png' ':(exclude)*.icns' ':(exclude)*.jpg' > "$TMP/in/diff.patch"

# the prompt as the reviewed commit has it, its header comment dropped and the inputs listed
PROMPT="$(python3 -I - "$TMP/src/.github/security-review.md" "$TMP/in" <<'PY'
import re, sys
text = re.sub(r"\A<!--.*?-->\s*", "", open(sys.argv[1], encoding="utf-8").read(), flags=re.S)
d = sys.argv[2]
print(text.replace("{{INPUTS}}", f"- Commits: {d}/commits.txt\n- Diffstat: {d}/diffstat.txt\n"
                                 f"- The diff to review: {d}/diff.patch"))
PY
)"

echo "Security review of $BASE..${HEAD_SHA:0:7} with Claude Code (about a minute)" >&2
(cd "$TMP/src" && claude -p "$PROMPT" --model sonnet --max-turns 60 --allowedTools Read Grep Glob \
  --disallowedTools Bash Write Edit MultiEdit NotebookEdit WebFetch WebSearch --add-dir "$TMP/in" \
  --output-format json) > "$TMP/out.json" 2> "$TMP/err.txt" ||
  { tail -5 "$TMP/err.txt" >&2; die "Claude Code did not finish the review"; }

mkdir -p "$ROOT/dist"
python3 -I - "$TMP/out.json" "$ROOT/dist/security-review.md" "$BASE" "$HEAD_SHA" <<'PY'
import json, re, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
review = (d.get("result") or "").strip()
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    fh.write(f"# Security review {sys.argv[3]}..{sys.argv[4][:7]}\n\n{review}\n")
print(review)
print(f"\n({d.get('num_turns')} turns, {round((d.get('duration_ms') or 0) / 1000)} s; kept in dist/security-review.md)",
      file=sys.stderr)
# the verdict is the last line, exactly: anything else is no pass
last = next((l.replace("*", "").replace("`", "").strip() for l in reversed(review.splitlines()) if l.strip()), "")
sys.exit(0 if last == "VERDICT: pass" else 1)
PY
