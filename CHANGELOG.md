# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

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
