# GTA VI on Linux (VIctory)

Xbox Series build of GTA VI. It runs on Linux through Proton and
[VIctory](https://github.com/Marvisak/VIctory), an Xbox GDK compatibility layer.
This repository contains the tooling and the findings. It contains no game data, no game binaries
and no VIctory build. Code: [vongikkingen/victory-linux](https://github.com/vongikkingen/victory-linux),
branch `gta6-linux`.

## Status (2026-11-25)

- Current state: close-to-console rendering. Work in progress.
- Scene: prologue, prison. Cell, characters, clothing and yard are drawn.
- Defects: light-blue blocky tiles at the bottom edge in some launches. Lighting changes between
  launches.
- Rate: approximately 3 fps (300 ms per frame). Main limit: waits on GPU fences during copy-back.

## Demo

https://github.com/user-attachments/assets/193b79bb-afd9-42af-8c03-873cdf0934ce

Prologue, prison dormitory, live play. Build: fixes 1 and 2 of FINDINGS. 1080p, 102 s. The desktop
outside the game window is blurred. Video file: [media/demo.mp4](media/demo.mp4).

## Configuration

- CPU: AMD Ryzen 5 5600X, 6 cores, 12 threads. RAM: 32 GB.
- Game GPU: NVIDIA RTX 3090, 24 GB, driver 620.70.06, PCIe 4.0 x8, Resizable BAR on.
- Display GPU: AMD RX 5700 XT.
- OS: Fedora Linux 44, kernel 7.3.3.
- Proton Experimental 11.0 (2026-11-12). vkd3d-proton git build `2576c0b`.
- VIctory: fxceless/victory-fork line, base `c96b4f2`, with the ports given in
  [FINDINGS.md](FINDINGS.md).

## Method

Agentic loop. A mini swarm of 6 or 7 agents works on this repository in a continuous loop.
Tasks: monitor VIctory and its forks, port fixes, run unattended trials, evaluate the results.

## Tooling

- `run/queue.sh`: command queue. One command per line, one trial at a time.
- `run/launch.sh`: one trial in a sandbox. Watchdogs, captures, end-of-trial report.
- `run/build.sh`, `run/scan-securite.sh`: security gate, then build.
- `run/marche.py`: scripted input (`VICTORY_KEYS`). Menu, then a fixed walk.
- `run/qualite.py`: still-shot window, wall detector `mur()`, instability, SSIM.
- `run/stats.py`: trial summary. Paired A/B with a 95 % interval.
- `run/planche.py`: contact sheet. Captures aligned on the scene, one row per trial.
- `run/census.sh`: offline census of all shaders in the game pack. No GPU necessary.
- `run/surveille.sh`: event monitor. Trial end, queue stop, game alert.

File names are in French. Code and messages are in English.

## Procedure

Requirements: Linux, `bash`, `bubblewrap`, `systemd-run`, `flock`. Python 3.10 or later with
Pillow and numpy. Rust with `cargo-xwin`. Proton Experimental, or set `PROTON_DIR`.

1. Set the paths: `export GTA6_GAME=/path/to/game GTA6_PORT=$PWD`.
2. Build the base tree: `run/build.sh base`.
3. Run one trial:
   `run/launch.sh 900 demo VICTORY_CAPTURE_WORLD=1 VICTORY_KEYS="$(run/marche.py)"`.
4. Compare two trials: `run/stats.py run/state/logs/<trial-a> run/state/logs/<trial-b>`.

Unattended operation: `run/queue.sh add '<command>'`, then `setsid run/queue.sh run &`.
All paths come from environment variables. Refer to the header of each script.

## Findings

Refer to [FINDINGS.md](FINDINGS.md).

## License

MIT. Refer to `LICENSE`.
