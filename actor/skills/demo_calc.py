#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Demo skill: drive the Windows Calculator the way a person does.

eyes    = UI Automation (structure channel, no pixels)
hands   = SendInput (real mouse/keyboard events)
focus   = explicit window raise + TOPMOST pin, because any app can steal focus
verify  = UI Automation again, polled until the expected value shows up

Run:  python skills/demo_calc.py
"""
import json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from act import send                                            # noqa: E402

try:                                    # keep Chinese element names readable on a GBK console
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # type: ignore[union-attr]
except Exception:
    pass

AID = 'CalculatorResults'
PKG = r'shell:appsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App'
NAV = ('打开导航', 'Open navigation', 'Navigation')
STANDARD = ('标准', 'Standard', '标准计算器', 'Standard Calculator')


def timed(label, req, echo=True):
    t0 = time.perf_counter()
    rep = send(req)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if echo:
        brief = 'ok' if rep.get('ok') else json.dumps(rep, ensure_ascii=False)[:200]
        print('  %-26s %8sms  %s' % (label, ms, brief))
    return rep, ms


def find_window(timeout=20.0):
    """Wait for the Calculator window - one cheap structure call per poll, no screenshots."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        rep = send({'op': 'uia', 'what': 'windows', 'max': 40})
        for w in rep.get('windows', []):
            n = w.get('name') or ''
            if '计算器' in n or 'Calculator' in n:
                return w, round((time.perf_counter() - t0) * 1000, 1)
        time.sleep(0.3)
    return None, round((time.perf_counter() - t0) * 1000, 1)


def find_el(hwnd, aid=AID):
    rep = send({'op': 'uia', 'what': 'find', 'selector': {'hwnd': hwnd, 'aid': aid}, 'max': 1})
    return (rep['hits'][0] if rep.get('n') else None), rep


def find_by_name(hwnd, names, ctype=None):
    """First element whose name is one of `names` (exact match wins over 'contains')."""
    for name in names:
        sel = {'hwnd': hwnd, 'name': name}
        if ctype:
            sel['ctype'] = ctype
        rep = send({'op': 'uia', 'what': 'find', 'selector': sel, 'max': 5})
        if rep.get('n'):
            return rep['hits'][0]
    return None


def pane_items(hwnd, timeout=2.0):
    """Navigation-pane entries (the mode names) are ListItems - poll until they show up."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        rep = send({'op': 'uia', 'what': 'find', 'selector': {'hwnd': hwnd, 'ctype': 'ListItem'}, 'max': 20})
        items = [h for h in (rep.get('hits') or []) if h.get('name')]
        if items:
            return items
        time.sleep(0.15)
    return []


def ensure_standard(hwnd, timeout=8.0):
    """The Calculator restores whatever mode it was last in, and graphing mode has no
    CalculatorResults - so walk the navigation pane back to Standard, structurally."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        el, _ = find_el(hwnd)
        if el:
            return True, 'Standard (display element present)'
        nav = find_by_name(hwnd, NAV)
        if not nav:
            return False, 'no navigation button - unknown layout'
        send({'op': 'click', 'target': {'uia': {'selector': {'hwnd': hwnd, 'name': nav['name']}}}, 'front': hwnd})
        for it in pane_items(hwnd):
            nm = (it.get('name') or '').strip()
            if any(s.lower() in nm.lower() for s in STANDARD):
                send({'op': 'click', 'target': {'uia': {'selector': {'hwnd': hwnd, 'name': it['name']}}}, 'front': hwnd})
                time.sleep(0.4)
                break
        else:
            time.sleep(0.3)
    el, _ = find_el(hwnd)
    return bool(el), ('switched to Standard' if el else 'could not reach Standard in %ss' % timeout)


def read_display(hwnd):
    el, rep = find_el(hwnd)
    if not el:
        return None, rep
    name = el.get('name') or ''
    m = re.findall(r'-?[\d,]+(?:\.\d+)?', name)
    return (m[-1].replace(',', '') if m else None), el


def wait_display(hwnd, want, timeout=3.0):
    """Poll the structure channel until the display reads `want` - never a blind sleep."""
    t0 = time.perf_counter()
    val, raw = None, None
    while time.perf_counter() - t0 < timeout:
        val, raw = read_display(hwnd)
        if val == want:
            break
        time.sleep(0.04)
    return val, raw, round((time.perf_counter() - t0) * 1000, 1)


def main():
    grand = time.perf_counter()
    print('== 1. launch Calculator; wait for it *structurally* (no screenshots) ==')
    subprocess.Popen('explorer.exe "%s"' % PKG, shell=True)
    win, waited = find_window()
    if not win:
        print('  FAIL: Calculator window never appeared')
        return 1
    hwnd = win['hwnd']
    print('  window after %sms: name=%r cls=%s hwnd=%s rect=%s' % (waited, win['name'], win['cls'], hwnd, win['rect']))
    t0 = time.perf_counter()
    mode_ok, why = ensure_standard(hwnd)
    print('  standard mode (%sms): %s - %s' % (round((time.perf_counter() - t0) * 1000, 1), mode_ok, why))
    el, _ = find_el(hwnd)
    print('  display element: %s' % json.dumps(el, ensure_ascii=False))

    print('== 2. take the foreground on purpose, then 7 * 8 = ==')
    rep, _ = timed('window front', {'op': 'window', 'mode': 'front', 'hwnd': hwnd})
    print('  foreground=%s is_foreground=%s' % (rep.get('foreground'), rep.get('is_foreground')))
    timed('window top (pin)', {'op': 'window', 'mode': 'top', 'hwnd': hwnd})
    timed('click CalculatorResults', {'op': 'click', 'target': {'uia': {'selector': {'hwnd': hwnd, 'aid': AID}}}})
    timed('key esc (clear)', {'op': 'key', 'key': 'esc'})
    timed('type "7*8"', {'op': 'type', 'text': '7*8'})
    timed('key enter', {'op': 'key', 'key': 'enter'})
    value, raw, vms = wait_display(hwnd, '56')
    print('  display now (%sms): %s' % (vms, json.dumps(raw, ensure_ascii=False)))

    print('== 3. the whole keypad is readable in ONE structure call ==')
    rep, ms = timed('uia find Button', {'op': 'uia', 'what': 'find', 'selector': {'hwnd': hwnd, 'ctype': 'Button'}, 'max': 60}, echo=False)
    names = [h.get('name') for h in (rep.get('hits') or [])]
    print('  %d buttons in %sms: %s' % (len(names), ms, ', '.join([n for n in names if n][:20])))

    print('== 4. click the equals button BY NAME from that tree, on a fresh sum ==')
    timed('type "12+30"', {'op': 'type', 'text': '12+30'})
    eq = [n for n in names if n and n.strip() in ('=', '等于', 'Equals')]
    if eq:
        timed('click "%s"' % eq[0], {'op': 'click', 'target': {'uia': {'selector': {'hwnd': hwnd, 'name': eq[0], 'ctype': 'Button'}}}})
    else:
        print('  (no equals button name found; using enter)')
        timed('key enter', {'op': 'key', 'key': 'enter'})
    value2, raw2, vms2 = wait_display(hwnd, '42')
    print('  display now (%sms): %s' % (vms2, json.dumps(raw2, ensure_ascii=False)))
    timed('window untop', {'op': 'window', 'mode': 'untop', 'hwnd': hwnd})

    ok1, ok2 = value == '56', value2 == '42'
    total = round((time.perf_counter() - grand) * 1000, 1)
    print('== result ==')
    print('  7*8   -> %-6s %s' % (value, 'PASS' if ok1 else 'FAIL'))
    print('  12+30 -> %-6s %s' % (value2, 'PASS' if ok2 else 'FAIL'))
    print('  whole skill: %sms wall clock (launch included), pixels read: 0' % total)
    print('  leave the app closed for the next run')
    timed('window close', {'op': 'window', 'mode': 'close', 'hwnd': hwnd}, echo=False)
    return 0 if (ok1 and ok2) else 1


if __name__ == '__main__':
    sys.exit(main())
