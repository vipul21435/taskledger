#!/usr/bin/env bash
# End-to-end demo of the TaskLedger CLI on the bundled sample data.
#
#   TASKLEDGER  command that runs the CLI (default: "uv run taskledger")
#   EXAMPLES    directory holding bundles/ and flawed/ (default: ./examples)
#
# Runs offline in a few seconds and exits non-zero if any step does not
# behave as described.
set -euo pipefail

TASKLEDGER=${TASKLEDGER:-uv run taskledger}
EXAMPLES=${EXAMPLES:-examples}
GOOD=("$EXAMPLES"/bundles/*)
FLAWED="$EXAMPLES/flawed/digit-sum-report"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

tl() { $TASKLEDGER "$@"; }
step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'demo failed: %s\n' "$*" >&2; exit 1; }

step "1. Installed version and registered lint rules"
tl --version
tl rules

step "2. Schema validation: two sample bundles and one flawed bundle"
tl validate "${GOOD[@]}" "$FLAWED"

step "3. Lint the sample bundles (expected: clean, exit 0)"
tl lint "${GOOD[@]}"

step "4. Lint the flawed bundle (expected: findings, exit 1)"
set +e
tl lint --no-hints "$FLAWED"
status=$?
set -e
[ "$status" -eq 1 ] || fail "lint of the flawed bundle exited $status, expected 1"
echo "(exit code $status, as expected)"

step "5. The same findings as SARIF 2.1.0 for GitHub code scanning"
sarif="$WORK/lint.sarif"
set +e
tl lint --format sarif "$FLAWED" > "$sarif"
status=$?
set -e
[ "$status" -eq 1 ] || fail "SARIF lint of the flawed bundle exited $status, expected 1"
grep -q '"version": "2.1.0"' "$sarif" || fail "the SARIF log does not declare version 2.1.0"
results=$(grep -c '"ruleId"' "$sarif")
echo "wrote $results SARIF results ($(wc -c < "$sarif" | tr -d ' ') bytes) for upload-sarif"

step "6. Canonical content hashes of the sample bundles"
for bundle in "${GOOD[@]}"; do tl hash "$bundle"; done

step "7. Cosmetic edits keep the hash: CRLF line endings, .DS_Store, new author"
original="${GOOD[0]}"
copy="$WORK/$(basename "$original")"
cp -R "$original" "$copy"
find "$copy" -name __pycache__ -prune -exec rm -rf {} +
awk '{printf "%s\r\n", $0}' "$copy/instruction.md" > "$WORK/crlf" && mv "$WORK/crlf" "$copy/instruction.md"
printf 'junk' > "$copy/.DS_Store"
awk '/^authors = /{print "authors = [\"Someone Else\"]"; next} {print}' "$copy/task.toml" > "$WORK/toml" \
  && mv "$WORK/toml" "$copy/task.toml"
before=$(tl hash "$original" | cut -d' ' -f1)
after=$(tl hash "$copy" | cut -d' ' -f1)
echo "original: $before"
echo "cosmetic: $after"
[ "$before" = "$after" ] || fail "a cosmetic edit changed the hash"
echo "(identical: a resubmission with only cosmetic edits is a duplicate)"

step "8. A one-character semantic edit changes the hash"
printf 'x' >> "$copy/instruction.md"
changed=$(tl hash "$copy" | cut -d' ' -f1)
echo "semantic: $changed"
[ "$changed" != "$before" ] || fail "a semantic edit kept the hash"
echo "(different: this is new content)"

step "9. Dedupe cache: store a bundle twice, then the edited copy"
export TASKLEDGER_HOME="$WORK/home"
export TASKLEDGER_DATABASE_URL="sqlite:///$WORK/home/ledger.db"
tl cache put "$original"
again=$(tl cache put "$original")
echo "$again"
case "$again" in *"already cached"*) ;; *) fail "storing an identical bundle wrote new objects" ;; esac
tl cache put "$copy"
echo "(the edited copy only adds its changed file and a new tree object)"

expect_exit() {
  local want=$1; shift
  set +e
  "$@"
  local got=$?
  set -e
  [ "$got" -eq "$want" ] || fail "'$*' exited $got, expected $want"
  echo "(exit code $got, as expected)"
}

step "10. Ledger: register, then an exact collision and an ID collision"
tl ledger init
tl ledger register --actor alice "$original"
expect_exit 1 tl ledger register --actor bob "$original"
expect_exit 1 tl ledger register --actor bob "$copy"

step "11. Review state machine and the hash-chained audit log"
slug=$(basename "$original")
tl ledger transition --actor alice "$slug" submitted
tl ledger transition --actor reviewer "$slug" in_review
tl ledger transition --actor reviewer --note "grader re-run twice, byte-exact" "$slug" accepted
expect_exit 1 tl ledger transition --actor alice "$slug" submitted
tl ledger history "$slug"
tl ledger verify

step "Demo finished"
