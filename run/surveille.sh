#!/bin/bash
# Watch the loop, one line per event: trial end (summary and preuve.py verdict), queue stop or
# failure, game alert. Seen events are not repeated: the watcher can be restarted safely.
cd "${GTA6_PORT:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}" || exit 1
mkdir -p run/state
D=run/state/verdicts-vus.txt
# First launch: trials already finished are not judged again. After that the marker persists, so a
# trial finished while the watcher was stopped gets its verdict when it restarts.
if [ ! -f "$D" ]; then
  find run/state/logs -mindepth 2 -maxdepth 2 -name stats.txt -printf '%h\n' 2>/dev/null | sed 's#.*/##' > "$D"
fi
touch "$D"
tail -n 0 -F run/queue.log 2>/dev/null | grep --line-buffered -E 'stopped|refused|removed|exit=[1-9]' | cut -c1-160 &
seen=$(find run/state/logs -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -1)
while true; do
  L=$(find run/state/logs -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -1)
  # Verdict as soon as a trial ends (stats.txt + a line in the registry), not when the next one
  # starts. Every unjudged trial is scanned: one that ended between two turns is not lost.
  for T in $(find run/state/logs -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort); do
    n=$(basename "$T")
    [ -f "$T/stats.txt" ] || continue
    grep -qxF "$n" "$D" && continue
    awk -F'\t' -v n="$n" '$1 == n { found = 1 } END { exit !found }' run/registre.tsv || continue
    labels=$(python3 -I - "$T" "$D" <<'PY'
import os, sys
base = os.path.abspath(sys.argv[1])
logs = os.path.dirname(base)
def field(d, name):
    try:
        for line in open(os.path.join(d, 'env.txt'), errors='replace'):
            if line.startswith(name + '='):
                return line.split('=', 1)[1].strip()
    except OSError:
        pass
def signature(d):
    out = set()
    try:
        for line in open(os.path.join(d, 'env.txt'), errors='replace'):
            if os.path.basename(d) not in line:
                out.add(line.rstrip('\n'))
    except OSError:
        pass
    return out
def exe(d):
    try:
        for line in open(os.path.join(d, 'host.txt'), errors='replace'):
            if line.startswith('victory.exe='):
                return line.strip()
    except OSError:
        pass
reg = set()
try:
    with open(os.path.join(os.path.dirname(os.path.dirname(logs)), 'registre.tsv'), errors='replace') as f:
        for line in f:
            reg.add(line.split('\t')[0])
except OSError:
    pass
tree, sig, ex = field(base, 'TREE'), signature(base), exe(base)
labels, names = [], []
for n in sorted(os.listdir(logs)):
    d = os.path.join(logs, n)
    if (n in reg and os.path.isfile(os.path.join(d, 'stats.txt')) and field(d, 'TREE') == tree
            and signature(d) == sig and exe(d) == ex):
        labels.append(n.split('-', 2)[2])
        names.append(n)
print(' '.join(labels))
seen = set()
try:
    with open(sys.argv[2], errors='replace') as f:
        seen = {line.strip() for line in f}
except OSError:
    pass
with open(sys.argv[2], 'a') as f:
    for n in names:
        if n not in seen:
            f.write(n + '\n')
PY
)
    if [ -n "$labels" ]; then
      if out=$(python3 -I run/preuve.py mur $labels 2>&1); then
        printf '%s\n' "$out" | sed 's/^/verdict /'
      else
        echo "verdict unavailable: $out"
      fi
    fi
  done
  if [ -n "$L" ] && [ "$L" != "$seen" ]; then
    [ -f "$seen/stats.txt" ] && echo "summary ${seen##*/}: $(grep -F "${seen##*/}" run/registre.tsv 2>/dev/null | tail -1 | cut -f5-7,9 | tr '\t' ' ') ; to look at: $seen/plan-fixe.jpg"
    seen=$L
  fi
  if [ -n "$L" ] && [ -f "$L/game.log" ]; then
    a=$(sed 's/\x1b\[[0-9;]*m//g' "$L/game.log" 2>/dev/null | grep -E 'exception 0x|device removed|DEVICE_HUNG|overflowed its stack|panicked' | head -1 | cut -c1-120)
    [ -n "$a" ] && [ ! -f "$L/.alert" ] && { echo "ALERT ${L##*/}: $a"; touch "$L/.alert"; }
  fi
  sleep 10
done
