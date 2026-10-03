"""OCR with bounding boxes, so a UI element can be located by its own label.

Wraps the tesseract CLI (chi_sim+eng) and returns line-level boxes in the
ORIGINAL image coordinates, which is what a click needs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image

TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def run_ocr(path: str, psm: int, lang: str):
    out = os.path.join(tempfile.gettempdir(), "dsh_ocr_boxes")
    subprocess.run([TESS, path, out, "-l", lang, "--psm", str(psm), "tsv"],
                   check=True, capture_output=True)
    rows = []
    with open(out + ".tsv", "r", encoding="utf-8") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        idx = {name: i for i, name in enumerate(header)}
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < len(header):
                continue
            try:
                conf = float(parts[idx["conf"]])
            except ValueError:
                continue
            text = parts[idx["text"]]
            if conf < 0 or not text.strip():
                continue
            rows.append({
                "level": int(parts[idx["level"]]),
                "key": (int(parts[idx["block_num"]]), int(parts[idx["par_num"]]), int(parts[idx["line_num"]])),
                "conf": conf,
                "text": text,
                "x": int(parts[idx["left"]]), "y": int(parts[idx["top"]]),
                "w": int(parts[idx["width"]]), "h": int(parts[idx["height"]]),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--crop", default="", help="x,y,w,h in original pixels")
    ap.add_argument("--psm", type=int, default=6)
    ap.add_argument("--lang", default="chi_sim+eng")
    ap.add_argument("--minconf", type=float, default=45)
    ap.add_argument("--grep", default="")
    ap.add_argument("--scale", type=float, default=1.0, help="upscale before OCR (small UI text)")
    ap.add_argument("--invert", action="store_true", help="light-on-dark text: invert before OCR")
    ap.add_argument("--contrast", action="store_true", help="autocontrast before OCR")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    src = args.image
    ox = oy = 0
    tmp = None
    if args.crop or args.scale != 1.0 or args.invert or args.contrast:
        img: Image.Image = Image.open(src)
        if args.crop:
            ox, oy, cw, ch = [int(v) for v in args.crop.split(",")]
            img = img.crop((ox, oy, ox + cw, oy + ch))
        if args.scale != 1.0:
            img = img.resize((int(img.width * args.scale), int(img.height * args.scale)), Image.Resampling.LANCZOS)
        if args.contrast or args.invert:
            from PIL import ImageOps
            img = ImageOps.autocontrast(img.convert("RGB"))
            if args.invert:
                img = ImageOps.invert(img)
        tmp = os.path.join(tempfile.gettempdir(), "dsh_ocr_crop.png")
        img.save(tmp)
        src = tmp

    rows = run_ocr(src, args.psm, args.lang)
    lines: dict = {}
    for r in rows:
        if r["level"] != 5:
            continue
        cur = lines.setdefault(r["key"], {"words": [], "conf": [], "texts": []})
        cur["words"].append(r)
        cur["conf"].append(r["conf"])
        cur["texts"].append(r["text"])

    out = []
    for cur in lines.values():
        xs = [w["x"] for w in cur["words"]]; ys = [w["y"] for w in cur["words"]]
        x2 = [w["x"] + w["w"] for w in cur["words"]]; y2 = [w["y"] + w["h"] for w in cur["words"]]
        text = "".join(cur["texts"])
        conf = sum(cur["conf"]) / len(cur["conf"])
        if conf < args.minconf:
            continue
        if args.grep and args.grep not in text:
            continue
        out.append({
            "text": text, "conf": round(conf, 1),
            "box": [int(min(xs) / args.scale) + ox, int(min(ys) / args.scale) + oy,
                    int(max(x2) / args.scale) + ox, int(max(y2) / args.scale) + oy],
            "center": [int((min(xs) + max(x2)) / 2 / args.scale) + ox,
                       int((min(ys) + max(y2)) / 2 / args.scale) + oy],
        })
    out.sort(key=lambda r: (r["box"][1], r["box"][0]))

    if args.json:
        print(json.dumps({"image": args.image, "crop": args.crop, "lines": out}, ensure_ascii=False))
    else:
        for r in out:
            print(f"{r['box'][0]:>5},{r['box'][1]:>5} {r['box'][2]-r['box'][0]:>4}x{r['box'][3]-r['box'][1]:<4} conf={r['conf']:>5}  {r['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
