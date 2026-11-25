#!/usr/bin/env python3
"""Contact sheet of scene-aligned captures: one row per trial, one column per instant counted
in seconds since world entry (the scene follows the clock, not the frame count; the instant of
a capture is its file date). Without world captures, the last frames.

Usage: planche.py <trial> [<trial> ...] [-o output.jpg]
        (default: <first trial>/planche.jpg)
"""
import os
import re
import sys

from PIL import Image, ImageDraw

W, H = 480, 270
SECONDS = [0, 15, 30, 60, 90, 120, 180, 240]


def captured(run):
    d = os.path.join(run, 'capture')
    if not os.path.isdir(d):
        return set()
    return {int(m[1]) for f in os.listdir(d) if (m := re.fullmatch(r'frame-(\d+)\.png', f))}


def offsets(run):
    also = []
    try:
        for line in open(os.path.join(run, 'env.txt')):
            if line.startswith('VICTORY_CAPTURE_WORLD_ALSO='):
                also = [int(x) for x in line.split('=', 1)[1].split(',') if x.strip().isdigit()]
    except OSError:
        pass
    return sorted({2, 6, 30, 80, *also})


def world_start(frames):
    # World captures are at frames W+2, W+6, W+30, W+80; the powers of two
    # imitate the first three, not the fourth.
    for n in sorted(frames):
        if n + 4 in frames and n + 28 in frames and n + 78 in frames:
            return n - 2
    return None


def main(argv):
    out = None
    if '-o' in argv:
        i = argv.index('-o')
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    runs = argv
    if not runs:
        sys.exit(__doc__)
    rows = []
    cols = offsets(runs[0])
    for run in runs:
        frames = captured(run)
        w = world_start(frames)
        if w is not None:
            at = {n: os.path.getmtime(os.path.join(run, 'capture', f'frame-{n}.png')) for n in frames if n >= w}
            t0 = at[w + 2]
            cells = []
            for sec in SECONDS:
                n = min(at, key=lambda k: abs(at[k] - t0 - sec))
                cells.append((f'+{sec} s', n if abs(at[n] - t0 - sec) <= 10 else None))
            rows.append((run, cells))
        else:
            last = sorted(frames)[-len(cols):]
            rows.append((run, [(f'#{n}', n) for n in last]))
    ncol = max(len(r[1]) for r in rows)
    sheet = Image.new('RGB', (W * ncol, (H + 18) * len(rows)), 'black')
    draw = ImageDraw.Draw(sheet)
    for r, (run, cells) in enumerate(rows):
        y = r * (H + 18)
        draw.text((4, y + 2), os.path.basename(run.rstrip('/')), fill='white')
        for c, (label, n) in enumerate(cells):
            x = c * W
            if n is not None:
                im = Image.open(os.path.join(run, 'capture', f'frame-{n}.png')).convert('RGB')
                sheet.paste(im.resize((W, H)), (x, y + 18))
                label += f' (image {n})'
            draw.text((x + 6, y + 22), label, fill='yellow')
    out = out or os.path.join(runs[0], 'planche.jpg')
    sheet.save(out, quality=82)
    print(out)


if __name__ == '__main__':
    main(sys.argv[1:])
