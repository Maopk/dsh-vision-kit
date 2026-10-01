# "What do I do without the DOM?" — measured vision capability on this machine

Measured 2026-10-01 · i7-14650HX / RTX 5060 Laptop 8 GB / DSH 0.2.0-rc.2 / Windows 11
Test image: a local 2560×1600 native capture from the plugin (`self-look-*.png`), dpr 1.5
Ground truth: DOM `getBoundingClientRect()` × 1.5

> No UI screenshots are published here — they would expose session contents. Every
> claim below carries its numbers instead, and the reproduction commands let you
> get the same values on your own machine.

## Results, by trustworthiness

| # | Method | Tool | Target | Measured | Trust |
|---|---|---|---|---|---|
| 1 | **DOM ground truth** | in-page `getBoundingClientRect` | any element | widget root `1457,769,250,250` CSS = `2186,1154,375,375` physical | exact (baseline) |
| 2 | **Template matching** (no model) | `tools/template_match.py`, FFT NCC | widget art (610×610 asset at scale 0.3656) | box `2337,1306→2560,1529`, NCC **0.955**, **IoU 0.991** | **pixel-level, usable as a ruler** |
| 3 | **Long-border detection** (no model) | `tools/cv_ui_geometry.py`, run-length edges | sidebar right edge | `x=419` vs truth 420 (**1 px**), run 1450 px | pixel-level |
| 3b | same | same | horizontal borders | `y=59` (truth 60), `1528 / 1222 / 1336 / 1484 / 172` — 6 lines | 1–2 px |
| 4 | **OCR** | tesseract `-l chi_sim+eng --psm 6` | sidebar budget text | reads "今日 / 本月预算 ¥2.1346 / ¥100 余额 ¥107.17 法定节假日…160 小时 43 分后进入高峰" | text-level, no preprocessing |
| 5 | Semantic blob / color heuristics | hand-written numpy | widget box | IoU 0.19–0.45 (code highlighting and colorful chrome interfere) | qualitative only |
| 6 | **Local VLM grounding** | `granite3.2-vision:2b` | widget box | answer `[0.81,0.78,0.9,0.82]` (direction right, box wrong), **IoU 0.000–0.004** | **not usable for measurement** |
| 7 | same | `qwen2.5vl:3b` | widget box | `[1059,582,1148,693]`, **IoU 0.000** | not usable for measurement |

## In one line

- **Geometry**: pixel methods (template matching, long-edge detection) reach **1 px** and need no model — but they must be **scored against truth**, or a wrong implementation looks exactly like a right one.
- **Text**: tesseract works on this dark UI with `--psm 6`; Chinese and currency amounts come out clean.
- **Semantics**: a local VLM that fits in 8 GB (2b/3b) can only say "roughly bottom-right". For a real box, use a template, the DOM, or a purpose-built grounding model.

## Reproduction

```powershell
$py   = "python"                                              # numpy + Pillow
$shot = (Get-Content "$env:USERPROFILE\.dsh-look-last.txt")   # latest native capture
$truth = "2337,1305,2561,1529"                                # your element's DOM rect × dpr

# template matching (IoU 0.991)
& $py tools\template_match.py $shot <path-to-610x610-asset> `
  --scales 0.34,0.3656,0.40 --expect 2337,1305,2561,1529 --annotate .\template-annotated.png

# border lines (1 px off)
& $py tools\cv_ui_geometry.py $shot --noise 3 --min-run 250 --annotate .\cv-annotated.png

# score a model's box
& $py tools\ground_test.py --image $shot --model granite3.2-vision:2b `
  --prompt "What is the bounding box of the whale?" --expect 2337,1305,2561,1529
```

## Pitfalls (the useful part)

1. **The first "proper" pipeline returned a confident lie.** `longest_run()`
   accumulates runs along axis 0 and returns one value per column; passing it
   `d.T` measured horizontal runs instead, and the tool reported "0 border lines
   in the whole image". Fixed, it found a 1450 px run at `x=419` — **1 px** from
   DOM truth. Without scoring against truth, that bug was invisible.
2. **VLMs do not agree on a coordinate system.** granite's docs say 0–1000
   normalized output; it actually emitted 0–1 fractions. qwen emitted pixels of
   the image it received. `ground_test.py` now computes all six interpretations
   (fractions/normalized_0_1000/pixels × of_sent/of_original) and takes the best,
   so a parsing mistake cannot masquerade as "the model is wrong".
3. **This dark UI has almost no contrast.** Panel is 27/255, background 21/255 —
   a single-pixel step of 5.67. Any "high-contrast edge" assumption fails; the
   threshold has to go to ≤3 and rely on run length to reject text.
4. **A VLM's box is not a measurement.** It is a guess with a plausible-looking
   format. Score it before you depend on it.

## What each question should use

| Question | Use |
|---|---|
| "where exactly is element X" and a template/asset exists | template matching |
| "where exactly is element X", no asset, element is flat-colored | connected component + morphology, verified against a known landmark |
| "where are the panels / how is it laid out" | run-length border lines |
| "what does this text say" | tesseract (`--psm 6` for full-width crops) |
| "what is this thing / what's on screen" | local VLM, qualitative answer only |
| anything that must not be wrong | the DOM, if the page is yours to query |
