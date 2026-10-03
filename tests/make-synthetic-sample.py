#!/usr/bin/env python3
"""Write a synthetic screenshot with a known box.

tests/score-pipeline.ps1 needs an image plus its ground-truth box.  On CI there is no
screen to grab and no sample screenshot in the repo (docs/images is ignored on purpose),
so this script draws one instead: anywhere the pipeline can be re-scored, byte for byte.

Layout, 600x400 on white:

  - black 3 px frame  (80, 60, 520, 380)   four border runs longer than 250 px, which is what
                                           the run-length detector in tools/cv_ui_geometry.py
                                           is looking for; it reports the inner edge of the stroke
  - solid blue block  (200, 150, 340, 260) a blob for the morphology pass

Usage: python tests/make-synthetic-sample.py [out/ci-sample.png]
"""
import os
import sys

from PIL import Image, ImageDraw

FRAME = (80, 60, 520, 380)
BLOCK = (200, 150, 340, 260)
SIZE = (600, 400)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("out", "ci-sample.png")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    im = Image.new("RGB", SIZE, "white")
    d = ImageDraw.Draw(im)
    d.rectangle(FRAME, outline="black", width=3)
    d.rectangle(BLOCK, fill=(0, 120, 215))
    im.save(out)
    x0, y0, x1, y1 = FRAME
    print("{}: frame {}, block {}, expect {},{},{},{}".format(out, FRAME, BLOCK, x0, y0, x1 + 1, y1 + 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
