#!/usr/bin/env python3
"""Summary of a trial: medians of VIctory reports (one line every ~5 s) in the world.

Usage: run/stats.py <trial dir> [<other trial>] [--min-draws N] [--skip S]
  --min-draws: draws/frame threshold to count a report as "world" (default 1000)
  --skip     : ignore the first S seconds spent in the world (default 60, caches warming up)
Steady regime: world reports with no pipeline queued ("0 being made"). That is the figure to
compare: while pipelines compile, their threads take the game's CPU.
Still shot: steady-regime reports falling in the imposed still-shot window (qualite.fenetre),
the only scene identical from one trial to another; one line, a paired comparison on that
window when there are two directories, and the still-shot ms column of the registry.
Fidelity (steady regime): draws and dispatches skipped per frame, by reason (fingerprints replaced
by #, xN = N distinct shaders; "refused [...]" = pipeline refused by the host,
"compute #" alone = untranslated shader), and luma of the captures (median 0-255 / % of pixels below 16):
those aligned on world entry (with seconds elapsed since), then all those of the steady regime
pooled. Rough alarm (black or washed-out image): it does not replace the sheet.
Two directories: both summaries, then the difference of the second against the first. The scene
follows the clock, not the frame count: reports in the steady regime taken at the same instant
since world entry are paired, and the median of the differences is given with a 95 % interval.
"""
import random, re, sys, statistics as st
from datetime import datetime
from pathlib import Path

try:  # planche.py (neighbour, out of sys.path under -I) and Pillow: without them, no luma line
    sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.dont_write_bytecode = True
    from planche import offsets, world_start
    from PIL import Image
except ImportError:
    Image = None

LINE = re.compile(
    r'^(\S+Z)\s+INFO victory_gpu::host: ([\d.]+) fps, ([\d.]+) ms/frame \(([\d.]+) preparing: copies ([\d.]+) ms [\d.]+ MB, '
    r'textures ([\d.]+) ms [\d.]+ MB; ([\d.]+) on the GPU; ([\d.]+) copying back [\d.]+ MB\), (\d+) draws/frame, '
    r'dropped (\d+) without a host pipeline \((\d+) being made\)')
MEM = re.compile(r'^(\S+Z)\s+INFO victory_gpu::copystat: memory: (\d+) MB private')
STAMP = re.compile(r'^(\d{4}-\d\d-\d\dT[\d:.]+Z)\s')
SAVED = re.compile(r'host pipeline library saved .*made=(\d+) loaded=(\d+)')
HITS = re.compile(r'shaders from the cache and translated so far hits=(\d+) misses=(\d+)')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
DROP = re.compile(r'victory_gpu::host: dropped ([\d.]+)/frame without a host pipeline: (.*)')
HASH = re.compile(r'\b[0-9a-f]{16}\b')
BVH = 'a stage of the ray-tracing structure builder, which is off'
DYN = re.compile(r'dynamic slots: [\d.]+ tables asked, ([\d.]+) refused')
CAP = re.compile(r'^(\S+Z)\s+INFO victory_gpu::capture: frame saved path=.*frame-(\d+)\.png')
MIN_PAIRS = 12   # report pairs at the same instant, below that no verdict (~1 min in common)
NEAR = 3         # seconds of difference allowed in a pair (one report every ~5 s)
BLOCK = 3        # neighbouring pairs drawn together (~15 s)

def secs(ts):
    return datetime.fromisoformat(ts).timestamp()

def span(s):
    return '?' if s is None else f'+{int(s) // 60} min {int(s) % 60:02d} s'

def plan_rows(d, entry, steady):
    """(steady-regime reports in the still-shot window, (start, end) in seconds since entry),
    from qualite.fenetre; ([], None) without captures, without GameInputCreate or without a still
    shot. qualite.bornes reads plan-fixe.txt written by qualite.py --fin; an old trial is reread."""
    if entry is None:
        return [], None
    try:
        import qualite
        lo, hi = qualite.bornes(str(d))
    except SystemExit:
        return [], None
    except Exception:
        return [], None
    if lo is None:
        return [], None
    return [row for row in steady if entry + lo <= row[9] <= entry + hi], (lo, hi)

def parse(d, min_draws, skip):
    r = dict(dir=d, rows=[], steady=[], plan=[], planwin=None, mem=[], start=None, end=None, world=None, drained=None,
             exit='?', rejected=False, made='?', loaded='?', hits='?', misses='?', drops={}, luma=None, dyn=[])
    first = last = None
    caps = {}     # captured frame -> time
    live = False  # the last report read is in the steady regime: the reasons that follow it count
    for raw in (d / 'game.log').read_text(errors='replace').splitlines():
        line = ANSI.sub('', raw)
        if line.startswith('exit='):
            r['exit'] = line[5:]
        if not (s := STAMP.match(line)):
            continue
        last = s[1]
        first = first or last
        if 'victory_gpu::host: ' in line and (m := LINE.match(line)):
            live = False
            if int(m[9]) < min_draws:
                continue
            t = secs(m[1])
            r['world'] = r['world'] if r['world'] is not None else t
            row = [float(x) for x in m.groups()[2:]] + [t]
            if t - r['world'] >= skip:
                r['rows'].append(row)
            if int(m[11]) == 0:
                r['drained'] = r['drained'] if r['drained'] is not None else t
                r['steady'].append(row)
                live = True
        elif live and (m := DROP.search(line)):
            why = r['drops'].setdefault(HASH.sub('#', m[2]).replace(BVH, 'BVH builder off'), [0.0, set()])
            why[0] += float(m[1])
            why[1].update(HASH.findall(m[2]))
        elif m := DYN.search(line):
            r['dyn'].append(float(m[1]))
        elif m := CAP.match(line):
            caps[int(m[2])] = secs(m[1])
        elif m := MEM.match(line):
            r['mem'].append(int(m[2]))
        elif m := SAVED.search(line):
            r['made'], r['loaded'] = m[1], m[2]
        elif m := HITS.search(line):
            r['hits'], r['misses'] = m[1], m[2]
        elif 'host pipeline library rejected' in line:
            r['rejected'] = True
    if first:
        r['start'], r['end'] = secs(first), secs(last)
    # World entry: the aligned capture +2 if it exists, otherwise the first world report.
    w = world_start(set(caps)) if Image else None
    r['entry'] = r['world'] if w is None else caps[w + 2]
    if Image:
        r['luma'] = luma(d, caps, w, r['drained'])
    r['plan'], r['planwin'] = plan_rows(d, r['entry'], r['steady'])
    return r

def dark(h):
    """Median luma and % of pixels below 16, from a 256-bin histogram."""
    n, acc = sum(h), 0
    for v, k in enumerate(h):
        acc += k
        if acc * 2 >= n:
            break
    return v, 100 * sum(h[:16]) / n

def luma(d, caps, w, drained):
    """({frame since world entry: (seconds since, median, % dark)}, number of steady-regime
    captures, their (median, % dark) pooled)."""
    world, late = {}, []
    for n, t in sorted(caps.items()):
        in_world, steady = w is not None and n >= w + 2, drained is not None and t >= drained
        f = d / 'capture' / f'frame-{n}.png'
        if (in_world or steady) and f.exists():
            h = Image.open(f).convert('L').histogram()
            if in_world:
                world[n - w] = (t - caps[w + 2], *dark(h))
            if steady:
                late.append(h)
    return world, len(late), dark([sum(c) for c in zip(*late)]) if late else None

def med(rows, i):
    return st.median(r[i] for r in rows)

def spread(rows):
    ms = sorted(r[0] for r in rows)
    return ms[len(ms) // 10], ms[len(ms) * 9 // 10]

def detail(rows, queue=True):
    return (f'preparing {med(rows, 1):.0f} ms (copies {med(rows, 2):.0f}, textures {med(rows, 3):.0f}), '
            f'GPU {med(rows, 4):.0f} ms, copy-back {med(rows, 5):.0f} ms, {med(rows, 6):.0f} draws/frame, '
            f'{med(rows, 7):.0f} without pipeline' + (f', {med(rows, 8):.0f} being made' if queue else ''))

def show(r, min_draws, skip):
    rows, steady = r['rows'], r['steady']
    print(f"{r['dir'].name}: ", end='')
    if not rows:
        print(f'no world report (threshold {min_draws} draws, {skip:.0f} s ignored)')
    else:
        lo, hi = spread(rows)
        print(f'{len(rows)} reports, {med(rows, 0):.0f} ms/frame median ({1000 / med(rows, 0):.2f} fps), '
              f'p10-p90 {lo:.0f}-{hi:.0f} ms')
        print(f'  {detail(rows)}')
    if steady:
        lo, hi = spread(steady)
        print(f'  steady regime (0 pipeline queued): {len(steady)} reports, {med(steady, 0):.0f} ms/frame median '
              f'({1000 / med(steady, 0):.2f} fps), p10-p90 {lo:.0f}-{hi:.0f} ms')
        print(f'    {detail(steady, queue=False)}')
        drops = sorted(r['drops'].items(), key=lambda kv: -kv[1][0])
        print('  fidelity, skipped per frame: ' + ('no reason noted (build without diagnostics?)' if not drops else ' ; '.join(
            f'{n / len(steady):.1f} {why}' + (f' x{len(sh)}' if sh else '') for why, (n, sh) in drops)))
    elif r['world'] is not None:
        print('  steady regime: not reached (pipelines were still compiling at the last report)')
    if r['planwin']:
        lo, hi = r['planwin']
        if r['plan']:
            p_lo, p_hi = spread(r['plan'])
            n = len(r['plan'])
            print(f'  still-shot speed (window +{lo:.0f} to +{hi:.0f} s): {n} report{"s" if n > 1 else ""}, '
                  f'{med(r["plan"], 0):.0f} ms/frame median ({1000 / med(r["plan"], 0):.2f} fps), '
                  f'p10-p90 {p_lo:.0f}-{p_hi:.0f} ms')
        else:
            print(f'  still-shot speed (window +{lo:.0f} to +{hi:.0f} s): no steady-regime report')
    if r['luma'] and (r['luma'][0] or r['luma'][1]):
        world, n, late = r['luma']
        aligned = ', '.join(f'+{o} ({world[o][0]:.0f} s) {world[o][1]}/{world[o][2]:.0f} %' for o in offsets(r['dir']) if o in world)
        print('  luma (median / % below 16): ' + ' ; '.join(filter(None, [
            aligned, f'steady regime ({n} captures) {late[0]}/{late[1]:.0f} %' if n else ''])))
    if r['start'] is not None:
        at = lambda k: span(None if r[k] is None else r[k] - r['start'])
        print(f"  world at {at('world')}, queue empty at {at('drained')}, end at {at('end')} (exit={r['exit']})")
        print(f"  caches: shaders translated {r['hits']} found, {r['misses']} to redo (last reading); library "
              f"{'refused' if r['rejected'] else 'accepted'}, {r['loaded']} pipelines loaded, {r['made']} compiled")
    if r['mem']:
        print(f"  private memory max {max(r['mem'])} MB")
    f = r['dir'] / 'instabilite.txt'
    if f.exists() and (v := f.read_text().split())[0] != '-':
        print(f"  still shot: instability {v[0]} (max {v[1]}) out of 255"
              + (f", cell wall drawn on {v[2]} % of captures" if len(v) > 2 else '') + ", image plan-fixe.jpg")
    if any(r['dyn']):
        print(f"  dynamic slots refused for lack of room: {sum(1 for x in r['dyn'] if x)} reports out of {len(r['dyn'])}, max {max(r['dyn']):.0f}")
    run, kill = xid(r['dir'])
    if run:
        print(f"  GPU: {len(run)} Xid error(s) during the trial: " + run[0][-120:])
    if kill:
        print(f"  GPU: {len(kill)} Xid just after our stop of the game (kill artifact, not counted)")

def settings(d, name):
    f = d / name
    return {l for l in f.read_text().splitlines() if d.name not in l} if f.exists() else set()

def interval(d, n=2000):
    """95 % interval of the median of differences d, by resampling (fixed seed) blocks of
    neighbouring pairs: they show the same scene, drawing them one by one would give too narrow
    an interval."""
    rng = random.Random(0)
    def once():
        out = []
        while len(out) < len(d):
            i = rng.randrange(len(d))
            out += (d + d)[i:i + BLOCK]
        return st.median(out[:len(d)])
    s = sorted(once() for _ in range(n))
    return s[n // 40], s[-n // 40]

def nearest(t, timed):
    """The (instant, ...) element of timed closest to t, if within NEAR seconds."""
    best = min(timed, key=lambda x: abs(x[0] - t), default=None)
    return best if best and abs(best[0] - t) <= NEAR else None

def compare_plan(a, b):
    """Paired difference of ms/frame on the still-shot window alone: the scene there is identical
    from one trial to another, unlike the rest of the steady regime (cinematic, walk)."""
    if not (a['planwin'] or b['planwin']):
        return
    since = lambda r: [(row[9] - r['entry'], row) for row in r['plan']]
    ta, tb = since(a), since(b)
    pairs = [(row, other[1]) for t, row in ta if (other := nearest(t, tb))]
    windows = ' and '.join(f'+{r["planwin"][0]:.0f} to +{r["planwin"][1]:.0f} s' if r['planwin'] else 'none'
                           for r in (a, b))
    head = f'  still-shot speed (windows {windows}), {len(pairs)} report pairs at the same instant'
    if len(pairs) < MIN_PAIRS:
        print(f'{head}, it takes {MIN_PAIRS}: too few, no conclusion on ms/frame at the still shot')
        return
    d = [y[0] - x[0] for x, y in pairs]
    ma = st.median(x[0] for x, _ in pairs)
    lo, hi = interval(d)
    print(f'{head}, {ma:.0f} ms/frame in the first, median difference {st.median(d):+.0f} ms '
          f'({st.median(d) / ma * 100:+.1f} %), 95 % interval [{lo:+.0f}, {hi:+.0f}] ms: '
          + ('contains 0, no conclusion' if lo <= 0 <= hi
             else '0 outside, clear difference on this pair of trials (repeat before attributing it to the change)'))

def compare(a, b):
    print(f"\ndifference of {b['dir'].name} against {a['dir'].name}:")
    since = lambda r: [(row[9] - r['entry'], row) for row in r['steady']]
    ta, tb = since(a), since(b)
    pairs = [(row, other[1]) for t, row in ta if (other := nearest(t, tb))]
    head = f'  same instant since world entry, steady regime: {len(pairs)} report pairs'
    if len(pairs) < MIN_PAIRS:
        windows = ' and '.join(f'{x[0][0]:.0f}-{x[-1][0]:.0f} s' if x else 'none' for x in (ta, tb))
        print(f'{head} (steady regimes: {windows}), it takes {MIN_PAIRS}: too few, no conclusion on ms/frame')
    else:
        d = [y[0] - x[0] for x, y in pairs]
        ma = st.median(x[0] for x, _ in pairs)
        # ponytail: blocks of BLOCK pairs; a scene that lasts longer makes the interval even
        # narrower. Lengthen the trial (STEADY_STOP) before refining the method.
        lo, hi = interval(d)
        print(f'{head}, {ma:.0f} ms/frame in the first, median difference {st.median(d):+.0f} ms ({st.median(d) / ma * 100:+.1f} %), '
              f'95 % interval [{lo:+.0f}, {hi:+.0f}] ms: '
              + ('contains 0, no conclusion' if lo <= 0 <= hi
                 else '0 outside, clear difference on this pair of trials (repeat before attributing it to the change)'))
        # Same scene, same work requested: a clear difference in requested draws betrays a shift.
        work = [y[6] + y[7] - x[6] - x[7] for x, y in pairs]
        lo, hi = interval(work)
        if not lo <= 0 <= hi:
            print(f'  different scenes? {st.median(work):+.0f} draws requested per frame, interval [{lo:+.0f}, {hi:+.0f}]')
    compare_plan(a, b)
    # Measured: when warm, the white flash of the cinematic falls 62 s after world entry whatever
    # the frame rate; 71 s in a trial that was retranslating its shaders.
    cold = [x['dir'].name for x in (a, b)
            if x['misses'] not in ('0', '?') or (x['drained'] or 0) - (x['entry'] or 0) > 30]
    if cold:
        print(f"  cold ({', '.join(cold)}): shaders retranslated or pipelines compiled in the world, "
              'the scene may have slid along the clock, pairing less reliable')
    if a['luma'] and b['luma']:
        seen = [(t, m, p, *other[1:]) for o in offsets(a['dir']) if o in a['luma'][0]
                for t, m, p in [a['luma'][0][o]] if (other := nearest(t, list(b['luma'][0].values())))]
        if seen:
            print('  luma at the same instant (median / % below 16): ' + ', '.join(
                '{:.0f} s {}/{:.0f} -> {}/{:.0f}'.format(*x) for x in seen))
    for name in ('env.txt', 'host.txt'):
        sa, sb = settings(a['dir'], name), settings(b['dir'], name)
        for l in sorted(sa - sb):
            print(f'  - {l}')
        for l in sorted(sb - sa):
            print(f'  + {l}')

def xid(d):
    """Xid lines of kernel.txt, split into (during the run, within 5 s after our own kill of the game)."""
    import re
    k = d / 'kernel.txt'
    lines = [l for l in k.read_text(errors='replace').splitlines() if 'Xid' in l] if k.exists() else []
    stop = None
    for f in d.glob('*stop*.txt'):
        m = re.match(r'(\d\d):(\d\d):(\d\d)', f.read_text(errors='replace'))
        if m:
            stop = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
    run, kill = [], []
    for l in lines:
        m = re.search(r' (\d\d):(\d\d):(\d\d)', l)
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None
        (kill if stop is not None and t is not None and 0 <= t - stop <= 5 else run).append(l)
    return run, kill


def registre(r):
    """One line of the trial registry (run/registre.tsv), tab-separated."""
    d = r['dir']
    kv = lambda f: dict(l.strip().split('=', 1) for l in open(d / f) if '=' in l) if (d / f).exists() else {}
    env, host = kv('env.txt'), kv('host.txt')
    if (d / 'hang-stop.txt').exists():
        issue = 'frozen'
    elif (d / 'memory-stop.txt').exists():
        issue = 'memory stop'
    elif r['steady']:
        issue = 'steady regime'
    elif r['world'] is not None:
        issue = 'world without steady'
    else:
        issue = 'before the world'
    run, _ = xid(d)
    if run:
        issue += f" Xid x{len(run)}"
    ms = f"{med(r['steady'], 0):.0f}" if r['steady'] else '-'
    drops = ' ; '.join(f"{n / len(r['steady']):.1f} {why}" for why, (n, _) in
                       sorted(r['drops'].items(), key=lambda kv: -kv[1][0])[:3]) if r['steady'] else ''
    q = ((d / 'instabilite.txt').read_text().split() if (d / 'instabilite.txt').exists() else []) + ['-'] * 3
    pm = f"{med(r['plan'], 0):.0f}" if r['plan'] else '-'
    print('\t'.join([d.name, env.get('TREE', '?'), env.get('BUILD', '?'), host.get('victory.exe', '?'),
                     'cold' if str(r['misses']).isdigit() and int(r['misses']) > 0 else 'warm',
                     f"{issue} exit={r['exit']}", ms, drops, q[0], q[2], pm]))

def main():
    opts, dirs = {'--min-draws': 1000, '--skip': 60}, []
    args = iter(sys.argv[1:])
    for a in args:
        if a in opts:
            opts[a] = float(next(args))
        else:
            dirs.append(Path(a))
    min_draws, skip = int(opts['--min-draws']), opts['--skip']
    if '--registre' in sys.argv:
        dirs = [d for d in dirs if str(d) != '--registre']
        for d in dirs:
            registre(parse(d, min_draws, skip))
        return
    runs = [parse(d, min_draws, skip) for d in dirs]
    for r in runs:
        show(r, min_draws, skip)
    if len(runs) == 2:
        compare(*runs)

if __name__ == '__main__':
    main()
