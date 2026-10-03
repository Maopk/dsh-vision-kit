# Contributing / 贡献指南

Thanks for helping. This repo is small, so the rules are short.

## Ground rules

1. **Every geometry claim needs a number.** If a tool or prompt is supposed to
   find something, it must be scored against ground truth (DOM rect, template,
   or a hand-verified box) and the IoU/Δpx recorded in the PR description.
   "It looked right" is not evidence — the first border detector in this repo
   returned a confident, entirely wrong answer until it was scored.
2. **No absolute paths in committed scripts.** Tools take paths as arguments.
   Docs may show a concrete example, but scripts must work from any checkout.
   Actor state (logs, `port.txt`, deps) belongs to the actor HOME, never to the
   checkout: `$ACTOR_HOME` → `actor/home.txt` → code dir, in that order.
3. **Model output is never used as a measurement.** VLMs may propose a region;
   they may not be the source of a coordinate that something depends on.
4. **No screenshots of the local UI in commits.** They carry session titles,
   balances, paths and whatever else was on screen. Commit the measured numbers
   and the command that produced them, not the picture. `docs/images/`,
   `sample-screenshot.png` and `*-annotated.png` are ignored on purpose.
5. **Keep the plugin's security posture.** `plugins/dsh-selflook-local` serves an
   RPC route on the DSH web server; it must keep its same-origin check
   (`isCrossSite`) and must not accept a caller-supplied output path outside the
   configured output directory.
6. **In `actor/`, keep the isolation guarantees.** All UI Automation COM objects
   stay on the single long-lived STA worker with a per-call timeout (never touch
   them from another thread — that is how a hung target app used to take the
   actor down), and a skill must **raise + pin its target window** before sending
   input, then **poll the structure channel** instead of sleeping. A skill that
   skips this reads stale values.

## Running the checks

The detector pipeline needs any screenshot — the repo does not ship one (rule 4),
so take your own. With the actor running:

```powershell
# 1. a fresh screenshot (any source works: the plugin, the actor, Snipping Tool)
.\actor\act.cmd '{"op":"shot","path":"shot.png"}'

# 2. the zero-model detectors against a ground-truth box (python + numpy + Pillow)
pwsh -File tests/score-pipeline.ps1 -Image .\shot.png -Expect <x1,y1,x2,y2>
#    optional: -Template <path to the asset you are looking for>

# 3. the actor's own end-to-end test: dirty state in, two clean runs out
python actor\tests\dirty_calc.py
python actor\skills\demo_calc.py      # expect 7*8 -> 56, 12+30 -> 42, PASS

# 4. the same thing CI runs, with no screen involved: draw a synthetic screenshot with a known
#    box, then score the detectors against it (-Strict exits 1 when an edge is off)
python tests\make-synthetic-sample.py out\ci-sample.png
pwsh -File tests\score-pipeline.ps1 -Image out\ci-sample.png -Expect 80,60,521,381 -Strict
```

If you change anything under `actor/`, also paste the per-step timings from
`act.cmd '{"op":"run",...}'` (or `actor.ps1 -Bench`) in the PR — this repo's
whole argument is measured latency.

## PR checklist

- [ ] `tests/score-pipeline.ps1` runs and its output is pasted in the PR (CI runs the `-Strict`
      form against `tests/make-synthetic-sample.py`; both should pass locally too).
- [ ] For `actor/` changes: `python actor\tests\dirty_calc.py` then
      `python actor\skills\demo_calc.py` both PASS, and the timings are in the PR.
- [ ] Docs updated when behaviour or measured numbers change (the READMEs carry a
      status marker per path — the actor is the current one).
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]` or the new version.
- [ ] No secrets, tokens, machine-specific credentials, or personal paths in the diff.
