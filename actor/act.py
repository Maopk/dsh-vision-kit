#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiny client for the DSH PC Actor: one JSON line out, one JSON line back.

  python act.py ping
  python act.py shot path=C:/tmp/x.png
  python act.py run skills/whatever.json
  python act.py skills/whatever.json
  python act.py '{"op":"click","target":{"xy":[100,200]}}'
  echo '{"op":"uia","what":"tree","depth":3}' | python act.py
"""
import json, os, socket, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def home():
    """Same resolution as actor.py: $ACTOR_HOME > <code dir>/home.txt > code dir."""
    h = os.environ.get('ACTOR_HOME')
    if not h:
        try:
            with open(os.path.join(HERE, 'home.txt'), encoding='utf-8-sig') as f:
                h = f.read().strip()
        except Exception:
            h = ''
    return (h or HERE).rstrip('\\/')


HOME = home()


def port():
    p = os.environ.get('ACTOR_PORT')
    if p:
        return int(p)
    try:
        with open(os.path.join(HOME, 'port.txt')) as f:
            return int(f.read().strip())
    except Exception:
        return 8731


def _autostart(wait=25.0):
    """The hand is always there: if nothing is listening, start the daemon and wait."""
    import subprocess, time
    logs = os.path.join(HOME, 'logs')
    os.makedirs(logs, exist_ok=True)
    env = dict(os.environ)
    env['ACTOR_HOME'] = HOME
    pylibs = os.path.join(HOME, 'pylibs')
    if os.path.isdir(pylibs):
        env['PYTHONPATH'] = pylibs + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    tmp = os.path.join(HOME, 'tmp')
    os.makedirs(tmp, exist_ok=True)
    env['TEMP'] = env['TMP'] = tmp
    flags = 0x00000008 | 0x00000200 if os.name == 'nt' else 0      # DETACHED | NEW_GROUP
    out = open(os.path.join(logs, 'stdout.log'), 'ab')
    err = open(os.path.join(logs, 'stderr.log'), 'ab')
    subprocess.Popen([sys.executable, os.path.join(HERE, 'actor.py'), '--port', str(port())],
                     cwd=HERE, env=env, creationflags=flags, stdout=out, stderr=err, stdin=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            s = socket.create_connection(('127.0.0.1', port()), timeout=1.0)
            s.close()
            return True
        except Exception:
            time.sleep(0.25)
    return False


def send(req, timeout=120.0):
    s = socket.create_connection(('127.0.0.1', port()), timeout=timeout)
    try:
        s.sendall((json.dumps(req, ensure_ascii=False) + '\n').encode('utf-8'))
        buf = b''
        while not buf.endswith(b'\n'):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    return json.loads(buf.decode('utf-8'))


def send_or_start(req, timeout=120.0):
    try:
        return send(req, timeout)
    except (ConnectionRefusedError, socket.timeout, OSError):
        if not _autostart():
            raise
        return send(req, timeout)


def build(argv):
    if not argv:
        return json.loads(sys.stdin.read())
    a = argv[0]
    if a.startswith('{'):
        return json.loads(a)
    if a in ('run', 'json') and len(argv) > 1 and os.path.exists(argv[1]):
        with open(argv[1], encoding='utf-8') as f:
            return json.load(f)
    if os.path.exists(a) and a.lower().endswith('.json'):
        with open(a, encoding='utf-8') as f:      # the other spelling in the README: act.cmd <file>
            return json.load(f)
    req = {'op': a}
    for kv in argv[1:]:
        if '=' not in kv:
            req.setdefault('args', []).append(kv)
            continue
        k, v = kv.split('=', 1)
        try:
            v = json.loads(v)
        except Exception:
            pass
        req[k] = v
    return req


def fmt(rep, pretty=True):
    if not pretty or not isinstance(rep, dict):
        return json.dumps(rep, ensure_ascii=False)
    if isinstance(rep.get('macros'), list):          # macro list: one line per macro
        out = ['macros: %d in %s' % (len(rep['macros']), rep.get('dir'))]
        for m in rep['macros']:
            last = 'never' if m.get('last_ms') is None else '%sms %s' % (
                m['last_ms'], 'ok' if m.get('last_ok') else 'FAILED')
            out.append('  %-24s %2s steps  %-10s replays=%-3s %-14s %s' % (
                m.get('name'), m.get('steps'), 'can-start' if m.get('launch') else 'hand-start',
                m.get('replays'), last, m.get('note') or ''))
        return '\n'.join(out)
    if not isinstance(rep.get('trace'), list):
        return json.dumps(rep, ensure_ascii=False)
    head = 'ok=%s steps=%s total=%sms' % (rep.get('ok'), rep.get('steps'), rep.get('total_ms'))
    if rep.get('macro'):                             # a replay says which macro it was
        head = 'macro=%s replay#%s %s' % (rep['macro'], rep.get('replays'), head)
    if rep.get('front_resolve_ms'):                  # the window lookup that runs before step 0
        head += ' front=%sms' % rep['front_resolve_ms']
    if isinstance(rep.get('launch'), dict):          # a replay may have had to start the app
        lz = rep['launch']
        head += ' start=%s' % ('reused' if lz.get('skipped') else '%sms' % lz.get('waited_ms'))
    out = [head]
    for t in rep['trace']:
        extra = ''
        for k in ('n', 'at', 'how', 'ratio', 'path', 'did', 'element', 'error'):
            if k in t:
                v = t[k]
                if k == 'element' and isinstance(v, dict):
                    v = '%s|%s' % (v.get('type'), v.get('name'))
                extra += ' %s=%s' % (k, v)
        line = '  %2d %-9s %7sms ok=%s%s' % (t.get('i'), t.get('op'), t.get('ms'), t.get('ok'), extra)
        out.append(line)
        if 'data' in t:                    # run with results=true: the step's own reply, pre-clipped
            out.append('       data=%s' % json.dumps(t['data'], ensure_ascii=False))
    return '\n'.join(out)


if __name__ == '__main__':
    try:                                    # element names are Chinese as often as not
        sys.stdout.reconfigure(encoding='utf-8')   # type: ignore[union-attr]
    except Exception:
        pass
    argv = [a for a in sys.argv[1:] if a != '--json']
    pretty = '--json' not in sys.argv[1:]
    try:
        rep = send_or_start(build(argv))
    except Exception as e:
        print(json.dumps({'ok': False, 'error': 'actor unreachable: %s' % e}, ensure_ascii=False))
        sys.exit(2)
    print(fmt(rep, pretty))
    sys.exit(0 if rep.get('ok') else 1)
