"""Deterministic UI geometry extraction from pixels only — no DOM, no vision model.

Two classical methods, chosen because the naive "deviation from modal background"
approach failed on this dark UI (it flagged 43.79% of the bottom-right quadrant as
foreground because text strokes are also non-background):

1. Long straight borders (`find_lines`)
   A panel border is a column (or row) whose neighbour-difference stays above the
   noise floor for a LONG CONSECUTIVE run. Text glyph edges are also above the
   noise floor but only for short runs, so run-length filtering separates real
   borders from glyphs — that is exactly what the earlier column-energy peak
   picking got wrong (it returned text columns x=73/844/921/974/1169).

2. Solid element blobs (`find_solid_blobs`)
   Median-background subtraction + binary morphology: erode first (removes thin
   text strokes and 1px borders), then dilate back. What survives is solid
   artwork, so the bounding box of the surviving mask is the element box.

Usage:
  python cv_ui_geometry.py <image.png> [--expect x0,y0,x1,y1] [--annotate out.png]
Prints a JSON report with the detected geometry and, when --expect is given, the
IoU of the best matching detection against that ground-truth box.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from PIL import Image, ImageDraw


def gray_of(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB")).astype(np.int16).mean(axis=2)


def longest_run(mask: np.ndarray) -> np.ndarray:
    """Longest run of True per column of a 2-D boolean array (returns 1-D, len = ncols)."""
    h, w = mask.shape
    best = np.zeros(w, dtype=np.int32)
    cur = np.zeros(w, dtype=np.int32)
    for y in range(h):
        row = mask[y]
        cur = np.where(row, cur + 1, 0)
        best = np.maximum(best, cur)
    return best


def run_span(mask: np.ndarray) -> tuple[int, int]:
    """First and last index of the longest run in a 1-D boolean array."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return (-1, -1)
    splits = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([0], splits + 1))
    ends = np.concatenate((splits, [idx.size - 1]))
    lengths = idx[ends] - idx[starts] + 1
    k = int(lengths.argmax())
    return int(idx[starts[k]]), int(idx[ends[k]])


def merge_adjacent(positions: list[int], gap: int = 3) -> list[list[int]]:
    out: list[list[int]] = []
    for p in sorted(positions):
        if out and p - out[-1][-1] <= gap:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def find_lines(g: np.ndarray, axis: str, min_run: int, noise: int = 6):
    """Detect long border lines.

    axis='v': scan columns (vertical lines, i.e. panel left/right edges)
    axis='h': scan rows    (horizontal lines, i.e. panel top/bottom edges)
    """
    if axis == "v":
        d = np.abs(np.diff(g, axis=1)) > noise      # (H, W-1), edge between x and x+1
        # Per boundary index x, the longest CONSECUTIVE run down the y axis:
        # longest_run() accumulates runs along axis 0 and returns one value per
        # COLUMN, so it must be applied to d itself (not d.T). Applying it to the
        # transpose measured horizontal runs instead and reported zero borders
        # even where a 1450-pixel-tall edge exists.
        runs = longest_run(d)
        cand = [int(x) for x in np.flatnonzero(runs >= min_run)]
        groups = merge_adjacent(cand)
        lines = []
        for grp in groups:
            mid = int(round(sum(grp) / len(grp)))
            col_mask = d[:, grp[0]] if grp[0] < d.shape[1] else d[:, -1]
            y0, y1 = run_span(col_mask)
            lines.append({"at": mid, "run": int(runs[grp[0]]), "span": [y0, y1]})
        return sorted(lines, key=lambda l: -l["run"])
    d = np.abs(np.diff(g, axis=0)) > noise           # (H-1, W)
    runs = longest_run(d.T)                          # per row index y, runs along x
    cand = [int(y) for y in np.flatnonzero(runs >= min_run)]
    groups = merge_adjacent(cand)
    lines = []
    for grp in groups:
        mid = int(round(sum(grp) / len(grp)))
        row_mask = d[grp[0], :] if grp[0] < d.shape[0] else d[-1, :]
        x0, x1 = run_span(row_mask)
        lines.append({"at": mid, "run": int(runs[grp[0]]), "span": [x0, x1]})
    return sorted(lines, key=lambda l: -l["run"])


def erode(mask: np.ndarray, iters: int = 1) -> np.ndarray:
    m = mask.copy()
    for _ in range(iters):
        e = m.copy()
        e[1:, :] &= m[:-1, :]
        e[:-1, :] &= m[1:, :]
        e[:, 1:] &= m[:, :-1]
        e[:, :-1] &= m[:, 1:]
        m = e
    return m


def dilate(mask: np.ndarray, iters: int = 1) -> np.ndarray:
    m = mask.copy()
    for _ in range(iters):
        d = m.copy()
        d[1:, :] |= m[:-1, :]
        d[:-1, :] |= m[1:, :]
        d[:, 1:] |= m[:, :-1]
        d[:, :-1] |= m[:, 1:]
        m = d
    return m


def find_solid_blobs(rgb: np.ndarray, roi_frac=0.55, thresh=45, erode_iters=2,
                     min_support=6, top=4):
    """Solid (non-text) element boxes, from median-background subtraction + morphology."""
    h, w, _ = rgb.shape
    y0r, x0r = int(h * roi_frac), int(w * roi_frac)
    roi = rgb[y0r:, x0r:]
    bg = np.median(roi.reshape(-1, 3), axis=0)
    dist = np.abs(roi - bg).sum(axis=2) > thresh
    mask = dilate(erode(dist, erode_iters), erode_iters)   # opening: kill thin strokes
    rows = np.flatnonzero(mask.sum(axis=1) >= min_support)
    cols = np.flatnonzero(mask.sum(axis=0) >= min_support)
    blobs = []
    if rows.size and cols.size:
        blobs.append({
            "box": [int(cols.min() + x0r), int(rows.min() + y0r),
                    int(cols.max() + x0r), int(rows.max() + y0r)],
            "pixels": int(mask.sum()),
            "bg": [int(v) for v in bg],
        })
    # also report per-half boxes so a small element is not swallowed by a big one
    for name, sub, oy, ox in (("br_quarter", mask[int(mask.shape[0] * 0.5):, int(mask.shape[1] * 0.5):],
                               y0r + int(mask.shape[0] * 0.5), x0r + int(mask.shape[1] * 0.5)),):
        rows = np.flatnonzero(sub.sum(axis=1) >= min_support)
        cols = np.flatnonzero(sub.sum(axis=0) >= min_support)
        if rows.size and cols.size:
            blobs.append({"box": [int(cols.min() + ox), int(rows.min() + oy),
                                  int(cols.max() + ox), int(rows.max() + oy)],
                          "pixels": int(sub.sum()), "roi": name})
    return blobs[:top]


def find_colorful_blobs(rgb: np.ndarray, roi_frac=0.5, sat_min=18, erode_iters=2,
                        min_support=6):
    """Saturated (colorful) element boxes: the only colorful thing in a dark gray UI
    is real artwork, so a saturation mask isolates the widget where a brightness
    mask cannot (panel backgrounds and text are all desaturated gray)."""
    h, w, _ = rgb.shape
    y0r, x0r = int(h * roi_frac), int(w * roi_frac)
    roi = rgb[y0r:, x0r:]
    sat = roi.max(axis=2) - roi.min(axis=2)
    mask = dilate(erode(sat > sat_min, erode_iters), erode_iters)
    rows = np.flatnonzero(mask.sum(axis=1) >= min_support)
    cols = np.flatnonzero(mask.sum(axis=0) >= min_support)
    if not rows.size or not cols.size:
        return []
    return [{
        "box": [int(cols.min() + x0r), int(rows.min() + y0r),
                int(cols.max() + x0r), int(rows.max() + y0r)],
        "pixels": int(mask.sum()),
        "colorful_frac": round(float((sat > sat_min).mean()), 4),
    }]


def iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return round(inter / ua, 3) if ua > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--expect", default="", help="x0,y0,x1,y1 ground-truth box")
    ap.add_argument("--annotate", default="")
    ap.add_argument("--min-run", type=int, default=250)
    ap.add_argument("--noise", type=int, default=3)
    ap.add_argument("--expect2", default="", help="second ground-truth box")
    args = ap.parse_args()

    img = Image.open(args.image).convert("RGB")
    rgb = np.asarray(img).astype(np.int16)
    g = gray_of(img)
    h, w = g.shape

    vertical = find_lines(g, "v", args.min_run, args.noise)
    horizontal = find_lines(g, "h", args.min_run, args.noise)
    blobs = find_solid_blobs(rgb)
    colorful = find_colorful_blobs(rgb)

    report = {
        "image": [w, h],
        "params": {"noise": args.noise, "min_run": args.min_run},
        "vertical_borders": vertical[:8],
        "horizontal_borders": horizontal[:6],
        "solid_blobs": blobs,
        "colorful_blobs": colorful,
    }
    if args.expect:
        exp = [int(v) for v in args.expect.split(",")]
        report["expect_root"] = exp
        report["iou_root"] = {str(b["box"]): iou(b["box"], exp) for b in (blobs + colorful)}
    if args.expect2:
        exp2 = [int(v) for v in args.expect2.split(",")]
        report["expect_img"] = exp2
        report["iou_img"] = {str(b["box"]): iou(b["box"], exp2) for b in (blobs + colorful)}

    if args.annotate:
        ann = img.copy()
        d = ImageDraw.Draw(ann)
        for l in vertical[:8]:
            d.line([(l["at"], l["span"][0]), (l["at"], l["span"][1])], fill=(0, 220, 120), width=3)
        for l in horizontal[:6]:
            d.line([(l["span"][0], l["at"]), (l["span"][1], l["at"])], fill=(90, 160, 255), width=3)
        for b in blobs:
            d.rectangle(b["box"], outline=(255, 40, 40), width=5)
        for b in colorful:
            d.rectangle(b["box"], outline=(0, 255, 255), width=5)
        if args.expect:
            d.rectangle([int(v) for v in args.expect.split(",")], outline=(255, 215, 0), width=3)
        ann.save(args.annotate)
        report["annotated"] = args.annotate

    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
