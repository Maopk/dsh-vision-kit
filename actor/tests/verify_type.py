"""Does the fast path still land the text? Types 150 chars into Notepad three ways
and proves each one with a pixel diff + an ASCII ink map (no PNG round trip).

    python verify_type.py          # Notepad must already be open
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # actor/ next door
sys.stdout.reconfigure(encoding='utf-8')
from act import send                                          # noqa: E402


def S(req):
    r = send(req)
    if r.get('ok') is False:
        raise SystemExit('op %r failed: %s' % (req.get('op'), r.get('error')))
    return r


TEXT = ('The quick brown fox jumps over the lazy dog. 0123456789 ' * 4)[:150]

def main():
    st = S({'op': 'state'})
    nb = [w for w in st.get('windows', []) if 'Notepad' in (w.get('name') or '')]
    if not nb:
        raise SystemExit('Notepad is not open: %s' % [(w['name'], w['rect']) for w in st.get('windows', [])])
    hwnd, rect = nb[0]['hwnd'], nb[0]['rect']
    reg = [rect[0] + 20, rect[1] + 90, rect[2] - 20, rect[3] - 20]
    print('notepad hwnd=%s rect=%s editor=%s' % (hwnd, rect, reg))

    S({'op': 'key', 'key': 'ctrl+a', 'front': hwnd})
    S({'op': 'key', 'key': 'delete'})
    time.sleep(0.4)

    print('\n== A. old style: separate calls, 12 ms per character ==')
    S({'op': 'probe', 'mode': 'mark', 'region': reg})
    t = time.perf_counter()
    r = S({'op': 'type', 'text': TEXT, 'per_char_ms': 12, 'front': hwnd})
    t_type = (time.perf_counter() - t) * 1000
    time.sleep(0.6)
    d = S({'op': 'probe', 'mode': 'diff', 'region': reg})
    print('type op wall %.0f ms  (op says %s ms, %s chars)  ->  diff %s px (%.2f%%)'
          % (t_type, r['ms'], r['chars'], d['changed_px'], d['pct'] * 100))
    print(S({'op': 'probe', 'mode': 'ink', 'region': reg, 'grid': [76, 12]})['ink'])

    print('\n== B. new style: ONE run call, batch type, polled diff ==')
    S({'op': 'key', 'key': 'ctrl+a', 'front': hwnd})
    S({'op': 'key', 'key': 'delete'})
    time.sleep(0.4)
    steps = [
        {'op': 'probe', 'mode': 'mark', 'region': reg},
        {'op': 'type', 'text': TEXT, 'per_char_ms': 0, 'front': hwnd},
        {'op': 'sleep', 'ms': 400},
        {'op': 'probe', 'mode': 'diff', 'region': reg},
    ]
    t = time.perf_counter()
    r = S({'op': 'run', 'steps': steps})
    wall = (time.perf_counter() - t) * 1000
    print('run wall %.0f ms   ok=%s' % (wall, r['ok']))
    for s in r['trace']:
        extra = {k: v for k, v in s.items() if k in ('changed_px', 'pct', 'chars', 'mode', 'chunks', 'op_ms')}
        print('    %-8s %7.1f ms  ok=%-5s %s' % (s['op'], s['ms'], s['ok'], extra or ''))
    print(S({'op': 'probe', 'mode': 'ink', 'region': reg, 'grid': [76, 12]})['ink'])

    print('\n== C. 500 chars in one batch (throughput) ==')
    S({'op': 'key', 'key': 'ctrl+a', 'front': hwnd})
    S({'op': 'key', 'key': 'delete'})
    time.sleep(0.4)
    big = ('abcdefghijklmnopqrstuvwxyz 0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ. ' * 8)[:500]
    t = time.perf_counter()
    r = S({'op': 'type', 'text': big, 'per_char_ms': 0, 'front': hwnd})
    print('500 chars in %.0f ms (%.0f chars/s)' % ((time.perf_counter() - t) * 1000, 500 / max(0.001, (time.perf_counter() - t))))
    time.sleep(0.6)
    d = S({'op': 'probe', 'mode': 'diff', 'region': reg})
    print('diff after 500 chars: %s px (%.1f%%)' % (d['changed_px'], d['pct'] * 100))
    S({'op': 'key', 'key': 'ctrl+a'})
    S({'op': 'key', 'key': 'delete'})


if __name__ == '__main__':
    main()
