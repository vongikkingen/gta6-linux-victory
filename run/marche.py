"""VICTORY_KEYS for an unattended trial: menu Enters, then from START s (after the cutscene and the 30 s still
shot, cold runs included) the fork #424 walk, 27 s every 2 min. A hold is the key repeated every 0.2 s.
W/A/S/D: left stick, J/L: right stick (victory-hle gameinput fallback pad)."""
import sys

START = 230   # s after GameInputCreate: end of cutscene + 30 s still shot, cold runs included
MENU = [8, 11, 14, 17, 20, 24, 28, 32, 36, 40]
WALK = [('W', 0, 6), ('J', 6, 8), ('W', 8, 14), ('L', 14, 16), ('S', 16, 19), ('A', 19, 23), ('D', 23, 27)]


def keys(start=START, times=3):
    out = [f'{t}:Enter' for t in MENU]
    for n in range(times):
        for key, a, b in WALK:
            out += [f'{start + n * 120 + a + i / 5:g}:{key}' for i in range((b - a) * 5)]
    return ','.join(out)


if __name__ == '__main__':
    k = keys(*map(int, sys.argv[1:]))
    assert k.startswith('8:Enter') and ',230:W,230.2:W,' in keys() and k.count(':') == len(k.split(','))
    print(k)
