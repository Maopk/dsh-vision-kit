#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for the headless half of the vision kit.

    python tests/unit-tests.py          # quiet: failures and the summary only
    python tests/unit-tests.py -v       # every assertion

No pytest, no network, no desktop.  Everything asserted here is a pure function: the
run-length and morphology helpers, the NCC matcher, the ink/blue counters, the JSON
request builder of the actor's one-shot CLI.  The other half of the kit - screens, real
input, UI Automation - cannot run on a CI runner; it is listed as desktop-only in
.github/workflows/ci.yml, recorded there and never judged.

Three facts worth knowing before editing the assertions:

  * Tools whose file name has a dash (pixel-verdict.py) cannot be imported by name, so
    every module - dashed or not - is loaded by path through load_module().
  * gray_of() and to_planes() take a PIL image; find_lines(), find_solid_blobs(),
    ncc_map() and match() take numpy arrays.  Mixing the two up is the classic mistake
    in this repo, so each call below says which one it is.
  * Several numbers here (the 0.6667 NCC score, the inclusive [x0,y0,x1,y1] boxes) were
    measured first and only then written down: they are behaviour, not wishes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import traceback

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERBOSE = '-v' in sys.argv[1:]

PASSED = []                 # type: list
FAILED = []                 # type: list


# ── tiny harness ────────────────────────────────────────────────────────────────────

def ok(name, cond, detail=''):
    if cond:
        PASSED.append(name)
        if VERBOSE:
            print('  ✔ %s' % name)
    else:
        FAILED.append((name, detail))
        print('  ✘ %s' % name)
        if detail:
            print('      %s' % detail)


def eq(name, got, want):
    ok(name, got == want, 'got %r, want %r' % (got, want))


def close(name, got, want, tol=1e-6):
    ok(name, abs(float(got) - float(want)) <= tol, 'got %r, want %r ± %r' % (got, want, tol))


def contains(name, haystack, needle):
    ok(name, needle in haystack, '%r not in %r' % (needle, haystack))


def group(title):
    if VERBOSE:
        print('\n── %s' % title)


def load_module(name, relpath):
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── modules under test ──────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.join(ROOT, 'actor'))
act = load_module('act', 'actor/act.py')
cv = load_module('cv_ui_geometry', 'tools/cv_ui_geometry.py')
tm = load_module('template_match', 'tools/template_match.py')
pv = load_module('pixel_verdict', 'tools/pixel-verdict.py')


# ── the actor's one-shot CLI: build() ───────────────────────────────────────────────

group('act.build - argv to a request dict')
eq('a leading { is parsed as JSON', act.build(['{"op": "ping"}']), {'op': 'ping'})
eq('the first token is the op, verbatim', act.build(['op=ping']), {'op': 'op=ping'})
eq('later k=v pairs are coerced with json.loads',
   act.build(['ping', 'n=3', 's=abc', 'b=true', 'f=1.5']),
   {'op': 'ping', 'n': 3, 's': 'abc', 'b': True, 'f': 1.5})
eq('tokens without = collect into args', act.build(['ping', 'pong', 'paf']),
   {'op': 'ping', 'args': ['pong', 'paf']})
eq('a bare path that is not a file is still the op', act.build(['C:/nope.json']),
   {'op': 'C:/nope.json'})

_req = os.path.join(tempfile.gettempdir(), 'dsh-vision-unit-req.json')
with open(_req, 'w', encoding='utf-8') as fh:
    json.dump({'op': 'shot', 'path': 'x.png'}, fh)
eq('run <file> reads the request from that file', act.build(['run', _req]),
   {'op': 'shot', 'path': 'x.png'})
eq('json <file> does the same', act.build(['json', _req]), {'op': 'shot', 'path': 'x.png'})
eq('and so does a bare <file>.json, the spelling the README uses', act.build([_req]),
   {'op': 'shot', 'path': 'x.png'})


# ── the actor's one-shot CLI: fmt() ─────────────────────────────────────────────────

group('act.fmt - a reply to human text')
eq('a reply without a trace list stays JSON',
   json.loads(act.fmt({'hello': 'world'})), {'hello': 'world'})
eq('and it stays on one line', len(act.fmt({'hello': 'world'}).splitlines()), 1)
eq('a non-list trace stays JSON too',
   json.loads(act.fmt({'ok': True, 'trace': {'op': 'ping'}})), {'ok': True, 'trace': {'op': 'ping'}})

_rep = {
    'ok': True, 'steps': 2, 'total_ms': 12.5,
    'trace': [
        {'i': 0, 'op': 'ping', 'ms': 1.5, 'ok': True, 'at': [10, 20]},
        {'i': 1, 'op': 'shot', 'ms': 11.0, 'ok': False, 'error': 'boom',
         'element': {'type': 'Button', 'name': 'OK'}},
    ],
}
_lines = act.fmt(_rep).splitlines()
eq('the header carries ok/steps/total', _lines[0], 'ok=True steps=2 total=12.5ms')
ok('step 0 prints index, op, ms and ok', _lines[1].startswith('   0 ping') and '1.5ms ok=True' in _lines[1],
   _lines[1])
contains('a list-valued extra is printed as-is', _lines[1], 'at=[10, 20]')
ok('step 1 prints the error', 'ok=False' in _lines[2] and 'error=boom' in _lines[2], _lines[2])
contains('an element extra is flattened to type|name', _lines[2], 'element=Button|OK')
eq('pretty=False forces JSON', json.loads(act.fmt(_rep, pretty=False))['steps'], 2)


# ── the actor's one-shot CLI: home()/port() ─────────────────────────────────────────

group('act.home / act.port - where the actor lives')
_tmp = tempfile.mkdtemp(prefix='dsh-vision-home-')
_saved_home = os.environ.get('ACTOR_HOME')
os.environ['ACTOR_HOME'] = _tmp
eq('ACTOR_HOME decides the home', act.home(), _tmp.rstrip('\\/'))
if _saved_home is None:
    del os.environ['ACTOR_HOME']
else:
    os.environ['ACTOR_HOME'] = _saved_home
ok('without ACTOR_HOME the home is still absolute', os.path.isabs(act.home()), act.home())

_saved_port = os.environ.get('ACTOR_PORT')
os.environ['ACTOR_PORT'] = '9123'
eq('ACTOR_PORT decides the port', act.port(), 9123)
ok('the port is an int', isinstance(act.port(), int), type(act.port()).__name__)
if _saved_port is None:
    del os.environ['ACTOR_PORT']
else:
    os.environ['ACTOR_PORT'] = _saved_port


# ── pixel geometry ──────────────────────────────────────────────────────────────────

group('cv_ui_geometry - runs, lines, blobs')
_g = cv.gray_of(Image.fromarray(np.array([[[0, 0, 0], [30, 60, 90]]], dtype=np.uint8)))
close('gray_of averages the channels (PIL in)', float(_g[0, 1]), 60.0)

_mask = np.array([[True, True, False], [True, False, True]])
eq('longest_run counts down each column', cv.longest_run(_mask).tolist(), [2, 1, 1])
eq('run_span returns the first and last index of the longest run',
   cv.run_span(np.array([False, True, True, True, False, True])), (1, 3))
eq('run_span on an empty mask', cv.run_span(np.array([False, False])), (-1, -1))
eq('merge_adjacent groups neighbours', cv.merge_adjacent([1, 2, 10, 11, 12, 20], gap=3),
   [[1, 2], [10, 11, 12], [20]])
eq('merge_adjacent on nothing', cv.merge_adjacent([], gap=3), [])

_white = np.full((40, 40), 255, dtype=np.int16)
_white[:, 5:7] = 0                                   # a dark 2px stripe, full height
_lines_v = cv.find_lines(_white, 'v', 5)
eq('find_lines reports one vertical line', len(_lines_v), 1)
eq('  at is the first column of the stripe', _lines_v[0]['at'], 5)
eq('  run is the length along the stripe', _lines_v[0]['run'], 40)
eq('  span is that whole length', _lines_v[0]['span'], [0, 39])

_two = _white.copy()
_two[:, 20:22] = 0
eq('two stripes are two lines', len(cv.find_lines(_two, 'v', 5)), 2)
_short = np.full((40, 40), 255, dtype=np.int16)
_short[10:20, 5:7] = 0                               # a 10px tall stripe
eq('a run shorter than min_run is not a line', cv.find_lines(_short, 'v', 30), [])
eq('but it is a line for a smaller min_run', len(cv.find_lines(_short, 'v', 5)), 1)
eq('reading the stripe along its length gives a 10px run',
   cv.find_lines(_short.T.copy(), 'h', 5)[0]['run'], 10)

_rgb = np.zeros((40, 40, 3), dtype=np.uint8)
_rgb[20:30, 20:30] = 255                             # a solid 10x10 block
_blobs = cv.find_solid_blobs(_rgb, roi_frac=0.4)
eq('find_solid_blobs finds the block', len(_blobs), 1)
eq('  the box is inclusive on both corners', _blobs[0]['box'], [20, 20, 29, 29])
eq('  and reports how many pixels survived', _blobs[0]['pixels'], 88)
eq('  against the background it measured', _blobs[0]['bg'], [0, 0, 0])

_sq = np.zeros((5, 5), dtype=bool)
_sq[1:4, 1:4] = True
eq('erode 1 leaves the centre of a 3x3 square',
   int(cv.erode(_sq.copy(), 1).sum()), 1)
eq('dilate 1 grows it to a 21-pixel kernel',
   int(cv.dilate(_sq.copy(), 1).sum()), 21)


# ── template matching ───────────────────────────────────────────────────────────────

group('template_match - NCC, boxes, IoU')
# A gradient on two channels and a flat third: the flat one scores 0 everywhere, which is
# exactly why the expected score below is 2/3 and not 1.0.
_arr = np.zeros((60, 60, 3), dtype=np.uint8)
_patch = np.zeros((10, 10, 3), dtype=np.uint8)
_patch[:, :, 0] = np.arange(10, dtype=np.uint8)[:, None] * 20 + 30
_patch[:, :, 1] = np.arange(10, dtype=np.uint8)[None, :] * 15 + 40
_arr[20:30, 20:30] = _patch

_planes = tm.to_planes(Image.fromarray(_arr))        # PIL in
_tpl = tm.to_planes(Image.fromarray(_patch))
eq('to_planes returns float64 planes', (_planes.shape, str(_planes.dtype)), ((60, 60, 3), 'float64'))

_ncc = tm.ncc_map(_planes[:, :, 0], _tpl[:, :, 0])   # arrays in
eq('ncc_map output is smaller than the search image', _ncc.shape, (51, 51))
close('ncc_map peaks at 1.0', float(_ncc.max()), 1.0, 1e-9)
eq('  and it peaks where the patch is',
   tuple(int(x) for x in np.unravel_index(int(_ncc.argmax()), _ncc.shape)), (20, 20))
ok('ncc_map of a flat template is all zeros (no variance to correlate)',
   float(np.abs(tm.ncc_map(_planes[:, :, 0], np.zeros((5, 5)))).max()) == 0.0)

eq('match finds the patch', tm.match(_planes, _tpl, [1.0]),
   {'score': 0.6667, 'scale': 1.0, 'box': [20, 20, 30, 30], 'template_size': [10, 10]})
eq('match honours roi coordinates', tm.match(_planes, _tpl, [1.0], roi=(10, 10, 40, 40))['box'],
   [20, 20, 30, 30])
eq('on a tie the first scale wins', tm.match(_planes, _tpl, [0.9, 1.0])['scale'], 0.9)
eq('a scale below the 8px floor is clamped', tm.match(_planes, _tpl, [0.05])['template_size'], [8, 8])

eq('iou of a box with itself', tm.iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)
eq('iou of disjoint boxes', tm.iou([0, 0, 10, 10], [10, 10, 20, 20]), 0.0)
eq('iou of a half overlap', tm.iou([0, 0, 10, 10], [5, 0, 15, 10]), 0.333)
eq('iou of empty boxes', tm.iou([0, 0, 0, 0], [0, 0, 0, 0]), 0.0)


# ── pixel verdicts ──────────────────────────────────────────────────────────────────

group('pixel-verdict - ink, blue, load')
eq('the two watched boxes are unchanged', (pv.INPUT, pv.BAND),
   ((640, 1352, 1260, 46), (620, 930, 1940, 430)))

_ink = np.zeros((4, 4, 3), dtype=np.int16)
_ink[0, 0] = (200, 200, 200)                         # bright, but grey: not blue
_ink[1, 1] = (0, 60, 200)                            # bright and blue
eq('ink counts every pixel brighter than 140', int(pv.ink(_ink)), 2)
eq('blue counts only the blue ones', int(pv.blue(_ink)), 1)

_png = os.path.join(tempfile.gettempdir(), 'dsh-vision-unit.png')
_im = Image.new('RGB', (60, 40), (0, 0, 0))
for _x in range(10, 20):
    for _y in range(5, 15):
        _im.putpixel((_x, _y), (255, 255, 255))
_im.save(_png)
_box = pv.load(_png, (10, 5, 10, 10))                # (x, y, w, h)
eq('load crops the requested box', _box.shape, (10, 10, 3))
eq('  and keeps the pixel values', (int(_box.min()), int(_box.max())), (255, 255))


# ── the actor's run trace: what each step carries back ──────────────────────────────

group('actor.o_run - one step can bring its own reply back')
actor_srv = load_module('actor_under_test', 'actor/actor.py')
_plain = actor_srv.o_run({'steps': [{'op': 'sleep', 'ms': 1}]})['trace'][0]
eq('by default the trace is unchanged', 'data' in _plain, False)
eq('  and every step reported ok', _plain['ok'], True)
_loud = actor_srv.o_run({'steps': [{'op': 'sleep', 'ms': 1}], 'results': True})['trace'][0]
eq('results=true folds the step reply in', _loud['data']['slept_ms'], 1)
eq('a number sets the per-string budget',
   actor_srv.o_run({'steps': [{'op': 'sleep', 'ms': 1}], 'results': 5})['trace'][0]['data']['slept_ms'], 1)
eq('short text is untouched', actor_srv._slim({'t': 'abc'}, 10), {'t': 'abc'})
contains('long text is cut with a count', actor_srv._slim({'t': 'x' * 100}, 10)['t'], '(90 more chars)')
eq('a long list keeps six items and a count',
   actor_srv._slim(list(range(10)), 999), [0, 1, 2, 3, 4, 5, '…(4 more items)'])
eq('a short list is left alone', actor_srv._slim([1, 2], 999), [1, 2])
eq('frame and png never ride along', actor_srv._slim({'png': 'AAAA', 'hit': 1}, 999), {'hit': 1})


# ── variables in a run, and the macro that freezes it ───────────────────────────────

group('actor - {{variables}} and macros (record once, replay in one call)')
_home_was = actor_srv.HOME
actor_srv.HOME = tempfile.mkdtemp(prefix='dsh-vision-macro-')     # macros land in the scratch dir

eq('a placeholder alone becomes the value itself',
   actor_srv.interp({'x': '{{a.b}}'}, {'a': {'b': [7, 8]}}), {'x': [7, 8]})
eq('a placeholder inside a string is inlined as JSON',
   actor_srv.interp({'x': 'at {{p}} now'}, {'p': [1, 2]}), {'x': 'at [1, 2] now'})
eq('no braces means no work', actor_srv.interp({'x': 'plain'}, {}), {'x': 'plain'})
eq('lists are walked too', actor_srv.interp(['{{n}}'], {'n': 3}), [3])

_cap = actor_srv.o_run({'steps': [{'op': 'sleep', 'ms': 1, 'as': 's'},
                                  {'op': 'sleep', 'ms': '{{s.slept_ms}}'}]})
eq('as captures a step reply, {{}} feeds it to the next step', _cap['ok'], True)
eq('  and both steps ran', _cap['steps'], 2)
_lit = actor_srv.o_run({'steps': [{'op': 'sleep', 'ms': 1, 'literal': True, 'note': '{{nope}}'}]})
eq('a literal step keeps its braces', _lit['ok'], True)
_bad = actor_srv.o_run({'steps': [{'op': 'sleep', 'ms': '{{nope.x}}'}]})
eq('an unresolvable variable fails that step', _bad['ok'], False)
contains('  and the error names it', _bad['trace'][0]['error'], '{{nope.x}}')
ok('the failing run still happened (run history)', _bad['run_id'] >= 1, _bad['run_id'])

_saved = actor_srv.o_macro({'what': 'save', 'name': 'unit-macro', 'note': 'unit',
                            'steps': [{'op': 'sleep', 'ms': 1}], 'args': {'n': 2}, 'vars': 'ignored'})
eq('a macro saves its steps', _saved['saved'], True)
eq('  and appears in the index', [m['name'] for m in actor_srv.macro_index()], ['unit-macro'])
eq('  with its declared args', actor_srv.macro_index()[0]['args'], ['n'])
_replay = actor_srv.o_macro({'what': 'run', 'name': 'unit-macro', 'args': {'n': 5}, 'results': True})
eq('a replay runs the whole macro', _replay['steps'], 1)
eq('  and the call args win over the recorded defaults', _replay['args'], {'n': 5})
eq('  and the replay is counted', _replay['replays'], 1)
eq('  and the stats went back to disk', actor_srv.macro_read('unit-macro')['stats']['replays'], 1)
_dry = actor_srv.o_macro({'what': 'run', 'name': 'unit-macro', 'dry': True})
eq('dry expands without executing', (_dry['dry'], _dry['steps'][0]['op']), (True, 'sleep'))
_rerec = actor_srv.o_macro({'what': 'save', 'name': 'unit-macro'})
eq('recording over a live macro is refused', _rerec['saved'], False)
contains('  and says how to replace it anyway', _rerec['why'], 'overwrite')
eq('a macro deletes', actor_srv.o_macro({'what': 'del', 'name': 'unit-macro'})['deleted'], 'unit-macro')
eq('  and the index is empty again', actor_srv.macro_index(), [])
_escape = False
try:
    actor_srv.macro_path('../escape')
except ValueError:
    _escape = True
ok('a macro name cannot climb out of the macros dir', _escape)
actor_srv.HOME = _home_was


# ── launch: the macro that starts its own app ────────────────────────────────────────

group('actor - launch steps and self-starting macros')
_home_was = actor_srv.HOME
actor_srv.HOME = tempfile.mkdtemp(prefix='dsh-vision-launch-')

eq('a launch step is lifted out of a recorded run',
   actor_srv._launch_from_steps([{'op': 'window', 'mode': 'front'},
                                 {'op': 'launch', 'path': 'C:\\App\\a.exe', 'wait': 'App',
                                  'as': 'started'}]),
   {'path': 'C:\\App\\a.exe', 'wait': 'App'})
eq('a run without a launch step carries none', actor_srv._launch_from_steps([{'op': 'sleep'}]), None)
_nopath = False
try:
    actor_srv.launch_app({})
except ValueError:
    _nopath = True
ok('launch refuses to guess what to start', _nopath)

_sv = actor_srv.o_macro({'what': 'save', 'name': 'unit-launch', 'overwrite': True,
                         'args': {'app': 'Notepad'},
                         'steps': [{'op': 'launch', 'path': 'C:\\App\\a.exe', 'wait': '{{app}}'},
                                   {'op': 'sleep', 'ms': 1}]})
eq('a recorded macro keeps the launch that starts it', _sv['saved'], True)
eq('  and the macro file carries it',
   actor_srv.macro_read('unit-launch')['launch'], {'path': 'C:\\App\\a.exe', 'wait': '{{app}}'})
eq('  and the index can say so', actor_srv.macro_index()[0]['launch'], True)
_dry = actor_srv.o_macro({'what': 'run', 'name': 'unit-launch', 'args': {'app': 'Calc'}, 'dry': True})
eq('a dry run expands the launch variables without starting anything',
   _dry['steps'][0]['wait'], 'Calc')
actor_srv.HOME = _home_was


# ── import smoke ────────────────────────────────────────────────────────────────────

group('import smoke - the headless modules still load')
SMOKE = [
    ('tools/cv_ui_geometry.py', True),
    ('tools/template_match.py', True),
    ('tools/pixel-verdict.py', True),
    ('tools/probe_and_ocr.py', False),               # two entry functions, no main()
    ('tools/brightmap.py', True),
    ('tools/ground_test.py', True),
    ('tools/ocr_boxes.py', True),
    ('tests/make-synthetic-sample.py', True),
    ('actor/act.py', False),                         # a CLI: no main(), it runs at the bottom
]
for _rel, _want_main in SMOKE:
    _name = os.path.basename(_rel).replace('.py', '').replace('-', '_') + '_smoke'
    try:
        _mod = load_module(_name, _rel)
    except Exception:
        ok('imports cleanly: %s' % _rel, False, traceback.format_exc(limit=3))
        continue
    if _want_main:
        ok('imports cleanly and has main(): %s' % _rel, callable(getattr(_mod, 'main', None)))
    else:
        ok('imports cleanly: %s' % _rel, True)

_sample = load_module('make_synthetic_sample_smoke2', 'tests/make-synthetic-sample.py')
eq('the synthetic sample keeps its frame', tuple(_sample.FRAME), (80, 60, 520, 380))
eq('the synthetic sample keeps its block', tuple(_sample.BLOCK), (200, 150, 340, 260))


# ── summary ─────────────────────────────────────────────────────────────────────────

print()
if FAILED:
    print('failing assertions:')
    for _name, _detail in FAILED:
        print('  ✘ %s' % _name)
        if _detail:
            print('      %s' % _detail)
    print()
print('════ Result: %d passed / %d failed ════' % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
