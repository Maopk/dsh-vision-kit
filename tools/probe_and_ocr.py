"""Two diagnostics that explain why unsupervised pixel methods struggle on this UI.

1. `probe_boundary` — is there any pixel edge where the DOM says the sidebar ends?
   The DOM reports the sidebar column as x 0..280 CSS (0..420 physical at dpr 1.5).
   If the pixel row crossing x=420 shows no step, then no edge/line detector can
   ever find that border: the DOM knows an edge exists where the pixels do not.

2. `make_ocr_crops` — prepare text crops in three variants (raw / inverted /
   inverted+2x upscale+threshold) so tesseract can be scored on each: dark-theme
   screenshots are the classic case where raw OCR fails and inversion fixes it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

SHOT = Path(r"C:\Users\28794\Downloads\dsh\图片\self-look-2026-10-01T08-17-52-065Z.png")
OUT = Path(r"C:\Users\28794\dsh-vision-work")


def probe_boundary():
    img = np.asarray(Image.open(SHOT).convert("RGB")).astype(np.int16)
    results = {}
    for y in (300, 800, 1200, 1400):
        row = img[y, 400:445]
        g = row.mean(axis=1)
        d = np.abs(np.diff(g))
        # strongest step and where
        k = int(d.argmax())
        results[f"y{y}"] = {
            "x_of_step": 400 + k,
            "step_luma": round(float(d[k]), 2),
            "mean_left": round(float(g[:k + 1].mean()), 2),
            "mean_right": round(float(g[k + 1:].mean()), 2),
            "row_min": round(float(g.min()), 2),
            "row_max": round(float(g.max()), 2),
        }
    # is there ANY long vertical edge anywhere in the image?
    gray = img.mean(axis=2)
    dcols = np.abs(np.diff(gray, axis=1))
    longest = []
    for x in range(1, dcols.shape[1], 37):          # sampled to stay quick
        col = dcols[:, x] > 6
        best = cur = 0
        for v in col:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        longest.append((x, best))
    longest.sort(key=lambda t: -t[1])
    return {"crossings": results, "strongest_long_edges_noise6": longest[:5]}


def make_ocr_crops():
    img = Image.open(SHOT).convert("RGB")
    W, H = img.size
    crops = {
        # chat transcript area: large light text on near-black
        "chat": (430, 700, 1900, 1060),
        # sidebar bottom: budget numbers, small text
        "budget": (18, 1224, 402, 1340),
    }
    made = []
    for name, box in crops.items():
        c = img.crop(box)
        raw = OUT / f"ocr-{name}-raw.png"
        inv = OUT / f"ocr-{name}-inv.png"
        inv2 = OUT / f"ocr-{name}-inv2x.png"
        c.save(raw)
        gt = ImageOps.grayscale(c)
        inverted = ImageOps.invert(gt)
        inverted.save(inv)
        big = inverted.resize((inverted.width * 2, inverted.height * 2), Image.LANCZOS)
        arr = np.asarray(big).astype(np.int16)
        # Otsu threshold on the (now bright-text) image
        hist, _ = np.histogram(arr, bins=256, range=(0, 256))
        total = arr.size
        sum_all = float((np.arange(256) * hist).sum())
        sum_b = 0.0
        w_b = 0.0
        best_t, best_var = 0, -1.0
        for t in range(256):
            w_b += hist[t]
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += t * hist[t]
            m_b = sum_b / w_b
            m_f = (sum_all - sum_b) / w_f
            var = w_b * w_f * (m_b - m_f) ** 2
            if var > best_var:
                best_var, best_t = var, t
        binary = ((arr > best_t) * 255).astype(np.uint8)
        Image.fromarray(binary).save(inv2)
        made.append({"name": name, "box": list(box), "otsu": int(best_t),
                     "raw": str(raw), "inv": str(inv), "inv2x": str(inv2)})
    return made


if __name__ == "__main__":
    print(json.dumps({"boundary_probe": probe_boundary(), "ocr_crops": make_ocr_crops()},
                     ensure_ascii=False, indent=1))
