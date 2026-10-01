#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test helper: leave the Calculator in a *dirty* state (leftover entry/result).

Proves that skills/demo_calc.py is deterministic: it must reach 56 and 42 no matter
what the app was showing before, because it raises the window on purpose and clears
the entry with Esc. Run:  python tests/dirty_calc.py
"""
import os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from act import send                                            # noqa: E402

AID = 'CalculatorResults'
PKG = r'shell:appsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App'


def window(timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for w in send({'op': 'uia', 'what': 'windows', 'max': 40}).get('windows', []):
            if '计算器' in (w.get('name') or '') or 'Calculator' in (w.get('name') or ''):
                return w
        time.sleep(0.3)
    return None


def display(hwnd):
    rep = send({'op': 'uia', 'what': 'find', 'selector': {'hwnd': hwnd, 'aid': AID}, 'max': 1})
    if not rep.get('n'):
        return None
    m = re.findall(r'-?[\d,]+(?:\.\d+)?', rep['hits'][0].get('name') or '')
    return m[-1].replace(',', '') if m else None


def main():
    subprocess.Popen('explorer.exe "%s"' % PKG, shell=True)
    w = window()
    if not w:
        print('FAIL: no calculator window')
        return 1
    hwnd = w['hwnd']
    send({'op': 'window', 'mode': 'front', 'hwnd': hwnd})
    send({'op': 'window', 'mode': 'top', 'hwnd': hwnd})
    send({'op': 'click', 'target': {'uia': {'selector': {'hwnd': hwnd, 'aid': AID}}}})
    send({'op': 'key', 'key': 'esc'})
    send({'op': 'type', 'text': '6*7'})
    send({'op': 'key', 'key': 'enter'})
    time.sleep(0.4)
    send({'op': 'window', 'mode': 'untop', 'hwnd': hwnd})
    val = display(hwnd)
    print('left the calculator OPEN showing %s (hwnd=%s) - now run skills/demo_calc.py' % (val, hwnd))
    return 0 if val == '42' else 1


if __name__ == '__main__':
    sys.exit(main())
