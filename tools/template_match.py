"""Pixel-only element localization by multi-scale normalized cross-correlation.

Superseded for full-screen work (2026-10-01) by the PC Actor's `find` op, which runs the
same ZNCC pyramid inside a warm process: ~91 ms for a full 2560x1600 frame against ~573 ms
for a cold start of this script. Kept as the standalone CLI for one-off measurements and
because tests/score-pipeline.ps1 scores it against ground truth.

Honest framing of why this method is used: the two unsupervised methods in
cv_ui_geometry.py both failed on this interface —
  * long-line detection found ZERO borders: DSH panels differ from the background
    by ~5/255 in luminance, so no column/row has a strong edge for 250+ pixels;
  * blob detection returned boxes 1.2-2x too large, because the chat area
    contains syntax-highlighted code and colored chrome, so "colorful" and
    "not background" both over-select.
Template matching is the method that does NOT guess: when the exact appearance of
the target is known (a widget asset, an icon, a button), NCC finds it at
sub-pixel-adjacent accuracy with no model and no heuristics.

NCC is computed with FFT so the search is exhaustive over the whole image at
every scale, instead of a sampled grid.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from PIL import Image


def to_planes(img: Image.Image, size=None) -> np.ndarray:
    if size is not None:
        img = img.resize(size, Image.LANCZOS)
    a = np.asarray(img.convert("RGB")).astype(np.float64)
    return a


def ncc_map(channel: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    """Zero-mean normalized cross-correlation, 'valid' mode, via FFT."""
    H, W = channel.shape
    h, w = tpl.shape
    t = tpl - tpl.mean()
    tn = np.sqrt((t * t).sum())
    if tn == 0:
        return np.zeros((H - h + 1, W - w + 1))

    F = np.fft.rfft2(channel)
    T = np.fft.rfft2(t[::-1, ::-1], s=channel.shape)
    corr = np.fft.irfft2(F * T, s=channel.shape)[h - 1:, w - 1:]

    ones = np.ones((h, w))
    To = np.fft.rfft2(ones[::-1, ::-1], s=channel.shape)
    s1 = np.fft.irfft2(F * To, s=channel.shape)[h - 1:, w - 1:]
    F2 = np.fft.rfft2(channel * channel)
    s2 = np.fft.irfft2(F2 * To, s=channel.shape)[h - 1:, w - 1:]

    n = h * w
    mean = s1 / n
    var = np.clip(s2 / n - mean * mean, 1e-9, None)
    return corr / (np.sqrt(var) * np.sqrt(n) * tn)


def match(img: np.ndarray, tpl: np.ndarray, scales, roi=None):
    if roi:
        x0, y0, x1, y1 = roi
        search = img[y0:y1, x0:x1]
        ox, oy = x0, y0
    else:
        search, ox, oy = img, 0, 0

    best = None
    th, tw = tpl.shape[:2]
    for s in scales:
        h, w = max(8, int(round(th * s))), max(8, int(round(tw * s)))
        if h >= search.shape[0] or w >= search.shape[1]:
            continue
        tpl_s = np.asarray(Image.fromarray(tpl.astype(np.uint8)).resize((w, h), Image.LANCZOS)).astype(np.float64)
        score = np.zeros((search.shape[0] - h + 1, search.shape[1] - w + 1))
        for c in range(3):
            score += ncc_map(search[:, :, c], tpl_s[:, :, c])
        score /= 3.0
        idx = int(np.argmax(score))
        yy, xx = np.unravel_index(idx, score.shape)
        v = float(score[yy, xx])
        if best is None or v > best["score"]:
            best = {
                "score": round(v, 4),
                "scale": round(float(s), 4),
                "box": [int(xx + ox), int(yy + oy), int(xx + ox + w), int(yy + oy + h)],
                "template_size": [w, h],
            }
    return best


def iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return round(inter / ua, 3) if ua > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("template")
    ap.add_argument("--expect", default="")
    ap.add_argument("--scales", default="0.30,0.34,0.3656,0.40,0.45")
    ap.add_argument("--roi", default="")
    ap.add_argument("--annotate", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    img = to_planes(Image.open(args.image))
    tpl = to_planes(Image.open(args.template))
    scales = [float(s) for s in args.scales.split(",") if s]
    roi = [int(v) for v in args.roi.split(",")] if args.roi else None

    best = match(img, tpl, scales, roi)
    report = {
        "image": list(img.shape[:2][::-1]),
        "template": list(tpl.shape[:2][::-1]),
        "scales_tried": scales,
        "best": best,
    }
    if best and args.expect:
        exp = [int(v) for v in args.expect.split(",")]
        report["expect"] = exp
        report["iou"] = iou(best["box"], exp)

    if best and args.annotate:
        ann = Image.open(args.image).convert("RGB")
        from PIL import ImageDraw
        d = ImageDraw.Draw(ann)
        d.rectangle(best["box"], outline=(0, 255, 255), width=5)
        if args.expect:
            d.rectangle([int(v) for v in args.expect.split(",")], outline=(255, 215, 0), width=3)
        ann.save(args.annotate)
        report["annotated"] = args.annotate

    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
