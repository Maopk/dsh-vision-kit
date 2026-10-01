# dsh-selflook-local (native-capture fork)

Lets a DSH agent look at the screen. Two capture paths, and the difference matters:

| | `native` | `dom` |
|---|---|---|
| what it captures | the real screen pixels (`CopyFromScreen`) | the page's own DOM, serialized to SVG `<foreignObject>` → canvas → PNG |
| images/widgets/canvas | ✅ visible | ❌ stripped before rendering |
| other windows, tray, other monitors | ✅ visible | ❌ invisible |
| DPI | per-monitor aware (PMv2 → PMv1), refuses to capture without a context | n/a (CSS pixels) |
| cost | ~1.1–1.6 s, one PowerShell child process | in-page, no process, ~2.5 s |

Measured on a 2560×1600 / dpr 1.5 desktop: in the bottom-right corner where the
whale widget sits, the DOM capture came back **flat** (pixel std-dev 0.004) while
the native capture showed the widget (24% colorful pixels in that box).

## RPC

`POST /__dsh__/selflook/rpc` (JSON body `{ "method": …, … }`). The route is
same-origin guarded: a cross-site `Sec-Fetch-Site` / mismatched `Origin` gets 403.

| method | body | returns |
|---|---|---|
| `info` | — | `{ ok, version, native, last, outdir, platform }` |
| `shot` | `{ id? }` | `{ ok, mode:"native", path, bytes, width, height, ms }` |
| `poll` | — | `{ pending:true }` (client should capture + `deliver`), or `{ pending:false, mode:"native", … }` when the host already captured |
| `deliver` | `{ id, base64, width?, height? }` | `{ ok, mode:"dom", path }` |
| `log` | `{ line }` | `{ ok }` |

```powershell
# one-shot native screenshot, no page round-trip
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:19387/__dsh__/selflook/rpc `
  -ContentType 'application/json' -Body '{"method":"shot"}'
```

## Trigger file protocol

Writing `~/.dsh-look-request` still works (the page polls every 2 s):

- empty body → **native** capture by the host; the page is not involved;
- body `dom` → the old path: the page serializes itself and calls `deliver`.

Either way the newest PNG path is appended to `~/.dsh-look-last.txt`, and images
go to `~/Downloads/dsh/图片/` on Windows (`~/storage/downloads/dsh/图片/` elsewhere).

## Requirements

- Windows (the native path is Win32-specific; the DOM path works anywhere).
- `Add-Type` needs a writable `TEMP`/`TMP`; the plugin runs the capture with a
  scratch directory as cwd/TEMP/TMP so non-ASCII temp paths cannot break Roslyn.

## Install

See the repository README. Short version: copy this directory into
`<DSH_HOME>/profiles/<profile>/local/`, add `"dsh-selflook-local": "file:./local/dsh-selflook-local"`
to that profile's `package.json` dependencies, add `dsh-selflook-local` to
`dsh.profile.bundles`, restart DSH.

## Scope warning

Native capture grabs **everything on screen**, including other applications and
potentially sensitive content. Use it deliberately.
