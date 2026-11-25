#!/bin/bash
# Queue: one command per line in run/queue.txt, run one at a time from run/,
# each after the game currently running has ended (builds and trials do not compete for RAM).
# Usage: queue.sh add <command ...> | list | run
#   queue.sh add ./build.sh matrix
#   queue.sh add TREE=matrix VKD3D=fix ./launch.sh 900 matrix VICTORY_KEYS=8:Enter,11:Enter
#   queue.sh add './caches.sh restore warm && ./launch.sh 900 b VICTORY_KEYS=8:Enter'
#   setsid ./queue.sh run </dev/null >/dev/null 2>&1 &      # detached; log: run/queue.log
# The line is handed to bash as is. Line 1 is the one running; it leaves the queue
# when it ends, except exit 75 (transient refusal: the line stays). Unattended: a failure
# is noted in queue-failures.txt, and a failed build also removes the trials of that tree
# (TREE=<tree>); when the queue is empty, wait for new lines instead of exiting.
{
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 1
Q=${QUEUE:-queue.txt}
GAME='^(Z:.victory.victory[.]exe|/proton/files/bin/wine /victory/)'
case ${1:-} in
  add) shift; flock "$Q.lock" sh -c 'printf "%s\n" "$1" >> "$2"' sh "$*" "$Q" ;;
  list) cat -n "$Q" 2>/dev/null; tail -n 3 "${Q%.txt}.log" 2>/dev/null ;;
  run)
    exec 8> "$Q.run"
    flock -n 8 || { echo "a queue is already running" >&2; exit 3; }
    while true; do
      cmd=$(grep -m 1 -v '^[[:space:]]*$' "$Q" 2>/dev/null)
      if [ -z "$cmd" ]; then
        [ "${idle:-}" = 1 ] || echo "$(date '+%F %T') queue empty: waiting"; idle=1; sleep 30; continue
      fi
      idle=
      while pgrep -f "$GAME" >/dev/null; do sleep 5; done; sleep 3
      echo "$(date '+%F %T') start: $cmd"
      bash -c "$cmd" 8>&-; rc=$?
      echo "$(date '+%F %T') end exit=$rc: $cmd"
      # 75: transient refusal from launch.sh (game already running, low RAM): keep the line, retry in 60 s.
      [ $rc = 75 ] && { sleep 60; continue; }
      # Remove the finished command (wherever it is), and on a failed build the trials of its tree.
      tree=; [ $rc = 0 ] || { echo "$(date '+%F %T') exit=$rc: $cmd" >> queue-failures.txt
        case $cmd in *build.sh\ *) tree=$(printf '%s' "$cmd" | sed -E 's/.*build\.sh ([^ ]+).*/\1/') ;; esac; }
      flock "$Q.lock" python3 -I -c '
import sys
q, cmd, tree = sys.argv[1:]
L = open(q).read().splitlines()
if cmd in L: L.remove(cmd)
if tree:
    gone = [l for l in L if f"TREE={tree} " in l]
    L = [l for l in L if l not in gone]
    for l in gone: print("removed, its build failed:", l[:100])
open(q, "w").write("".join(l + "\n" for l in L))' "$Q" "$cmd" "$tree"
    done >> "${Q%.txt}.log" 2>&1 ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
exit
}
