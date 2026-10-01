# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

## [1.1.0] — 2026-10-01

GUI automation: the toolkit now drives a real desktop instead of only measuring it.

### Added

- `tools/gui-steps.ps1` — plan-driven desktop GUI driver. One pwsh process executes a JSON
  plan of `focus | top | rect | move | click | press | glide | release | paste | key |
  match | sleep | shot` steps, so a whole interaction costs one process start. Includes
  `top` (pin the target window with `HWND_TOPMOST` so keystrokes cannot land in DSH) and
  `match`, an in-process ZNCC template match with a jitter search.
- `tools/contact-send.ps1` — one command: search a contact, pick the row, verify the
  header template, paste, send with Ctrl+Enter, then pixel-verify; `-DryRun` types and
  checks without sending.
- `tools/pixel-verdict.py` — OCR-free verdicts (composer ink, bubble-blue pixel counts).
- `tools/ocr_boxes.py`, `tools/brightmap.py` — tesseract TSV wrapper (original-coordinate
  boxes) and an ASCII brightness heat-map for finding panel edges.
- `docs/界面自动化日志.md` — the work log: widget heart-path drag, the message send with
  three independent proofs, and the recognition speed-up — with every measured number.

### Measured

| Step | Before | Now |
|---|---|---|
| Recognise a contact header | tesseract 0.68–0.70 s per call | in-process ZNCC **≈5 ms** (1.000 right / 0.336 wrong, threshold 0.80) |
| Capture for that check | full screen 2560×1600 ≈0.25 s | 175×50 header strip ≈5 ms |
| Whole search→pick→verify→send | minutes, dozens of calls | **1.89 s in one call** |
| Precompiled P/Invoke | csc compile ≈0.5 s per run | `Add-Type -Path` ≈0.05 s |

### Notes

- Browser screenshots must never be committed (they expose session content); docs carry
  numbers and reproduction commands instead.
- Coordinates in the log are 2560×1600 measurements for one app version — a layout change
  makes the checks **FAIL rather than send blindly**.

## [1.0.0] — 2026-10-01

First public release: everything in this repo was measured on one machine
(i7-14650HX / RTX 5060 Laptop 8 GB / DSH 0.2.0-rc.2 / Windows 11) against DOM
ground truth, and the numbers in the docs are those measurements.

### Added

- `plugins/dsh-selflook-local` — fork of the upstream self-look plugin with
  **native, per-monitor-DPI-aware desktop capture**:
  - new RPC method `shot`: one HTTP call returns a real screen PNG;
  - the `~/.dsh-look-request` trigger now captures natively by default and only
    falls back to the in-page DOM serializer when the request body is `dom`;
  - `poll` returns `{pending:false, mode:"native", path, width, height, ms}` so
    callers can tell which path produced the image.
- `tools/` — zero-model geometry toolkit and diagnostics:
  - `template_match.py` — FFT normalized cross-correlation, per-scale;
  - `cv_ui_geometry.py` — run-length border lines + morphology blob opening;
  - `ground_test.py` — scores a VLM's box, trying 6 coordinate interpretations;
  - `probe_and_ocr.py` — step-contrast probe + tesseract crop OCR;
  - `dsh-look-native.ps1` — take a screenshot through the plugin's `shot` RPC;
  - `reset-ollama.ps1` — kill orphan `llama-server` processes holding VRAM and
    bring back exactly one server.
- `docs/视觉能力实测报告.md` / `docs/vision-capability-report.md` — the measured
  capability table, with reproduction commands.
- `docs/windows-ollama-setup.md` — Ollama-on-Windows notes for DSH vision.
- `examples/vision-router-tuned.yml` — the vision-router config block used here.
- `tests/score-pipeline.ps1` — runs the two zero-model detectors on an image and
  prints their IoU against a ground-truth box.

### Measured results (see docs for the full table)

| Method | Target | Result |
|---|---|---|
| Template matching (no model) | whale widget image | NCC 0.955, **IoU 0.991** |
| Run-length border detection | sidebar right edge | x=419 vs truth 420 (**1 px**) |
| tesseract `--psm 6` | sidebar budget text | readable, no preprocessing |
| granite3.2-vision:2b grounding | widget box | IoU 0.000–0.004 |
| qwen2.5vl:3b grounding | widget box | IoU 0.000 |

### Notes

- Local VLM boxes are **not** measurements. Use DOM, templates, or the CV tools
  for geometry; use tesseract for text; use the VLM only for coarse semantics.
- The first version of the border detector returned "0 borders" because of an
  axis bug in `longest_run()`. It was only caught by scoring against DOM truth —
  which is why the scoring harness ships with the tools.
