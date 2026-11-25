# Findings

Measurements from unattended trials of the GTA VI Xbox build under Proton with VIctory, on the
machine described in the README (2026-11-25). Each one cost us trials to separate from noise;
they may save another port the same work.

## 1. The cell wall is missing at random per launch: stale copies of aliased pages

In the fixed shot after the opening cutscene (Lucia at her cell window, camera still), the wall
left of the window is drawn or absent, all or nothing per launch, with the same executable.
Base rate on our line: drawn in about 1 launch out of 4.

- Fork PR #571 (DirectStorage page-destination writes now notify the GPU side) did not change
  that rate here: 1 launch out of 4.
- Adding the upstream fix for aliased pages (Marvisak/VIctory #1122, `c75fdba`: a write through
  one mapping of title pages reaches every mapping of them) drew the wall in 3 valid launches
  out of 3. By chance alone that is 1 in 64.
- A diagnostic mode that compares every tracked host copy with title memory still reports about
  2 differing ranges per frame after both fixes, all on buffers. Whether they are missed writes or
  GPU results not yet merged back into title memory is still open.

Metric: `qualite.py`'s `mur()` reads the region left of the window in that shot. A drawn wall is
dark (mean luma under 80); without it the sky shows (150-200). It only works for this framing.

Lesson: one launch per side proves nothing for a per-launch bug. We count a fix as proven after
three good launches in a row and refuted after two bad ones.

## 2. Clothing without colour: packed textures marked stale

In the fixed shot, Lucia's jumpsuit came out black, as a wrong pattern or as coloured blocks in
every earlier launch we checked (over 20), never orange as in the cutscene.
fxceless/victory-fork `310c6eb6` changes two things: a newer texture view makes an older one stale only when it starts at the same address (packed small mips of a
neighbour no longer do), and a texture already on the card is refreshed even when the card is
full. With those two parts ported, the orange "INMATE" jumpsuit, the poster and the inmates in
the yard are drawn in 2 valid launches out of 2.

## 3. Speed: copy-back waits on GPU fences, not on PCIe

About 85 MB a frame come back from the card in about 200 ms: 3-4 % of what PCIe 4.0 x8 carries.
The time goes into about 7.5 fence round trips a frame (one copy list, one signal, one blocking
wait each), about 18 ms apiece behind queued work. A wider link would not help; fewer waits
would. Fork PR #648 and upstream PR #1124 attack exactly this.

## 4. Speed noise: compare on the still shot only

Two identical warm trials differed by +23 ms/frame [+3, +48] over the whole run: the cutscene
drifts along the clock from one launch to the next, so paired samples do not show the same
scene. On the fixed shot alone the same pair differs by +9 ms [-11, +33], which contains 0.
Compare speed on the still shot, with two independent pairs.

## 5. Xid 31 one second after our own kill is a teardown artifact

NVIDIA Xid 31 (GPU MMU fault) appeared about one second after our own `pkill -KILL` at the end
of a trial, never during one. `stats.py` splits kernel Xid lines into "during the trial" and
"within 5 s after our own stop"; only the first group counts.

## Reproducing

- Wall and clothing: three or more launches with `VICTORY_CAPTURE_WORLD=1`, then
  `run/qualite.py --fin <trial>` and look at `plan-fixe.jpg`.
- Speed: two identical warm trials, then `run/stats.py <a> <b>` (it prints the still-shot pair).
- Xid: keep `kernel.txt` per trial; `run/stats.py` prints the two groups separately.
