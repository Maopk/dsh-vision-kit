# gui-steps.ps1 — plan-driven desktop GUI driver (mouse / keys / clipboard / screenshot / template match)
#
# A JSON plan drives a sequence of steps in ONE PowerShell process, so a whole
# interaction (raise window -> click -> type -> verify) costs one process start
# instead of one per action. Steps: focus | top | rect | move | click | press |
# glide | release | paste | key | match | sleep | shot.
#
#   pwsh -File gui-steps.ps1 -PlanPath plan.json
#
# Notes for this copy: the window is matched by process name "QQ" (see
# DshInput.Top) and the send hotkey used by the caller is Ctrl+Enter — both are
# examples; adjust for your own target app.
param([Parameter(Mandatory = $true)][string]$PlanPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$dll = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'DshInput.dll'
if (Test-Path $dll) {
    Add-Type -Path $dll            # ~50 ms instead of ~500 ms of csc
} else {
    $src = @"
using System;
using System.Runtime.InteropServices;

public static class DshInput
{
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }

    [DllImport("user32.dll")] private static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] private static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
    [DllImport("user32.dll")] private static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);
    [DllImport("user32.dll")] private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr c);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern int GetWindowTextW(IntPtr h, System.Text.StringBuilder s, int n);

    private static readonly IntPtr PerMonitorV2 = new IntPtr(-4);
    private const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004, KEYUP = 0x0002;

    public static string EnterPerMonitor()
    {
        IntPtr prev = SetThreadDpiAwarenessContext(PerMonitorV2);
        return prev == IntPtr.Zero ? "none" : "pmv2";
    }

    public static void Move(int x, int y) { SetCursorPos(x, y); }
    public static void LeftDown() { mouse_event(LEFTDOWN, 0, 0, 0, IntPtr.Zero); }
    public static void LeftUp() { mouse_event(LEFTUP, 0, 0, 0, IntPtr.Zero); }
    public static void KeyDown(byte vk) { keybd_event(vk, 0, 0, IntPtr.Zero); }
    public static void KeyUp(byte vk) { keybd_event(vk, 0, KEYUP, IntPtr.Zero); }

    public static void Combo(byte[] vks)
    {
        foreach (byte v in vks) { KeyDown(v); System.Threading.Thread.Sleep(30); }
        System.Threading.Thread.Sleep(40);
        for (int i = vks.Length - 1; i >= 0; i--) { KeyUp(vks[i]); System.Threading.Thread.Sleep(25); }
    }

    public static string Foreground(int pid, int cmd)
    {
        var procs = System.Diagnostics.Process.GetProcessesByName("QQ");
        foreach (var p in procs)
        {
            if (pid != 0 && p.Id != pid) continue;
            if (p.MainWindowHandle == IntPtr.Zero) continue;
            ShowWindow(p.MainWindowHandle, cmd);
            SetForegroundWindow(p.MainWindowHandle);
            System.Threading.Thread.Sleep(250);
            RECT r; GetWindowRect(p.MainWindowHandle, out r);
            var t = new System.Text.StringBuilder(256); GetWindowTextW(p.MainWindowHandle, t, 256);
            return p.Id + "|" + t.ToString() + "|" + r.L + "," + r.T + "," + r.R + "," + r.B;
        }
        return "not-found";
    }

    public static string Rect(int pid)
    {
        foreach (var p in System.Diagnostics.Process.GetProcessesByName("QQ"))
        {
            if (pid != 0 && p.Id != pid) continue;
            if (p.MainWindowHandle == IntPtr.Zero) continue;
            RECT r; GetWindowRect(p.MainWindowHandle, out r);
            return r.L + "," + r.T + "," + r.R + "," + r.B;
        }
        return "not-found";
    }

    // Pure byte[] maths: Add-Type cannot reference System.Drawing, so the bitmap
    // reading stays in PowerShell and only the arithmetic lives here.
    public static byte[] Gray32(byte[] bgra, int stride, int w, int h)
    {
        var g = new byte[w * h];
        for (int j = 0; j < h; j++)
            for (int i = 0; i < w; i++)
            {
                int o = j * stride + i * 4;   // BGRA in memory
                g[j * w + i] = (byte)((bgra[o] * 29 + bgra[o + 1] * 150 + bgra[o + 2] * 77) >> 8);
            }
        return g;
    }

    public static double Zncc(byte[] a, byte[] b)
    {
        int n = a.Length; double sa = 0, sb = 0;
        for (int i = 0; i < n; i++) { sa += a[i]; sb += b[i]; }
        double ma = sa / n, mb = sb / n, num = 0, da = 0, db = 0;
        for (int i = 0; i < n; i++) { double x = a[i] - ma, y = b[i] - mb; num += x * y; da += x * x; db += y * y; }
        return (da <= 0 || db <= 0) ? -1 : num / System.Math.Sqrt(da * db);
    }

    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);

    // SM_XVIRTUALSCREEN(76)/Y(77)/CX(78)/CY(79) — avoids loading WinForms just for this.
    public static int[] VirtualScreen()
    {
        return new int[] { GetSystemMetrics(76), GetSystemMetrics(77), GetSystemMetrics(78), GetSystemMetrics(79) };
    }

    public static string ForegroundTitle()
    {
        var t = new System.Text.StringBuilder(256);
        GetWindowTextW(GetForegroundWindow(), t, 256);
        return t.ToString();
    }

    private static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
    private static readonly IntPtr HWND_NOTOPMOST = new IntPtr(-2);
    private const uint SWP_NOMOVE = 0x0002, SWP_NOSIZE = 0x0001;

    // The DSH window keeps stealing the foreground, so keyboard steps must run while
    // QQ is pinned above it: a click then both raises QQ and gives it focus.
    public static string Top(int pid, bool on)
    {
        foreach (var p in System.Diagnostics.Process.GetProcessesByName("QQ"))
        {
            if (pid != 0 && p.Id != pid) continue;
            if (p.MainWindowHandle == IntPtr.Zero) continue;
            SetWindowPos(p.MainWindowHandle, on ? HWND_TOPMOST : HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
            BringWindowToTop(p.MainWindowHandle);
            SetForegroundWindow(p.MainWindowHandle);
            System.Threading.Thread.Sleep(200);
            return (on ? "topmost" : "normal") + " pid=" + p.Id;
        }
        return "not-found";
    }
}
"@
    Add-Type -TypeDefinition $src -OutputAssembly $dll
    Add-Type -Path $dll
}
# One grayscale byte[] for a rectangular region — LockBits once, arithmetic in C#.
function Get-GrayArray {
    param($bmp, [int]$x, [int]$y, [int]$w, [int]$h)
    $rect = New-Object System.Drawing.Rectangle($x, $y, $w, $h)
    $clone = $bmp.Clone($rect, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $full = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
    $bd = $clone.LockBits($full, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $stride = $bd.Stride
    $buf = New-Object byte[] ($stride * $h)
    [System.Runtime.InteropServices.Marshal]::Copy($bd.Scan0, $buf, 0, $buf.Length)
    $clone.UnlockBits($bd); $clone.Dispose()
    return [DshInput]::Gray32($buf, $stride, $w, $h)
}

$script:matchFailed = $false

$plan = Get-Content $PlanPath -Raw | ConvertFrom-Json
$ctx = [DshInput]::EnterPerMonitor()
"dpi-context=$ctx  steps=$($plan.steps.Count)"
$held = $false
try {
    foreach ($s in $plan.steps) {
        switch ($s.t) {
            'focus' { "focus -> " + [DshInput]::Foreground([int]$s.pid, [int]$s.cmd) }
            'top' { "top   -> " + [DshInput]::Top([int]$s.pid, [bool]$s.on) }
            'rect' { "rect  -> " + [DshInput]::Rect([int]$s.pid) }
            'move' { [DshInput]::Move([int]$s.x, [int]$s.y) }
            'click' {
                $n = if ($s.n) { [int]$s.n } else { 1 }
                $gap = if ($s.gap) { [int]$s.gap } else { 300 }
                for ($i = 0; $i -lt $n; $i++) {
                    [DshInput]::Move([int]$s.x, [int]$s.y)
                    Start-Sleep -Milliseconds 60
                    [DshInput]::LeftDown(); Start-Sleep -Milliseconds 45; [DshInput]::LeftUp()
                    if ($i -lt $n - 1) { Start-Sleep -Milliseconds $gap }
                }
            }
            'press' { [DshInput]::Move([int]$s.x, [int]$s.y); Start-Sleep -Milliseconds 60; [DshInput]::LeftDown(); $held = $true }
            'glide' {
                foreach ($pt in $s.pts) { [DshInput]::Move([int]$pt[0], [int]$pt[1]); Start-Sleep -Milliseconds ([int]$s.ms) }
            }
            'release' { [DshInput]::LeftUp(); $held = $false }
            'paste' {
                Set-Clipboard -Value ([string]$s.text)
                Start-Sleep -Milliseconds 180
                [DshInput]::Combo(@(0x11, 0x56))
            }
            'key' {
                switch ([string]$s.name) {
                    'enter' { [DshInput]::Combo(@(0x0D)) }
                    'esc' { [DshInput]::Combo(@(0x1B)) }
                    'tab' { [DshInput]::Combo(@(0x09)) }
                    'backspace' { [DshInput]::Combo(@(0x08)) }
                    'ctrl+a' { [DshInput]::Combo(@(0x11, 0x41)) }
                    'ctrl+enter' { [DshInput]::Combo(@(0x11, 0x0D)) }
                    'ctrl+v' { [DshInput]::Combo(@(0x11, 0x56)) }
                    'ctrl+f' { [DshInput]::Combo(@(0x11, 0x46)) }
                    default { throw "unknown key $($s.name)" }
                }
            }
            'match' {
                $rf = New-Object System.Drawing.Bitmap([string]$s.ref)
                $rw = $rf.Width; $rh = $rf.Height; $jit = [int]$s.jitter
                if ($s.live) {
                    # grab just the header strip off the live screen: ~5 ms, no full-frame shot
                    $shot = New-Object System.Drawing.Bitmap(($rw + 2 * $jit), ($rh + 2 * $jit))
                    $g = [System.Drawing.Graphics]::FromImage($shot)
                    $g.CopyFromScreen(([int]$s.x - $jit), ([int]$s.y - $jit), 0, 0, $shot.Size)
                    $g.Dispose()
                    $baseX = $jit; $baseY = $jit
                } else {
                    $shot = New-Object System.Drawing.Bitmap([string]$s.shot)
                    $baseX = [int]$s.x; $baseY = [int]$s.y
                }
                $refg = Get-GrayArray $rf 0 0 $rw $rh
                $best = -2.0; $bx = 0; $by = 0
                for ($dy = -$jit; $dy -le $jit; $dy += 2) {
                    for ($dx = -$jit; $dx -le $jit; $dx += 2) {
                        $px = $baseX + $dx; $py = $baseY + $dy
                        if ($px -lt 0 -or $py -lt 0 -or ($px + $rw) -gt $shot.Width -or ($py + $rh) -gt $shot.Height) { continue }
                        $sc = [DshInput]::Zncc($refg, (Get-GrayArray $shot $px $py $rw $rh))
                        if ($sc -gt $best) { $best = $sc; $bx = $dx; $by = $dy }
                    }
                }
                $shot.Dispose(); $rf.Dispose()
                $verdict = if ($best -ge [double]$s.min) { 'PASS' } else { 'FAIL' }
                "match $($s.label) = {0:F3} (min $($s.min), offset $bx,$by) $verdict" -f $best
                if ($best -lt [double]$s.min) { $script:matchFailed = $true }
            }
            'sleep' { Start-Sleep -Milliseconds ([int]$s.ms) }
            'shot' {
                $vs = [DshInput]::VirtualScreen()
                $bmp = New-Object System.Drawing.Bitmap($vs[2], $vs[3])
                $g = [System.Drawing.Graphics]::FromImage($bmp)
                $g.CopyFromScreen($vs[0], $vs[1], 0, 0, $bmp.Size)
                $bmp.Save([string]$s.path, [System.Drawing.Imaging.ImageFormat]::Png)
                $g.Dispose(); $bmp.Dispose()
                "shot $($s.path)"
            }
            default { throw "unknown step $($s.t)" }
        }
    }
} finally {
    if ($held) { [DshInput]::LeftUp(); "button released by finally" }
    [DshInput]::Move(666, 1032)
    "done (cursor restored); foreground=" + [DshInput]::ForegroundTitle()
}

