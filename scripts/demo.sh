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

step "5. Canonical content hashes of the sample bundles"
for bundle in "${GOOD[@]}"; do tl hash "$bundle"; done

step "6. Cosmetic edits keep the hash: CRLF line endings, .DS_Store, new author"
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

step "7. A one-character semantic edit changes the hash"
printf 'x' >> "$copy/instruction.md"
changed=$(tl hash "$copy" | cut -d' ' -f1)
echo "semantic: $changed"
[ "$changed" != "$before" ] || fail "a semantic edit kept the hash"
echo "(different: this is new content)"

step "Demo finished"
