"""Coarse brightness map of a screenshot region: shows UI rects as ASCII shading."""
from __future__ import annotations

import argparse
import sys

import numpy as np
from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--crop", required=True, help="x,y,w,h")
    ap.add_argument("--cell", type=int, default=10)
    ap.add_argument("--cols", type=int, default=100, help="max columns to print")
    args = ap.parse_args()

    ox, oy, w, h = [int(v) for v in args.crop.split(",")]
    img = Image.open(args.image).convert("L").crop((ox, oy, ox + w, oy + h))
    a = np.asarray(img).astype(np.float32)
    c = args.cell
    hs, ws = a.shape[0] // c, a.shape[1] // c
    blocks = a[: hs * c, : ws * c].reshape(hs, c, ws, c).mean(axis=(1, 3))
    ramp = " .:-=+*#%@"
    lo, hi = float(blocks.min()), float(blocks.max())
    step = max(1, ws // args.cols)
    print(f"crop=({ox},{oy}) size={w}x{h} cell={c}  brightness {lo:.0f}..{hi:.0f}")
    print("    " + "".join(f"{(ox + i * c) // 100 % 10}" for i in range(0, ws, step)))
    for y in range(hs):
        row = "".join(ramp[min(9, int((blocks[y, x] - lo) / max(1e-6, hi - lo) * 9.999))] for x in range(0, ws, step))
        print(f"{oy + y * c:>4}{row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
