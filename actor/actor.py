#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSH PC Actor - a persistent, human-like operator for Windows.

One long-lived process owns:
  eyes      : cached GDI capture of the whole virtual desktop (~5-15 ms/frame)
  hands     : SendInput mouse/keyboard, human-like motion, ~1-2 ms/event
  structure : in-process UI Automation (comtypes), every call guarded by a timeout
  memory    : last frame, template/colour cache, UIA element cache
  reflexes  : skills - a batch of steps executed server-side (one call = one skill)

The model sends a *skill*, not a single step; the loop lives in here.

Protocol: TCP 127.0.0.1:<port>, one JSON request per line, one JSON reply per line.
Ops: ping shot save find click move drag type key scroll uia window launch wait_for watch state probe log bench run macro capture stop
(kept in step with the @op(...) decorators by tools/check-skill-ops.py, which runs in CI)
"""
import argparse, collections, ctypes, json, os, queue, re, socketserver, sys, threading, time, traceback
import shlex, subprocess
from ctypes import wintypes as wt

try:
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]
    sliding_window_view = None  # type: ignore[assignment]

HERE = os.path.dirname(os.path.abspath(__file__))


def resolve_home():
    """Where the actor keeps its mutable state (logs, port.txt, tmp, pylibs).

    Machine local and deliberately outside the repo: the code stays portable and
    nothing generated ever lands in git. Order: $ACTOR_HOME > <code dir>/home.txt
    (written by `actor.ps1 -Setup`) > the code dir itself.
    """
    h = os.environ.get('ACTOR_HOME')
    if not h:
        f = os.path.join(HERE, 'home.txt')
        try:
            with open(f, encoding='utf-8-sig') as fh:
                h = fh.read().strip()
        except Exception:
            h = ''
    return (h or HERE).rstrip('\\/')


HOME = resolve_home()
LOG_LOCK = threading.Lock()


# --------------------------------------------------------------------------- log
def log(event, **kw):
    rec = dict(ts=time.strftime('%H:%M:%S'), ev=event, **kw)
    line = json.dumps(rec, ensure_ascii=False)
    with LOG_LOCK:
        try:
            with open(os.path.join(HOME, 'logs', 'actor.log'), 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass
    if os.environ.get('ACTOR_VERBOSE'):
        print(line, flush=True)


# ----------------------------------------------------------------------- dpi
def set_dpi_awareness():
    u = ctypes.windll.user32
    for fn, arg, name in ((getattr(u, 'SetProcessDpiAwarenessContext', None), ctypes.c_void_p(-4), 'pmv2'),):
        if fn:
            try:
                if fn(arg):
                    return name
            except Exception:
                pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return 'per-monitor'
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return 'system'
    except Exception:
        return 'none'


# ------------------------------------------------------------------- capture
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG),
                ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wt.DWORD * 3)]


class Screen:
    """Cached GDI capture: one DC + one bitmap, reused for every frame."""

    SRCCOPY = 0x00CC0020
    DIB_RGB_COLORS = 0

    def __init__(self):
        self.u = ctypes.windll.user32
        self.g = ctypes.windll.gdi32
        self.lock = threading.Lock()
        self.hdc = self.mem = self.bmp = self.old = None
        self.buf = None
        self.geom = (0, 0, 0, 0)
        self._open()

    def metrics(self):
        u = self.u
        return (u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79))

    def _open(self):
        self.geom = self.metrics()
        x, y, w, h = self.geom
        self.hdc = self.u.GetDC(0)
        self.mem = self.g.CreateCompatibleDC(self.hdc)
        self.bmp = self.g.CreateCompatibleBitmap(self.hdc, w, h)
        self.old = self.g.SelectObject(self.mem, self.bmp)
        self.buf = ctypes.create_string_buffer(w * h * 4)
        self.bi = BITMAPINFO()
        bh = self.bi.bmiHeader
        bh.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bh.biWidth = w
        bh.biHeight = -h          # top-down
        bh.biPlanes = 1
        bh.biBitCount = 32
        bh.biCompression = 0

    def grab(self):
        with self.lock:
            if self.metrics() != self.geom:
                self.close()
                self._open()
            x, y, w, h = self.geom
            self.g.BitBlt(self.mem, 0, 0, w, h, self.hdc, x, y, self.SRCCOPY)
            self.g.GetDIBits(self.mem, self.bmp, 0, h, self.buf, ctypes.byref(self.bi), self.DIB_RGB_COLORS)
            a = np.frombuffer(self.buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3][:, :, ::-1]
            return np.ascontiguousarray(a)

    def close(self):
        try:
            if self.old:
                self.g.SelectObject(self.mem, self.old)
            if self.bmp:
                self.g.DeleteObject(self.bmp)
            if self.mem:
                self.g.DeleteDC(self.mem)
            if self.hdc:
                self.u.ReleaseDC(0, self.hdc)
        except Exception:
            pass
        self.hdc = self.mem = self.bmp = self.old = None


# --------------------------------------------------------------------- input
PUL = ctypes.POINTER(ctypes.c_ulong)


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', PUL)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', PUL)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', wt.DWORD), ('wParamL', wt.WORD), ('wParamH', wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _INPUTUNION)]


MOVE, LDOWN, LUP, RDOWN, RUP, MDOWN, MUP, WHEEL = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0020, 0x0040, 0x0800
ABSOLUTE, VIRTUALDESK = 0x8000, 0x4000
KEYUP, UNICODE, SCANCODE = 0x0002, 0x0004, 0x0008

VK = {'back': 0x08, 'tab': 0x09, 'enter': 0x0D, 'return': 0x0D, 'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12,
      'pause': 0x13, 'caps': 0x14, 'esc': 0x1B, 'escape': 0x1B, 'space': 0x20, 'pgup': 0x21, 'pgdn': 0x22,
      'end': 0x23, 'home': 0x24, 'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28, 'print': 0x2C,
      'insert': 0x2D, 'delete': 0x2E, 'del': 0x2E, 'win': 0x5B, 'lwin': 0x5B, 'apps': 0x5D,
      'num0': 0x60, 'num1': 0x61, 'num2': 0x62, 'num3': 0x63, 'num4': 0x64, 'num5': 0x65, 'num6': 0x66,
      'num7': 0x67, 'num8': 0x68, 'num9': 0x69, 'multiply': 0x6A, 'add': 0x6B, 'subtract': 0x6D,
      'decimal': 0x6E, 'divide': 0x6F, 'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74,
      'f6': 0x75, 'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B}


class Hands:
    """SendInput mouse/keyboard with human-like motion profiles."""

    def __init__(self, screen):
        self.u = ctypes.windll.user32
        self.screen = screen
        self.lock = threading.Lock()

    def _send(self, items):
        if not items:
            return
        arr = (INPUT * len(items))(*items)
        sent = self.u.SendInput(len(items), arr, ctypes.sizeof(INPUT))
        if sent != len(items):
            raise OSError('SendInput sent %d/%d (err %d)' % (sent, len(items), ctypes.get_last_error()))

    def _mi(self, flags, dx=0, dy=0, data=0):
        it = INPUT()
        it.type = 0
        it.u.mi = MOUSEINPUT(dx, dy, data, flags, 0, None)
        return it

    def _ki(self, vk=0, scan=0, flags=0):
        it = INPUT()
        it.type = 1
        it.u.ki = KEYBDINPUT(vk, scan, flags, 0, None)
        return it

    def pos(self):
        p = wt.POINT()
        self.u.GetCursorPos(ctypes.byref(p))
        return (p.x, p.y)

    def _abs(self, x, y):
        vx, vy, vw, vh = self.screen.geom
        nx = int(round((x - vx) * 65535.0 / max(1, vw - 1)))
        ny = int(round((y - vy) * 65535.0 / max(1, vh - 1)))
        return max(0, min(65535, nx)), max(0, min(65535, ny))

    def move(self, x, y, dur_ms=160, human=True):
        """Move the pointer like a hand: eased coarse approach + two micro corrections."""
        with self.lock:
            x0, y0 = self.pos()
            dx, dy = x - x0, y - y0
            dist = max(1.0, (dx * dx + dy * dy) ** 0.5)
            if not human or dist < 6:
                self.u.SetCursorPos(int(x), int(y))
                return {'from': [x0, y0], 'to': [int(x), int(y)], 'mode': 'direct'}
            steps = int(max(6, min(28, dist / 24)))
            t0 = time.perf_counter()
            for i in range(1, steps + 1):
                t = i / steps
                e = t * t * (3 - 2 * t)                      # smoothstep
                sx = x0 + dx * e + (2.5 * (1 - t) * (1 if dx >= 0 else -1))
                sy = y0 + dy * e
                self.u.SetCursorPos(int(round(sx)), int(round(sy)))
                time.sleep(max(0.001, (dur_ms / 1000.0) / steps / 2))
            self.u.SetCursorPos(int(x), int(y))
            time.sleep(0.012)
            return {'from': [x0, y0], 'to': [int(x), int(y)], 'steps': steps,
                    'ms': round((time.perf_counter() - t0) * 1000, 1)}

    def click(self, x=None, y=None, button='left', n=1, gap_ms=60, ease=False, settle_ms=25):
        """ease=False teleports the cursor instead of gliding there (~100 ms saved per
        click); keep ease=True only for hover-sensitive UI."""
        with self.lock:
            if x is not None:
                if ease:
                    self.move_unlocked(x, y)
                else:
                    self.u.SetCursorPos(int(x), int(y))
                    if settle_ms:
                        time.sleep(settle_ms / 1000.0)
            down = {'left': LDOWN, 'right': RDOWN, 'middle': MDOWN}[button]
            up = {'left': LUP, 'right': RUP, 'middle': MUP}[button]
            t0 = time.perf_counter()
            for i in range(int(n)):
                self._send([self._mi(down), self._mi(up)])
                if i + 1 < n:
                    time.sleep(gap_ms / 1000.0)
            return {'ms': round((time.perf_counter() - t0) * 1000, 2), 'n': int(n), 'button': button}

    def move_unlocked(self, x, y):
        x0, y0 = self.pos()
        dx, dy = x - x0, y - y0
        dist = max(1.0, (dx * dx + dy * dy) ** 0.5)
        if dist < 6:
            self.u.SetCursorPos(int(x), int(y))
            return
        steps = int(max(6, min(24, dist / 30)))
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            self.u.SetCursorPos(int(round(x0 + dx * e)), int(round(y0 + dy * e)))
            time.sleep(0.004)
        self.u.SetCursorPos(int(x), int(y))

    def drag(self, x1, y1, x2, y2, steps=30, ms=240, button='left'):
        with self.lock:
            down = {'left': LDOWN, 'right': RDOWN}[button]
            up = {'left': LUP, 'right': RUP}[button]
            self.move_unlocked(x1, y1)
            time.sleep(0.06)
            self._send([self._mi(down)])
            time.sleep(0.05)
            t0 = time.perf_counter()
            for i in range(1, int(steps) + 1):
                t = i / float(steps)
                e = t * t * (3 - 2 * t)
                self.u.SetCursorPos(int(round(x1 + (x2 - x1) * e)), int(round(y1 + (y2 - y1) * e)))
                time.sleep(max(0.002, (ms / 1000.0) / steps))
            time.sleep(0.05)
            self._send([self._mi(up)])
            return {'ms': round((time.perf_counter() - t0) * 1000, 1), 'from': [x1, y1], 'to': [x2, y2]}

    def key(self, *names):
        """Press a chord, e.g. key('ctrl','z') or key('enter')."""
        seqs = []
        for spec in names:
            parts = [p.strip().lower() for p in str(spec).replace('+', ' ').split() if p.strip()]
            codes = []
            for p in parts:
                if p in VK:
                    codes.append(VK[p])
                elif len(p) == 1:
                    codes.append(ord(p.upper()))
                else:
                    raise ValueError('unknown key %r' % p)
            seqs.append(codes)
        t0 = time.perf_counter()
        for codes in seqs:
            for c in codes[:-1]:
                self._send([self._ki(c)])
            self._send([self._ki(codes[-1])])
            time.sleep(0.02)
            self._send([self._ki(codes[-1], 0, KEYUP)])
            for c in reversed(codes[:-1]):
                self._send([self._ki(c, 0, KEYUP)])
            time.sleep(0.02)
        return {'ms': round((time.perf_counter() - t0) * 1000, 1), 'keys': list(names)}

    def type_text(self, text, per_char_ms=12, chunk=0):
        """per_char_ms=0 sends the whole string as ONE SendInput batch (~70 ms for 150
        chars instead of ~1.8 s of per-character sleeps). Some apps drop keystrokes that
        arrive in one burst - then pass chunk=40 to send it in 40-char bursts."""
        t0 = time.perf_counter()
        if per_char_ms <= 0:
            groups = [text] if not chunk else [text[i:i + int(chunk)] for i in range(0, len(text), int(chunk))]
            for g in groups:
                items = []
                for ch in g:
                    code = ord(ch)
                    items.append(self._ki(0, code, UNICODE))
                    items.append(self._ki(0, code, UNICODE | KEYUP))
                if items:
                    self._send(items)
                if len(groups) > 1:
                    time.sleep(0.004)
            return {'ms': round((time.perf_counter() - t0) * 1000, 1), 'chars': len(text),
                    'mode': 'batch', 'chunks': len(groups)}
        for ch in text:
            code = ord(ch)
            self._send([self._ki(0, code, UNICODE), self._ki(0, code, UNICODE | KEYUP)])
            time.sleep(per_char_ms / 1000.0)
        return {'ms': round((time.perf_counter() - t0) * 1000, 1), 'chars': len(text)}

    def scroll(self, dy=0, dx=0):
        t0 = time.perf_counter()
        if dy:
            self._send([self._mi(WHEEL, 0, 0, int(dy) & 0xFFFFFFFF)])
        if dx:
            self._send([self._mi(WHEEL, 0, 0, (int(dx) << 16) & 0xFFFFFFFF)])
        return {'ms': round((time.perf_counter() - t0) * 1000, 1)}


# ----------------------------------------------------------------------- uia
CONTROL_TYPES = {50000: 'Button', 50001: 'Calendar', 50002: 'CheckBox', 50003: 'ComboBox', 50004: 'Edit',
                 50005: 'Hyperlink', 50006: 'Image', 50007: 'ListItem', 50008: 'List', 50009: 'Menu',
                 50010: 'MenuBar', 50011: 'MenuItem', 50012: 'ProgressBar', 50013: 'RadioButton',
                 50014: 'ScrollBar', 50015: 'Slider', 50016: 'Spinner', 50017: 'StatusBar', 50018: 'Tab',
                 50019: 'TabItem', 50020: 'Text', 50021: 'ToolBar', 50022: 'ToolTip', 50023: 'Tree',
                 50024: 'TreeItem', 50025: 'Custom', 50026: 'Group', 50027: 'Thumb', 50028: 'DataGrid',
                 50029: 'DataItem', 50030: 'Document', 50031: 'SplitButton', 50032: 'Window', 50033: 'Pane',
                 50034: 'Header', 50035: 'HeaderItem', 50036: 'Table', 50037: 'TitleBar', 50038: 'Separator',
                 50039: 'SemanticZoom', 50040: 'AppBar'}

_UIA = {'w': None, 'lock': threading.Lock(), 'iunknown': None}


class _UiaWorker(threading.Thread):
    """One long-lived STA thread owns every COM/UIA object.

    The call sites keep using (UIA, client) exactly as before; `client` is a lazy proxy
    that serialises each individual COM call onto this thread with its own timeout, so a
    hung target app can never hang the actor (the stuck thread is abandoned and replaced).
    """

    def __init__(self):
        super().__init__(daemon=True, name='uia')
        self.q = queue.Queue()
        self.ready = threading.Event()
        self.UIA = None
        self.client = None
        self.err = None

    def run(self):
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)      # STA is comtypes' own default
        except Exception:
            pass
        try:
            import comtypes, comtypes.client
            comtypes.client.GetModule('UIAutomationCore.dll')
            from comtypes.gen import UIAutomationClient as UIA
            self.UIA = UIA
            self.client = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
            _UIA['iunknown'] = comtypes.IUnknown
            log('uia.ready')
        except Exception as e:
            self.err = 'UIA unavailable: %s' % e
            log('uia.fail', err=str(e))
        finally:
            self.ready.set()
        while True:
            job = self.q.get()
            if job is None:
                return
            fn, box, ev = job
            try:
                box['v'] = fn()
            except Exception as e:
                box['e'] = e
            finally:
                ev.set()

    def call(self, fn, timeout):
        ev, box = threading.Event(), {}
        self.q.put((fn, box, ev))
        if not ev.wait(timeout):
            raise TimeoutError('UIA call exceeded %.1fs (target app busy or hung)' % timeout)
        if 'e' in box:
            raise box['e']
        return box.get('v')


def _worker():
    with _UIA['lock']:
        w = _UIA['w']
        if w is None or not w.is_alive():
            w = _UiaWorker()
            _UIA['w'] = w
            w.start()
            w.ready.wait(20)
        return w


def _remote_call(fn, timeout=4.0):
    w = _worker()
    if w.err:
        raise RuntimeError(w.err)
    try:
        return w.call(fn, timeout)
    except TimeoutError:
        with _UIA['lock']:
            if _UIA['w'] is w:
                _UIA['w'] = None        # abandon the stuck thread; the next call gets a fresh one
        raise


def _wrap(v):
    iu = _UIA.get('iunknown')
    if iu is not None and isinstance(v, iu):
        return _Remote(lambda: v)
    if isinstance(v, (list, tuple)):
        return type(v)(_wrap(x) for x in v)
    if callable(v) and not isinstance(v, (str, bytes)):
        return _Remote(lambda: v)
    return v


def _unwrap(v):
    """A proxy passed as an argument to another remote call must be resolved to the real
    object first - comtypes would otherwise try to marshal the Python proxy itself."""
    return v._res() if isinstance(v, _Remote) else v


class _Remote(object):
    """Lazy handle to a COM object that lives on the UIA thread (attribute read or call)."""

    __slots__ = ('_res',)

    def __init__(self, res):
        object.__setattr__(self, '_res', res)

    def __getattr__(self, item):
        res = object.__getattribute__(self, '_res')
        return _wrap(_remote_call(lambda: getattr(res(), item)))

    def __call__(self, *a, **kw):
        res = object.__getattribute__(self, '_res')
        a2 = tuple(_unwrap(x) for x in a)
        kw2 = {k: _unwrap(v) for k, v in kw.items()}
        return _wrap(_remote_call(lambda: res()(*a2, **kw2)))

    def __repr__(self):
        return '<remote uia object>'


def uia_client():
    """(UIA module, lazily-proxied IUIAutomation).  Safe to call from any thread."""
    w = _worker()
    if w.err:
        raise RuntimeError(w.err)
    return w.UIA, _Remote(lambda: w.client)


def guarded(fn, timeout=4.0):
    """Historic name kept for the existing call sites: the proxy already serialises and
    time-limits every COM call on the UIA thread, so this is now a plain call."""
    return fn()


def el_info(el, UIA):
    try:
        r = el.CurrentBoundingRectangle
        rect = [int(r.left), int(r.top), int(r.right), int(r.bottom)]
    except Exception:
        rect = None
    def g(name):
        try:
            return getattr(el, name)
        except Exception:
            return None
    return {'name': g('CurrentName'), 'aid': g('CurrentAutomationId'), 'cls': g('CurrentClassName'),
            'type': CONTROL_TYPES.get(g('CurrentControlType'), str(g('CurrentControlType'))),
            'rect': rect, 'hwnd': g('CurrentNativeWindowHandle'), 'enabled': g('CurrentIsEnabled'),
            'offscreen': g('CurrentIsOffscreen')}


def walk(el, UIA, depth, limit, out, view):
    if depth < 0 or len(out) >= limit:
        return
    try:
        walker = uia_client()[1].RawViewWalker      # IUIAutomation.RawViewWalker (not UIA.*)
        c = walker.GetFirstChildElement(el)
    except Exception:
        return
    while c is not None and len(out) < limit:
        out.append((depth, c))
        walk(c, UIA, depth + 1, limit, out, view)
        try:
            c = walker.GetNextSiblingElement(c)
        except Exception:
            break


def uia_find(client, UIA, sel, limit=40, root=None, scope=None):
    """sel: name / name_contains / aid / cls / ctype / hwnd / point.

    scope: an hwnd to search INSIDE first. A window-scoped FindAll measures ~95 ms
    against ~1.5 s for the desktop-wide one, and a selector recorded without an hwnd
    stays portable: nothing found inside the scope falls back to the whole desktop,
    so the result is never narrower than an unscoped search.
    """
    scoped = False
    if root is None and scope and not sel.get('point') and not sel.get('hwnd'):
        el = client.ElementFromHandle(wt.HWND(int(scope)))
        if el is not None:
            root, scoped = el, True
    root = root or client.GetRootElement()
    if sel.get('point'):
        el = client.ElementFromPoint(wt.POINT(int(sel['point'][0]), int(sel['point'][1])))
        return [el] if el else []
    if sel.get('hwnd'):
        el = client.ElementFromHandle(wt.HWND(int(sel['hwnd'])))
        if el is None:
            return []
        if not any(k in sel for k in ('name', 'name_contains', 'aid', 'cls', 'ctype')):
            return [el]
        root = el
    conds = []
    if sel.get('name') is not None:
        conds.append(client.CreatePropertyCondition(UIA.UIA_NamePropertyId, sel['name']))
    if sel.get('aid') is not None:
        conds.append(client.CreatePropertyCondition(UIA.UIA_AutomationIdPropertyId, sel['aid']))
    if sel.get('cls') is not None:
        conds.append(client.CreatePropertyCondition(UIA.UIA_ClassNamePropertyId, sel['cls']))
    if sel.get('ctype') is not None:
        ct = sel['ctype']
        if isinstance(ct, str):
            ct = {v: k for k, v in CONTROL_TYPES.items()}.get(ct)
        conds.append(client.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, ct))
    cond = None
    for c in conds:
        cond = c if cond is None else client.CreateAndCondition(cond, c)
    if cond is None:
        return []
    found = root.FindAll(4, cond)          # TreeScope_Descendants = 4 (NOT 1: 1 is Element!)
    els = [found.GetElement(i) for i in range(min(found.Length, limit))]
    if sel.get('name_contains'):
        needle = sel['name_contains']
        els = [e for e in els if needle in (el_info(e, UIA)['name'] or '')]
    if not els and scoped:                 # never narrower than the unscoped search
        return uia_find(client, UIA, sel, limit=limit, root=client.GetRootElement())
    return els


def windows(client, UIA, limit=60):
    """Top-level windows (walk the desktop root, keep real windows)."""
    out = []
    try:
        walker = client.RawViewWalker            # IUIAutomation.RawViewWalker (not UIA.*)
        root = client.GetRootElement()
        c = walker.GetFirstChildElement(root)
        while c is not None and len(out) < limit:
            info = el_info(c, UIA)
            if info['type'] == 'Window' or (info['rect'] and info['rect'][2] - info['rect'][0] > 120):
                out.append({'hwnd': info['hwnd'], 'name': info['name'], 'cls': info['cls'],
                            'type': info['type'], 'rect': info['rect']})
            try:
                c = walker.GetNextSiblingElement(c)
            except Exception:
                break
    except Exception:
        pass
    return out


WINDOW_CACHE: dict = {}          # title substring -> hwnd (see resolve_hwnd)


def _hwnd_valid(hwnd, title_contains=None):
    """Is this still a live window, and does its title still match? Two Win32 calls
    (microseconds) instead of the ~1.2 s UIA desktop walk."""
    u = ctypes.windll.user32
    hwnd = int(hwnd or 0)
    if not hwnd or not u.IsWindow(wt.HWND(hwnd)):
        return False
    if not title_contains:
        return True
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(wt.HWND(hwnd), buf, 512)
    # Shell-hosted apps (Flutter/Electron/UWP) often keep an EMPTY Win32 title while
    # UIA reports a name, so an empty title must not invalidate a UIA-resolved entry.
    return (not buf.value) or (title_contains in buf.value)


def resolve_hwnd(hwnd=0, title_contains=None, limit=80, timeout=4.0):
    """hwnd, or the first window whose UIA name contains title_contains.

    The UIA walk is the single most expensive thing a step can do (~1.2 s), and a run
    or a macro replay repeats it for EVERY input step just to re-raise the target.
    Resolve once, then re-validate cheaply for as long as the window lives.
    """
    hwnd = int(hwnd or 0)
    if hwnd:
        return hwnd
    if not title_contains:
        return 0
    hit = WINDOW_CACHE.get(title_contains)
    if hit and _hwnd_valid(hit, title_contains):
        return int(hit)
    UIA, client = uia_client()
    for w in guarded(lambda: windows(client, UIA, limit), timeout):
        if title_contains in (w.get('name') or ''):
            WINDOW_CACHE[title_contains] = int(w['hwnd'])
            return int(w['hwnd'])
    WINDOW_CACHE.pop(title_contains, None)
    return 0


# --------------------------------------------------------------- vision (fallback)
def gray(a):
    return (a[:, :, 0] * 0.299 + a[:, :, 1] * 0.587 + a[:, :, 2] * 0.114).astype(np.float32)


def _zncc(SW, tn):
    """Normalised cross-correlation of every window in SW (ny, nx, th, tw) against a
    normalised template tn - fully vectorised, so the Python loop count is zero."""
    mu = SW.mean(axis=(-2, -1), keepdims=True)
    sd = SW.std(axis=(-2, -1), keepdims=True)
    return 1.0 - ((SW - mu) / (sd + 1e-6) * tn).mean(axis=(-2, -1))


def _scan(F, T, tn, step):
    """Best (score, x, y) over a frame at the given stride, or None if it does not fit."""
    th, tw = T.shape
    H, W = F.shape
    if th > H or tw > W:
        return []
    SW = sliding_window_view(F, (th, tw))[::step, ::step]
    sc = _zncc(SW, tn)
    flat = np.argsort(sc, axis=None)[:8]
    nx = sc.shape[1]
    return [(float(sc.ravel()[i]), int(i % nx) * step, int(i // nx) * step) for i in flat]


def match_template(frame, tmpl, region=None, thr=0.18, maxhits=5, ds=4):
    """Three-stage pyramid: stride-search on a ds-downsampled frame, then ds/2, then full
    resolution around the survivors.  Vectorised, so a 2560x1600 search costs ~100 ms."""
    f = frame
    ox, oy = 0, 0
    if region:
        x1, y1, x2, y2 = [int(v) for v in region]
        f = frame[y1:y2, x1:x2]
        ox, oy = x1, y1
    F = gray(f)
    T = gray(tmpl) if tmpl.ndim == 3 else tmpl.astype(np.float32)
    th, tw = T.shape
    if th > F.shape[0] or tw > F.shape[1]:
        return []
    Tn = (T - T.mean()) / (T.std() + 1e-6)
    cands = []
    if ds > 1 and min(th, tw) >= ds * 4:
        Fs, Ts = F[::ds, ::ds], T[::ds, ::ds]
        cands = _scan(Fs, Ts, (Ts - Ts.mean()) / (Ts.std() + 1e-6), max(2, min(Ts.shape) // 3))
        cands = [(s, x * ds, y * ds) for s, x, y in cands]
    if not cands:
        cands = _scan(F, T, Tn, max(2, min(th, tw) // 3))
        cands = [(s, x, y) for s, x, y in cands]
    # middle level (ds/2) then full resolution, each confined to a small window
    for level, pad in ((max(2, ds // 2), None), (1, 2)):
        Fl, Tl = (F[::level, ::level], T[::level, ::level]) if level > 1 else (F, T)
        if min(Tl.shape) < 3:
            Fl, Tl = F, T
            level = 1
        thl, twl = Tl.shape
        tnl = (Tl - Tl.mean()) / (Tl.std() + 1e-6)
        nxt = []
        for score, x, y in cands:
            xs, ys = x // level, y // level
            p = pad if pad is not None else max(2, level)
            y1 = max(0, ys - p); x1 = max(0, xs - p)
            y2 = min(Fl.shape[0] - thl, ys + p)
            x2 = min(Fl.shape[1] - twl, xs + p)
            if y2 < y1 or x2 < x1:
                nxt.append((score, x, y))
                continue
            sub = Fl[y1:y2 + thl, x1:x2 + twl]
            if sub.shape[0] < thl or sub.shape[1] < twl:
                nxt.append((score, x, y))
                continue
            SW = sliding_window_view(sub, (thl, twl))
            sc = _zncc(SW, tnl)
            i = int(np.argmin(sc))
            nxt.append((float(sc.ravel()[i]), (x1 + int(i % sc.shape[1])) * level,
                        (y1 + int(i // sc.shape[1])) * level))
        cands = nxt
    hits, seen = [], set()
    for score, x, y in sorted(cands):
        key = (x // max(4, tw // 3), y // max(4, th // 3))
        if key in seen:
            continue
        seen.add(key)
        hits.append({'score': round(score, 4), 'rect': [x + ox, y + oy, tw, th],
                     'center': [x + ox + tw // 2, y + oy + th // 2]})
    return [h for h in hits if h['score'] <= thr][:maxhits]


def find_color(frame, rgb, tol=40, min_area=80, region=None):
    """Every 4-connected blob of a colour, in one numpy pass + a run-based union-find."""
    a = frame
    ox, oy = 0, 0
    if region:
        x1, y1, x2, y2 = [int(v) for v in region]
        a = frame[y1:y2, x1:x2]
        ox, oy = x1, y1
    m = (np.abs(a.astype(np.int16) - np.array(rgb, dtype=np.int16)).max(axis=2) <= tol)
    if not m.any():
        return []
    parent = {}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    runs = []
    prev = []                       # runs of the row above: (start, end, index)
    for y in range(m.shape[0]):
        row = m[y]
        if not row.any():
            prev = []
            continue
        d = np.diff(row.astype(np.int8))
        starts = list(np.where(d == 1)[0] + 1)
        ends = list(np.where(d == -1)[0] + 1)
        if row[0]:
            starts = [0] + starts
        if row[-1]:
            ends = ends + [row.size]
        cur = []
        for s, e in zip(starts, ends, strict=True):
            idx = len(runs)
            parent[idx] = idx
            for s2, e2, j in prev:              # only the row above can be 4-adjacent
                if s2 < e and s < e2:
                    union(idx, j)
            runs.append((s, e, y))
            cur.append((s, e, idx))
        prev = cur
    groups = {}
    for i, (s, e, y) in enumerate(runs):
        groups.setdefault(find(i), []).append((s, e, y))
    out = []
    for g in groups.values():
        x1 = min(r[0] for r in g); x2 = max(r[1] for r in g)
        y1 = min(r[2] for r in g); y2 = max(r[2] for r in g) + 1
        area = sum(r[1] - r[0] for r in g)
        if area >= min_area:
            out.append({'rect': [x1 + ox, y1 + oy, x2 - x1, y2 - y1], 'area': int(area),
                        'center': [(x1 + x2) // 2 + ox, (y1 + y2) // 2 + oy]})
    out.sort(key=lambda h: -h['area'])
    return out


# -------------------------------------------------------------------- runtime
class Actor:
    def __init__(self):
        self.screen = Screen()
        self.hands = Hands(self.screen)
        self.frame = None
        self.frame_t = 0.0
        self.frame_id = 0
        self.watch = {'on': False, 'fps': 6, 'changes': 0}
        self.t0 = time.time()

    def ensure(self, max_age_ms=250):
        age = (time.time() - self.frame_t) * 1000
        if self.frame is None or age > max_age_ms:
            self.frame = self.screen.grab()
            self.frame_t = time.time()
            self.frame_id += 1
        return self.frame

    def shot(self, path=None, region=None):
        f = self.ensure(0)
        res = {'size': [f.shape[1], f.shape[0]], 'geom': list(self.screen.geom), 'frame_id': self.frame_id}
        if region:
            x1, y1, x2, y2 = [int(v) for v in region]
            f = f[y1:y2, x1:x2]
            res['crop'] = [x1, y1, x2 - x1, y2 - y1]
        if path:
            from PIL import Image
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            Image.fromarray(f).save(path)
            res['path'] = path
        return res

    def wait_for(self, cond, timeout_ms=5000, poll_ms=60):
        """cond: change(region,thr) | image(path|b64,thr,region) | uia(selector) | gone(selector)"""
        t0 = time.perf_counter()
        kind = cond.get('kind', 'change')
        base = None
        if kind == 'change':
            base = self.ensure(0).copy()
        while True:
            elapsed = (time.perf_counter() - t0) * 1000
            if elapsed > timeout_ms:
                return {'ok': False, 'reason': 'timeout', 'ms': round(elapsed, 1)}
            time.sleep(poll_ms / 1000.0)
            try:
                if kind == 'change':
                    cur = self.ensure(0)
                    x1, y1, x2, y2 = cond.get('region') or [0, 0, cur.shape[1], cur.shape[0]]
                    d = np.abs(cur[y1:y2, x1:x2].astype(np.int16) - base[y1:y2, x1:x2].astype(np.int16)).sum(axis=2)
                    ratio = float((d > 60).mean())
                    if ratio >= cond.get('thr', 0.01):
                        return {'ok': True, 'ms': round(elapsed, 1), 'ratio': round(ratio, 4)}
                elif kind == 'image':
                    from PIL import Image
                    tmpl = np.asarray(Image.open(cond['path']).convert('RGB'))
                    hits = match_template(self.ensure(0), tmpl, cond.get('region'), cond.get('thr', 0.18), 1)
                    if hits:
                        return {'ok': True, 'ms': round(elapsed, 1), 'hit': hits[0]}
                elif kind in ('uia', 'gone'):
                    UIA, client = uia_client()
                    els = guarded(lambda client=client, UIA=UIA: uia_find(
                        client, UIA, cond['selector'], 5, scope=cond.get('scope_hwnd')), 4.0)
                    if (kind == 'uia' and els) or (kind == 'gone' and not els):
                        return {'ok': True, 'ms': round(elapsed, 1), 'n': len(els)}
            except Exception as e:
                return {'ok': False, 'reason': str(e), 'ms': round(elapsed, 1)}

    def start_watch(self, on=True, fps=6):
        self.watch['on'] = bool(on)
        self.watch['fps'] = max(1, min(20, int(fps)))
        if on and not self.watch.get('thread'):
            def loop():
                last = None
                while self.watch['on']:
                    f = self.screen.grab()
                    if last is not None:
                        d = np.abs(f.astype(np.int16) - last.astype(np.int16)).sum(axis=2)
                        if float((d > 60).mean()) > 0.002:
                            self.watch['changes'] += 1
                    last = f
                    self.frame = f
                    self.frame_t = time.time()
                    self.frame_id += 1
                    time.sleep(1.0 / self.watch['fps'])
            t = threading.Thread(target=loop, daemon=True)
            self.watch['thread'] = t
            t.start()
        return {'watch': self.watch['on'], 'fps': self.watch['fps']}


A = None          # the singleton Actor


def resolve_target(t, actor, scope=None):
    """target: {xy:[x,y]} | {uia:{selector,index}} | {image:'path'} | {color:[r,g,b]}

    scope: hwnd to search first for a `uia` target (see uia_find). A run or a macro
    replay passes the front window down, so a selector recorded WITHOUT an hwnd stays
    portable across app restarts and still resolves in ~95 ms instead of ~1.5 s.
    """
    if not t:
        raise ValueError('no target')
    if 'xy' in t:
        return [int(t['xy'][0]), int(t['xy'][1])], {'how': 'xy'}
    if 'uia' in t:
        UIA, client = uia_client()
        sel = t['uia'].get('selector', t['uia'])
        idx = int(t['uia'].get('index', 0))
        els = guarded(lambda: uia_find(client, UIA, sel, scope=scope or t.get('scope_hwnd')), 4.0)
        if len(els) <= idx:
            raise LookupError('uia target not found (matched %d): %s' % (len(els), sel))
        info = el_info(els[idx], UIA)
        if not info['rect']:
            raise LookupError('uia element has no rectangle: %s' % info)
        x1, y1, x2, y2 = info['rect']
        return [(x1 + x2) // 2, (y1 + y2) // 2], {'how': 'uia', 'element': info}
    if 'image' in t:
        from PIL import Image
        tmpl = np.asarray(Image.open(t['image']).convert('RGB'))
        hits = match_template(actor.ensure(0), tmpl, t.get('region'), t.get('thr', 0.18), 1)
        if not hits:
            raise LookupError('image target not found: %s' % t['image'])
        return hits[0]['center'], {'how': 'image', 'hit': hits[0]}
    if 'color' in t:
        hits = find_color(actor.ensure(0), t['color'], t.get('tol', 40), t.get('min_area', 80), t.get('region'))
        if not hits:
            raise LookupError('colour target not found: %s' % t['color'])
        return hits[0]['center'], {'how': 'color', 'hit': hits[0]}
    raise ValueError('unknown target %r' % t)


# ------------------------------------------------------------------------ ops
OPS: dict = {}


def op(name):
    def deco(fn):
        OPS[name] = fn
        return fn
    return deco


@op('ping')
def o_ping(req):
    return {'ok': True, 'pid': os.getpid(), 'uptime_s': round(time.time() - A.t0, 1),
            'geom': list(A.screen.geom), 'dpi': set_dpi_awareness(), 'py': sys.version.split()[0]}


@op('shot')
def o_shot(req):
    return dict(ok=True, **A.shot(req.get('path'), req.get('region')))


@op('save')
def o_save(req):
    return dict(ok=True, **A.shot(req['path'], req.get('region')))


@op('find')
def o_find(req):
    f = A.ensure(req.get('max_age_ms', 150))
    if req.get('image') or req.get('template'):
        from PIL import Image
        src = req.get('image') or req['template']
        tmpl = np.asarray(Image.open(src).convert('RGB'))
        rc = req.get('tmpl_rect')          # take the template from a rect inside that image
        if rc:
            x, y, w, h = [int(v) for v in rc]
            tmpl = tmpl[y:y + h, x:x + w]
        hits = match_template(f, tmpl, req.get('region'), req.get('thr', 0.18), req.get('max', 5))
    elif req.get('color'):
        hits = find_color(f, req['color'], req.get('tol', 40), req.get('min_area', 80), req.get('region'))
    elif req.get('uia'):
        UIA, client = uia_client()
        els = guarded(lambda: uia_find(client, UIA, req['uia'], req.get('max', 40),
                                      scope=req.get('scope_hwnd')), req.get('timeout', 4.0))
        hits = [el_info(e, UIA) for e in els]
    else:
        raise ValueError('find needs image|color|uia')
    return {'ok': True, 'n': len(hits), 'hits': hits, 'frame_id': A.frame_id}


@op('click')
def o_click(req):
    t0 = time.perf_counter()
    fr = _maybe_front(req)
    pos, how = resolve_target(req.get('target'), A, req.get('scope_hwnd'))
    r = A.hands.click(pos[0], pos[1], req.get('button', 'left'), int(req.get('n', 1)), req.get('gap_ms', 60),
                      req.get('ease', False), req.get('settle_ms', 25))
    out = {'ok': True, 'at': pos, 'how': how, 'click': r, 'ms': round((time.perf_counter() - t0) * 1000, 1)}
    if fr:
        out['front'] = fr
    return out


@op('move')
def o_move(req):
    fr = _maybe_front(req)
    if req.get('target'):
        pos, how = resolve_target(req['target'], A, req.get('scope_hwnd'))
    else:
        pos, how = [int(req['x']), int(req['y'])], {'how': 'xy'}
    out = {'ok': True, 'at': pos, 'how': how,
           'move': A.hands.move(pos[0], pos[1], req.get('dur_ms', 160), req.get('human', False))}
    if fr:
        out['front'] = fr
    return out


@op('drag')
def o_drag(req):
    fr = _maybe_front(req)
    if req.get('from'):
        p1, _ = resolve_target(req['from'], A, req.get('scope_hwnd'))
    else:
        p1 = [int(req['x1']), int(req['y1'])]
    if req.get('to'):
        p2, _ = resolve_target(req['to'], A, req.get('scope_hwnd'))
    else:
        p2 = [int(req['x2']), int(req['y2'])]
    r = A.hands.drag(p1[0], p1[1], p2[0], p2[1], req.get('steps', 30), req.get('ms', 240), req.get('button', 'left'))
    out = {'ok': True, 'from': p1, 'to': p2, 'drag': r}
    if fr:
        out['front'] = fr
    return out


@op('type')
def o_type(req):
    fr = _maybe_front(req)
    out = dict(ok=True, **A.hands.type_text(req['text'], req.get('per_char_ms', 12), req.get('chunk', 0)))
    if fr:
        out['front'] = fr
    return out


@op('key')
def o_key(req):
    fr = _maybe_front(req)
    keys = req.get('keys') or [req['key']]
    if isinstance(keys, str):
        keys = [keys]
    out = dict(ok=True, **A.hands.key(*keys))
    if fr:
        out['front'] = fr
    return out


@op('scroll')
def o_scroll(req):
    return dict(ok=True, **A.hands.scroll(req.get('dy', 0), req.get('dx', 0)))


@op('uia')
def o_uia(req):
    UIA, client = uia_client()
    what = req.get('what', 'tree')
    to = req.get('timeout', 4.0)
    if what == 'windows':
        return {'ok': True, 'windows': guarded(lambda: windows(client, UIA, req.get('max', 60)), to)}
    if what == 'find':
        els = guarded(lambda: uia_find(client, UIA, req['selector'], req.get('max', 40),
                                       scope=req.get('scope_hwnd')), to)
        return {'ok': True, 'n': len(els), 'hits': [el_info(e, UIA) for e in els]}
    if what == 'point':
        el = guarded(lambda: client.ElementFromPoint(wt.POINT(int(req['x']), int(req['y']))), to)
        return {'ok': True, 'hit': el_info(el, UIA) if el else None}
    if what == 'tree':
        root = None
        if req.get('hwnd'):
            root = guarded(lambda: client.ElementFromHandle(wt.HWND(int(req['hwnd']))), to)
        elif req.get('selector'):
            els = guarded(lambda: uia_find(client, UIA, req['selector'], 1,
                                           scope=req.get('scope_hwnd')), to)
            root = els[0] if els else None
        if root is None:
            root = guarded(lambda: client.GetRootElement(), to)
        if req.get('compact'):
            return {'ok': True, **_win_elements(req)}
        out = []
        guarded(lambda: walk(root, UIA, int(req.get('depth', 3)), int(req.get('limit', 300)), out, req.get('view', 'raw')), to)
        return {'ok': True, 'n': len(out), 'tree': [dict(depth=d, **el_info(e, UIA)) for d, e in out]}
    if what in ('invoke', 'focus', 'setvalue'):
        els = guarded(lambda: uia_find(client, UIA, req['selector'], req.get('max', 5),
                                       scope=req.get('scope_hwnd')), to)
        idx = int(req.get('index', 0))
        if len(els) <= idx:
            raise LookupError('no element %d for %s' % (idx, req['selector']))
        el = els[idx]
        if what == 'focus':
            guarded(lambda: el.SetFocus(), to)
        elif what == 'setvalue':
            pat = guarded(lambda: el.GetCurrentPattern(UIA.UIA_ValuePatternId), to)
            guarded(lambda: pat.SetValue(req['value']), to)
        else:
            pat = guarded(lambda: el.GetCurrentPattern(UIA.UIA_InvokePatternId), to)
            guarded(lambda: pat.Invoke(), to)
        return {'ok': True, 'did': what, 'element': el_info(el, UIA)}
    raise ValueError('unknown uia what=%r' % what)


@op('window')
def o_window(req):
    """Window management - the missing half of every SendInput script.

    DSH (or any other app) can steal the foreground at any moment, so keystrokes
    land in the wrong window. Fix: raise the target, work, then drop it again.

      {"op":"window","mode":"front","hwnd":123}      raise + activate (once)
      {"op":"window","mode":"top","hwnd":123}        pin TOPMOST (while working)
      {"op":"window","mode":"untop","hwnd":123}      unpin
      {"op":"window","mode":"info","hwnd":123}       rect/visible/iconic/foreground
      max | min | restore | close | move(x,y,w,h) | title_contains:"计算器"
    """
    u = ctypes.windll.user32
    mode = req.get('mode', 'front')
    hwnd = int(req.get('hwnd') or 0)
    if not hwnd and req.get('title_contains'):
        hwnd = resolve_hwnd(title_contains=req['title_contains'], limit=80, timeout=4.0)
    if not hwnd:
        raise LookupError('window: pass hwnd= or title_contains=')

    def info():
        r = wt.RECT()
        u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(wt.HWND(hwnd), buf, 512)
        return dict(hwnd=hwnd, title=buf.value,
                    rect=[r.left, r.top, r.right, r.bottom],
                    visible=bool(u.IsWindowVisible(wt.HWND(hwnd))),
                    iconic=bool(u.IsIconic(wt.HWND(hwnd))),
                    foreground=int(u.GetForegroundWindow()),
                    is_foreground=int(u.GetForegroundWindow()) == hwnd)

    if mode == 'info':
        return {'ok': True, 'mode': 'info', **info()}
    if mode == 'close':
        u.PostMessageW(wt.HWND(hwnd), 0x0010, 0, 0)                 # WM_CLOSE
        for k, v in list(WINDOW_CACHE.items()):                     # a dead window must
            if int(v) == hwnd:                                      # not stay cached
                WINDOW_CACHE.pop(k, None)
        return {'ok': True, 'mode': 'close', **info()}
    if mode == 'max':
        u.ShowWindow(wt.HWND(hwnd), 3)
        return {'ok': True, 'mode': 'max', **info()}
    if mode == 'min':
        u.ShowWindow(wt.HWND(hwnd), 6)
        return {'ok': True, 'mode': 'min', **info()}
    if mode == 'restore':
        u.ShowWindow(wt.HWND(hwnd), 9)
        return {'ok': True, 'mode': 'restore', **info()}
    if mode == 'move':
        u.SetWindowPos(wt.HWND(hwnd), wt.HWND(0), int(req.get('x', 0)), int(req.get('y', 0)),
                       int(req.get('w', 0)), int(req.get('h', 0)), 0x0004 | 0x0010)   # NOZORDER|NOACTIVATE
        return {'ok': True, 'mode': 'move', **info()}
    if mode in ('top', 'untop'):
        after = -1 if mode == 'top' else -2                         # HWND_TOPMOST / HWND_NOTOPMOST
        u.SetWindowPos(wt.HWND(hwnd), wt.HWND(after), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        return {'ok': True, 'mode': mode, **info()}

    # front: un-minimise, pin topmost, attach to the foreground thread queue so
    # SetForegroundWindow is allowed, activate, then drop topmost again.
    u.ShowWindow(wt.HWND(hwnd), 9)                                   # SW_RESTORE
    u.SetWindowPos(wt.HWND(hwnd), wt.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
    fg = u.GetForegroundWindow()
    tid_fg = u.GetWindowThreadProcessId(wt.HWND(fg), None)
    tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(u.AttachThreadInput(wt.DWORD(tid_fg), wt.DWORD(tid_me), True))
    try:
        u.BringWindowToTop(wt.HWND(hwnd))
        u.SetForegroundWindow(wt.HWND(hwnd))
        u.SetActiveWindow(wt.HWND(hwnd))
    finally:
        if attached:
            u.AttachThreadInput(wt.DWORD(tid_fg), wt.DWORD(tid_me), False)
    u.SetWindowPos(wt.HWND(hwnd), wt.HWND(-2), 0, 0, 0, 0, 0x0001 | 0x0002)
    return {'ok': True, 'mode': 'front', **info()}


# ------------------------------------------------------------------------ launch
def _pid_windows(pid):
    """Visible titled top-level windows of one process: [(hwnd, title), ...]."""
    u = ctypes.windll.user32
    out = []

    def cb(hwnd, _lparam):
        h = wt.HWND(hwnd)
        p = ctypes.c_ulong()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == int(pid) and u.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(h, buf, 512)
            if buf.value:
                out.append((int(hwnd or 0), buf.value))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def launch_app(spec):
    """Start a program and, if asked, block until its window exists.

    Shared by the `launch` op and by `macro run`, so a recorded skill can bring up the app
    it needs instead of requiring you to open it first.  "The app did not show up" is a
    normal reply (ok:false, with the pid), not an exception, so a run can trace it.
    """
    path = str(spec.get('path') or spec.get('exe') or '').strip()
    if not path:
        raise ValueError('launch: pass path= (an .exe, a .lnk/.bat, or shell:appsFolder\\<AUMID>)')
    tail = spec.get('args')
    if isinstance(tail, (list, tuple)):
        argv = [path] + [str(x) for x in tail]
    elif isinstance(tail, str) and tail.strip():
        argv = [path] + shlex.split(tail)
    else:
        argv = [path]
    timeout = float(spec.get('timeout_ms', 20000))
    wait = str(spec.get('wait') or spec.get('title_contains') or '')
    by_pid = bool(spec.get('wait_pid'))
    if wait and not spec.get('force'):      # idempotent by default: a replay that runs twice
        open_now = resolve_hwnd(title_contains=wait, limit=80, timeout=2.0)   # must not start
        if open_now:                                                          # two instances
            out = {'ok': True, 'path': path, 'how': 'already running', 'pid': 0, 'hwnd': open_now,
                   'title': (_win_info(open_now) or {}).get('title', ''), 'waited_ms': 0.0,
                   'skipped': 'a window matching %r is already open (force: true starts another)'
                              % wait}
            if spec.get('front', True):
                out['front'] = _front(open_now)
            return out
    t0 = time.perf_counter()
    pid, hwnd = 0, 0
    if path.lower().startswith('shell:'):
        subprocess.Popen(['explorer.exe', path], close_fds=True)   # only explorer starts a UWP app
        how = 'explorer.exe ' + path
    elif path.lower().endswith('.exe'):
        proc = subprocess.Popen(argv, cwd=spec.get('cwd') or None, close_fds=True)
        pid, how = proc.pid, ' '.join(argv)
    else:                                                          # .lnk / .bat / document
        os.startfile(path)
        how = 'startfile ' + path
    if wait:
        deadline = t0 + timeout / 1000.0
        while time.perf_counter() < deadline:
            hwnd = resolve_hwnd(title_contains=wait, limit=80, timeout=2.0)
            if hwnd:
                break
            time.sleep(0.15)
    elif by_pid and pid:
        deadline = t0 + timeout / 1000.0
        while time.perf_counter() < deadline:
            got = _pid_windows(pid)
            if got:
                hwnd = got[0][0]
                break
            time.sleep(0.15)
    out = {'ok': True, 'path': path, 'how': how, 'pid': pid, 'hwnd': hwnd, 'title': '',
           'waited_ms': round((time.perf_counter() - t0) * 1000, 1)}
    if hwnd:
        info = _win_info(hwnd) or {}
        out['title'] = info.get('title', '')
        out['rect'] = info.get('rect')
        if spec.get('front', True):
            out['front'] = _front(hwnd)
    elif wait or (by_pid and pid):
        out['ok'] = False
        out['error'] = 'launch: %s did not open a window within %d ms' % (
            ('no window titled %r' % wait) if wait else ('pid %d' % pid), int(timeout))
    return out


@op('launch')
def o_launch(req):
    """Start a program and wait for its window - the missing first step of a recorded skill.

      {"op":"launch","path":"C:\\\\Program Files\\\\App\\\\app.exe","args":["-x"],"wait":"App"}
      {"op":"launch","path":"shell:appsFolder\\\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
       "wait":"计算器"}
      {"op":"launch","path":"C:\\\\Apps\\\\app.lnk"}          .lnk / .bat / 文档走 shell 打开

    `wait` is a substring of the window name (UIA): the call blocks until that window
    exists, so the step after it can click straight into it.  `wait_pid: true` waits for
    any window of the started process instead.  With neither, it returns as soon as the
    process exists - that is the right call for a launcher that opens its own window.

    Idempotent: if a window matching `wait` is already open, that window is reused and
    nothing new is started (`force: true` starts another instance anyway).  A recording
    that begins with `launch` therefore replays correctly whether or not the app is open.
    """
    return launch_app(req)


@op('wait_for')
def o_wait(req):
    return A.wait_for(req.get('cond', {}), req.get('timeout_ms', 5000), req.get('poll_ms', 60))


@op('watch')
def o_watch(req):
    return {'ok': True, **A.start_watch(req.get('on', True), req.get('fps', 6))}


# ------------------------------------------------------ fast loop: state / probe
def _win_info(hwnd):
    u = ctypes.windll.user32
    hwnd = int(hwnd or 0)
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(wt.HWND(hwnd), buf, 512)
    cls = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(wt.HWND(hwnd), cls, 256)
    r = wt.RECT()
    u.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    pid = ctypes.c_ulong()
    u.GetWindowThreadProcessId(wt.HWND(hwnd), ctypes.byref(pid))
    return {'hwnd': hwnd, 'title': buf.value, 'cls': cls.value,
            'rect': [int(r.left), int(r.top), int(r.right), int(r.bottom)],
            'pid': int(pid.value), 'visible': bool(u.IsWindowVisible(wt.HWND(hwnd)))}


def _front(hwnd=None, title_contains=None, top=True, timeout=4.0):
    r = OPS['window']({'op': 'window', 'mode': 'top' if top else 'front', 'hwnd': hwnd,
                       'title_contains': title_contains, 'timeout': timeout})
    return {k: r.get(k) for k in ('hwnd', 'title', 'foreground', 'is_foreground', 'mode') if k in r}


def _maybe_front(req):
    """Input ops may carry front=<hwnd> or front_title=<substring>: raise (and pin) the
    target first, so DSH stealing the foreground cannot eat the keystrokes."""
    if req.get('front') is None and not req.get('front_title'):
        return None
    return _front(req.get('front'), req.get('front_title'), req.get('front_top', True))


def _win_elements(req):
    """Flat list of the NAMED elements of one window - the compact replacement for a
    300 KB tree dump."""
    UIA, client = uia_client()
    to = req.get('timeout', 5.0)
    hwnd = req.get('hwnd') or ctypes.windll.user32.GetForegroundWindow()
    root = guarded(lambda: client.ElementFromHandle(wt.HWND(int(hwnd))), to) if hwnd \
        else guarded(lambda: client.GetRootElement(), to)
    seen = []
    guarded(lambda: walk(root, UIA, int(req.get('depth', 12)), int(req.get('scan', 900)), seen, 'raw'), to)
    needle, clsf = req.get('name_contains'), req.get('cls_contains')
    items = []
    for d, e in seen:
        i = el_info(e, UIA)
        if not i['name'] or not i['rect']:
            continue
        if needle and needle not in i['name']:
            continue
        if clsf and clsf not in (i['cls'] or ''):
            continue
        items.append({'d': d, 'name': i['name'][:70], 'aid': i['aid'], 'type': i['type'], 'rect': i['rect']})
    lim = int(req.get('limit', 60))
    return {'scanned': len(seen), 'n': len(items), 'items': items[:lim]}


@op('state')
def o_state(req):
    """One call = the whole desktop digest, small enough to read on every step."""
    t0 = time.perf_counter()
    out = {'ok': True, 'foreground': _win_info(ctypes.windll.user32.GetForegroundWindow())}
    if req.get('windows', True):
        UIA, client = uia_client()
        to = req.get('timeout', 4.0)
        ws = guarded(lambda: windows(client, UIA, req.get('max', 40)), to)
        out['windows'] = [{'hwnd': w['hwnd'], 'name': (w['name'] or '')[:70], 'rect': w['rect']}
                          for w in ws if w.get('rect') and w['rect'][2] > w['rect'][0]]
    if req.get('uia'):
        out['elements'] = _win_elements(req)
    out['ms'] = round((time.perf_counter() - t0) * 1000, 1)
    return out


_PROBE: dict = {}


def _top_colors(sub, n):
    q = (((sub[:, :, 0].astype(np.int32) >> 3) << 10) | ((sub[:, :, 1].astype(np.int32) >> 3) << 5)
         | (sub[:, :, 2].astype(np.int32) >> 3))
    cnt = np.bincount(q.ravel(), minlength=32768)
    out = []
    for b in np.argsort(cnt)[::-1][:n]:
        if cnt[b] == 0:
            continue
        ys, xs = np.nonzero(q == int(b))
        out.append({'rgb': [int((b >> 10) << 3) + 4, int(((b >> 5) & 31) << 3) + 4, int((b & 31) << 3) + 4],
                    'n': int(cnt[b]), 'bbox': [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]})
    return out


@op('probe')
def o_probe(req):
    """Pixel bookkeeping inside the actor: no PNG round trip, no PIL in the caller.

      mode=colors (top colours) | grid (colour cells) | ink (ASCII map) | mark | diff
    'mark' stores the region as the baseline, 'diff' compares against it (changed px,
    ratio, bbox) - one tiny call answers "did my click do anything?".
    """
    t0 = time.perf_counter()
    f = A.ensure(req.get('max_age_ms', 0))
    x1, y1, x2, y2 = [int(v) for v in (req.get('region') or [0, 0, f.shape[1], f.shape[0]])]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(f.shape[1], x2), min(f.shape[0], y2)
    sub = f[y1:y2, x1:x2]
    out = {'ok': True, 'region': [x1, y1, x2, y2], 'px': int(sub.size // 3), 'frame_id': A.frame_id}
    mode = req.get('mode', 'colors')
    if mode == 'mark':
        _PROBE[(x1, y1, x2, y2)] = sub.copy()
        out['marked'] = True
        out['top'] = _top_colors(sub, int(req.get('top', 3)))
    elif mode == 'diff':
        base = _PROBE.get((x1, y1, x2, y2))
        if base is None or base.shape != sub.shape:
            _PROBE[(x1, y1, x2, y2)] = sub.copy()
            out.update(first=True, changed_px=0, pct=0.0)
        else:
            d = np.abs(sub.astype(np.int16) - base.astype(np.int16)).sum(axis=2)
            m = d > int(req.get('thr', 60))
            n = int(m.sum())
            out.update(changed_px=n, pct=round(n / max(1, m.size), 4))
            if n:
                ys, xs = np.nonzero(m)
                out['bbox'] = [x1 + int(xs.min()), y1 + int(ys.min()), x1 + int(xs.max()) + 1, y1 + int(ys.max()) + 1]
    elif mode == 'ink':
        cols, rows = [int(v) for v in (req.get('grid') or [64, 22])]
        ramp = ' .:-=+*#%@'
        bg = _top_colors(sub, 1)[0]['rgb']
        dist = np.abs(sub.astype(np.int16) - np.array(bg, dtype=np.int16)).sum(axis=2)
        mask = (dist > int(req.get('tol', 60))).astype(np.float32)
        ch, cw = max(1, sub.shape[0] // rows), max(1, sub.shape[1] // cols)
        mm = mask[:rows * ch, :cols * cw].reshape(rows, ch, cols, cw).mean(axis=(1, 3))
        out.update(bg=bg, cell=[cw, ch],
                   ink='\n'.join(''.join(ramp[min(9, int(v * 22))] for v in row) for row in mm))
    elif mode == 'grid':
        cols, rows = [int(v) for v in (req.get('grid') or [16, 8])]
        ch, cw = max(1, sub.shape[0] // rows), max(1, sub.shape[1] // cols)
        mm = sub[:rows * ch, :cols * cw].reshape(rows, ch, cols, cw, 3).mean(axis=(1, 3)).astype(int)
        out['cell'] = [cw, ch]
        out['grid'] = [' '.join('%02x%02x%02x' % tuple(int(v) for v in px) for px in row) for row in mm]
        out['top'] = _top_colors(sub, int(req.get('top', 5)))
    else:
        out['top'] = _top_colors(sub, int(req.get('top', 6)))
    out['ms'] = round((time.perf_counter() - t0) * 1000, 1)
    return out


@op('log')
def o_log(req):
    p = os.path.join(HOME, 'logs', 'actor.log')
    with open(p, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    return {'ok': True, 'lines': [l.rstrip('\n') for l in lines[-int(req.get('tail', 20)):]]}


@op('bench')
def o_bench(req):
    n = int(req.get('n', 20))
    out = {}
    t0 = time.perf_counter()
    for _ in range(n):
        A.screen.grab()
    out['capture_ms'] = round((time.perf_counter() - t0) * 1000 / n, 2)
    t0 = time.perf_counter()
    for _ in range(n):
        A.hands.pos()
    out['cursor_pos_ms'] = round((time.perf_counter() - t0) * 1000 / n, 2)
    x, y = A.hands.pos()
    far = (max(10, min(x + 600, A.screen.geom[2] - 10)), y)
    t0 = time.perf_counter()
    for i in range(max(2, n // 4)):                      # a real ease-in move, not a 4 px nudge
        A.hands.move(*(far if i % 2 == 0 else (x, y)), 160, True)
    out['human_move_ms'] = round((time.perf_counter() - t0) * 1000 / max(2, n // 4), 2)
    frame = A.screen.grab()
    t0 = time.perf_counter()
    for _ in range(max(2, n // 4)):
        find_color(frame, (255, 255, 255), 10)      # label every near-white blob on a 2560x1600 frame
    out['vision_blobs_ms'] = round((time.perf_counter() - t0) * 1000 / max(2, n // 4), 2)
    tmpl = frame[400:460, 400:460].copy()
    t0 = time.perf_counter()
    for _ in range(max(2, n // 4)):
        match_template(frame, tmpl)
    out['vision_template_ms'] = round((time.perf_counter() - t0) * 1000 / max(2, n // 4), 2)
    try:
        UIA, client = uia_client()
        t0 = time.perf_counter()
        for _ in range(max(2, n // 4)):
            guarded(lambda: client.GetRootElement(), 4.0)
        out['uia_root_ms'] = round((time.perf_counter() - t0) * 1000 / max(2, n // 4), 2)
        t0 = time.perf_counter()
        ws = guarded(lambda: windows(client, UIA, 40), 6.0)
        out['uia_windows_ms'] = round((time.perf_counter() - t0) * 1000, 2)
        out['uia_windows'] = len(ws)
    except Exception as e:
        out['uia_error'] = str(e)
    out['frame'] = [A.screen.geom[2], A.screen.geom[3]]
    return {'ok': True, **out}


DATA_SKIP = ('frame', 'png')      # a frame id and a file blob are never worth a trace line


def _slim(v, budget):
    """Keep a step's own reply readable inside a trace: long text is cut, long lists keep a head."""
    if isinstance(v, str):
        return v if len(v) <= budget else '%s…(%d more chars)' % (v[:budget], len(v) - budget)
    if isinstance(v, list):
        head = [_slim(x, budget) for x in v[:6]]
        return head + ['…(%d more items)' % (len(v) - 6)] if len(v) > 6 else head
    if isinstance(v, dict):
        return {k: _slim(x, budget) for k, x in v.items() if k not in DATA_SKIP}
    return v


# ------------------------------------------------------ macros: record once, replay
# The expensive part of a GUI task is the model's round trips, not the pixels: one `run`
# already executes a whole skill server-side.  A macro freezes such a run, so the second
# time the same flow is needed it costs one call instead of thirty.
MACRO_SCHEMA = 'dsh-actor-macro/1'
MACRO_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
VAR_RE = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_.\-]*)\s*\}\}')
RUN_KEEP = 8                      # finished runs of this daemon that can still be recorded
RUNS: collections.deque = collections.deque(maxlen=RUN_KEEP)
RUN_SEQ = 0
INPUT_OPS = frozenset(('click', 'move', 'drag', 'type', 'key', 'scroll'))


def _dict(v):
    """A dict, or {} - the one-shot CLI stuffs bare words into `args` as a list."""
    return dict(v) if isinstance(v, dict) else {}


def macros_dir():
    d = os.path.join(HOME, 'macros')
    os.makedirs(d, exist_ok=True)
    return d


def macro_path(name):
    """One file per macro, $ACTOR_HOME/macros/<name>.json: greppable, diffable, portable."""
    if not MACRO_NAME_RE.match(str(name or '')):
        raise ValueError('bad macro name %r: letters, digits, dot, dash, underscore, max 64' % (name,))
    return os.path.join(macros_dir(), str(name) + '.json')


def macro_read(name):
    with open(macro_path(name), encoding='utf-8') as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not doc.get('steps'):
        raise ValueError('macro %r has no steps' % (name,))
    return doc


def macro_write(doc):
    p = macro_path(doc['name'])
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write('\n')
    os.replace(tmp, p)
    return p


def macro_index():
    """Every macro, one line each - what a later session needs to pick one up."""
    out = []
    d = macros_dir()
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.json'):
            continue
        try:
            with open(os.path.join(d, fn), encoding='utf-8') as f:
                doc = json.load(f)
        except Exception:
            out.append({'name': fn[:-5], 'error': 'unreadable'})
            continue
        st = doc.get('stats') or {}
        out.append({'name': doc.get('name', fn[:-5]), 'steps': len(doc.get('steps') or []),
                    'args': sorted(_dict(doc.get('args')).keys()),
                    'created': doc.get('created'), 'updated': doc.get('updated'),
                    'note': (doc.get('note') or '')[:60], 'replays': st.get('replays', 0),
                    'launch': bool(doc.get('launch')),
                    'last_ms': st.get('last_ms'), 'last_ok': st.get('last_ok'),
                    'last_at': st.get('last_at')})
    return out


def _front_now():
    """Title of the foreground window right now - a hint kept with a recording."""
    try:
        if A is None:
            return None
        return (_win_info(ctypes.windll.user32.GetForegroundWindow()) or {}).get('title')
    except Exception:
        return None


def _lookup(ctx, path):
    cur = ctx
    for part in path.split('.'):
        if isinstance(cur, (list, tuple)):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            if part not in cur:
                raise KeyError('no key %r' % part)
            cur = cur[part]
        else:
            raise KeyError('%s has no %r' % (type(cur).__name__, part))
    return cur


def _resolve(ctx, path, where):
    try:
        return _lookup(ctx, path)
    except Exception as e:
        raise ValueError('%s: {{%s}} %s' % (where or 'step', path, e)) from None


def interp(v, ctx, where=''):
    """`{{name.path}}` inside any string of a step.

    A string that is nothing but one placeholder becomes the value itself, so a list can
    stand in for a coordinate; inside a longer string it is inlined as JSON.  A step that
    must carry literal braces sets "literal": true and is left alone.
    """
    if isinstance(v, str):
        one = VAR_RE.fullmatch(v)
        if one:
            return _resolve(ctx, one.group(1), where)
        if '{{' not in v:
            return v

        def sub(m):
            got = _resolve(ctx, m.group(1), where)
            return got if isinstance(got, str) else json.dumps(got, ensure_ascii=False)
        return VAR_RE.sub(sub, v)
    if isinstance(v, dict):
        return {k: interp(x, ctx, where) for k, x in v.items()}
    if isinstance(v, list):
        return [interp(x, ctx, where) for x in v]
    return v


def exec_steps(steps, req, ctx=None):
    """The loop behind `run` and `macro run`: one call, many ops, one trace entry each.

    `results: true` (or a number = characters per string) folds each step's own reply into
    its trace entry as `data`, so a step that reads structure does not need a second call.
    Without it the trace stays as it was: index, op, ms, ok and a few scalar extras.

    `as: <name>` keeps a step's whole reply in the variable namespace; any later step may
    reach it as `{{<name>.a.b}}` (list indexes are just numbers: `{{hit.hits.0.center}}`).
    The request's `args` seed that namespace, and a top-level `front`/`front_title` is
    applied to every input step that does not name a window itself.
    """
    trace = []
    ctx = dict(ctx or {})
    results = req.get('results')
    budget = 400 if results is True else (
        int(results) if isinstance(results, (int, float)) and results > 0 else 0)
    top_front = req.get('front') is not None or req.get('front_title') is not None
    # The front window is also the best search scope for every UIA selector in this run:
    # resolved once here (then cached), so each step searches ONE window (~95 ms) instead
    # of the whole desktop (~1.5 s). A step that brings its own scope/hwnd keeps it, and a
    # `launch` step that has to start the app hands its new window over to the steps below.
    t_scope = time.perf_counter()
    scope_hwnd = resolve_hwnd(req.get('front'), req.get('front_title')) if top_front else 0
    scope_ms = round((time.perf_counter() - t_scope) * 1000, 1)
    for i, raw in enumerate(steps):
        name = raw.get('op')
        t1 = time.perf_counter()
        try:
            st = raw if raw.get('literal') else interp(raw, ctx, 'step %d' % i)
            if top_front and name in INPUT_OPS and st.get('front') is None and not st.get('front_title'):
                st['front'] = req.get('front')
                st['front_title'] = req.get('front_title')
            if scope_hwnd and st.get('scope_hwnd') is None:
                st['scope_hwnd'] = scope_hwnd
            if name == 'sleep':
                time.sleep(float(st.get('ms', 100)) / 1000.0)
                res = {'ok': True, 'slept_ms': st.get('ms', 100)}
            else:
                res = OPS[name](st)
        except Exception as e:
            res = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
            log('step.fail', i=i, op=name, err=str(e))
        if name == 'launch' and not scope_hwnd and res.get('hwnd'):
            scope_hwnd = int(res['hwnd'])           # what we just started is the best scope
        entry = {'i': i, 'op': name, 'ms': round((time.perf_counter() - t1) * 1000, 1), 'ok': res.get('ok', True)}
        for k in ('n', 'at', 'how', 'hit', 'hits', 'ratio', 'path', 'did', 'value', 'element', 'windows', 'ms'):
            if k in res and k != 'ms':
                entry[k] = res[k]
            if k == 'ms' and isinstance(res.get('ms'), (int, float)):
                entry['op_ms'] = res['ms']
        if not entry['ok']:
            entry['error'] = res.get('error')
        if budget:
            data = {k: v for k, v in res.items() if k not in DATA_SKIP}
            if data:
                entry['data'] = _slim(data, budget)
        trace.append(entry)
        if entry['ok'] and raw.get('as'):
            ctx[str(raw['as'])] = res
        if not entry['ok'] and not req.get('continue_on_error'):
            break
    return trace, ctx, scope_ms


@op('run')
def o_run(req):
    """One call = one skill. steps: list of ops; stops on the first error unless continue_on_error.

    `as` on a step plus `{{name.path}}` in a later one makes the skill parameterised, and
    `record: <name>` freezes exactly these steps as a macro once the run has finished ok
    (see the `macro` op).  Every run also lands in this daemon's run history, so a flow that
    worked can be recorded afterwards without having planned for it: `macro save`.
    """
    global RUN_SEQ
    t0 = time.perf_counter()
    steps = list(req.get('steps') or [])
    trace, _ctx, scope_ms = exec_steps(steps, req, _dict(req.get('args')))
    ok = all(t['ok'] for t in trace)
    RUN_SEQ += 1
    ms = round((time.perf_counter() - t0) * 1000, 1)
    RUNS.append({'id': RUN_SEQ, 'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'ok': ok,
                 'ms': ms, 'steps': steps, 'front': _front_now()})
    out = {'ok': ok, 'steps': len(trace), 'total_ms': ms, 'front_resolve_ms': scope_ms,
           'trace': trace, 'run_id': RUN_SEQ}
    if req.get('record'):
        out['recorded'] = _record(req['record'], req, steps, ms=ms, run_id=RUN_SEQ,
                                  overwrite=bool(req.get('overwrite')),
                                  failed_at=next((t['i'] for t in trace if not t['ok']), None)) \
            if ok else {'saved': False, 'why': 'the run did not finish ok',
                        'failed_step': next((t['i'] for t in trace if not t['ok']), None)}
    return out


def _launch_from_steps(steps):
    """The launch step of a recorded run, if it has one - so a replay can start its app."""
    for s in steps:
        if isinstance(s, dict) and s.get('op') == 'launch':
            return {k: v for k, v in s.items() if k not in ('op', 'as', 'literal')}
    return None


def _record(name, req, steps, ms=0, run_id=None, overwrite=False, failed_at=None):
    """Freeze a run's steps as a named macro; never clobbers one without overwrite."""
    p = macro_path(name)
    if os.path.exists(p) and not overwrite:
        return {'saved': False, 'path': p, 'why': 'macro %r already exists - add overwrite: true '
                                                  'to replace it' % (name,)}
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    created = now
    if os.path.exists(p):
        try:
            created = macro_read(name).get('created', now)
        except Exception:
            pass
    captured = {str(s['as']) for s in steps if isinstance(s, dict) and s.get('as')}
    doc = {'schema': MACRO_SCHEMA, 'name': name, 'created': created, 'updated': now,
           'steps': steps, 'args': {k: v for k, v in _dict(req.get('args')).items() if k not in captured},
           'front': req.get('macro_front') or None, 'note': req.get('note') or '',
           'launch': _dict(req.get('macro_launch')) or _launch_from_steps(steps),
           'from_run': {'id': run_id, 'ms': ms, 'front': _front_now()},
           'stats': {'replays': 0}}
    return {'saved': True, 'name': name, 'steps': len(steps), 'path': macro_write(doc),
            'failed_step': failed_at}


def _save_macro(req):
    steps = req.get('steps')
    rec = None
    if not steps:
        src = req.get('from', 'last')
        if isinstance(src, str) and src not in ('last', 'latest'):
            rec = next((r for r in RUNS if str(r['id']) == src), None)
        else:
            rec = RUNS[-1] if RUNS else None
        if rec is None:
            raise ValueError('no run of this daemon to record from (it keeps the last %d); '
                             'pass steps directly, or run the flow once first' % RUN_KEEP)
        steps = rec['steps']
    if not steps:
        raise ValueError('nothing to save: give steps, or run something first')
    out = _record(req.get('name'), req, steps, ms=(rec or {}).get('ms', 0),
                  run_id=(rec or {}).get('id'), overwrite=bool(req.get('overwrite')))
    if rec:
        out['from_run'] = {'id': rec['id'], 'ok': rec['ok'], 'ms': rec['ms'], 'at': rec['at']}
    return out


def _run_macro(req):
    """One call replays the whole macro: re-resolved variables, per-step ms, one trace."""
    doc = macro_read(req['name'])
    args = _dict(doc.get('args'))
    args.update(_dict(req.get('args')))
    steps = doc.get('steps') or []
    if req.get('dry'):
        ctx = dict(args)
        return {'ok': True, 'macro': doc['name'], 'dry': True, 'args': args,
                'steps': [interp(s, ctx, 'step %d' % i) for i, s in enumerate(steps)]}
    front = _dict(req.get('front')) or _dict(doc.get('front')) or None
    started = None
    spec = _dict(req.get('launch')) or _dict(doc.get('launch'))
    if spec:                                    # a macro that knows how to start its own app
        spec = dict(interp(spec, dict(args), 'launch'))
        already = resolve_hwnd(front.get('hwnd'), front.get('title_contains')) if front else 0
        if already and not spec.get('force'):
            started = {'ok': True, 'skipped': 'the window was already open', 'hwnd': already}
        else:
            if front and not spec.get('wait') and not spec.get('title_contains') \
                    and front.get('title_contains'):
                spec['wait'] = front['title_contains']
            started = launch_app(spec)
    if started is not None and started.get('ok') is False:
        return {'ok': False, 'macro': doc['name'], 'steps': 0, 'total_ms': 0, 'trace': [],
                'launch': started, 'error': started.get('error'),
                'hint': 'the macro could not start its app - check launch.path / launch.wait'}
    eff = dict(req)
    fronted = None
    front_ms = 0.0
    if front:
        eff['front'] = front.get('hwnd')
        eff['front_title'] = front.get('title_contains')
        t_front = time.perf_counter()
        fronted = _front(front.get('hwnd'), front.get('title_contains'), front.get('top', True))
        front_ms = (time.perf_counter() - t_front) * 1000
    t0 = time.perf_counter()
    trace, _ctx, scope_ms = exec_steps(steps, eff, dict(args))
    ok = all(t['ok'] for t in trace)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    st = dict(doc.get('stats') or {})
    st.update({'replays': int(st.get('replays', 0)) + 1, 'last_ms': ms, 'last_ok': ok,
               'last_at': time.strftime('%Y-%m-%dT%H:%M:%S')})
    doc['stats'] = st
    try:
        macro_write(doc)
    except Exception as e:                       # a replay must not fail over bookkeeping
        log('macro.stats_fail', name=doc.get('name'), err=str(e))
    out = {'ok': ok, 'macro': doc['name'], 'steps': len(trace), 'total_ms': ms, 'trace': trace,
           'front_resolve_ms': round(front_ms + scope_ms, 1),
           'args': args, 'replays': st['replays'], 'front': fronted,
           'launch': started, 'recorded_ms': (doc.get('from_run') or {}).get('ms')}
    failed = next((t['i'] for t in trace if not t['ok']), None)
    if failed is not None:
        out['failed_step'] = failed
        out['hint'] = ('step %s broke the replay: run that part by hand with results=true, then '
                       'record the macro again' % failed)
    return out


@op('macro')
def o_macro(req):
    """Recorded skills: what list | get | run | save | del   (`act.cmd macro list` works too).

    save : {name, from: 'last'|<run id>, overwrite} or {name, steps}, plus optional args/note
           - `from` defaults to this daemon's most recent run, so a flow that just worked is
           one call away from being a macro.
    run  : {name, args: {...}, results: true, front: {...}, dry}
           - one call replays every step; `dry` expands the templates without touching
           anything, which is how you debug a `{{var}}` that no longer resolves.
    """
    what = req.get('what')
    if not isinstance(what, str):
        a = req.get('args')
        what = a[0] if isinstance(a, list) and a and isinstance(a[0], str) else 'list'
    if what in ('list', 'ls'):
        return {'ok': True, 'dir': macros_dir(), 'n': len(macro_index()), 'macros': macro_index()}
    if what in ('get', 'show'):
        return {'ok': True, 'macro': macro_read(req['name'])}
    if what in ('del', 'rm'):
        p = macro_path(req['name'])
        os.remove(p)
        return {'ok': True, 'deleted': req['name'], 'path': p}
    if what == 'save':
        return _save_macro(req)
    if what == 'run':
        return _run_macro(req)
    raise ValueError('macro needs what=list|get|run|save|del, got %r' % (what,))


# ------------------------------------------------------------------- capture
# A human demonstration recorded into macro steps. Both low-level hooks
# (WH_MOUSE_LL / WH_KEYBOARD_LL) live in ONE pump thread and their procs only
# timestamp and append, so a 150 ms UIA lookup can never stall your input; a worker
# thread then resolves what was under each click - UIA selector first, image anchor
# second, plain coordinates last. Scope: events count only while the foreground window
# belongs to the same PROCESS as the window you named, so the rest of your desktop
# cannot leak into the recording (modal dialogs of the same app stay in scope).

MOUSE_LL, KEYBOARD_LL = 14, 13
WM_LDOWN, WM_LUP, WM_RDOWN, WM_RUP, WM_MDOWN, WM_MUP = 0x0201, 0x0202, 0x0204, 0x0205, 0x0207, 0x0208
WM_WHEEL, WM_HWHEEL, WM_QUIT = 0x020A, 0x020E, 0x0012
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LL_MOUSE_INJECTED, LL_KEY_INJECTED = 0x01, 0x10
MOD_VKS = {0x10: 'shift', 0x11: 'ctrl', 0x12: 'alt', 0x5B: 'win', 0x5C: 'win'}
VK_NAME: dict = {}
for _name, _code in VK.items():
    VK_NAME.setdefault(_code, _name)      # 'return'/'escape'/'del'/'lwin' lose to the first spelling
CJK_LAYOUTS = (0x0804, 0x0404, 0x0C04, 0x1004, 0x0411, 0x0412)      # zh-CN/TW/HK/SG, ja, ko
ANCHOR_PX, ANCHOR_MIN_STD, MAX_SEL_AREA, CAPTURE_LATE_S = 68, 7.0, 400000, 2.0

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
u32.GetClipboardData.restype = ctypes.c_void_p
u32.SetWindowsHookExW.restype = ctypes.c_void_p
u32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wt.DWORD]
u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
u32.CallNextHookEx.restype = ctypes.c_ssize_t
u32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('pt', wt.POINT), ('mouseData', wt.DWORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]


def clipboard_text():
    """CF_UNICODETEXT, or '' - it turns a demonstrated Ctrl+V into a `type` step, which
    is also the only way to record CJK typed through an IME."""
    if not u32.OpenClipboard(None):
        return ''
    try:
        if not u32.IsClipboardFormatAvailable(13):
            return ''
        h = u32.GetClipboardData(13)
        if not h:
            return ''
        p = k32.GlobalLock(h)
        if not p:
            return ''
        try:
            return ctypes.c_wchar_p(p).value or ''
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def window_pid(hwnd):
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(wt.HWND(int(hwnd)), ctypes.byref(pid))
    return int(pid.value)


def capture_how(step):
    """uia / anchor / xy - what a recorded step will aim at when it replays."""
    for key in ('target', 'from'):
        t = step.get(key)
        if isinstance(t, dict):
            if 'uia' in t:
                return 'uia'
            if 'image' in t:
                return 'anchor'
            if 'xy' in t:
                return 'xy'
    return 'other'


def _target_text(t):
    """A recorded target as one printable word."""
    if not isinstance(t, dict):
        return '?'
    if 'uia' in t:
        sel = _dict(t['uia']).get('selector') or t['uia']
        return 'uia ' + ' '.join('%s=%s' % (k, v) for k, v in _dict(sel).items())
    if 'image' in t:
        return 'anchor ' + os.path.basename(str(t['image']))
    if 'xy' in t:
        return 'xy %s' % (t['xy'],)
    return '?'


def compile_events(events):
    """Recorded events -> (macro steps, warnings). Pure: no UIA, no screen, no clock.

    A demonstration is turned into the same step format a `run` records, so a macro
    made this way replays through exactly the same engine.
    """
    steps = []
    warnings = []
    i = 0
    while i < len(events):
        ev = events[i]
        kind = ev.get('kind')
        if kind == 'click':
            nxt = events[i + 1] if i + 1 < len(events) else {}
            dbl = (ev.get('button') == 'left' and nxt.get('kind') == 'click' and nxt.get('button') == 'left'
                   and abs(nxt.get('t', 0) - ev.get('t', 0)) <= 0.5
                   and abs(nxt.get('x', 0) - ev.get('x', 0)) <= 6 and abs(nxt.get('y', 0) - ev.get('y', 0)) <= 6)
            step = {'op': 'click', 'target': ev.get('target') or {'xy': [ev.get('x'), ev.get('y')]}}
            if ev.get('button') != 'left':
                step['button'] = ev.get('button')
            if dbl:
                step['n'] = 2
                i += 1
            steps.append(step)
        elif kind == 'drag':
            step = {'op': 'drag', 'from': ev.get('from_target') or {'xy': ev.get('from')},
                    'to': ev.get('to_target') or {'xy': ev.get('to')}}
            if ev.get('button') != 'left':
                step['button'] = ev.get('button')
            steps.append(step)
        elif kind == 'scroll':
            tgt = ev.get('target') or {'xy': [ev.get('x'), ev.get('y')]}
            steps.append({'op': 'move', 'target': tgt, 'dur_ms': 1})     # the wheel goes to the cursor
            step = {'op': 'scroll'}
            if ev.get('dy'):
                step['dy'] = int(ev['dy'])
            if ev.get('dx'):
                step['dx'] = int(ev['dx'])
            steps.append(step)
        elif kind == 'type':
            text = ev.get('text') or ''
            if text:
                if ev.get('ime'):
                    warnings.append('the IME layout 0x%04x was active while you typed %r - a hook sees the keys, '
                                    'not the composed characters' % (int(ev['ime']), text[:12]))
                steps.append({'op': 'type', 'text': text})
        elif kind == 'key':
            keys = [k for k in (ev.get('keys') or []) if k]
            if keys:
                steps.append({'op': 'key', 'keys': keys} if len(keys) > 1 else {'op': 'key', 'key': keys[0]})
        i += 1
    return steps, warnings


def capture_trace(steps):
    """One printable line per recorded step - act.cmd prints them like a run trace."""
    out = []
    for i, s in enumerate(steps):
        t = {'i': i, 'op': s.get('op'), 'ok': True, 'ms': 0}
        if s.get('n'):
            t['n'] = s['n']
        if s.get('key'):
            t['did'] = s['key']
        elif s.get('keys'):
            t['did'] = '+'.join(s['keys'])
        elif s.get('text') is not None:
            t['did'] = (s['text'][:24] + '...') if len(s['text']) > 27 else s['text']
        if s.get('target'):
            t['how'] = _target_text(s['target'])
        elif s.get('from'):
            t['how'] = _target_text(s.get('from'))
            t['element'] = 'to ' + _target_text(s.get('to'))
        out.append(t)
    return out


class Capture(object):
    """A demonstration in, macro steps out (see the `capture` op)."""

    def __init__(self):
        self.lock = threading.RLock()
        self._reset()

    def _reset(self):
        self.active = False
        self.name = ''
        self.front_title = ''
        self.scope_hwnd = 0
        self.scope_pid = 0
        self.scope_rect: list = []
        self.rect_at = 0.0
        self.t0 = 0.0
        self.seq = 0
        self.dropped = 0
        self.injected = 0
        self.pump_tid = 0
        self.events: list = []
        self.errors: list = []
        self.warnings: list = []
        self.mods: list = []
        self.hooks: list = []
        self.threads: dict = {}
        self.procs: dict = {}
        self.foreign: dict = {}
        self.drag: dict = {}
        self.text: dict = {}
        self.scroll: dict = {}
        self.down_keys: set = set()
        self.queue = queue.Queue()
        self.ready = threading.Event()

    # ---- the hook procs: timestamp and append only (no UIA, no PIL, no disk, no sleep)
    def _hook_fail(self, err):
        if len(self.errors) < 6:
            self.errors.append(str(err))

    def _mouse_proc(self, n_code, w_param, l_param):
        if n_code >= 0 and self.active:
            try:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self.on_mouse(int(w_param), info)
            except Exception as e:
                self._hook_fail('mouse: %s' % e)
        return u32.CallNextHookEx(None, n_code, w_param, l_param)

    def _key_proc(self, n_code, w_param, l_param):
        if n_code >= 0 and self.active:
            try:
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self.on_key(int(w_param), info)
            except Exception as e:
                self._hook_fail('key: %s' % e)
        return u32.CallNextHookEx(None, n_code, w_param, l_param)

    def _fg_ok(self):
        """True while the foreground window belongs to the app being demonstrated."""
        hwnd = int(u32.GetForegroundWindow())
        pid = window_pid(hwnd)
        if self.scope_pid and pid != self.scope_pid:
            if pid not in self.foreign:
                self.foreign[pid] = (_win_info(hwnd) or {}).get('title') or ('pid %d' % pid)
            self.dropped += 1
            self.drag = {}
            return False
        return True

    def _inside(self, x, y):
        """A click only counts when it lands inside the window being watched: the pid check
        alone accepts a click that misses a moved window and hits whatever is behind it.
        The rect is re-read (cheaply, no UIA) so moving the window mid-demo is still fine."""
        now = time.perf_counter()
        if self.scope_hwnd and (not self.scope_rect or now - self.rect_at > 0.5):
            self.scope_rect = list((_win_info(self.scope_hwnd) or {}).get('rect') or [])
            self.rect_at = now
        r = self.scope_rect
        if not r:
            return True
        if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
            return True
        self.dropped += 1
        return False

    def on_mouse(self, wparam, info):
        t = time.perf_counter()
        x, y = int(info.pt.x), int(info.pt.y)
        if info.flags & LL_MOUSE_INJECTED:
            self.injected += 1
        if wparam == 0x0200:                                    # WM_MOUSEMOVE: only a drag path
            d = self.drag
            if not d or t - d['last'] < 0.03:
                return
            d['last'] = t
            d['path'].append([x, y])
            return
        if wparam in (WM_WHEEL, WM_HWHEEL):
            if self.drag or not self._fg_ok() or not self._inside(x, y):
                return
            sc = self.scroll
            if sc and abs(sc['x'] - x) + abs(sc['y'] - y) > 48:
                self.scroll = sc = {}
            delta = ctypes.c_short((int(info.mouseData) >> 16) & 0xFFFF).value
            if not sc:
                sc = {'kind': 'scroll', 'dx': 0, 'dy': 0, 'x': x, 'y': y, 't': t, 'target': None}
                self.scroll = sc
                self.events.append(sc)
                self.queue.put(sc)
            sc['x'], sc['y'] = x, y
            if wparam == WM_WHEEL:
                sc['dy'] += int(delta)
            else:
                sc['dx'] += int(delta)
            return
        if wparam in (WM_LDOWN, WM_RDOWN, WM_MDOWN):
            self.scroll = {}
            self.drag = {}
            if not self._fg_ok() or not self._inside(x, y):
                return
            self.drag = {'x': x, 'y': y, 't': t, 'last': t, 'path': [],
                         'button': {WM_LDOWN: 'left', WM_RDOWN: 'right', WM_MDOWN: 'middle'}[wparam]}
            return
        if wparam in (WM_LUP, WM_RUP, WM_MUP):
            d = self.drag
            self.drag = {}
            if not d:
                return
            self._close_text()
            far = max((abs(p[0] - d['x']) + abs(p[1] - d['y']) for p in d['path']), default=0)
            if far >= 14:
                ev = {'kind': 'drag', 'from': [d['x'], d['y']], 'to': [x, y], 't': d['t'],
                      'button': d['button'], 'from_target': None, 'to_target': None}
            else:
                ev = {'kind': 'click', 'x': d['x'], 'y': d['y'], 't': d['t'], 'button': d['button'],
                      'n': 1, 'target': None}
            self.events.append(ev)
            self.queue.put(ev)

    def _ime(self):
        try:
            lang = int(u32.GetKeyboardLayout(0)) & 0xFFFF
        except Exception:
            return 0
        return lang if lang in CJK_LAYOUTS else 0

    def _to_text(self, vk, scan):
        state = (ctypes.c_ubyte * 256)()
        for m in self.mods:
            if m in VK:
                state[VK[m]] = 0x80
        buf = ctypes.create_unicode_buffer(8)
        try:
            n = u32.ToUnicodeEx(vk, scan, state, buf, 8, 0, u32.GetKeyboardLayout(0))
        except Exception:
            return ''
        return buf[:n] if n and n > 0 else ''

    def _text_ev(self):
        if not self.text:
            self.text = {'kind': 'type', 'text': '', 't': time.perf_counter(), 'ime': self._ime()}
            self.events.append(self.text)
        return self.text

    def _close_text(self):
        if self.text and not self.text.get('text'):
            for i in range(len(self.events) - 1, -1, -1):       # an empty run is not a step
                if self.events[i] is self.text:
                    del self.events[i]
                    break
        self.text = {}

    def on_key(self, wparam, info):
        vk = int(info.vkCode)
        down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if info.flags & LL_KEY_INJECTED:
            self.injected += 1
        if vk in MOD_VKS:
            mod = MOD_VKS[vk]
            if down:
                if mod not in self.mods:
                    self.mods.append(mod)
            elif mod in self.mods:
                self.mods.remove(mod)
            return
        if not down:
            self.down_keys.discard(vk)
            return
        if vk in self.down_keys:                                # auto-repeat
            return
        self.down_keys.add(vk)
        if not self._fg_ok():
            return
        name = VK_NAME.get(vk) or ''
        ctrl, alt = 'ctrl' in self.mods, 'alt' in self.mods
        if ctrl or alt:
            key = name or (chr(vk).lower() if 0x30 <= vk <= 0x5A else '')
            if not key:
                return
            self._close_text()
            self.scroll = {}
            if key == 'v' and ctrl and 'alt' not in self.mods and 'shift' not in self.mods:
                clip = clipboard_text()
                if clip:                                        # CJK pasted in: record the text itself
                    self._text_ev()['text'] += clip
                    self.warnings.append('Ctrl+V became a type step (%d chars from the clipboard)' % len(clip))
                    return
            self.events.append({'kind': 'key', 'keys': self.mods + [key], 't': time.perf_counter()})
            return
        txt = self._to_text(vk, int(info.scanCode))
        if txt:
            self._text_ev()['text'] += txt
            return
        if name:
            self._close_text()
            self.scroll = {}
            self.events.append({'kind': 'key', 'keys': self.mods + [name], 't': time.perf_counter()})

    # ---- lifecycle
    def start(self, req):
        if self.active:
            return {'ok': False, 'error': 'a capture is already running (%r) - act.cmd capture stop first' % self.name}
        name = str(req.get('name') or req.get('macro') or time.strftime('demo-%m%d-%H%M%S'))
        if not MACRO_NAME_RE.match(name):
            return {'ok': False, 'error': 'capture: bad macro name %r (letters, digits, . _ -)' % name}
        if os.path.exists(macro_path(name)) and not req.get('overwrite'):
            return {'ok': False, 'error': 'macro %r already exists - add overwrite: true' % name}
        front = req.get('front_title') or req.get('title_contains') or _dict(req.get('front')).get('title_contains')
        hwnd = int(req.get('hwnd') or 0)
        if not hwnd:
            if not front:
                return {'ok': False, 'error': 'capture start needs front_title=<part of the window title> (or hwnd=)'}
            hwnd = int(resolve_hwnd(title_contains=front, timeout=2.0))
            if not hwnd:
                return {'ok': False, 'error': 'capture: no window matches %r' % front}
        self._reset()
        self.name = name
        self.front_title = front or (_win_info(hwnd) or {}).get('title') or ''
        self.scope_hwnd, self.scope_pid = hwnd, window_pid(hwnd)
        self.t0 = time.perf_counter()
        self.procs = {'mouse': HOOKPROC(self._mouse_proc), 'key': HOOKPROC(self._key_proc)}
        self.active = True
        self.threads['worker'] = threading.Thread(target=self._work, name='capture-worker', daemon=True)
        self.threads['worker'].start()
        self.threads['pump'] = threading.Thread(target=self._pump, name='capture-pump', daemon=True)
        self.threads['pump'].start()
        self.ready.wait(3.0)
        if len(self.hooks) < 2:
            self.active = False
            self._stop_hooks()
            return {'ok': False, 'error': 'capture: could not install the input hooks', 'errors': self.errors[:3]}
        if req.get('front', True):
            _front(hwnd)
        log('capture.start', name=name, hwnd=hwnd, pid=self.scope_pid, title=self.front_title)
        return {'ok': True, 'capture': name, 'scope': {'hwnd': hwnd, 'pid': self.scope_pid, 'title': self.front_title},
                'anchors': os.path.join(macros_dir(), '_anchors', name),
                'next': 'demo it now, then: act.cmd capture stop'}

    def stop(self, req=None):
        req = _dict(req)
        if not self.active:
            return {'ok': False, 'error': 'no capture is running'}
        self.active = False
        self._stop_hooks()
        with self.lock:
            self._close_text()
            self.scroll = {}
        self.queue.put(None)
        w = self.threads.get('worker')
        if w:
            w.join(timeout=10.0)
        events = list(self.events)
        ms = round((time.perf_counter() - self.t0) * 1000, 1)
        base = {'capture': self.name, 'events': len(events), 'dropped': self.dropped, 'injected': self.injected,
                'total_ms': ms, 'notes': self.warnings, 'foreign': sorted(set(self.foreign.values()))[:5]}
        if self.errors:
            base['errors'] = self.errors[:3]
        if req.get('discard'):
            return dict(base, ok=True, discarded=True)
        steps, notes = compile_events(events)
        if not steps:
            return dict(base, ok=False, steps=0, notes=notes,
                        error='nothing was recorded - only clicks, keys and text that arrive while the app you '
                              'named is in front become steps')
        counts = collections.Counter(capture_how(s) for s in steps)
        now = time.strftime('%Y-%m-%dT%H:%M:%S')
        doc = {'schema': MACRO_SCHEMA, 'name': self.name, 'created': now, 'updated': now, 'steps': steps,
               'args': {}, 'front': {'title_contains': self.front_title} if self.front_title else None,
               'note': 'recorded from a demonstration (%d events, %d steps)' % (len(events), len(steps)),
               'source': 'capture', 'launch': None,
               'from_demo': {'events': len(events), 'dropped': self.dropped, 'injected': self.injected, 'ms': ms},
               'stats': {'replays': 0}}
        path = macro_write(doc)
        log('capture.stop', name=self.name, steps=len(steps), dropped=self.dropped)
        return dict(base, ok=True, steps=len(steps), saved=path, how=dict(counts),
                    notes=notes + self.warnings, trace=capture_trace(steps),
                    next='act.cmd macro run name=%s' % self.name)

    def status(self):
        return {'ok': True, 'active': bool(self.active), 'capture': self.name,
                'scope': {'hwnd': self.scope_hwnd, 'pid': self.scope_pid, 'title': self.front_title},
                'events': len(self.events), 'dropped': self.dropped, 'injected': self.injected,
                'pending': self.queue.qsize(),
                'ms': round((time.perf_counter() - self.t0) * 1000, 1) if self.active else 0,
                'foreign': sorted(set(self.foreign.values()))[:5],
                'next': 'demo it now, then: act.cmd capture stop' if self.active else 'nothing is being recorded'}

    def cancel(self):
        if not self.active:
            return {'ok': False, 'error': 'no capture is running'}
        return self.stop({'discard': True})

    # ---- threads
    def _pump(self):
        """Both hooks are installed HERE: a low-level hook is delivered to the thread that
        installed it, and that thread must pump messages for the procs to ever run."""
        self.pump_tid = int(k32.GetCurrentThreadId())
        for kind, which in ((MOUSE_LL, 'mouse'), (KEYBOARD_LL, 'key')):
            h = u32.SetWindowsHookExW(kind, self.procs[which], None, 0)
            if h:
                self.hooks.append(h)
            else:
                self.errors.append('SetWindowsHookExW(%d) failed (err %s)' % (kind, k32.GetLastError()))
        self.ready.set()
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    def _stop_hooks(self):
        if self.pump_tid:
            u32.PostThreadMessageW(self.pump_tid, WM_QUIT, 0, 0)
        for h in self.hooks:
            try:
                u32.UnhookWindowsHookEx(h)
            except Exception:
                pass
        self.hooks = []
        pump = self.threads.get('pump')
        if pump:
            pump.join(timeout=1.0)
        self.pump_tid = 0

    def _work(self):
        while True:
            ev = self.queue.get()
            if ev is None:
                return
            try:
                self._resolve(ev)
            except Exception as e:
                self._hook_fail('resolve: %s' % e)

    # ---- what was under that click?
    def _resolve(self, ev):
        late = (time.perf_counter() - float(ev.get('t') or 0)) > CAPTURE_LATE_S
        if ev.get('kind') == 'drag':
            for side in ('from', 'to'):
                p = ev.get(side) or [0, 0]
                ev[side + '_target'] = {'xy': [int(p[0]), int(p[1])]} if late else self._target_at(p[0], p[1])
        else:
            x, y = int(ev.get('x', 0)), int(ev.get('y', 0))
            ev['target'] = {'xy': [x, y]} if late else self._target_at(x, y)
        self.seq += 1

    def _target_at(self, x, y):
        sel = self._selector_at(int(x), int(y))
        if sel:
            return sel
        img = self._anchor_at(int(x), int(y))
        return img or {'xy': [int(x), int(y)]}

    def _selector_at(self, x, y):
        """A selector for the element under (x, y), but only if it re-finds the SAME
        element and stays small: a big container's centre is not where you clicked."""
        UIA, client = uia_client()
        els = guarded(lambda: uia_find(client, UIA, {'point': [x, y]}), 3.0)
        if not els:
            return None
        info = el_info(els[0], UIA)
        rect = info.get('rect')
        if not rect or info.get('offscreen'):
            return None
        w, h = int(rect[2]) - int(rect[0]), int(rect[3]) - int(rect[1])
        if w <= 0 or h <= 0 or w * h > MAX_SEL_AREA:
            return None
        for key in ('aid', 'name'):
            v = str(info.get(key) or '').strip()
            if not v or len(v) > 80 or (key == 'name' and v == self.front_title):
                continue
            idx = self._confirm({key: v}, x, y)
            if idx is not None:
                return {'uia': {key: v}} if not idx else {'uia': {'selector': {key: v}, 'index': idx}}
        return None

    def _confirm(self, sel, x, y):
        UIA, client = uia_client()
        els = guarded(lambda: uia_find(client, UIA, sel, limit=8, scope=self.scope_hwnd), 3.0)
        for i, el in enumerate(els):
            r = el_info(el, UIA).get('rect')
            if r and r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return i
        return None

    def _anchor_at(self, x, y):
        """A small picture of the spot, for apps that expose nothing to UIA."""
        if A is None:
            return None
        half = ANCHOR_PX // 2
        frame = A.ensure(0)
        h, w = int(frame.shape[0]), int(frame.shape[1])
        x1, y1 = max(0, x - half), max(0, y - half)
        x2, y2 = min(w, x + half), min(h, y + half)
        if x2 - x1 < 24 or y2 - y1 < 24:
            return None
        patch = frame[y1:y2, x1:x2]
        if float(patch.std()) < ANCHOR_MIN_STD:
            return None                                          # a flat crop matches everywhere
        d = os.path.join(macros_dir(), '_anchors', self.name)
        try:
            from PIL import Image
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, '%02d-%d-%d.png' % (self.seq, x1, y1))
            Image.fromarray(patch).save(p)
        except Exception as e:
            self._hook_fail('anchor: %s' % e)
            return None
        return {'image': p}


CAP = Capture()


@op('capture')
def o_capture(req):
    """Record a human demonstration: what = start | stop | status | cancel.

      act.cmd capture start name=calc-demo front_title=计算器   # then just do it by hand
      act.cmd capture stop                                      # -> a replayable macro
    """
    what = req.get('what')
    if not what:
        a = req.get('args')
        what = (a[0] if isinstance(a, list) and a else a) or ''
    what = str(what).lower()
    if what in ('start', 'record', 'on'):
        return CAP.start(req)
    if what in ('stop', 'end', 'off'):
        return CAP.stop(req)
    if what in ('status', 'ls', 'state'):
        return CAP.status()
    if what in ('cancel', 'abort', 'discard'):
        return CAP.cancel()
    return {'ok': False, 'error': 'capture: what=start|stop|status|cancel, got %r' % (what,)}


@op('stop')
def o_stop(req):
    log('stop')
    threading.Thread(target=lambda: (time.sleep(0.25), os._exit(0)), daemon=True).start()
    return {'ok': True, 'bye': True}


# --------------------------------------------------------------------- server
class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line = self.rfile.readline()
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line.decode('utf-8'))
                name = req.get('op')
                if name not in OPS:
                    rep = {'ok': False, 'error': 'unknown op %r (have %s)' % (name, ','.join(sorted(OPS)))}
                else:
                    rep = OPS[name](req)
            except Exception as e:
                rep = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e),
                       'trace': traceback.format_exc()[-600:]}
            try:
                self.wfile.write((json.dumps(rep, ensure_ascii=False) + '\n').encode('utf-8'))
            except Exception:
                return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    global A
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8731)
    ap.add_argument('--host', default='127.0.0.1')
    args = ap.parse_args()
    os.makedirs(os.path.join(HOME, 'logs'), exist_ok=True)
    dpi = set_dpi_awareness()
    A = Actor()
    with open(os.path.join(HOME, 'port.txt'), 'w') as f:
        f.write(str(args.port))
    log('start', pid=os.getpid(), dpi=dpi, geom=list(A.screen.geom), port=args.port, home=HOME)
    srv = Server((args.host, args.port), Handler)
    print('actor listening on %s:%d (dpi=%s, screen=%s, home=%s)' % (args.host, args.port, dpi, list(A.screen.geom), HOME), flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
