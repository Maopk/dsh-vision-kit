<#
  DSH PC Actor - Windows side helper.

    .\actor.ps1 -Setup            provision: create the home, install python deps there (idempotent)
    .\actor.ps1 -Start            start the daemon detached (auto-runs -Setup if deps are missing)
    .\actor.ps1 -Status           ping it (pid / uptime / screen geometry / dpi)
    .\actor.ps1 -Bench            measure capture / move / UIA costs
    .\actor.ps1 -Send '{"op":"shot","path":"D:/tmp/a.png"}'
    .\actor.ps1 -File skills\whatever.json     (a saved skill = {"op":"run","steps":[...]})
    .\actor.ps1 -Tail             tail the actor log
    .\actor.ps1 -Stop             stop the daemon
    .\actor.ps1 -Where            print the resolved home (state dir)  [not -Home: $HOME is read-only in PS]

  Layout: this directory holds CODE only.  Every mutable thing - python deps
  (pylibs), logs, port.txt, tmp, pip cache - lives in the HOME, which is machine
  local and typically outside the repo (this box: D:\DSH\dsh-actor, recorded in
  home.txt next to this script).  Nothing generated ever lands in git, and no
  download or temp file is written to C:.
#>
param(
  [switch]$Setup, [switch]$Start, [switch]$Stop, [switch]$Status, [switch]$Bench,
  [switch]$Tail, [switch]$Where, [string]$Send, [string]$File, [int]$Port = 8731,
  [string]$HomePath
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
# DSH's bundled runtime (python 3.12 + numpy 2.3.5); it is the interpreter the actor is tested on.
$py = 'C:\Users\28794\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'

function Resolve-Home {
  if ($HomePath) { return $HomePath }
  if ($env:ACTOR_HOME) { return $env:ACTOR_HOME }
  $f = Join-Path $here 'home.txt'
  if (Test-Path $f) { $v = (Get-Content $f -Raw).Trim(); if ($v) { return $v } }
  return $here
}

$actorHome = (Resolve-Home).TrimEnd('\', '/')
$env:ACTOR_HOME      = $actorHome
$env:PYTHONPATH      = Join-Path $actorHome 'pylibs'
$env:PYTHONUTF8      = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:TEMP            = Join-Path $actorHome 'tmp'     # keep C: from growing
$env:TMP             = $env:TEMP
$env:PIP_CACHE_DIR   = Join-Path $actorHome 'cache\pip'
$env:ACTOR_PORT      = "$Port"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

function Get-ActorPython { if (Test-Path $py) { return $py } return (Get-Command python).Source }

function Test-Actor {
  try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $Port); $c.Close(); return $true }
  catch { return $false }
}

function Initialize-Home {
  foreach ($d in 'pylibs', 'logs', 'tmp', 'cache\pip') {
    New-Item -ItemType Directory -Force -Path (Join-Path $actorHome $d) | Out-Null
  }
  if ($actorHome -ne $here) { Set-Content -Path (Join-Path $here 'home.txt') -Value $actorHome -Encoding utf8 }
  $marker = Join-Path $actorHome 'pylibs\comtypes\__init__.py'
  if (-not (Test-Path $marker)) {
    "provisioning comtypes into $actorHome\pylibs ..."
    & (Get-ActorPython) -m pip install --target (Join-Path $actorHome 'pylibs') --upgrade --no-warn-script-location comtypes
    if ($LASTEXITCODE -ne 0) { "pip failed ($LASTEXITCODE)"; exit 1 }
  }
  "pylibs ready: $(Test-Path $marker)"
}

if ($Setup) { Initialize-Home; "home = $actorHome"; exit 0 }
elseif ($Where) { "home = $actorHome"; "code = $here"; "deps = $(Test-Path (Join-Path $actorHome 'pylibs\comtypes\__init__.py'))"; exit 0 }

if ($Start) {
  if (Test-Actor) { "actor already running on port $Port"; exit 0 }
  if (-not (Test-Path (Join-Path $actorHome 'pylibs\comtypes\__init__.py'))) { Initialize-Home }
  New-Item -ItemType Directory -Force -Path (Join-Path $actorHome 'logs') | Out-Null
  Start-Process -FilePath (Get-ActorPython) -ArgumentList @("$here\actor.py", '--port', "$Port") -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $actorHome 'logs\stdout.log') -RedirectStandardError (Join-Path $actorHome 'logs\stderr.log')
  for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 250; if (Test-Actor) { break } }
  if (Test-Actor) { "actor started on port $Port (home=$actorHome)" } else { "FAILED to start - see $actorHome\logs\stderr.log"; exit 1 }
}
elseif ($Stop) { & (Get-ActorPython) "$here\act.py" '{"op":"stop"}'; Start-Sleep -Milliseconds 400; "stopped" }
elseif ($Status) { & (Get-ActorPython) "$here\act.py" ping }
elseif ($Bench) { & (Get-ActorPython) "$here\act.py" '{"op":"bench","n":20}' --json }
elseif ($Tail) { & (Get-ActorPython) "$here\act.py" '{"op":"log","tail":30}' --json }
elseif ($Send) { & (Get-ActorPython) "$here\act.py" $Send }
elseif ($File) { & (Get-ActorPython) "$here\act.py" run $File }
else { Get-Help $PSCommandPath }
