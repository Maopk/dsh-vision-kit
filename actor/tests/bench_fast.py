"""Fast-loop bench: proves what one call can do now (state/probe/run/front/batch type).

Run with the bundled interpreter (PIL/numpy), against the resident actor:
    python bench_fast.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # actor/ next door
sys.stdout.reconfigure(encoding='utf-8')   # type: ignore[union-attr]
from act import send as _send                                  # noqa: E402


def send(req):
    r = _send(req)
    if r.get('ok') is False:
        raise SystemExit('op %r failed: %s' % (req.get('op'), r.get('error')))
    return r


def T(fn, n=3):
    tot, best, r = 0.0, 1e9, None
    for _ in range(n):
        t = time.perf_counter()
        r = fn()
        dt = (time.perf_counter() - t) * 1000
        tot += dt
        best = min(best, dt)
    return r, tot / n, best


def line(label, avg, best, extra=''):
    print('%-26s avg %7.1f ms   best %7.1f ms   %s' % (label, avg, best, extra))


def main():
    print('== protocol / digest ==========================================')
    r, a, b = T(lambda: send({'op': 'ping'}), 5)
    line('ping (round trip)', a, b, 'pid=%s' % r.get('pid'))
    r, a, b = T(lambda: send({'op': 'state'}))
    fg = r.get('foreground') or {}
    line('state (windows list)', a, b, 'wins=%d fg=%r' % (len(r.get('windows', [])), (fg.get('title') or '')[:34]))
    r, a, b = T(lambda: send({'op': 'state', 'uia': True, 'scan': 900, 'limit': 40}), 2)
    e = r.get('elements') or {}
    line('state (uia, compact)', a, b, 'scanned=%s named=%s' % (e.get('scanned'), e.get('n')))

    print('== pixel bookkeeping (no PNG round trip) ======================')
    r, a, b = T(lambda: send({'op': 'probe', 'region': [0, 0, 600, 400]}))
    line('probe colors 600x400', a, b, 'top=%s' % [c['rgb'] for c in r.get('top', [])][:3])
    r, a, b = T(lambda: send({'op': 'probe'}), 2)
    line('probe colors full screen', a, b, 'top=%s' % [c['rgb'] for c in r.get('top', [])][:3])
    r, a, b = T(lambda: send({'op': 'probe', 'mode': 'ink', 'region': [0, 0, 1000, 640], 'grid': [64, 22]}), 2)
    line('probe ink 1000x640', a, b, 'bg=%s' % r.get('bg'))
    print(r.get('ink', ''))
    send({'op': 'probe', 'mode': 'mark', 'region': [0, 0, 2560, 1600]})
    r, a, b = T(lambda: send({'op': 'probe', 'mode': 'diff', 'region': [0, 0, 2560, 1600]}), 2)
    line('probe diff full screen', a, b, 'changed=%s pct=%s' % (r.get('changed_px'), r.get('pct')))

    print('== hands ======================================================')
    r, a, b = T(lambda: send({'op': 'move', 'x': 620, 'y': 620}))
    line('move fast (teleport)', a, b, r['move'].get('mode'))
    r, a, b = T(lambda: send({'op': 'move', 'x': 940, 'y': 720, 'human': True, 'dur_ms': 160}))
    line('move human=True', a, b)
    st = send({'op': 'state', 'windows': False})
    fg = st['foreground']
    cx, cy = (fg['rect'][0] + fg['rect'][2]) // 2, fg['rect'][1] + 18
    r, a, b = T(lambda: send({'op': 'click', 'target': {'xy': [cx, cy]}, 'front': fg['hwnd']}))
    line('click fast (+auto front)', a, b, 'front=%s' % (r.get('front') or {}).get('is_foreground'))
    r, a, b = T(lambda: send({'op': 'click', 'target': {'xy': [cx, cy]}, 'ease': True}), 2)
    line('click ease=True', a, b)

    print('== one call drives a whole interaction ========================')
    text = ('The quick brown fox jumps over the lazy dog. 0123456789 ' * 4)[:150]
    steps = [
        {'op': 'state'},
        {'op': 'probe', 'mode': 'mark', 'region': [0, 0, 2560, 1600]},
        {'op': 'type', 'text': text, 'per_char_ms': 0, 'front_title': 'Notepad'},
        {'op': 'probe', 'mode': 'diff', 'region': [0, 0, 2560, 1600]},
    ]
    t = time.perf_counter()
    r = send({'op': 'run', 'steps': steps})
    dt = (time.perf_counter() - t) * 1000
    print('run(%d steps, 150 chars typed)  total %8.1f ms   ok=%s' % (len(steps), dt, r.get('ok')))
    for s in r['trace']:
        extra = {k: v for k, v in s.items() if k in ('changed_px', 'pct', 'n', 'chars', 'mode')}
        print('    %-8s %8.1f ms  ok=%-5s %s' % (s['op'], s['ms'], s['ok'], extra if extra else ''))
    e = (send({'op': 'state', 'uia': True, 'limit': 12}).get('elements') or {})
    print('foreground elements now: %s' % [(i['type'], i['name'][:26]) for i in e.get('items', [])][:6])


if __name__ == '__main__':
    main()
