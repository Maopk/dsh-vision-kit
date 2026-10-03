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
Ops: ping shot save find click move drag type key scroll uia wait_for run bench watch log stop
"""
import argparse, ctypes, json, os, queue, socketserver, sys, threading, time, traceback
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


def uia_find(client, UIA, sel, limit=40, root=None):
    """sel: name / name_contains / aid / cls / ctype / hwnd / point."""
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
                    els = guarded(lambda client=client, UIA=UIA: uia_find(client, UIA, cond['selector'], 5), 4.0)
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


def resolve_target(t, actor):
    """target: {xy:[x,y]} | {uia:{selector,index}} | {image:'path'} | {color:[r,g,b]}"""
    if not t:
        raise ValueError('no target')
    if 'xy' in t:
        return [int(t['xy'][0]), int(t['xy'][1])], {'how': 'xy'}
    if 'uia' in t:
        UIA, client = uia_client()
        sel = t['uia'].get('selector', t['uia'])
        idx = int(t['uia'].get('index', 0))
        els = guarded(lambda: uia_find(client, UIA, sel), 4.0)
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
        els = guarded(lambda: uia_find(client, UIA, req['uia'], req.get('max', 40)), req.get('timeout', 4.0))
        hits = [el_info(e, UIA) for e in els]
    else:
        raise ValueError('find needs image|color|uia')
    return {'ok': True, 'n': len(hits), 'hits': hits, 'frame_id': A.frame_id}


@op('click')
def o_click(req):
    t0 = time.perf_counter()
    fr = _maybe_front(req)
    pos, how = resolve_target(req.get('target'), A)
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
        pos, how = resolve_target(req['target'], A)
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
        p1, _ = resolve_target(req['from'], A)
    else:
        p1 = [int(req['x1']), int(req['y1'])]
    if req.get('to'):
        p2, _ = resolve_target(req['to'], A)
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
        els = guarded(lambda: uia_find(client, UIA, req['selector'], req.get('max', 40)), to)
        return {'ok': True, 'n': len(els), 'hits': [el_info(e, UIA) for e in els]}
    if what == 'point':
        el = guarded(lambda: client.ElementFromPoint(wt.POINT(int(req['x']), int(req['y']))), to)
        return {'ok': True, 'hit': el_info(el, UIA) if el else None}
    if what == 'tree':
        root = None
        if req.get('hwnd'):
            root = guarded(lambda: client.ElementFromHandle(wt.HWND(int(req['hwnd']))), to)
        elif req.get('selector'):
            els = guarded(lambda: uia_find(client, UIA, req['selector'], 1), to)
            root = els[0] if els else None
        if root is None:
            root = guarded(lambda: client.GetRootElement(), to)
        if req.get('compact'):
            return {'ok': True, **_win_elements(req)}
        out = []
        guarded(lambda: walk(root, UIA, int(req.get('depth', 3)), int(req.get('limit', 300)), out, req.get('view', 'raw')), to)
        return {'ok': True, 'n': len(out), 'tree': [dict(depth=d, **el_info(e, UIA)) for d, e in out]}
    if what in ('invoke', 'focus', 'setvalue'):
        els = guarded(lambda: uia_find(client, UIA, req['selector'], req.get('max', 5)), to)
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
        UIA, client = uia_client()
        for w in guarded(lambda: windows(client, UIA, 80), 4.0):
            if req['title_contains'] in (w.get('name') or ''):
                hwnd = int(w['hwnd'])
                break
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


@op('run')
def o_run(req):
    """One call = one skill. steps: list of ops; stops on the first error unless continue_on_error."""
    trace = []
    t0 = time.perf_counter()
    for i, st in enumerate(req.get('steps', [])):
        name = st.get('op')
        t1 = time.perf_counter()
        try:
            if name == 'sleep':
                time.sleep(float(st.get('ms', 100)) / 1000.0)
                res = {'ok': True, 'slept_ms': st.get('ms', 100)}
            else:
                res = OPS[name](st)
        except Exception as e:
            res = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
            log('step.fail', i=i, op=name, err=str(e))
        entry = {'i': i, 'op': name, 'ms': round((time.perf_counter() - t1) * 1000, 1), 'ok': res.get('ok', True)}
        for k in ('n', 'at', 'how', 'hit', 'hits', 'ratio', 'path', 'did', 'value', 'element', 'windows', 'ms'):
            if k in res and k != 'ms':
                entry[k] = res[k]
            if k == 'ms' and isinstance(res.get('ms'), (int, float)):
                entry['op_ms'] = res['ms']
        if not entry['ok']:
            entry['error'] = res.get('error')
        trace.append(entry)
        if not entry['ok'] and not req.get('continue_on_error'):
            break
    return {'ok': all(t['ok'] for t in trace), 'steps': len(trace),
            'total_ms': round((time.perf_counter() - t0) * 1000, 1), 'trace': trace}


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
