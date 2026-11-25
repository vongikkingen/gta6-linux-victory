#!/bin/bash
# Body between { } : bash reads the whole block before executing it, so editing this file
# during a trial does not change the trial in progress.
{
# Launch victory.exe under Proton Experimental's wine, in a bubblewrap sandbox:
# no network, game read-only on /game, nothing from the home directory except the port state
# on /port, empty environment (env -i), RAM capped by systemd.
# Usage: run/launch.sh <seconds> <label> [VAR=value ...]
#   WINESERVER_BIN=stock|viewtree   (default: viewtree if it exists)
#   BUILD=diag|release              (default: diag)
#   STEADY_STOP=N                   stop the game after N world reports with no pipeline queued
#                                   (~5 s per report; without it the trial lasts <seconds>)
#   HANG_S=N                        stop the game if game.log has not moved for N s (default 120)
# Per trial: env.txt, tree.txt (tree launched), host.txt (exe and d3d12core.dll fingerprints,
# memory and load at start), stats.txt (run/stats.py at the end).
set -u
SECS=${1:?seconds}; LABEL=${2:?label}; shift 2
PORT=${GTA6_PORT:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}
GAME=${GTA6_GAME:?set GTA6_GAME to the game directory}
GAME_ORIG=$GAME
GAME=$(cd "$GAME" 2>/dev/null && pwd) || { echo "game directory not found: $GAME_ORIG" >&2; exit 2; }
PROTON=${PROTON_DIR:-"$HOME/.local/share/Steam/steamapps/common/Proton - Experimental"}
PROTON_ORIG=$PROTON
PROTON=$(cd "$PROTON" 2>/dev/null && pwd) || { echo "Proton directory not found: $PROTON_ORIG" >&2; exit 2; }
RUNTIME=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
BUILD=${BUILD:-diag}
STATE=$PORT/run/state                      # seen as /port in the sandbox
RUN=$(date +%m%d-%H%M%S)-$LABEL
LOG=$STATE/logs/$RUN
mkdir -p "$LOG/capture" "$STATE/home/.cache" "$STATE/cache" "$STATE/data"

if pgrep -f '^(Z:.victory.victory[.]exe|/proton/files/bin/wine /victory/)' >/dev/null; then echo "a game is already running" >&2; exit 75; fi   # 75: transient refusal, the queue retries

# The patched wineserver is mounted over Proton's, in the sandbox only:
# it then finds share/wine/nls next to itself. Proton is not modified on disk.
VIEWTREE=$PORT/wine/out/wineserver
WSBIND=()
case ${WINESERVER_BIN:-auto} in
  stock) WS=stock ;;
  viewtree|auto) if [ -x "$VIEWTREE" ]; then WS=viewtree; WSBIND=(--ro-bind "$VIEWTREE" /proton/files/bin/wineserver); else WS=stock; fi ;;
esac
[ "${WINESERVER_BIN:-auto}" = viewtree ] && [ "$WS" = stock ] && { echo "patched wineserver missing" >&2; exit 4; }

# VKD3D=fix : vkd3d-proton rebuilt with the dxil-spirv fix, mounted over
# the prefix DLLs in the sandbox only.
# TREE=matrix : build of the PR stack (work/wt-matrix) instead of src/victory.
case "${TREE:-base}" in
  base) TREE_DIR=${GTA6_SRC:-$PORT/src/victory} ;;
  *) TREE_DIR=$PORT/work/wt-$TREE; [ -d "$TREE_DIR" ] || { echo "unknown TREE: $TREE"; exit 2; } ;;
esac
[ -d "$TREE_DIR" ] || { echo "tree not found: $TREE_DIR" >&2; exit 2; }
TREE_DIR=$(cd "$TREE_DIR" && pwd)
# Shader cache and pipeline library: one per lineage (base and matrix share the first).
case "${TREE:-base}" in base|matrix) CACHE=cache ;; *) CACHE=cache-$TREE ;; esac
mkdir -p "$STATE/$CACHE"
VKBIND=()
# VKD3D=noopt : same build plus VKD3D_PIPELINE_NO_OPT (vkd3d/out/x64-noopt).
case "${VKD3D:-stock}" in fix) VKDIR=x64 ;; noopt) VKDIR=x64-noopt ;; *) VKDIR= ;; esac
if [ -n "$VKDIR" ]; then
  for f in d3d12.dll d3d12core.dll; do
    VKBIND+=(--ro-bind "$PORT/vkd3d/out/$VKDIR/$f" "/port/prefix/pfx/drive_c/windows/system32/$f")
  done
fi
ENVV=(
  HOME=/port/home USER=player PATH=/usr/bin:/bin LANG=en_US.UTF-8
  DISPLAY="$DISPLAY" XAUTHORITY=/xauth XDG_RUNTIME_DIR="$RUNTIME"
  PULSE_SERVER="unix:$RUNTIME/pulse/native"
  WINEPREFIX=/port/prefix/pfx WINEFSYNC=1 SteamGameId=victory SteamAppId=victory WINEDEBUG=-all
  WINEDLLOVERRIDES="d3d12=n;d3d12core=n;dxgi=n;d3d11=n"
  VK_LOADER_DRIVERS_SELECT='*nvidia*'
  VKD3D_CONFIG=descriptor_heap VKD3D_DISABLE_EXTENSIONS=VK_NV_raw_access_chains
  VKD3D_DEBUG=warn VKD3D_LOG_FILE="Z:\\port\\logs\\$RUN\\vkd3d.log"
  VKD3D_SHADER_CACHE_PATH=/port/home/.cache/vkd3d
  __GL_SHADER_DISK_CACHE=1 __GL_SHADER_DISK_CACHE_PATH=/port/home/.cache/nvidia
  __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1 __GL_SHADER_DISK_CACHE_SIZE=10737418240
  VICTORY_CACHE_DIR="Z:\\port\\$CACHE" VICTORY_DATA_DIR='Z:\port\data'
  VICTORY_LOG=info VICTORY_NO_WATCHDOG=1 VICTORY_SHADER_LOOP_LIMIT=256
  VICTORY_CAPTURE="Z:\\port\\logs\\$RUN\\capture"
)
for kv in "$@"; do ENVV+=("$kv"); done
printf '%s\n' "BUILD=$BUILD" "WINESERVER=$WS" "VKD3D=${VKD3D:-stock}" "TREE=${TREE:-base}" "${ENVV[@]}" | grep -vE '^(XAUTHORITY|DISPLAY|PULSE)' > "$LOG/env.txt"
(cd "$TREE_DIR" && git log --oneline -1 && git log --oneline --merges -10 && git --no-optional-locks status --short) > "$LOG/tree.txt"

CMD=(/proton/files/bin/wine /victory/victory.exe run 'Z:\game\game_scarlett_master.exe' --execute)
[ "$LABEL" = init ] && CMD=(/proton/files/bin/wine wineboot -u)   # create the prefix, in the sandbox
# prebuild* label: translate and build pipelines for the shader pack outside the game (source tree), JOBS threads.
case "$LABEL" in prebuild*)
  CMD=(/proton/files/bin/wine /victory/victory.exe translate --cache --pipelines --jobs "${JOBS:-10}"
       'Z:\game\shaders.rpf' 'Z:\game\game_scarlett_master.exe' 'Z:\game\XboxBvhBuilder.dll' 'Z:\game\amd_fidelityfx_upscaler_xbox.dll')
  NEED_MB=${NEED_MB:-8000} ;;
esac
# Wine writes the registry only when the wineserver stops: wait for it (wineserver -w) before the
# sandbox kills its processes, otherwise the prefix is left without system.reg.
# Memory guard: the game takes ~16 GB resident in the world. Refuse to launch if available
# RAM plus free swap is not enough, so the system is not pushed into a global OOM.
NEED_MB=${NEED_MB:-18000}
AVAIL_MB=$(awk '/MemAvailable/{a=$2} /SwapFree/{s=$2} END{print int((a+s)/1024)}' /proc/meminfo)
if [ "$LABEL" != init ] && [ "$AVAIL_MB" -lt "$NEED_MB" ] && [ -z "${FORCE:-}" ]; then
  echo "not enough memory: ${AVAIL_MB} MB available (RAM + free swap), ${NEED_MB} MB requested" >&2; exit 75
fi
D3D=$STATE/prefix/pfx/drive_c/windows/system32/d3d12core.dll
for b in "${VKBIND[@]}"; do case $b in "$PORT"/*/d3d12core.dll) D3D=$b ;; esac; done
{ for f in "$TREE_DIR/target/x86_64-pc-windows-msvc/$BUILD/victory.exe" "$D3D"; do
    echo "${f##*/}=$(sha256sum "$f" 2>/dev/null | cut -c1-16)"
  done
  echo "AVAIL_MB=$AVAIL_MB"; echo "LOADAVG=$(cut -d' ' -f1-3 /proc/loadavg)"; } > "$LOG/host.txt"
SINCE=$(date '+%F %T')
echo "logs: $LOG"
# Watchdog: below FLOOR_MB of available RAM + free swap, stop the game ourselves before
# the kernel's global OOM picks a victim elsewhere (shell, terminal).
FLOOR_MB=${FLOOR_MB:-1500}
( while sleep 2; do
    a=$(awk '/MemAvailable/{a=$2} /SwapFree/{s=$2} END{print int((a+s)/1024)}' /proc/meminfo)
    if [ "$a" -lt "$FLOOR_MB" ]; then
      echo "$(date '+%T') memory stop: ${a} MB available" >> "$LOG/memory-stop.txt"
      pkill -KILL -f '^(Z:.victory.victory[.]exe|/proton/files/bin/wine /victory/)'; break
    fi
    # Frozen game: game.log immobile for HANG_S seconds (default 120), stop the trial.
    sz=$(stat -c %s "$LOG/game.log" 2>/dev/null || echo 0)
    if [ "$sz" != "${last_sz:-}" ]; then last_sz=$sz; still=0; else still=$((still + 2)); fi
    if [ "$still" -ge "${HANG_S:-120}" ] && [ "$sz" -gt 0 ]; then
      echo "$(date '+%T') stop: game.log immobile for ${still} s" >> "$LOG/hang-stop.txt"
      pkill -KILL -f '^(Z:.victory.victory[.]exe|/proton/files/bin/wine /victory/)'; break
    fi
    if [ -n "${STEADY_STOP:-}" ]; then
      n=$(grep -acE '[0-9]{4} draws/frame, dropped [0-9]+ without a host pipeline \(0 being made\)' "$LOG/game.log" 2>/dev/null)
      if [ "${n:-0}" -ge "$STEADY_STOP" ] 2>/dev/null; then
        echo "$(date '+%T') stop: $n steady-regime reports" >> "$LOG/steady-stop.txt"
        pkill -KILL -f '^(Z:.victory.victory[.]exe|/proton/files/bin/wine /victory/)'; break
      fi
    fi
  done ) &
GUARD=$!
# VRAM, load and card temperature every 5 s (degradation, leaks): gpu.csv.
nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used,temperature.gpu,power.draw --format=csv,noheader -l 5 > "$LOG/gpu.csv" 2>/dev/null &
GPUMON=$!
# PCIe traffic (rxpci/txpci, MB/s) every 5 s: is the copy-back limited by the bus (3090 at 4.0 x8)?
nvidia-smi dmon -s t -d 5 > "$LOG/pcie.txt" 2>/dev/null &
GPUMON="$GPUMON $!"
# Scope caps, and maximum OOM priority: if memory runs out, the kernel kills the game first.
systemd-run --user --scope -q -p MemoryMax=${MEMORY_MAX:-20G} -p MemoryHigh=${MEMORY_HIGH:-19G} \
  -p MemorySwapMax=${MEMORY_SWAP_MAX:-2G} \
  choom -n 1000 -- timeout -k 20 "$SECS" \
  bwrap --die-with-parent --unshare-net --unshare-pid --new-session \
    --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
    --symlink usr/bin /bin --symlink usr/sbin /sbin --ro-bind /etc /etc \
    --dev-bind /dev /dev --proc /proc --ro-bind /sys /sys --ro-bind /run/udev /run/udev \
    --tmpfs /tmp --ro-bind /tmp/.X11-unix /tmp/.X11-unix \
    --ro-bind "$XAUTHORITY" /xauth \
    --ro-bind "$RUNTIME/pulse" "$RUNTIME/pulse" \
    --ro-bind "$PROTON" /proton "${WSBIND[@]}" \
    --ro-bind "$GAME" /game \
    --ro-bind "$TREE_DIR/target/x86_64-pc-windows-msvc/$BUILD" /victory \
    --bind "$STATE" /port "${VKBIND[@]}" \
    --chdir /port \
    env -i "${ENVV[@]}" /bin/sh -c '"$@"; rc=$?; /proton/files/bin/wineserver -w; exit $rc' sh "${CMD[@]}" \
  > "$LOG/game.log" 2>&1
echo "exit=$?" >> "$LOG/game.log"
kill $GUARD $GPUMON 2>/dev/null
journalctl -k --since "$SINCE" -o short-precise 2>/dev/null | grep -iE 'NVRM|Xid|oom|killed process' > "$LOG/kernel.txt"
cp -f "$STATE/data/crashes.log" "$LOG/" 2>/dev/null
# Visual signal: imposed still shot (instability, plan-fixe.jpg against run/reference.txt) and sheet against the reference.
python3 -I "$PORT/run/qualite.py" --fin "$LOG" > "$LOG/qualite.txt" 2>&1
if [ -f "$PORT/run/reference.txt" ]; then
  python3 -I "$PORT/run/planche.py" "$PORT/run/state/logs/$(cat "$PORT/run/reference.txt")" "$LOG" -o "$LOG/planche.jpg" > /dev/null 2>&1
fi
python3 -I "$PORT/run/stats.py" "$LOG" > "$LOG/stats.txt" 2>&1
python3 -I "$PORT/run/stats.py" --registre "$LOG" >> "$PORT/run/registre.tsv" 2>/dev/null   # one line per trial, real outcome
python3 -I "$PORT/run/planche.py" "$LOG" > /dev/null 2>&1   # planche.jpg: captures aligned on the scene
echo "end: $LOG"
exit
}
