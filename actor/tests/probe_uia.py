import ctypes, sys
ctypes.windll.ole32.CoInitializeEx(None, 0x2)
import comtypes.client
comtypes.client.GetModule('UIAutomationCore.dll')
from comtypes.gen import UIAutomationClient as UIA
c = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
root = c.GetRootElement()
print('root name=%r type=%s' % (root.CurrentName, root.CurrentControlType))
print('UIA.RawViewWalker exists:', hasattr(UIA, 'RawViewWalker'))
try:
    w = c.RawViewWalker
    ch = w.GetFirstChildElement(root)
    n = 0
    while ch is not None and n < 8:
        try:
            print('  child %-28r type=%-4s cls=%r' % (ch.CurrentName, ch.CurrentControlType, ch.CurrentClassName))
        except Exception as e:
            print('  child err', e)
        ch = w.GetNextSiblingElement(ch)
        n += 1
    print('children listed:', n)
except Exception as e:
    print('walker failed:', type(e).__name__, e)
cond = c.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, 50032)   # Window
found = root.FindAll(1, cond)
print('windows via FindAll:', found.Length)
for i in range(min(found.Length, 8)):
    e = found.GetElement(i)
    r = e.CurrentBoundingRectangle
    print('   ', repr(e.CurrentName), [r.left, r.top, r.right, r.bottom])
