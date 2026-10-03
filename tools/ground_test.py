"""Head-to-head grounding benchmark: local vision models vs ground truth, pixel-only CV.

Sends a screenshot to a local Ollama vision model and asks for the bounding box of
a named UI element, then scores the answer with IoU against the DOM-derived truth.

Coordinate handling: Granite Vision answers in a 0-1000 normalized space, other
models answer in the pixel space of whatever image they were given, and the image
is downscaled before sending. All three cases are normalized back to the ORIGINAL
image resolution before scoring, so the IoU is comparable across models.

Usage:
  python ground_test.py --image shot.png --model granite3.2-vision:2b \
      --prompt "What is the bounding box of the whale?" --expect x0,y0,x1,y1
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
import urllib.request

from PIL import Image

BOX_RE = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]")


def prepare(path: str, max_dim: int):
    img = Image.open(path).convert("RGB")
    w0, h0 = img.size
    scale = min(1.0, max_dim / max(w0, h0))
    if scale < 1.0:
        img = img.resize((int(round(w0 * scale)), int(round(h0 * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return img.size, base64.b64encode(buf.getvalue()).decode("ascii")


def ask(model: str, prompt: str, b64: str, endpoint: str, num_predict: int, timeout: int):
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        "options": {"num_predict": num_predict, "temperature": 0},
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response", ""), round(time.time() - t0, 2), data


def candidate_boxes(box, sent_size, orig_size, model: str):
    """Every plausible coordinate convention a model could have meant.

    Vision models are not consistent about this and a single wrong guess turns a
    decent answer into a fake IoU of 0: Granite Vision documents a 0-1000
    normalized space but frequently emits plain 0-1 fractions, while Qwen2.5-VL
    answers in pixels of whatever image it received (which may be the sent
    downscale or the original file). So build them all and score them all.
    """
    sw, sh = sent_size
    ow, oh = orig_size
    x0, y0, x1, y1 = box
    out = []
    if max(box) <= 1.5:
        out.append(("fractions_of_sent", [x0 * sw, y0 * sh, x1 * sw, y1 * sh]))
        out.append(("fractions_of_original", [x0 * ow, y0 * oh, x1 * ow, y1 * oh]))
    if max(box) <= 1000.5:
        out.append(("normalized_0_1000_of_sent", [x0 / 1000 * sw, y0 / 1000 * sh,
                                                  x1 / 1000 * sw, y1 / 1000 * sh]))
        out.append(("normalized_0_1000_of_original", [x0 / 1000 * ow, y0 / 1000 * oh,
                                                      x1 / 1000 * ow, y1 / 1000 * oh]))
    out.append(("pixels_of_sent", [x0, y0, x1, y1]))
    out.append(("pixels_of_original", [x0, y0, x1, y1]))
    return [(name, [int(round(a)), int(round(b)), int(round(c)), int(round(d))])
            for name, (a, b, c, d) in out]


def iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return round(inter / ua, 3) if ua > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--expect", default="")
    ap.add_argument("--endpoint", default="http://127.0.0.1:11434/api/generate")
    ap.add_argument("--max-dim", type=int, default=1024)
    ap.add_argument("--num-predict", type=int, default=128)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    orig = Image.open(args.image).size
    sent, b64 = prepare(args.image, args.max_dim)
    answer, elapsed, raw = ask(args.model, args.prompt, b64, args.endpoint,
                               args.num_predict, args.timeout)

    report = {
        "model": args.model,
        "prompt": args.prompt,
        "image": list(orig),
        "sent_size": list(sent),
        "elapsed_s": elapsed,
        "eval_count": raw.get("eval_count"),
        "prompt_eval_count": raw.get("prompt_eval_count"),
        "answer": answer.strip()[:400],
    }
    m = BOX_RE.search(answer)
    if not m:
        report["parsed"] = None
        report["iou"] = None
    else:
        cands = candidate_boxes([float(v) for v in m.groups()], sent, orig, args.model)
        report["raw_numbers"] = [float(v) for v in m.groups()]
        report["interpretations"] = {name: box for name, box in cands}
        if args.expect:
            exp = [int(v) for v in args.expect.split(",")]
            report["expect"] = exp
            scored = {name: iou(box, exp) for name, box in cands}
            report["iou_by_interpretation"] = scored
            best = max(scored, key=lambda k: scored[k])
            report["best_interpretation"] = best
            report["best_box"] = dict(cands)[best]
            report["iou"] = scored[best]
        else:
            report["parsed"] = dict(cands)["pixels_of_original"]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
