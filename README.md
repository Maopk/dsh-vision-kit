# dsh-vision-kit

A Windows/desktop toolkit that lets a **DeepSeek Harness** agent actually see the screen: a self-look plugin with **native screenshot capture**, a **zero-model** geometry/OCR toolchain, and a **measured** capability report.

> Every capability claim in this repo is scored against ground truth. Nothing here is "it looked right".

- 中文: [README.zh-CN.md](README.zh-CN.md) · Reports: [English](docs/vision-capability-report.md) · [中文](docs/视觉能力实测报告.md)

## Why

The stock self-look plugin renders the page by **serializing the DOM** into an SVG `<foreignObject>` and rasterizing it. That pipeline drops `<img> <svg> <canvas> <picture>` and knows nothing about anything outside the browser window, so:

- images, widgets and charts **never appear** in the screenshot (measured: in the widget's corner the DOM capture has a pixel std-dev of 0.004 — flat color);
- only the browser surface is visible; the desktop, other apps and the tray are invisible.

This plugin captures the **real screen** instead (Win32 `CopyFromScreen` under a per-monitor DPI-awareness context), and keeps the DOM path as an explicit fallback.

## Layout

```
plugins/dsh-selflook-local/   DSH plugin: native capture + `shot` RPC (fork of upstream self-look)
tools/
  template_match.py           FFT normalized cross-correlation template matching (no model)
  cv_ui_geometry.py           run-length border lines + morphological blob opening (no model)
  ground_test.py              score a VLM's box across 6 coordinate interpretations
  probe_and_ocr.py            step-contrast probe + tesseract crop OCR
  dsh-look-native.ps1         take one screenshot through the plugin RPC
  reset-ollama.ps1            kill orphan llama-server processes holding VRAM, restart one server
docs/
  vision-capability-report.md English report (table, reproduction, pitfalls)
  视觉能力实测报告.md          Chinese report
  windows-ollama-setup.md     Ollama-on-Windows notes for DSH vision
examples/vision-router-tuned.yml   the vision-router config block used here
tests/score-pipeline.ps1      score both zero-model detectors against a ground-truth box
```

## Quick start

### 1. Install the plugin

Following the upstream dsh-termux-kit convention, drop it into the profile's `local/`:

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

### 2. Take a screenshot

```powershell
# A: one HTTP call, image immediately (recommended)
pwsh -File tools\dsh-look-native.ps1                     # default http://127.0.0.1:19387
pwsh -File tools\dsh-look-native.ps1 -Port 3080

# B: drop a trigger file, the plugin polls and captures
Set-Content "$env:USERPROFILE\.dsh-look-request" ""      # native capture
Set-Content "$env:USERPROFILE\.dsh-look-request" "dom"   # fall back to the DOM serializer
```

Both write the latest path to `~/.dsh-look-last.txt`; PNGs land in `~/Downloads/dsh/图片/`.

### 3. Use the tools

```powershell
$py = "python"   # needs numpy + Pillow
$shot = Get-Content "$env:USERPROFILE\.dsh-look-last.txt"

# template matching — the most accurate option when you have the asset
& $py tools\template_match.py $shot .\assets\role.png --scales 0.34,0.3656,0.40 --expect 2337,1305,2561,1529

# borders / layout — no asset needed
& $py tools\cv_ui_geometry.py $shot --noise 3 --min-run 250 --annotate .\cv.png

# score a model's box
& $py tools\ground_test.py --image $shot --model granite3.2-vision:2b --prompt "bounding box of the whale" --expect 2337,1305,2561,1529

# both zero-model detectors in one go
pwsh -File tests\score-pipeline.ps1 -Image $shot -Expect 2337,1305,2561,1529
```

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

- Windows 10/11 (native capture uses Win32 `SetThreadDpiAwarenessContext` + `CopyFromScreen`), PowerShell 5.1+.
- DSH desktop (0.1.7 / 0.2.x), plugin installed as a bundle.
- Toolchain: Python 3 with numpy + Pillow; optionally [tesseract](https://github.com/UB-Mannheim/tesseract/wiki) with `chi_sim+eng`.
- Optional local vision model: Ollama + `qwen2.5vl:3b` or `granite3.2-vision:2b` — see [docs/windows-ollama-setup.md](docs/windows-ollama-setup.md).

## Security

The plugin registers the RPC route `/__dsh__/selflook/rpc` on the DSH web server. DSH core does not authenticate plugin routes, so the plugin enforces its own **same-origin check** (`Sec-Fetch-Site` / `Origin` vs `Host`, cross-site → 403) and fixes its own output directory instead of accepting a caller-supplied path. It captures the **entire screen**, including other windows — make sure that is what you want.

## Credits & license

- The plugin is a fork of `dsh-selflook-local` from [Maopk/dsh-termux-kit](https://github.com/Maopk/dsh-termux-kit); the DPI-aware capture approach follows `dsh-vision-router`.
- No UI screenshots are published here (they would expose session contents); results are given as numbers plus reproduction commands.
- MIT — see [LICENSE](LICENSE).
