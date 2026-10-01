"""Verdict for qq-send.ps1 — no OCR, pure pixels.

dry  : the draft must be visible in the input strip.
send : the input strip must change (draft gone) AND new QQ-blue bubble pixels
       must appear in the message band.
"""
import argparse
import numpy as np
from PIL import Image

INPUT = (640, 1352, 1260, 46)       # x, y, w, h  — the composer's text line only
BAND = (620, 930, 1940, 430)        # message area just above the composer


def load(p, box):
    x, y, w, h = box
    return np.asarray(Image.open(p).convert('RGB'))[y:y + h, x:x + w].astype(np.int16)


def ink(a):
    """Light glyph pixels on the dark composer background."""
    return int((a.max(axis=2) > 140).sum())


def blue(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return int(((b > 110) & (b - r > 50) & (g > 40) & (g < b)).sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pre', required=True)
    ap.add_argument('--post', required=True)
    ap.add_argument('--mode', choices=['dry', 'send'], required=True)
    ap.add_argument('--cleared')
    ap.add_argument('--draft')
    a = ap.parse_args()

    i_pre, i_post = ink(load(a.pre, INPUT)), ink(load(a.post, INPUT))
    pre_b, post_b = blue(load(a.pre, BAND)), blue(load(a.post, BAND))
    print(f'composer ink:      before={i_pre} after={i_post}')
    print(f'blue bubble px:    before={pre_b} after={post_b} (delta={post_b - pre_b})')

    if a.mode == 'dry':
        ok = i_post > i_pre + 200
        print(f'VERDICT={"PASS" if ok else "FAIL"} text is typed in the composer')
        if a.cleared:
            i_clr = ink(load(a.cleared, INPUT))
            back = abs(i_clr - i_pre) <= max(120, i_post // 4)
            print(f'composer ink after clear = {i_clr}  (empty baseline {i_pre})')
            print(f'CLEARED={"yes" if back else "no"}')
    else:
        i_draft = ink(load(a.draft, INPUT)) if a.draft else i_post
        typed = i_draft > i_pre + 200
        left = i_post < i_draft - 200
        bubbled = (post_b - pre_b) > 300
        print(f'composer ink:      draft={i_draft}')
        print(f'checks: typed={typed} left_the_box={left} new_bubble={bubbled}')
        print(f'VERDICT={"PASS" if (typed and left and bubbled) else "FAIL"}')


if __name__ == '__main__':
    main()
