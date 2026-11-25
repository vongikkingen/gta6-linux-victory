#!/usr/bin/env python3
"""Prototype: image notes for the captures of a trial (Pillow + numpy, nothing else).

Usage: qualite.py <trial> [<reference>]   qualite.py --fin <trial> (called by launch.sh)   qualite.py --test
World captures only, dated in seconds since world entry (like planche.py).
Phase: cinematic = black bars top and bottom, game = no bars.
Per 30 s slice: median luma, % of pixels below 16 (outside bars), detail (mean gradient of the
image reduced 4x), mean change between two neighbouring captures (192x108 thumbnails).
With a reference: each cinematic capture is compared to the reference capture that looks most
like it within SLIP seconds (the scene slides along the clock from one trial to another).
Similarity = SSIM on thumbnails. The worst pairs are named: those are the ones to look at.
Indicative only: two trials of the same build (w64, noskips) already only resemble each other at 0.48.
Still shot: after the cinematic, as long as nobody touches the pad, the camera does not move and
it is the same from one trial to another (seen on fidw, fid2 and w64). It is recognised by STILL
neighbouring captures changing by less than CALM (out of 255). Within one trial they resemble
each other at 0.88 at least; between two trials of the same build, it is not measured.
Still-shot window (fenetre): it ends at the first non-Enter key of the trial, read from
VICTORY_KEYS in env.txt minus 2 s, not at a fixed time since launch: a cold trial whose
cinematic ends late keeps its still shot. Without a scripted walk (VICTORY_KEYS with no
non-Enter key), there is no upper bound. If the keys fall before the still shot starts, they
were swallowed by the cinematic: no bound either.
Does not see, without a reference: mosaic, local noise, missing object. Nor an alternation of
one image out of two: captures fall every 8 frames, on the same parity.
"""
import datetime as dt
import os
import re
import statistics as st
import sys

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from planche import captured, world_start
import marche

SLIP = 12     # s: the white flash falls at +62 s warm, +71 s cold
SLICE = 30    # s
WORST = 4
STILL = 4     # neighbouring captures
CALM = 5      # mean difference, out of 255


def read(path):
    """(bars, median luma, % below 16, detail, thumbnail) of one capture."""
    y = np.asarray(Image.open(path).convert('L'))
    lit = np.flatnonzero(y.max(axis=1) >= 16)
    top, bottom = (lit[0], len(y) - 1 - lit[-1]) if len(lit) else (0, 0)
    # ponytail: bars = equal black margins of at least 40 lines; a black game shot at the top and
    # bottom would pass for a cinematic. Read the phase in game.log if this happens.
    bars = top >= 40 and abs(top - bottom) <= 8
    a = y[top:len(y) - bottom] if bars else y
    im = Image.fromarray(a)
    s = np.asarray(im.reduce(4), dtype=np.float32)
    detail = (np.abs(np.diff(s, axis=1)).mean() + np.abs(np.diff(s, axis=0)).mean()) / 2
    thumb = np.asarray(im.resize((192, 108), Image.BOX), dtype=np.float32)
    return bool(bars), float(np.median(a)), 100 * float((a < 16).mean()), float(detail), thumb


def ssim(a, b):
    """Mean SSIM of two 192x108 thumbnails, in non-overlapping 8x8 blocks."""
    cut = lambda x: x[:104].reshape(13, 8, 24, 8).swapaxes(1, 2).reshape(13, 24, 64)
    a, b = cut(a), cut(b)
    ma, mb = a.mean(-1), b.mean(-1)
    cov = (a * b).mean(-1) - ma * mb
    return float((((2 * ma * mb + 6.5) * (2 * cov + 58.5))
                  / ((ma ** 2 + mb ** 2 + 6.5) * (a.var(-1) + b.var(-1) + 58.5))).mean())


def load(run):
    """[(seconds, image, bars, median, dark, detail, thumbnail)] of the world captures."""
    frames = captured(run)
    w = world_start(frames)
    if w is None:
        sys.exit(f'{run}: no captures aligned on the world (VICTORY_CAPTURE_WORLD=1)')
    path = lambda n: os.path.join(run, 'capture', f'frame-{n}.png')
    t0 = os.path.getmtime(path(w + 2))
    h = hub(run)
    return [(os.path.getmtime(path(n)) - t0, n, *read(path(n)), os.path.getmtime(path(n)) - h if h else None)
            for n in sorted(frames) if n >= w + 2]


def hub(run):
    """Time (epoch) of GameInputCreate, the zero of VICTORY_KEYS, or None."""
    with open(os.path.join(run, 'game.log'), errors='replace') as f:
        for line in f:
            if 'GameInputCreate' in line and (m := re.search(r'(\d{4}-\d\d-\d\dT[\d:.]+)Z', line)):
                return dt.datetime.fromisoformat(m[1]).replace(tzinfo=dt.timezone.utc).timestamp()


def borne(run=None):
    """Upper bound of the still shot, in seconds since GameInputCreate: first non-Enter key of
    VICTORY_KEYS (env.txt) minus 2 s, or None if the trial has no scripted walk. Without run, or
    without env.txt (trial from before VICTORY_KEYS), the old bound marche.START - 2."""
    if run is None:
        return marche.START - 2
    try:
        with open(os.path.join(run, 'env.txt'), errors='replace') as f:
            keys = next((line.split('=', 1)[1].strip() for line in f if line.startswith('VICTORY_KEYS=')), None)
    except OSError:
        return marche.START - 2
    if keys is None:      # env.txt without VICTORY_KEYS: no scripted walk
        return None
    return borne_clefs(keys)


def borne_clefs(keys):
    """Upper bound from the VICTORY_KEYS value: first non-Enter key minus 2 s, or None."""
    times = []
    for item in keys.split(','):
        t, _, key = item.strip().rpartition(':')
        if key and key != 'Enter':
            try:
                times.append(float(t))
            except ValueError:
                pass
    return min(times) - 2 if times else None


def fenetre(caps, run=None):
    """Captures of the imposed still shot: game, 5 s after the last cinematic capture, up to the
    first non-Enter key of the trial (VICTORY_KEYS from env.txt minus 2 s). No upper bound if the
    trial has no scripted walk, or if its keys fall before the still shot starts (cold cinematic):
    they were swallowed and will not move the camera again."""
    film = [c[0] for c in caps if c[2]]
    start = (film[-1] if film else 0) + 5
    b = borne(run)
    if b is not None:
        # b is since GameInputCreate, start since world entry: shift b.
        delta = next((c[0] - c[7] for c in caps if c[7] is not None), None)
        if delta is not None and b + delta <= start:
            b = None
    return [c for c in caps if not c[2] and c[0] >= start and c[7] is not None
            and (b is None or c[7] < b)]


def bornes(run):
    """(start, end) of the still-shot window in seconds since world entry, or (None, None).
    Reads plan-fixe.txt written by --fin; otherwise rereads the captures (old trial)."""
    f = os.path.join(run, 'plan-fixe.txt')
    if os.path.exists(f):
        try:
            with open(f, errors='replace') as g:
                a, b = g.read().split()
            return (None, None) if a == '-' else (float(a), float(b))
        except (OSError, ValueError):
            pass
    try:
        win = fenetre(load(run), run)
    except (SystemExit, OSError):
        return None, None
    return (win[0][0], win[-1][0]) if win else (None, None)


def mur(run, win):
    """Share of still-shot captures where the wall left of the cell window is drawn (dark zone,
    luma < 80); without the wall one sees the sky (luma 150 to 200). Specific to this shot: the
    framing is imposed."""
    v = []
    for c in win:
        y = np.asarray(Image.open(os.path.join(run, 'capture', f'frame-{c[1]}.png')).convert('L'), dtype=np.float32)
        h, w = y.shape
        v.append(float(y[int(h * .1):int(h * .5), :int(w * .08)].mean()))
    return 100 * sum(x < 80 for x in v) / len(v) if v else None


def instable(win):
    """(median, max) of the mean difference between neighbouring captures of the still shot, out
    of 255; None without a still shot."""
    d = [float(np.abs(b[6] - a[6]).mean()) for a, b in zip(win, win[1:])]
    return (st.median(d), max(d)) if d else None


def still(caps):
    """Captures of the first still shot of the game, or []."""
    run = []
    for c in (c for c in caps if not c[2]):
        if run and np.abs(c[6] - run[-1][6]).mean() >= CALM:
            if len(run) >= STILL:
                break
            run = []
        run.append(c)
    return run if len(run) >= STILL else []


def show(run, caps):
    name = os.path.basename(run.rstrip('/'))
    film = [c for c in caps if c[2]]
    print(f'{name}: {len(caps)} world captures, {len(film)} of them cinematic'
          + (f' (up to +{film[-1][0]:.0f} s)' if film else ''))
    change = {c[1]: float(np.abs(c[6] - p[6]).mean()) for p, c in zip(caps, caps[1:])}
    for lo in range(0, int(caps[-1][0]) + 1, SLICE):
        part = [c for c in caps if lo <= c[0] < lo + SLICE]
        if not part:
            continue
        phases = {'cinematic' if c[2] else 'game' for c in part}
        moved = [change[c[1]] for c in part if c[1] in change]
        print(f'  +{lo:3d} s {" and ".join(sorted(phases)):19s}: luma {st.median(c[3] for c in part):3.0f}, '
              f'dark {st.median(c[4] for c in part):2.0f} %, detail {st.median(c[5] for c in part):.1f}, '
              f'change {st.median(moved) if moved else 0:.0f}')
    dark = [c for c in caps if c[4] >= 40]
    if dark:
        print('  more than 40 % dark pixels: ' + ', '.join(f'+{c[0]:.0f} s (image {c[1]})' for c in dark[:6])
              + (f' and {len(dark) - 6} more' if len(dark) > 6 else ''))
    fixed = still(caps)
    print('  still shot: ' + (
        f'+{fixed[0][0]:.0f} to +{fixed[-1][0]:.0f} s, {len(fixed)} captures (images {fixed[0][1]} to {fixed[-1][1]}), '
        f'similarity between neighbours {min(ssim(a[6], b[6]) for a, b in zip(fixed, fixed[1:])):.2f} at least'
        if fixed else 'none (was someone playing from the end of the cinematic?)'))


def against(caps, ref_run, ref):
    name = os.path.basename(ref_run.rstrip('/'))
    a, b = still(caps), still(ref)
    if a and b:
        score, x, y = max(((ssim(x[6], y[6]), x, y) for x in a for y in b), key=lambda p: p[0])
        print(f'  still shot against the one of {name}: similarity {score:.2f} at best (image {x[1]} / image {y[1]})')
    else:
        print(f'  still shot against the one of {name}: no still shot in ' + ('both' if not a and not b else 'one of them'))
    film, ref_film = [c for c in caps if c[2]], [c for c in ref if c[2]]
    pairs = []
    for c in film:
        near = [(ssim(c[6], r[6]), r) for r in ref_film if abs(r[0] - c[0]) <= SLIP]
        if near:
            score, r = max(near, key=lambda x: x[0])
            pairs.append((score, c, r))
    if not pairs:
        print('  no cinematic capture to compare')
        return
    scores = sorted(p[0] for p in pairs)
    print(f'  cinematic against {name} (indicative), closest frame within +/-{SLIP} s: '
          f'{len(pairs)} pairs, median similarity {st.median(scores):.2f}, lowest {scores[0]:.2f}')
    print('  to look at (least similar): ' + ' ; '.join(
        f'+{c[0]:.0f} s image {c[1]} / image {r[1]} ({s:.2f})' for s, c, r in sorted(pairs, key=lambda p: p[0])[:WORST]))


def test():
    rng = np.random.default_rng(0)
    a = rng.uniform(0, 255, (108, 192)).astype(np.float32)
    assert abs(ssim(a, a) - 1) < 1e-6
    assert ssim(a, a[::-1]) < 0.2
    assert ssim(a, np.zeros_like(a)) < 0.05
    assert borne_clefs('8:Enter,230:W,230.2:W') == 228
    assert borne_clefs('8:Enter') is None and borne_clefs('') is None
    assert borne(None) == marche.START - 2
    print('ok')


def fin(run):
    """End of trial: instabilite.txt, plan-fixe.txt (window bounds for stats.py), plan-fixe.jpg
    (middle of the still shot, trial on the left, reference on the right)."""
    from PIL import ImageDraw
    base = os.path.dirname(os.path.abspath(__file__))
    ref = None
    try:
        name = open(os.path.join(base, 'reference.txt')).read().strip()
        if name:
            candidate = os.path.join(base, 'state', 'logs', name)
            if os.path.isdir(candidate):
                ref = candidate
    except OSError:
        pass
    if ref is None:
        print('no usable reference.txt: plan-fixe.jpg shows this trial only')
    tiles = []
    for d in ([run] if ref is None else [run, ref]):
        win = fenetre(load(d), d)
        if d == run:
            i = instable(win)
            m = mur(run, win)
            open(os.path.join(run, 'instabilite.txt'), 'w').write(
                f'{i[0]:.1f} {i[1]:.1f} {m:.0f}\n' if i else '- - -\n')
            with open(os.path.join(run, 'plan-fixe.txt'), 'w') as g:
                g.write(f'{win[0][0]:.3f} {win[-1][0]:.3f}\n' if win else '- -\n')
            print(f'imposed still shot: {len(win)} captures, instability {i[0]:.1f} (max {i[1]:.1f}) out of 255, '
                  f'cell wall drawn on {m:.0f} % of captures'
                  if i else 'imposed still shot: no capture (cinematic too long or no GameInputCreate)')
        if win:
            c = win[len(win) // 2]
            im = Image.open(os.path.join(d, 'capture', f'frame-{c[1]}.png')).convert('RGB').resize((1280, 720))
            ImageDraw.Draw(im).text((8, 8), f'{os.path.basename(d)}  +{c[0]:.0f} s  image {c[1]}', fill=(255, 255, 0))
            tiles.append(im)
    if tiles:
        out = Image.new('RGB', (1290 * len(tiles) - 10, 720))
        for k, im in enumerate(tiles):
            out.paste(im, (1290 * k, 0))
        out.save(os.path.join(run, 'plan-fixe.jpg'), quality=88)


def main(argv):
    if argv == ['--test']:
        return test()
    if len(argv) == 2 and argv[0] == '--fin':
        return fin(argv[1])
    if not 1 <= len(argv) <= 2:
        sys.exit(__doc__)
    caps = load(argv[0])
    show(argv[0], caps)
    if len(argv) == 2:
        against(caps, argv[1], load(argv[1]))


if __name__ == '__main__':
    main(sys.argv[1:])
