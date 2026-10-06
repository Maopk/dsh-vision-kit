# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

## [Unreleased]

### Added

- `tools/check-counts.py` — **one source per documented count**, wired as stage 7 of
  `tools/ci-static.ps1` and runnable on its own. Three checks, stdlib only, no screen: (1) a count
  written down in more than one place must equal its source — the pitfall count claimed by
  `README.md` / `README.zh-CN.md` / `skills/*/SKILL.md` against the numbered list in
  `actor/README.md` (it had drifted twice: seven -> nine -> eleven), and the stage count claimed by
  `CONTRIBUTING.md` against the `# ── N.` markers of `ci-static.ps1` plus the list in its own
  docstring (that one was already wrong: five against six running stages); (2) every number the
  English README carries must also appear in the Chinese one, and the same for the two capability
  reports — the reverse direction needs an entry in the allowlist with a reason (today: the OCR'd
  预算/余额 row); (3) every file named in a README Layout block exists on disk. The design note that
  asked for it is `docs/计数闸设计.md`. Boundary: it compares numbers and names, not prose — a
  wrong sentence next to the right numbers still passes.

- `actor.py` — **a background input channel** (`bg:true`, plus `hwnd` / `title_contains` on the input
  ops) **and the Tk key names that used to be dropped silently**. `click` / `move` / `drag` / `type` /
  `key` / `scroll` can post to a window instead of driving the global cursor and keyboard: a primitive
  layer (`post_click` / `post_drag` / `post_text` / `post_key` / `post_scroll`, `focus_hwnd`,
  `grab_window`) maps global coordinates into the window's client area, `window` gained
  `mode:"foreground"` / `"bottom"` / `"focus"`, and `shot` / `save` can crop a window's own pixels
  (`hwnd=`) through `PrintWindow` while that window sits behind another one. `focus_hwnd` exists
  because **Tk throws posted keys away unless it believes it has focus**, so the focus handover and
  the post have to travel in the same request. The key table also learned `backspace` and the
  `prior` / `pageup` / `next` / `pagedown` spellings. No new op (23), no new dependency, no absolute
  path, and the state directory order is still `$ACTOR_HOME` > `home.txt` > the code dir.
  Honest boundary: on the Tk practice target **posted mouse messages do not drive the app** — three
  runs left it on task 0 while the driver reported its clicks and drags, so that half is recorded as
  an inference, not a measurement (keys posted after a focus handover do work). `PrintWindow` asks for
  `PW_RENDERFULLCONTENT` (flag 2) and falls back to flag 0 for windows that refuse it.
  Measured (`836c867`, Calculator, 2026-10-06): `actor\tests\dirty_calc.py` leaves the app dirty in
  26.1 ms, then `actor\skills\demo_calc.py` passes both sums — `7*8 -> 56`, `12+30 -> 42`, **0 pixels
  read** — with window front 3.5 ms, TOPMOST pin 1.6 ms, click 42.1 ms, `esc` 52.1 ms, `type` 55.9 ms,
  `enter` 55.8 ms, display poll 16.4 ms, the whole 36-button keypad in one structure call 47.4 ms,
  `type` 81.8 ms, click-by-name 63.2 ms, untop 3.1 ms, and **1719.0 ms wall clock including the launch**.

- `actor.py` op `capture` — record a demonstration **by hand** and get a macro back:
  `act.cmd capture start name=calc-demo front_title=计算器`, do it once yourself,
  `act.cmd capture stop`. Low-level mouse and keyboard hooks watch one window; every click is
  resolved *at the moment it happens* to a UIA selector (`aid`, else `name`, and only after the
  element is confirmed to still contain the click point), falling back to a 68×68 image anchor
  cropped around the point, and to raw coordinates only when that crop is too flat to match on.
  Typing becomes `type` steps, chords `key` steps, the wheel `move`+`scroll`, drags `from`/`to`,
  so the result is an ordinary macro: parameterise it, replay it, or give it a `launch`. A click
  that misses the watched window (it moved, or you hit what is behind it) is **dropped and
  counted** (`dropped=`), and keys typed elsewhere are ignored, so a demonstration never records
  the rest of your desktop. Measured: 4 raw coordinate clicks (7 + 3 =) came back as
  `num7Button` / `plusButton` / `num3Button` / `equalButton` from a 226 ms demonstration, and
  replayed in 0.30 s with the display reading 「显示为 10」.
- `actor.py` op `launch` — start a program and, if asked, block until its window exists
  (`wait: "<substring>"` for a window name, `wait_pid: true` for any window of the process).
  `.exe` goes through `subprocess`, `shell:appsFolder\<AUMID>` (UWP) through `explorer.exe`,
  anything else (`.lnk`, `.bat`, document) through `os.startfile`. It is **idempotent**: a
  window matching `wait` that is already open is reused, so replaying a skill never starts a
  second instance (`force: true` does). A `run` whose steps contain a `launch` records that
  spec into the macro, and `macro run` starts the app itself when the window is missing — a
  recorded skill is now one call away from a cold desktop. The window a `launch` step brings
  up also becomes the UIA search scope for the steps below it (full-desktop scan 1500 ms →
  20 ms, click 520 ms → 58 ms). Measured: Calculator started cold (2.6 s waiting for the
  window, reported as `start=` and not counted in `total_ms`) + the 8 steps in 218 ms, read
  back 「显示为 42」; with the app already open the launch is skipped (`start=reused`).
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

- `run` can bring each step's own reply back: `{"op":"run","results":true,"steps":[…]}` folds the
  op's reply into that step's trace entry as `data` — strings clipped at 400 characters by default,
  a longer list keeps six items and says how many it dropped, and `frame` / `png` never ride along.
  Passing a number instead of `true` sets the per-string budget. Without it the trace is exactly what
  it was. This closes the gap that made every reader op (`uia`, `state`, `probe`, `find`) cost a
  second call: in the measured 280 s GUI task, 10 of 32 tool calls were precisely that.

- **Record once, replay in one call** (`macro`): a `run` that finishes ok can keep itself —
  `"record": "<name>"` writes it to `$ACTOR_HOME\macros\<name>.json` (`overwrite: true` replaces an
  existing name, a failed run answers `saved: false` plus the step that broke it), and
  `{"op":"macro","what":"run","name":"<name>"}` replays it in **one** call, with the same per-step
  trace and `total_ms`. `macro list | get | del | save` manage the set; a daemon keeps its last 8 runs
  addressable, so `save from: last|<run id>` can also turn a run that has already happened into a macro.
  Steps are templates: `{{name}}` reads the macro's `args` (defaults stored in the macro, overridable
  per replay) or any earlier step's reply captured with `as: "x"` — `{{disp.hits.0.name}}`. A string
  that is exactly one placeholder returns the value itself, so a captured rect can serve as a target;
  `"literal": true` leaves a step untouched, `dry: true` expands without executing.
  Measured on Calculator, 7 steps (raise window → click the display → Esc → type → Enter → read the
  display back → unpin): record 1.8 s, replay **0.30 s** wall (0.20 s server + 0.10 s client start),
  three replays returning 56 / 579 / 81 — one call each instead of seven. Both `run` and `macro run`
  also report `front_resolve_ms`: the front window is resolved *before* step 0, and with a cold cache
  that lookup alone measured **1361 ms** (1–3 ms warm), so the first replay after the daemon or the
  target app starts costs about a second more than the ones after it. `act.cmd` prints it as `front=`.

### Fixed

- `CONTRIBUTING.md` said the static job runs "all five stages" and the `ci-static.ps1` docstring
  listed five, while the script had been running six — the `skills` stage was in neither list.
  Both now say seven, which is what the script does, and stage 7 keeps the number honest.

- `act.py` accepts the spelling the README always used: a bare existing `<file>.json` as the first
  argument is read as the request, exactly like `run <file>`. Until now `act.cmd skills\x.json` sent
  `{"op": "D:\\…\\x.json"}` to the actor and came back `unknown op '…'` — one wasted round trip,
  and it hit anyone who followed the manual literally.
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

- `README.zh-CN.md` is back in step with the English README, which stays the authority. The latency
  table now carries the same numbers the English one does — windows **48 ms**, colour blobs
  **102 ms**, template match **83 ms**, click **33–46 ms** with the old easing path named at 120 ms,
  whole skill **0.62–0.72 s** warm — instead of the superseded 41 / 110 / 91 / ~120 ms / 0.62 s, and
  the three rows that were missing are translated: macro replay **0.30 s** in one call, replay with a
  `launch` first step **2.6 s + 0.22 s** from a cold desktop, and `capture` turning **4 raw clicks
  into 4 UIA selectors** (demo 0.23 s, replayed in 0.30 s). The two narrative paragraphs behind those
  rows are translated as well: what `"record"` writes and how `{{arg}}` / `as:` turn a recorded run
  into a template, and the `capture start` … `capture stop` demonstration path. A section-by-section
  diff of the two files put the entire gap in this one section — the other seven are already in sync
  — and confirmed that the English numbers are the measured ones (`actor/README.md`, plus the 1.3.0
  entry that replaced 41 / 110 / 91 / ~120), so the Chinese table was the stale side, not the English
  one. One deliberate difference stays: the Chinese tesseract row quotes the text it read
  (「本月预算 ¥2.1346 / ¥100 余额 ¥107.17」) where the English row only says "reads it with no
  preprocessing". Prototype check after this change: **no number appears in the English README that
  the Chinese one lacks**.

- The front door now answers its three questions before the reader scrolls. **Why**: a new
  `## Why this exists` / `## 为什么有这个仓库` section states the actual problem (the loop, not
  recognition), who needs it and the prerequisite (an interactive Windows desktop, not a headless
  session). **Where to start**: an explicit `Start here: actor/` line, and Generation 1 is now
  titled `## Generation 1: seeing the screen (reference)` — the older generations read as
  reference, not as alternatives. **How to add one**: the Skills section gained a recipe — create
  `skills/<name>/SKILL.md`, run `install-skills.ps1`, then `check-skill-ops.py`; the mirror *is*
  the install, and stage 6 of `tools/ci-static.ps1` already fails a skill that names an op the
  actor does not have.
- `README.zh-CN.md` was the last place still disagreeing with the English text: it presented this
  machine's HOME (`D:\DSH\dsh-actor`) as the project's, and said **seven** pitfalls where
  `actor/README.md` lists **eleven**. Both fixed; the HOME now reads the way the English text
  reads it (`-Where` prints it, `-HomePath` / `ACTOR_HOME` move it, nothing generated lands in the
  repo). Honest boundary: the Chinese performance table still carries three numbers from an older
  measurement (**41 / 110 / 91 ms** against the English **48 / 102 / 83 ms**, and the click row as
  the pre-change easing path **~120 ms** instead of **33–46 ms**), and the macro / `capture`
  narrative plus the last three replay rows of that table have no Chinese text yet — that is a
  translation pass, listed rather than silently rewritten.
- `docs/视觉能力实测报告.md` is aligned with `docs/vision-capability-report.md`: both now carry
  pitfall 4 (**a VLM's box is not a measurement**) and the "what each question should use" table,
  and their section headings match one to one. No number changed — the two results tables were
  already identical, which is exactly what made the missing prose stand out.

- Two lookups that used to cost ~1.2–1.5 s *per step* are now ~0, which is what makes a replay fast
  rather than merely automatic. A window resolved from `title_contains` is remembered and re-validated
  with two Win32 calls instead of a UIA walk of the whole desktop (**1371 ms → 3 ms**; dropped when the
  window dies or is closed through the actor, and an empty Win32 title — Flutter, Electron, UWP shells —
  does not invalidate an entry that UIA resolved). A UIA selector searches the run's front window first
  (**95 ms**) and falls back to the whole desktop (**1500 ms**) when it finds nothing, so a selector
  recorded without an `hwnd` stays portable *and* fast. `run` / `macro run` resolve their front window
  once and hand it to every step as `scope_hwnd`; `click` / `move` / `drag` targets, `uia` and `find`
  honour it too. The same 7 steps: record 10.3 s → 4.6 s → **1.8 s**, replay 2.8 s (window cache alone)
  → **0.30 s**.
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
