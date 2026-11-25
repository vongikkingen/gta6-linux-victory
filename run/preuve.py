#!/usr/bin/env python3
"""Proof of a random image signal, and still-shot speed.

Read-only: writes no file.

Usage: preuve.py mur <label> [<label> ...]   preuve.py vitesse <prefix>   preuve.py --test

mur: the arguments are exact trial labels (ds571a, not ds571): ds571dbg therefore does not mix
with ds571a. Each label is looked up in run/registre.tsv; if it was reused, the last trial counts.
Trials are grouped by TREE (line of env.txt) and preuve.py prints one line per tree.
A trial gives one bit: wall or not, read from the first FIRST calm still-shot captures, within
WITHIN s after the cinematic, before the camera moves. Without a recognised cinematic or without
that still shot, the trial does not count: it is named "excluded" and is replayed.
Sequential rule between p0 (wall rate without the fix, 0.25) and p1 (0.9), 5 % error on each
side: 3 walls in a row = proven; 2 absences = refuted; one absence then 4 walls = proven;
nothing clear after 6 counted trials = parked (the queue stops).
vitesse: median ms/frame of steady-regime reports during the first SHOT seconds of the still
shot, the only scene identical from one trial to another. <prefix>: label prefix (as before;
mur itself takes exact labels).
"""
import math
import os
import statistics as st
import sys

sys.dont_write_bytecode = True
RUN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RUN)

P0, P1, ERR = 0.25, 0.9, 0.05
FIRST = 5     # captures
FILM = 10     # cinematic captures required before the still shot
DARK = 80     # luma of the zone: wall below, sky above (threshold of qualite.mur)
CALM = 8      # mean difference between neighbouring captures, out of 255 (night still shots: 7.3 at most)
WITHIN = 40   # s after the last cinematic capture (the fade lasts up to 15 s when cold)
SHOT = 45     # s


def sequentiel(bits, p0=P0, p1=P1, err=ERR):
    """('proven' | 'refuted' | 'running', counted trials): cumulative likelihood ratio of p1 against
    p0, stopping as soon as it passes +/-log((1 - err) / err)."""
    lim, s = math.log((1 - err) / err), 0.0
    for n, b in enumerate(bits, 1):
        s += math.log(p1 / p0) if b else math.log((1 - p1) / (1 - p0))
        if abs(s) >= lim:
            return ('proven' if s > 0 else 'refuted'), n
    return 'running', len(bits)


def regle(bits):
    """Verdict of the audit over at most 6 counted trials: 3 walls in a row prove, 2 absences
    refute, nothing clear after 6 trials parks. Trials beyond the 6th are ignored (the queue
    would have stopped)."""
    bits = list(bits)[:6]
    v, n = sequentiel(bits)
    return ('parked', 6) if v == 'running' and len(bits) == 6 else (v, n)


def zone(thumb):
    """Mean luma of the wall zone (the one of qualite.mur) on a 192x108 thumbnail."""
    return float(thumb[10:54, :15].mean())


def verdict(thumbs, film):
    """True (wall), False (absent), or the reason why the trial does not count."""
    if film < FILM:
        return 'cinematic not recognised'
    if len(thumbs) < FIRST:
        return f'no calm still shot within {WITHIN} s'
    return st.median(zone(t) for t in thumbs) < DARK


def calme(shot, t, thumb):
    """The still shot in progress, extended by this capture or restarted at it if the image moved."""
    still = shot and float(abs(thumb - shot[-1][1]).mean()) <= CALM
    return (shot if still else []) + [(t, thumb)]


def debut(run):
    """(time of the first still-shot capture, its FIRST thumbnails, number of cinematic captures)."""
    from planche import captured, world_start
    from qualite import read
    frames = captured(run)
    w = world_start(frames)
    film, last, shot = 0, 0.0, []
    # ponytail: still shot = FIRST captures without bars after FILM captures with bars; a black game
    # shot at the top and bottom defeats it (limit of qualite.read). Read the phase in game.log if
    # this happens.
    for n in sorted(f for f in frames if w is not None and f >= w + 2):
        p = os.path.join(run, 'capture', f'frame-{n}.png')
        bars, *_, thumb = read(p)
        if bars:
            film, last, shot = film + 1, os.path.getmtime(p), []
        elif film >= FILM:
            if os.path.getmtime(p) > last + WITHIN:
                shot = []
                break
            shot = calme(shot, os.path.getmtime(p), thumb)
            if len(shot) == FIRST:
                break
    return (shot[0][0] if shot else None), [t for _, t in shot], film


def bilans(run):
    """[(time, ms/frame)] of world reports in the steady regime (0 pipeline queued)."""
    import stats
    out = []
    with open(os.path.join(run, 'game.log'), errors='replace') as f:
        for raw in f:
            if ('draws/frame' in raw and (m := stats.LINE.match(stats.ANSI.sub('', raw)))
                    and int(m[9]) >= 1000 and m[11] == '0'):
                out.append((stats.secs(m[1]), float(m[3])))
    return out


def etiquette(name):
    """Label of a trial directory: 1125-045634-ds571a -> ds571a."""
    return os.path.basename(name).split('-', 2)[2]


def noms():
    """Names of the trials in the registry, in the order they were added."""
    with open(os.path.join(RUN, 'registre.tsv')) as f:
        return [line.split('\t')[0] for line in f][1:]


def choisir(names, labels):
    """Directories of the exact labels requested; the last occurrence of a reused label counts."""
    out = []
    for lab in labels:
        found = [n for n in names if etiquette(n) == lab]
        if not found:
            raise SystemExit(f'trial not found: {lab}')
        d = os.path.join(RUN, 'state', 'logs', found[-1])
        if d not in out:
            out.append(d)
    return out


def essais(prefix):
    """Directories of the trials whose label starts with prefix (vitesse mode, compatibility)."""
    return [os.path.join(RUN, 'state', 'logs', n) for n in noms() if etiquette(n).startswith(prefix)]


def arbre(run):
    """TREE from env.txt, or '?': it is the group of trials whose verdict is one line."""
    try:
        with open(os.path.join(run, 'env.txt'), errors='replace') as f:
            for line in f:
                if line.startswith('TREE='):
                    return line[5:].strip() or '?'
    except OSError:
        pass
    return '?'


def ligne(tree, dirs):
    """The verdict line of one tree: bits in chronological order, excluded trials and their reason."""
    bits, detail, ecartes = [], [], []
    for run in sorted(dirs, key=os.path.basename):
        label = etiquette(run)
        _, thumbs, film = debut(run)
        v = verdict(thumbs, film)
        if isinstance(v, str):
            ecartes.append(f'{label} ({v})')
        else:
            bits.append(v)
            detail.append('wall' if v else 'absent')
    v, n = regle(bits)
    nb = len(bits)
    out = (f'{tree}: {sum(bits)} wall{"s" if sum(bits) > 1 else ""} in {nb} counted trial'
           f'{"s" if nb > 1 else ""}'
           + (f' ({", ".join(detail)})' if detail else ''))
    if v == 'parked':
        out += f' -> parked after {n} counted trials'
    elif v == 'running':
        out += ' -> running'
    else:
        out += f' -> {v} at trial {n}'
    if ecartes:
        out += ' ; excluded: ' + ', '.join(ecartes)
    return out


def test():
    import numpy as np
    assert sequentiel([1, 1, 1]) == ('proven', 3) and sequentiel([1, 1]) == ('running', 2)
    assert sequentiel([0, 0, 1]) == ('refuted', 2) and sequentiel([0, 1, 1, 1, 1]) == ('proven', 5)
    assert sequentiel([0, 0], 0.9, 0.25) == ('proven', 2)             # regression, base at 0.9
    assert sequentiel([0] * 11, 0.25, 0.02) == ('running', 11)        # regression, base at 0.25: too expensive
    assert regle([1, 1, 1]) == ('proven', 3) and regle([0, 0]) == ('refuted', 2)
    assert regle([1, 0, 1, 0, 1, 0]) == ('parked', 6) and regle([1, 1]) == ('running', 2)
    assert regle([1, 0, 1, 0, 1, 0, 1, 1, 1]) == ('parked', 6)       # beyond the 6th, the queue parked
    names = ['1125-045634-ds571a', '1125-050214-ds571b', '1125-051638-ds571dbg']
    assert etiquette(names[0]) == 'ds571a' and etiquette(names[2]) == 'ds571dbg'
    assert [os.path.basename(d) for d in choisir(names, ['ds571a', 'ds571b'])] == names[:2]
    assert choisir(names + ['1125-060000-ds571a'], ['ds571a'])[0].endswith('1125-060000-ds571a')
    try:
        choisir(names, ['ds571'])
    except SystemExit:
        pass
    else:
        raise AssertionError('an exact label must not be a prefix')
    sky = np.full((108, 192), 170, np.float32)
    wall = sky.copy()
    wall[10:54, :15] = 30
    assert verdict([wall] * FIRST, FILM) is True and verdict([sky] * FIRST, FILM) is False
    assert isinstance(verdict([wall] * FIRST, 0), str) and isinstance(verdict([wall] * 2, FILM), str)
    shot = []
    for t, thumb in enumerate([sky, sky * 0, sky * 0, wall * 0]):
        shot = calme(shot, t, thumb)
    assert [t for t, _ in shot] == [1, 2, 3]
    print('ok')


def main(argv):
    if argv == ['--test']:
        return test()
    if len(argv) >= 2 and argv[0] == 'mur':
        groups = {}
        for d in choisir(noms(), argv[1:]):
            groups.setdefault(arbre(d), []).append(d)
        for tree in sorted(groups):
            print(ligne(tree, groups[tree]))
    elif len(argv) == 2 and argv[0] == 'vitesse':
        import marche
        from qualite import hub
        meds = []
        for run in essais(argv[1]):
            t0, thumbs, film = debut(run)
            h, v = hub(run), verdict(thumbs, film)
            if isinstance(v, str) or h is None:
                print(f"{os.path.basename(run)}: does not count ({v if isinstance(v, str) else 'no GameInputCreate'})")
                continue
            ms = [x for t, x in bilans(run) if t0 <= t < min(t0 + SHOT, h + marche.START - 2)]
            if ms:
                meds.append(st.median(ms))
            print(f'{os.path.basename(run)}: '
                  + (f'{st.median(ms):.0f} ms/frame at the still shot ({len(ms)} reports)' if ms else 'no report'))
        if len(meds) > 1:
            print(f'difference between these {len(meds)} trials: {max(meds) - min(meds):.0f} ms')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
