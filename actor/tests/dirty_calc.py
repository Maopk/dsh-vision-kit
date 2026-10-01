#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test helper: leave the Calculator in a *dirty* state (leftover entry/result).

Proves that skills/demo_calc.py is deterministic: it must reach 56 and 42 no matter
what the app was showing before, because it raises the window on purpose and clears
the entry with Esc. Run:  python tests/dirty_calc.py
"""
import os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'skills'))
from act import send                                            # noqa: E402
from demo_calc import ensure_standard, read_display, wait_display   # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

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


def main():
    subprocess.Popen('explorer.exe "%s"' % PKG, shell=True)
    w = window()
    if not w:
        print('FAIL: no calculator window')
        return 1
    hwnd = w['hwnd']
    mode_ok, why = ensure_standard(hwnd)
    print('calculator mode: %s - %s' % (mode_ok, why))
    send({'op': 'window', 'mode': 'front', 'hwnd': hwnd})
    send({'op': 'window', 'mode': 'top', 'hwnd': hwnd})
    send({'op': 'click', 'target': {'uia': {'selector': {'hwnd': hwnd, 'aid': AID}}}})
    send({'op': 'key', 'key': 'esc'})
    send({'op': 'type', 'text': '6*7'})
    send({'op': 'key', 'key': 'enter'})
    val, raw, ms = wait_display(hwnd, '42', timeout=3.0)
    send({'op': 'window', 'mode': 'untop', 'hwnd': hwnd})
    print('left the calculator OPEN showing %s after %sms (hwnd=%s) - now run skills/demo_calc.py'
          % (val, ms, hwnd))
    return 0 if val == '42' else 1


if __name__ == '__main__':
    sys.exit(main())
