#!/bin/bash
# Offline shader census per worktree.
# No game, no wine, no GPU, no launch.sh/build.sh/queue.txt; never touches the worktrees'
# sources (read-only path dependencies).
# Usage: run/census.sh <tree>        tree = base | fid4 | permlane | ...
#        PACK=<pack> run/census.sh <tree>   (default: the game's shaders.rpf)
#        run/census.sh <tree> --cache       fill run/state/cache-<tree>/shaders ahead of a
#                                           run (source-keyed trees only)
# Builds the probe natively for the host (nice -n 19, -j 2, one cargo at a time -- a game
# trial is measuring), runs it on the pack with host wave 32, writes run/census-<tree>.txt
# (one line per program: class, hash, SGD5 name, reason) and prints counts per class.
# With --cache it instead walks each distinct container once, translates it as the game's
# shader_cache::host_shader does, and writes the exact cache entry under
# run/state/cache-<tree>/shaders (never overwriting an existing entry); it compares what
# it computes with entries already there and prints the before/after count. Runtime log:
# work/census/<tree>/cache.log. The lock work/census/.lock serialises every census run.
# Exit 0 even if programs fail; non-zero only if the tool itself fails.
set -u
PORT=${GTA6_PORT:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}
T=${1:?usage: run/census.sh <tree> [--cache]}
MODE=census
case ${2:-} in
  "") ;;
  --cache) MODE=cache ;;
  *) echo "unknown option: $2" >&2; exit 2 ;;
esac
case $T in
  base) D=${GTA6_SRC:-$PORT/src/victory} ;;
  *) D=$PORT/work/wt-$T; [ -d "$D" ] || { echo "unknown tree: $T" >&2; exit 2; } ;;
esac
[ -d "$D" ] || { echo "tree not found: $D" >&2; exit 2; }
D=$(cd "$D" && pwd)
PACK=${PACK:-${GTA6_GAME:?set GTA6_GAME to the game directory}/shaders.rpf}
[ -r "$PACK" ] || { echo "pack unreadable: $PACK" >&2; exit 2; }
C=$PORT/work/census/$T
OUT=$PORT/run/census-$T.txt
[ -f "$PORT/env.sh" ] && . "$PORT/env.sh"
# A cache keyed by translator sources is the only one an offline probe can reproduce without the
# game's exact victory.exe.
KEYED=0
if [ -f "$D/crates/victory-shader/src/lib.rs" ] &&
   grep -q 'pub const SOURCE_HASH' "$D/crates/victory-shader/src/lib.rs"; then
  KEYED=1
fi
if [ "$MODE" = cache ] && [ "$KEYED" -ne 1 ]; then
  echo "census $T --cache: tree without a source key (no SOURCE_HASH) -- impossible outside the game" >&2
  echo "  the key is the BLAKE3 of victory.exe; host.txt only gives its truncated sha256" >&2
  exit 2
fi
mkdir -p "$C/src"
# One source for the probe (run/census-probe.rs); a small Cargo.toml per tree makes it
# path-depend on the worktree under test.
SHADER_FEATURES=
[ "$MODE" = cache ] && SHADER_FEATURES=', features = ["diagnostics"]'
cat > "$C/Cargo.toml" <<EOF
[package]
name = "census-probe"
version = "0.0.0"
edition = "2024"
publish = false

[workspace]

[features]
source-key = []

[dependencies]
victory-shader = { path = "$D/crates/victory-shader"$SHADER_FEATURES }
victory-game = { path = "$D/crates/victory-game" }
blake3 = "1.8.7"
memchr = "2.8.3"
EOF
cp "$PORT/run/census-probe.rs" "$C/src/main.rs"
# One cargo at a time.
exec 9>"$PORT/work/census/.lock"
flock 9
BUILD=(nice -n 19 cargo build --release -j 2 --manifest-path "$C/Cargo.toml")
[ "$MODE" = cache ] && BUILD+=(--features source-key)
"${BUILD[@]}" > "$C/build.log" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "census $T: build exit=$rc (log: $C/build.log)" >&2
  tail -n 20 "$C/build.log" >&2
  exit $rc
fi
if [ "$MODE" = cache ]; then
  case $T in base|matrix) CACHE=cache ;; *) CACHE=cache-$T ;; esac
  CDIR=$PORT/run/state/$CACHE/shaders
  mkdir -p "$CDIR"
  # The game sets the null slot from the GPU (probe_view_limit): the latest run of this tree
  # logged "host view heaps hold N views", so the key uses N-1.
  VIEW=${VIEW_LIMIT:-}
  if [ -z "$VIEW" ]; then
    LAST=$(grep -l "^TREE=$T$" "$PORT"/run/state/logs/*/env.txt 2>/dev/null | sort | tail -1)
    if [ -n "$LAST" ]; then
      N=$(sed 's/\x1b\[[0-9;]*m//g' "${LAST%/env.txt}/game.log" 2>/dev/null |
          grep -o 'host view heaps hold [0-9]* views' | tail -1 | awk '{print $5}')
      { [ -n "$N" ] && [ "$N" -gt 1 ]; } 2>/dev/null && VIEW=$((N - 1))
    fi
  fi
  VIEW=${VIEW:-999999}   # the NVIDIA cap this host logs (1000000 views)
  ARGS=(--cache "$CDIR" --encoding "$D/crates/victory-gpu/src/shader_cache.rs" --view-limit "$VIEW")
  # Some trees list VICTORY_WAVE_LDS_UNORDERED a second time in their cache key, so repeat it here
  # exactly where the game does.
  grep -q 'chain(\["VICTORY_WAVE_LDS_UNORDERED"\])' \
    "$D/crates/victory-gpu/src/shader_cache.rs" 2>/dev/null &&
    ARGS+=(--repeat-switch VICTORY_WAVE_LDS_UNORDERED)
  [ "${AOT_DRY:-0}" = 1 ] && ARGS+=(--dry-run)
  [ -n "${AOT_LIMIT:-}" ] && ARGS+=(--limit "$AOT_LIMIT")
  BEFORE=$(find "$CDIR" -type f 2>/dev/null | wc -l)
  SECONDS=0
  VICTORY_HOST_WAVE=${VICTORY_HOST_WAVE:-32} \
  VICTORY_SHADER_LOOP_LIMIT=${VICTORY_SHADER_LOOP_LIMIT:-256} \
    "$C/target/release/census-probe" "$PACK" "${ARGS[@]}" > "$C/cache.log" 2>&1
  rc=$?
  AFTER=$(find "$CDIR" -type f 2>/dev/null | wc -l)
  cat "$C/cache.log"
  echo "== cache $T: $BEFORE -> $AFTER files, ${SECONDS}s (log: $C/cache.log)"
  exit $rc
fi
VICTORY_HOST_WAVE=${VICTORY_HOST_WAVE:-32} "$C/target/release/census-probe" "$PACK" | tee "$OUT"
rc=${PIPESTATUS[0]}
if [ $rc -ne 0 ]; then
  echo "census $T: tool exit=$rc" >&2
  exit $rc
fi
echo "== census $T, counts by class:"
awk '{c[$1]++} END {for (k in c) printf "  %s %d\n", k, c[k]}' "$OUT" | sort
exit 0
