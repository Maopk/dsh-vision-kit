// dsh-selflook-local — let the AI look at the DSH UI as an image.
// Two capture paths:
//   native  — DPI-aware Windows screen capture (real pixels: images, widgets, overlays, other windows)
//   dom     — the browser serialises its own DOM to PNG (text/layout only; <img|svg|canvas> are stripped)
// bundle convention: named name/inject exports + apply.inject + export default apply
import { execFile } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile, appendFile, stat } from 'node:fs/promises'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'

export const name = 'dsh-selflook-local'
export const inject = ['webServer']

const HOME = homedir()
const REQ = join(HOME, '.dsh-look-request')
const LAST = join(HOME, '.dsh-look-last.txt')
const LOG_PATH = join(HOME, '.dsh-look-client.log')
// Termux keeps its old shared-storage location; Windows uses the real Downloads folder.
const OUTDIR = process.platform === 'win32' ? join(HOME, 'Downloads', 'dsh', '图片') : join(HOME, 'storage', 'downloads', 'dsh', '图片')

// Per-monitor-DPI-aware capture of the whole virtual desktop. Without the PMv2/PMv1
// context the bitmap comes back in logical coordinates and is silently misaligned /
// cropped on scaled displays, so a missing context is a hard failure, not a fallback.
function buildCaptureScript(outPath) {
  const quoted = `'${String(outPath).replace(/'/g, "''")}'`
  const source = String.raw`using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public static class DshSelfLookCapture
{
    private static readonly IntPtr PerMonitorV2 = new IntPtr(-4);
    private static readonly IntPtr PerMonitorV1 = new IntPtr(-3);

    [DllImport("user32.dll")]
    private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr dpiContext);

    private static IntPtr TrySetContext(IntPtr context)
    {
        try { return SetThreadDpiAwarenessContext(context); }
        catch (EntryPointNotFoundException) { return IntPtr.Zero; }
        catch (DllNotFoundException) { return IntPtr.Zero; }
    }

    private static IntPtr EnterPerMonitorContext()
    {
        IntPtr previous = TrySetContext(PerMonitorV2);
        if (previous == IntPtr.Zero) previous = TrySetContext(PerMonitorV1);
        if (previous == IntPtr.Zero)
        {
            throw new InvalidOperationException(
                "could not enter a per-monitor DPI awareness context; refusing a misaligned screenshot");
        }
        return previous;
    }

    private static void RestoreContext(IntPtr previous)
    {
        try { SetThreadDpiAwarenessContext(previous); } catch { }
    }

    public static void Capture(string outputPath)
    {
        IntPtr previous = EnterPerMonitorContext();
        try
        {
            Rectangle bounds = SystemInformation.VirtualScreen;
            if (bounds.Width <= 0 || bounds.Height <= 0)
            {
                throw new InvalidOperationException("the Windows virtual screen has no drawable area");
            }

            using (var bitmap = new Bitmap(bounds.Width, bounds.Height))
            using (var graphics = Graphics.FromImage(bitmap))
            {
                graphics.CopyFromScreen(bounds.X, bounds.Y, 0, 0, bounds.Size);
                bitmap.Save(outputPath);
            }
        }
        finally { RestoreContext(previous); }
    }
}`

  return [
    'Add-Type -AssemblyName System.Windows.Forms,System.Drawing',
    `$__dshSelfLookSource = @'\n${source}\n'@`,
    'try {',
    "  Add-Type -TypeDefinition $__dshSelfLookSource -ReferencedAssemblies 'System.Windows.Forms.dll','System.Drawing.dll' -ErrorAction Stop",
    '} catch {',
    "  throw ('selflook: failed to initialize the DPI-aware capture helper: ' + $_.Exception.Message)",
    '}',
    `[DshSelfLookCapture]::Capture(${quoted})`,
  ].join('\n')
}

function execFileAsync(file, args, options) {
  return new Promise((resolve, reject) => {
    execFile(file, args, options, (error, stdout, stderr) => {
      if (error) {
        try {
          error.stdout = stdout
          error.stderr = stderr
        } catch (_) { /* keep the original error */ }
        reject(error)
        return
      }
      resolve({ stdout, stderr })
    })
  })
}

function apply(ctx) {
  let inFlightAt = 0   // last trigger time; past 15s counts as abandoned and may be retriggered

  // DSH's web server hands plugin routes to the handler without authenticating them, so
  // refuse anything a browser marks as cross-site: a page the user merely visited could
  // otherwise POST here. Local non-browser callers send no Sec-Fetch-Site/Origin and are
  // still served — they already run with this user's own privileges.
  function isCrossSite(req) {
    const site = String(req.headers['sec-fetch-site'] || '').toLowerCase()
    if (site === 'cross-site' || site === 'same-site') return true
    const origin = String(req.headers['origin'] || '')
    if (origin === '') return false
    try {
      return new URL(origin).host !== String(req.headers['host'] || '')
    } catch (_) {
      return true
    }
  }

  // Real screen pixels. CodeDom-backed Add-Type compiles C# through TEMP/TMP, so the
  // helper runs in a fresh scratch dir to survive non-ASCII temp paths.
  async function captureNative() {
    if (process.platform !== 'win32') throw new Error('native capture is only implemented for win32')
    const stamp = new Date().toISOString().replace(/[:.]/g, '-')
    await mkdir(OUTDIR, { recursive: true })
    const out = join(OUTDIR, 'self-look-' + stamp + '.png')
    const scratch = await mkdtemp(join(tmpdir(), 'dsh-selflook-capture-'))
    const started = Date.now()
    try {
      await execFileAsync('powershell.exe', ['-NoProfile', '-NonInteractive', '-STA', '-Command', buildCaptureScript(out)], {
        windowsHide: true,
        cwd: scratch,
        timeout: 30000,
        env: { ...process.env, TEMP: scratch, TMP: scratch },
      })
    } finally {
      await rm(scratch, { recursive: true, force: true, maxRetries: 3, retryDelay: 50 }).catch(() => { /* best effort */ })
    }
    const info = await stat(out)
    const png = await readFile(out)
    const width = png.length >= 24 ? png.readUInt32BE(16) : 0
    const height = png.length >= 24 ? png.readUInt32BE(20) : 0
    await writeFile(LAST, out)
    return { mode: 'native', path: out, bytes: info.size, width: width, height: height, ms: Date.now() - started }
  }

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/__dsh__/selflook/rpc',
    handler: async (req, res) => {
      if (isCrossSite(req)) {
        try { res.writeHead(403, { 'Content-Type': 'text/plain' }); res.end('forbidden') } catch (_) { /* ignore */ }
        return
      }
      const send = (code, obj) => {
        try { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)) } catch (_) { /* ignore */ }
      }
      try {
        const chunks = []
        for await (const c of req) chunks.push(c)
        const body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')
        const method = String(body.method || '')

        if (method === 'info') {
          return send(200, {
            ok: true,
            home: HOME,
            outdir: OUTDIR,
            platform: process.platform,
            native: process.platform === 'win32',
            last: await readFile(LAST, 'utf8').then((s) => s.trim()).catch(() => null),
          })
        }

        // Native capture on demand — one HTTP call, no page round-trip.
        if (method === 'shot') {
          const r = await captureNative()
          console.log('[selflook] native ' + r.path + ' ' + r.width + 'x' + r.height + ' ' + r.ms + 'ms')
          return send(200, { ok: true, ...r })
        }

        // File-triggered capture (the classic path). A request whose body says `dom`
        // still asks the page to render itself; anything else prefers native pixels.
        if (method === 'poll') {
          const raw = await readFile(REQ, 'utf8').then((s) => s.trim().toLowerCase()).catch(() => null)
          const stale = (Date.now() - inFlightAt) > 15000
          if (raw !== null && stale) {
            inFlightAt = Date.now()
            if (raw !== 'dom') {
              try {
                const r = await captureNative()
                await rm(REQ, { force: true })
                inFlightAt = 0
                console.log('[selflook] native ' + r.path + ' ' + r.width + 'x' + r.height + ' ' + r.ms + 'ms')
                return send(200, { pending: false, captured: r })
              } catch (e) {
                // Fall back to the DOM path rather than losing the trigger.
                await appendFile(LOG_PATH, '[' + new Date().toISOString() + '] native failed: ' + (e && e.message ? e.message : String(e)) + '\n').catch(() => {})
              }
            }
            await rm(REQ, { force: true })
            return send(200, { pending: true })
          }
          return send(200, { pending: false })
        }

        if (method === 'deliver') {
          const b64 = String(body.base64 || '')
          if (b64 === '') throw new Error('empty image data')
          const stamp = new Date().toISOString().replace(/[:.]/g, '-')
          const path = join(OUTDIR, 'self-look-' + stamp + '.png')
          await mkdir(OUTDIR, { recursive: true })
          const bytes = Buffer.from(b64, 'base64')
          await writeFile(path, bytes)
          await writeFile(LAST, path)
          inFlightAt = 0
          console.log('[selflook] saved ' + path)
          return send(200, { ok: true, mode: 'dom', path: path })
        }

        if (method === 'log') {
          const msg = String(body.message || '').slice(0, 2000)
          const line = '[' + new Date().toISOString() + '] ' + msg + '\n'
          await appendFile(LOG_PATH, line)
          return send(200, { ok: true })
        }

        return send(200, { ok: false, error: 'unknown method ' + method })
      } catch (e) {
        inFlightAt = 0
        return send(500, { ok: false, error: (e && e.message) ? e.message : String(e) })
      }
    },
  }))

  console.log('[selflook] host ready (native=' + (process.platform === 'win32') + ')')
}

apply.inject = inject
export default apply
