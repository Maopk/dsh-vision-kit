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
3. **Model output is never used as a measurement.** VLMs may propose a region;
   they may not be the source of a coordinate that something depends on.
4. **No screenshots of the local UI in commits.** They carry session titles,
   balances, paths and whatever else was on screen. Commit the measured numbers
   and the command that produced them, not the picture.
5. **Keep the plugin's security posture.** `plugins/dsh-selflook-local` serves an
   RPC route on the DSH web server; it must keep its same-origin check
   (`isCrossSite`) and must not accept a caller-supplied output path outside the
   configured output directory.

## Running the checks

```powershell
# zero-model detectors against a ground-truth box (needs python + numpy + Pillow)
pwsh -File tests/score-pipeline.ps1 -Image .\docs\images\sample-screenshot.png `
  -Expect 2337,1305,2561,1529 `
  -Template "$env:LOCALAPPDATA\..\.dsh\profiles\desktop\node_modules\dsh-whale-widget\assets\DSniang1.png"
```

## PR checklist

- [ ] `tests/score-pipeline.ps1` runs and its output is pasted in the PR.
- [ ] Docs updated when behaviour or measured numbers change.
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]` or the new version.
- [ ] No secrets, tokens, machine-specific credentials, or personal paths in the diff.
