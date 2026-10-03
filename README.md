# dsh-vision-kit

A Windows/desktop toolkit that lets a **DeepSeek Harness** agent see the screen **and act on it**. Two generations live in this repo:

| Generation | What it is | Status |
|---|---|---|
| **`actor/` — the PC Actor** | A resident daemon: UI Automation for structure, `SendInput` for hands, pixel methods as fallback, and **one `run` call per skill**. 40–140 ms per step. | **current path** |
| `plugins/`, `tools/`, `docs/`, `examples/`, `tests/` | The original zero-model vision toolkit — native-capture plugin, template matching, geometry, OCR, VLM scoring — plus the measured reports that justify every number. | reference; still the only OCR / geometry / VLM-scoring path |

> Every capability claim in this repo is scored against ground truth. Nothing here is "it looked right".

- 中文: [README.zh-CN.md](README.zh-CN.md) · Actor manual: [actor/README.md](actor/README.md) · Reports: [English](docs/vision-capability-report.md) · [中文](docs/视觉能力实测报告.md)

## The current path: the PC Actor

`actor/` is a long-lived process that holds the screen, the input queue and the automation
session, and answers one JSON line per request on `127.0.0.1:8731`. It exists because the
slow part of GUI automation was never recognition — it was **the loop**: one hypothesis per
round trip, each round trip paying for a fresh shell and a cold Python.

```powershell
# nothing to install first: the client boots the daemon on demand
.\actor\act.cmd ping
.\actor\act.cmd '{"op":"uia","mode":"windows"}'
.\actor\act.cmd '{"op":"run","steps":[ ... ]}'      # a whole skill in one call, per-step timings back
.\actor\actor.ps1 -Setup                            # provision deps into the actor HOME
.\actor\actor.ps1 -Where                            # code dir / HOME / deps
```

Channel order is deliberate: **structure first** (`uia`), **hands second** (`click`, `type`,
`key`, `move`, `drag`, `scroll`, `window`), **pixels last** (`shot`, `save`, `find` — colour
blobs and a vectorised ZNCC pyramid) for canvases and self-drawn UI that UIA cannot reach.

| Step | Old way (one tool round trip each) | Actor |
|---|---|---|
| List top-level windows (name/class/hwnd/rect) | 2–4 s | **48 ms** |
| Find a control by name / automation id | 1–3 s | **59 ms** (36 buttons in one call) |
| Click (no human-like easing by default) | 1–2 s | **33–46 ms** (the old easing path: 120 ms) |
| Type text / press Enter | 2–4 s / 1–2 s | **50 ms / 54 ms** |
| Full-screen colour blobs | 1.1 s | **102 ms** |
| Full-screen 60×60 template match | 573 ms | **83 ms** |
| Protocol round trip / client start-up | — | **12 ms / 107 ms** |
| Whole skill: launch Calculator → `7*8` → read 56 → `12+30` → click "=" → read 42 | minutes, dozens of calls | **0.62–0.72 s** (warm) / **1.4 s** (cold UWP launch), **zero pixel reads** |

Details, the op table, the state layout and eleven hard-won pitfalls
(`TreeScope_Descendants = 4`, single-STA COM ownership with per-call timeouts, UWP launch
via `shell:appsFolder`, `SendInput` only reaches the focused window → raise + pin first,
poll the structure channel instead of sleeping, don't hand JSON to `act.cmd` from PowerShell
— the full list is in `actor/README.md`):
[actor/README.md](actor/README.md). The HOME that holds state is machine-local: `-Where` prints
it, `-HomePath` / `ACTOR_HOME` move it, and nothing generated lands in the repo.

## Generation 1: seeing the screen

### Why

The stock self-look plugin renders the page by **serializing the DOM** into an SVG `<foreignObject>` and rasterizing it. That pipeline drops `<img> <svg> <canvas> <picture>` and knows nothing about anything outside the browser window, so:

- images, widgets and charts **never appear** in the screenshot (measured: in the widget's corner the DOM capture has a pixel std-dev of 0.004 — flat color);
- only the browser surface is visible; the desktop, other apps and the tray are invisible.

This plugin captures the **real screen** instead (Win32 `CopyFromScreen` under a per-monitor DPI-awareness context), and keeps the DOM path as an explicit fallback.

### Layout

```
actor/                        the PC Actor — see actor/README.md (current path)
plugins/dsh-selflook-local/   DSH plugin: native capture + `shot` RPC (fork of upstream self-look)
                              ⚠ not installed by default; the actor's `shot` op supersedes it
tools/
  template_match.py           FFT normalized cross-correlation template matching (no model)
                              ⚠ superseded for full-screen work by the actor's `find` op
  cv_ui_geometry.py           run-length border lines + morphological blob opening (no model)
  ground_test.py              score a VLM's box across 6 coordinate interpretations
  probe_and_ocr.py            step-contrast probe + tesseract crop OCR
  ocr_boxes.py                tesseract TSV wrapper: crop, upscale, psm, invert, original-coord boxes
                              (the actor has no OCR — this is still the only text path)
  brightmap.py                ASCII brightness heat-map of a screen region
  reset-ollama.ps1            kill orphan llama-server processes holding VRAM, restart one server
  dsh-look-native.ps1         take one screenshot through the plugin RPC (needs the plugin)
  gui-steps.ps1               plan-driven desktop GUI driver — the one-process idea, 2026-10-01
                              ⚠ superseded by `actor run`; kept as the reference implementation
  contact-send.ps1            one command: search a contact, pick it, verify identity, send, re-check
  pixel-verdict.py            OCR-free pixel verdicts (composer ink / bubble blue)
docs/
  vision-capability-report.md English report (table, reproduction, pitfalls)
  视觉能力实测报告.md          Chinese report
  界面自动化日志.md            Chinese work log: widget drag, QQ send, recognition speed-up
  windows-ollama-setup.md     Ollama-on-Windows notes for DSH vision
examples/vision-router-tuned.yml   the vision-router config block used here
tests/score-pipeline.ps1      score both zero-model detectors against a ground-truth box
```

### Quick start (generation 1)

#### 1. Install the plugin

The plugin is **optional** and not installed here — install it only if you want the
plugin-side capture path. Following the upstream dsh-termux-kit convention, drop it into the
profile's `local/`:

```powershell
$profile = "$env:USERPROFILE\.dsh\profiles\desktop"   # adjust to your profile
Copy-Item .\plugins\dsh-selflook-local "$profile\local\" -Recurse -Force
```

Then add the dependency and the bundle entry to `$profile\package.json`:

```jsonc
{
  "dependencies": { "dsh-selflook-local": "file:./local/dsh-selflook-local" },
  "dsh": { "profile": { "bundles": [ /* … */ "dsh-selflook-local" ] } }
}
```

Restart DSH (or keep `hmr.root: ["."]` in the profile's `cordis.patch.yml` to reload live). DSH's own plugin manager can install the same directory.

#### 2. Take a screenshot

```powershell
# with the plugin:
pwsh -File tools\dsh-look-native.ps1                     # default http://127.0.0.1:19387

# without any plugin — the actor does it directly:
.\actor\act.cmd '{"op":"shot","path":"shot.png"}'
```

The plugin also accepts a trigger file (`~/.dsh-look-request`, contents `dom` to fall back to the DOM serializer) and writes the latest path to `~/.dsh-look-last.txt`.

#### 3. Use the tools

```powershell
$py = "python"   # needs numpy + Pillow
$shot = "shot.png"

# template matching — the most accurate option when you have the asset
& $py tools\template_match.py $shot .\assets\role.png --scales 0.34,0.3656,0.40 --expect 2337,1305,2561,1529

# borders / layout — no asset needed
& $py tools\cv_ui_geometry.py $shot --noise 3 --min-run 250 --annotate .\cv.png

# score a model's box
& $py tools\ground_test.py --image $shot --model granite3.2-vision:2b --prompt "bounding box of the whale" --expect 2337,1305,2561,1529

# both zero-model detectors in one go
pwsh -File tests\score-pipeline.ps1 -Image $shot -Expect 2337,1305,2561,1529
```

### Generation 1.5: scripted GUI driving (2026-10-01, superseded)

Not just seeing — acting. `tools/gui-steps.ps1` runs a JSON plan of mouse / keyboard /
clipboard / screenshot / template-match steps **inside a single pwsh process**, so a whole
interaction costs one process start instead of one per action. **This is the ancestor of the
PC Actor**; `actor run` now does the same thing with a resident process, structure lookups
and per-step timings, so new work should use the actor.

- **Send a message, measured**: one command does search → pick → verify identity with a
  header template → send → pixel re-check in **1.89 s / one call**; three independent
  signals (bubble OCR conf 94.9, a new centred timestamp, composer emptied + bubble-blue
  pixels at 7.2% of the band).
- **Two real traps**: the DSH window keeps stealing the foreground (pin the target with
  `SetWindowPos(HWND_TOPMOST)`, restore afterwards), and QQ sends on **Ctrl+Enter** —
  a bare Enter only inserts a newline.
- **Recognition stopped being the bottleneck**: contact-header recognition went from
  tesseract at 0.7 s per call to in-process **ZNCC template matching at ≈5 ms** (1.000 on
  the right header, 0.336 when deliberately displaced 400 px, threshold 0.80) — reading only
  a 175×50 header strip (≈5 ms) instead of the full screen (≈0.25 s).

Process, pitfalls and reproduction commands: [docs/界面自动化日志.md](docs/界面自动化日志.md).

## Measured results (2026-10-01, 2560×1600 native capture, truth = DOM × dpr 1.5)

| Method | Target | Result | Usable as a ruler? |
|---|---|---|---|
| DOM `getBoundingClientRect` | any element | widget root `2186,1154,375,375` physical | ✅ exact (baseline) |
| **Template matching** (no model) | widget art asset | NCC 0.955, **IoU 0.991** | ✅ pixel-level |
| **Run-length border detection** (no model) | sidebar right edge | `x=419` vs truth 420 (**1 px**, run 1450 px) | ✅ pixel-level |
| tesseract `-l chi_sim+eng --psm 6` | sidebar budget text | reads it with no preprocessing | ✅ text-level |
| Semantic blob / color heuristics | widget box | IoU 0.19–0.45 | ⚠️ qualitative only |
| granite3.2-vision:2b | widget box | `[0.81,0.78,0.9,0.82]`, IoU 0.000–0.004 | ❌ not a measurement |
| qwen2.5vl:3b | widget box | `[1059,582,1148,693]`, IoU 0.000 | ❌ not a measurement |

**In one line:** geometry comes from pixel algorithms (1 px), text comes from OCR, and a small local VLM only answers "roughly where". Full table, reproduction commands and pitfalls: [docs/vision-capability-report.md](docs/vision-capability-report.md).

## Requirements

- Windows 10/11, PowerShell 5.1+.
- For the actor: Python 3.9+ with numpy (`actor.ps1 -Setup` installs `comtypes` into the
  actor HOME; the client itself is pure standard library). Windows UI Automation comes from
  the OS — no SDK, no installer, no admin rights.
- For the vision plugin/tools: Win32 `SetThreadDpiAwarenessContext` + `CopyFromScreen`;
  DSH desktop (0.1.7 / 0.2.x) if you install the plugin bundle; Python 3 with numpy +
  Pillow; optionally [tesseract](https://github.com/UB-Mannheim/tesseract/wiki) with
  `chi_sim+eng`.
- Optional local vision model: Ollama + `qwen2.5vl:3b` or `granite3.2-vision:2b` — see [docs/windows-ollama-setup.md](docs/windows-ollama-setup.md).

## Security

- The plugin registers the RPC route `/__dsh__/selflook/rpc` on the DSH web server. DSH core does not authenticate plugin routes, so the plugin enforces its own **same-origin check** (`Sec-Fetch-Site` / `Origin` vs `Host`, cross-site → 403) and fixes its own output directory instead of accepting a caller-supplied path. It captures the **entire screen**, including other windows — make sure that is what you want.
- The actor listens on **`127.0.0.1:8731` only** and has no authentication: anything that can
  reach that port can move the mouse, type and read the screen. It is a local automation
  tool, not a service to expose. Input synthesis goes to the **focused** window, which is why
  skills raise and pin their target (`op window front|top`) before acting.

## Credits & license

- The plugin is a fork of `dsh-selflook-local` from [Maopk/dsh-termux-kit](https://github.com/Maopk/dsh-termux-kit); the DPI-aware capture approach follows `dsh-vision-router`.
- The actor follows the standard desktop-automation recipe (UIA for structure, `SendInput` for events, pixels as fallback) in the shape of a resident engine — the same architecture as RPA agents, the Chrome DevTools Protocol and Appium servers.
- No UI screenshots are published here (they would expose session contents); results are given as numbers plus reproduction commands.
- MIT — see [LICENSE](LICENSE).
