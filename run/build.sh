#!/bin/bash
# Build victory.exe (diag profile) from a tree, named like TREE in launch.sh.
# Usage: build.sh [base|matrix|up|...]      base = src/victory, otherwise work/wt-<tree>
# Log: run/build-<tree>.log. Exits with cargo's code, to stop a chain or the queue.
set -u
PORT=${GTA6_PORT:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}
T=${1:-base}
case $T in
  base) D=${GTA6_SRC:-$PORT/src/victory} ;;
  *) D=$PORT/work/wt-$T; [ -d "$D" ] || { echo "unknown tree: $T" >&2; exit 2; } ;;
esac
[ -d "$D" ] || { echo "tree not found: $D" >&2; exit 2; }
D=$(cd "$D" && pwd)
# Security gate: the tree branch against the line (run/ligne.txt). On "REVIEW", refuse,
# unless SCAN_OK=<head commit> after review.
LIGNE=${GTA6_SCAN_BASE:-}
[ -n "$LIGNE" ] || LIGNE=$(cat "$PORT/run/ligne.txt" 2>/dev/null)
[ -n "$LIGNE" ] || LIGNE=main
HEAD_REF=$(git -C "$D" rev-parse --abbrev-ref HEAD)
HEAD=$(git -C "$D" rev-parse --short HEAD)
if ! "$PORT/run/scan-securite.sh" "$LIGNE" "$HEAD_REF" > "$PORT/run/scan-$T.log" 2>&1 \
   && [ "${SCAN_OK:-}" != "$HEAD" ]; then
  echo "build $T refused: security gate 'REVIEW' (run/scan-$T.log); SCAN_OK=<head> after review" >&2; exit 4
fi
# Uncommitted changes: only those in work/local-edits.patch are known and reviewed.
# git diff HEAD is compared with the patch minus its "index" and "@@" lines (fingerprints and
# line numbers change from one branch to another).
if [ "${SCAN_OK:-}" != "$HEAD" ] && ! git -C "$D" diff --quiet HEAD; then
  if [ ! -f "$PORT/work/local-edits.patch" ] \
     || ! diff -q <(git -C "$D" diff HEAD | grep -vE '^(index |@@)') <(grep -vE '^(index |@@)' "$PORT/work/local-edits.patch") >/dev/null; then
    echo "build $T refused: uncommitted changes outside work/local-edits.patch; SCAN_OK=$HEAD after review" >&2; exit 4
  fi
fi
[ -f "$PORT/env.sh" ] && . "$PORT/env.sh"
cd "$D" || exit 1
# Second argument "release": release profile without diagnostics (no captures, no per-reason skips).
if [ "${2:-diag}" = release ]; then P=(--release); else P=(--profile diag --features diagnostics); fi
cargo xwin build "${P[@]}" -p victory --target x86_64-pc-windows-msvc > "$PORT/run/build-$T.log" 2>&1
rc=$?
echo "build $T exit=$rc : $(grep -aE '^error|Finished' "$PORT/run/build-$T.log" | tail -n 3 | tr '\n' ' ')"
exit $rc
