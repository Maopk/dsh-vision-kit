# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

## [Unreleased]

### Added

- `.github/workflows/ci.yml` — the repo can now check itself on a clean Windows runner:
  `py_compile` over every Python file, then `tests/score-pipeline.ps1` against a synthetic
  screenshot whose expected box is known, so the scoring pipeline runs end to end without this
  box's screen, its sample screenshot or its interpreter path.
- `tests/make-synthetic-sample.py` — draws that screenshot (frame + block at known coordinates),
  so the pipeline can be re-scored anywhere. CI is not special: the same two commands are in
  CONTRIBUTING's checks.
- `tests/score-pipeline.ps1 -Strict` — the script used to print numbers and always exit 0, which
  made it useless as a gate. With `-Strict` every expected edge must have a detected border line
  within `-TolerancePx` (default 4 px, the run-length detector reports the inner edge of a stroke)
  and the script exits 1 otherwise.
- `tools/ci-static.ps1` — every offline static check behind one command: parse each Python file
  (`ast.parse`, so no `.pyc` lands in the tree), `ruff`, `mypy` **one file at a time** (separate
  entry scripts all look like `__main__` to mypy when passed together), `PSScriptAnalyzer` for the
  seven scripts (it checks itself too), `node --check` for the two plugin bundles. Each stage prints
  its own count and the script exits 1 if any of them failed, so local and CI share one entry point.
- `tests/unit-tests.py` — 69 assertions over the headless half of the kit: `act.build` / `act.fmt`
  (the argv → request and reply → text rules), `act.home` / `act.port`, the run-length and
  morphology helpers, `find_lines` / `find_solid_blobs`, the NCC matcher and `iou`, the
  `ink` / `blue` counters and `load`'s box slicing, plus an import smoke test for every tool that
  must stay importable. No pytest dependency: it is a script that prints
  `N passed / M failed` and exits 1.
- `ruff.toml`, `mypy.ini`, `PSScriptAnalyzerSettings.psd1`, `requirements.txt` and
  `requirements-dev.txt`. The linters are pinned narrow on purpose (ruff runs `E9`, `F`, `B`; the
  analyzer runs Error + Warning), because this repo writes `%`-formatting, one-line `if x: y` and
  `except Exception: pass` deliberately and the default rule sets answer with ~160 style hits that
  would bury the real ones. `numpy` and `pillow` are pinned too: a silent numpy upgrade must not be
  able to move the scored detector run.
- The workflow grew from one job to four: `static` (the analyzer above), `unit` (the tests above),
  `detectors` (the scored synthetic sample, unchanged) and `desktop-recorded`, which lists the
  checks that need this box's screen or its UI Automation — `actor/tests/*.py`,
  `actor/skills/demo_calc.py`, `tools/gui-steps.ps1`, `tools/contact-send.ps1`, `ground_test.py`,
  `ocr_boxes.py`, `reset-ollama.ps1` — runs what can run headless, and records the rest in the run
  summary and an artifact instead of pretending to judge them.

- `skills/` — the model-facing half of the kit now lives in the repo as well. A skill is
  `<name>/SKILL.md` with `name` / `description` front matter, and DSH loads it from
  `$DSH_HOME\skills\<name>\SKILL.md`; the repo is the single source of truth and the skills home is
  a mirror of it. The first skill, `drive-a-windows-gui`, is the method this kit hands a model:
  decide whether pixels are needed at all (structural channel → logs and saves → deep link →
  pixels), read structure before screenshots, wait with `wait_for` rather than `sleep`, check one
  signal after every action, run a whole sequence in one `run`, fail loudly, and hand back to the
  user for anything irreversible. Its rules carry the measurement that justifies them (93 s of
  hand-written sleeps against ~3 s of real mouse and keyboard work in one 280 s task), and the
  machine-specific facts sit in an appendix next to the command that rediscovers them, so the body
  stays portable to another box.
- `tools/install-skills.ps1` — mirrors `skills/` into the skills home and reports drift instead of
  guessing: `-Check` prints `N 一致 · M 需同步 · K 多余` and exits 1 when out of sync; a plain run
  links (symbolic link, so the installed skill follows later repo edits) and falls back to a copy,
  saying so, when linking is not permitted; `-Mode Copy|Link` forces either; `-SkillsHome` points it
  somewhere else. Nothing outside the named skills is ever touched, and nothing is ever deleted.
- `tools/check-skill-ops.py` — takes the op registry straight from `actor/actor.py`'s `@op('name')`
  decorators and fails when a skill names an op that does not exist. Names are read only from real
  requests (`"op": "click"` in an example, `act.cmd run <file>` in a command line), never from the
  prose that explains an op, and the module docstring's `Ops:` line is checked against the same set
  so the summary cannot drift away from the decorators. It runs as stage 6 of `tools/ci-static.ps1`,
  so CI picked it up without a workflow edit.

### Fixed

- `actor/actor.py`'s module docstring said the op set was `ping … log stop` (17 names) and left out
  `window`, `state` and `probe`; the decorators register 20. The line now matches the registry, and
  `tools/check-skill-ops.py` keeps it that way — that check is how the drift was found.
- `actor/act.cmd` and `actor/actor.ps1` no longer hard-code one machine's interpreter path
  (CONTRIBUTING, ground rule 2: no absolute paths in committed scripts). Both now try
  `-Python` / `%ACTOR_PY%` → DSH's bundled runtime → `python` on PATH → the `py` launcher, and
  say what to set when none of them works. `tests/score-pipeline.ps1` got the same candidates.
- The top-level README's latency table now carries the numbers measured in `actor/README.md`
  (windows 48 ms, colour blobs 102 ms, template match 83 ms, click 33–46 ms) instead of the older
  41 / 110 / 91 / ~120 ms, and says nine hard-won pitfalls, not seven.
- `actor/README.md` no longer presents this machine's HOME (`D:\DSH\dsh-actor`) as part of the
  layout — it is one example, and `-Where` prints the real one.
- `tools/pixel-verdict.py` pointed at `qq-send.ps1`, which does not exist; the script it verifies is
  `tools/contact-send.ps1`.
- `actor/tests/bench_fast.py` and `actor/tests/verify_type.py` put this machine's repo path on
  `sys.path`; they now derive `actor/` from `__file__`, so the benches run from any checkout.
- `actor/tests/bench_fast.py` and `actor/tests/verify_type.py` used to do their work at import
  time: importing one moved the mouse, clicked and typed into whatever window was focused. The
  definitions stay at module level and the runs now sit in `main()` behind
  `if __name__ == '__main__'`; the bodies are unchanged (dedent them back and they diff clean
  against the previous revision), and `python actor\tests\bench_fast.py` behaves as before.
- Findings from the three new linters, all of them small and none of them behavioural: an unused
  `socket` import in `actor/actor.py`, `zip(starts, ends, strict=True)` where both arrays come from
  one `np.where` and so cannot differ in length, two lambdas in the `probe` path that now bind
  `client` / `UIA` as defaults (a late rebind can no longer leak into a timed-out call), the unused
  `numpy` import in `tools/ground_test.py`, the unused loop variable in `tools/ocr_boxes.py`, and
  the three empty `catch { }` blocks (`actor/actor.ps1`, `tests/score-pipeline.ps1`,
  `tools/reset-ollama.ps1`), which now say in `Write-Verbose` what they swallowed.
- `sys.stdout.reconfigure(...)` carries `# type: ignore[union-attr]` at its five call sites:
  typeshed types `sys.stdout` as `TextIO`, which has no `reconfigure`, so mypy is right and the
  code is right — the comment records why instead of silencing a whole file.
- The remaining mypy findings, all of them type-level and none behavioural: `Image.LANCZOS` became
  `Image.Resampling.LANCZOS` in `tools/template_match.py`, `tools/ground_test.py` and
  `tools/ocr_boxes.py` (the old alias still resolves at runtime, but Pillow's own annotations do not
  declare it), that script's crop/scale variable is annotated `Image.Image` because it starts as an
  `ImageFile` and every later step returns an `Image`, and the two numpy fallbacks in
  `actor/actor.py` carry `# type: ignore[assignment]` — a failed import assigns `None` to a name
  mypy has already typed as a module.
- Six non-ASCII PowerShell scripts (`tests/score-pipeline.ps1`, `tools/contact-send.ps1`,
  `tools/dsh-look-native.ps1`, `tools/gui-steps.ps1`, `tools/reset-ollama.ps1` and the new
  `tools/ci-static.ps1`) now start with a UTF-8 BOM. `PSScriptAnalyzer` asks for it and
  `actor/actor.ps1` already had one, so this is the repo's existing convention, not a new rule.

### Changed

- `actor/README.md` gained two pitfalls from an end-to-end screen-driving session: JSON handed to
  `act.cmd` from PowerShell arrives without its inner quotes (call `act.py` instead, or pass a
  request file), and a web editor already has focus when it loads — pasting, clicking the editor
  and pasting again doubles the file. CONTRIBUTING's checks carry the same shell caveat, plus the
  `?filename=<path>` URL form GitHub actually needs for a new file.

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
