# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

## [1.3.0] — 2026-10-01

The **fast loop**: the daemon stops handing back PNGs and tree dumps, and every input op can raise
its own target window — one `run` call now covers look → act → verify.

### Added

- `op state` — one call returns the foreground window plus every top-level window (name truncated to
  70 chars + rect); `uia:true` adds a flat list of **named elements with rects** instead of a tree dump.
- `op probe` — pixel accounting **inside** the actor with text-only results: `colors` (dominant
  colours, each with a bbox), `grid`, `ink` (ASCII ink map), `mark` + `diff` (baseline kept, so a
  region can be diffed repeatedly). No file, no PNG, no PIL on the client side.
- `front=<hwnd>` / `front_title=<substring>` on every input op (`click` / `move` / `type` / `key` /
  `drag`): raise (and pin) the target first, then act — `SendInput` only reaches the focused window.
- `uia {what:"tree", compact:true}` — flat named-element list instead of the full tree.
- `tests/bench_fast.py`, `tests/verify_type.py`.

### Changed

- `click` no longer eases the pointer by default (24 `SetCursorPos` steps ≈ 100 ms); pass `ease:true`
  for the old behaviour. Real clicks are **33–46 ms**.
- `type` accepts `per_char_ms: 0` → the whole string goes out as **one SendInput batch** (72 ms for
  150 chars; `chunk:40` when an app drops input). The per-character path (12 ms/char) stays the default.
- `move` defaults to `human=false`; `windows` returns only hwnd/name/class/type/rect.
- `skills/demo_calc.py` / `tests/dirty_calc.py` first **restore the app's expected state**:
  `ensure_standard()` walks the Calculator's navigation pane back to Standard, because the app
  remembers its last mode and graphing mode has no `CalculatorResults` — which is exactly how the
  demo failed while nothing was actually broken. `dirty_calc` also polls the display instead of sleeping.

### Measured

| Step | Before (1.2.0) | Now |
|---|---|---|
| Desktop summary (foreground + windows) | 2–4 s (shell + dump) | **45–92 ms** |
| Named element list (109 scanned → 41 named) | seconds + 300 KB | **155–196 ms, a few hundred bytes** |
| Pixel verdict (colours / full-screen diff / ink map) | 1 round trip + file + PIL ≈ 1 s | **63–194 ms** |
| Click | ~120 ms | **33–46 ms** |
| Type 150 chars | 1 956 ms (12 ms/char) | **72 ms** (1 504 chars/s sustained) |
| Look → type 150 → verify, one call | 4 calls ≈ 6 s | **622 ms** |

## [1.2.0] — 2026-10-01

The resident **PC Actor**: the toolkit stops paying for a process per action, and the docs
now say which generation is which.

### Added

- `actor/` — a long-lived automation daemon on `127.0.0.1:8731`, one JSON line per request:
  - `actor.py` — GDI screen grab (cached DC), `SendInput` hands with optional human-like
    easing, UIA structure lookups, pixel fallback (per-row colour runs, vectorised ZNCC
    pyramid template match), the op table (`ping shot save find click move drag type key
    scroll uia window wait_for watch log bench run stop`) and a `run` op that executes a
    whole skill in one call with per-step timings.
  - `act.py` — stdlib-only client that boots the daemon on demand (0.4 s).
  - `actor.ps1` / `act.cmd` — `-Setup` (provision `comtypes` into the actor HOME),
    `-Where`, `-Start`, `-Stop`, `-Status`, `-Bench`, `-Send`, `-Tail`.
  - `skills/demo_calc.py`, `tests/dirty_calc.py`, `tests/probe_uia.py`.
- `op window` — `front` (restore + raise + pin so `SendInput` lands), `top` / `untop`,
  `info`, `max` / `min` / `restore` / `move` / `close`.
- Docs restructure: both READMEs lead with the actor and mark the superseded pieces
  (`tools/gui-steps.ps1`, `tools/template_match.py`, `tools/dsh-look-native.ps1`, the
  selflook plugin) instead of advertising them as the way to work.

### Measured

| Step | Before | Now |
|---|---|---|
| Cost of one step | 2–4 s (new shell + cold Python per action) | **40–140 ms** |
| Whole skill (launch Calculator → `7*8` → read 56 → `12+30` → read 42) | minutes, dozens of calls | **0.62 s** warm / **1.4 s** cold, **0 pixel reads** |
| List top-level windows (name/class/hwnd/rect) | 2–4 s | **41 ms** |
| Click / type / key | 1–2 s / 2–4 s / 1–2 s | **~120 ms / 50 ms / 54 ms** |
| UIA root / find control | 1–3 s | **0.35–2.6 ms / 59 ms** |
| Full-screen colour blobs | 1113 ms | **110 ms** (merge with the previous row only) |
| Full-screen 60×60 template match | 573 ms | **91 ms** (vectorised `sliding_window_view` + ZNCC pyramid) |
| Protocol round trip / client start-up | — | **12 ms / 107 ms** |

### Fixed

- UIA `FindAll` was called with `TreeScope_Element` (1) instead of `TreeScope_Descendants`
  (4) and silently returned zero results.
- UIA COM objects created on an MTA thread raised `RPC_E_CHANGED_MODE`; they are now owned
  by one long-lived STA worker with a per-call timeout, so a hung target app cannot hang
  the actor.
- `SendInput` only reaches the **focused** window, so a skill could type into the wrong app
  and read a stale value (the Calculator still showing the previous result). Skills now
  raise + pin the target, clear with Esc, poll the structure channel, and close the window
  at the end; `tests/dirty_calc.py` reproduces the old failure on purpose.

### Notes

- Actor state (logs, `port.txt`, deps, temp) lives outside the checkout: `$ACTOR_HOME` →
  `actor/home.txt` → code dir.
- Nothing here is novel research: UIA, `SendInput`, ZNCC matching and OCR are the standard
  desktop-automation ingredients; the shape (resident engine + one call per skill) follows
  RPA agents, CDP and Appium.

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
