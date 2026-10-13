# NeonCosmos

**English** | [Русский](README.ru.md)

A music-reactive GPU screensaver for Windows: *the birth of a universe*.
A point of light flashes in the void, particles burst out, a gas nebula grows and stars light up around it. Then it dissolves and the next nebula is born on another monitor. Once in a while a black hole drifts across the screen and bends the light of the stars and gas around it, and a comet with a double tail flies past.

![demo](assets/demo.gif)

*Screen recording of the windowed test mode; it looks sharper on a real screen.*

## Features

- **Five different nebulae** (lagoon, Orion-like, bubble, witch-head, flame), each with its own composition, colours and signature stars. Everything is procedural, generated in shaders, with no image files.
- **Black hole with gravitational lensing**: stars and nebula gas are re-sampled through a point-mass lens, giving arcs and an Einstein ring, plus a soft shadow and a thin photon ring.
- **Comets** with a glowing head, a blue ion (gas) tail and a curved yellow dust tail. The tails sway with the "solar wind" and ripple gently on bass.
- **Reacts to the music playing on your speakers** (WASAPI loopback, no microphone needed). The reaction is deliberately gentle: no harsh flashes.
- **Built for multi-monitor setups**: it covers the whole virtual desktop, nebulae are born inside one monitor and not on the seam between monitors. With a single monitor it automatically switches to a one-screen layout.
- Real `.scr` screensaver: settings dialog (`/c`), single-instance protection.

## Requirements

- Windows 10 / 11
- A GPU with OpenGL 3.3 (any discrete GPU or a recent integrated one)
- Python 3 (tested on 3.14), only if you run it from source

## Run from source

```bash
pip install -r requirements.txt
python neon_cosmos.py window     # test in a window
python neon_cosmos.py /s         # full screen on all monitors (any key / mouse move exits)
python neon_cosmos.py /c         # settings dialog
```

Keys in `window` mode: `S` screenshot, `N` next nebula, `H` black hole now, `C` comet now, `Esc` quit.
The window title shows the current FPS.

## Install as a screensaver

```bash
pip install pyinstaller
python -m PyInstaller --onefile --noconsole --hidden-import pyaudiowpatch --hidden-import moderngl --collect-all moderngl --name NeonCosmos neon_cosmos.py
```

Rename `dist\NeonCosmos.exe` to `NeonCosmos.scr`, copy it to `C:\Windows\System32` (administrator rights needed), then choose it in *Settings → Personalization → Lock screen → Screen saver settings*.

## Settings

The `/c` dialog saves to `~/.neon_cosmos_config.json`: number of burst particles, gas speed, seconds per nebula, glow, black hole strength and frequency, comet brightness and frequency (0 turns black holes / comets off).

More knobs are constants at the top of `neon_cosmos.py`: `BEAT_STRENGTH` (reaction to bass), `EXPOSURE`, `NEB_GAIN`, `LENS_SIZE` and others.

**If the FPS is low** (below ~45, see the window title or the log), set `NEB_DIV = 4` or `SCENE_SCALE = 0.75`.

The log is written to `~/.neon_cosmos.log` (GPU name, monitor layout, FPS, errors).

## How it works (short)

Everything is drawn by shaders with ModernGL: the nebula gas is domain-warped fractal noise rendered at 1/3 resolution, stars are hash-grid layers, burst particles are quads generated in the vertex shader, and a 4-level bloom pyramid plus tone mapping gives the glow. The black hole is a lens equation applied to the sampling coordinates of the gas and star passes. Audio is captured with `pyaudiowpatch` (WASAPI loopback), the bass band is analysed with an FFT, and a beat detector drives the soft pulses.

## Known limitations

- Windows only (WASAPI loopback, Win32 calls).
- Tested on one machine and one GPU. Other GPUs (AMD, integrated Intel) may show shader quirks. Please open an issue and attach `~/.neon_cosmos.log`.
- The lensing is a point-mass model: it looks convincing but is not a physically exact black hole simulation.

## Notes

Written together with an AI assistant (Claude) and tuned by hand through many rounds of screenshots and feedback.

## License

MIT, see [LICENSE](LICENSE).
