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
    .\actor.ps1 -Python <exe>     pick the interpreter (default chain: -Python / %ACTOR_PY% ->
                                  DSH's bundled runtime -> python on PATH -> py)

  Layout: this directory holds CODE only.  Every mutable thing - python deps
  (pylibs), logs, port.txt, tmp, pip cache - lives in the HOME, which is machine
  local and typically outside the repo (whatever this box uses is recorded in
  home.txt next to this script, and -Where prints it).  Nothing generated ever
  lands in git, and no download or temp file is written to C:.
#>
param(
  [switch]$Setup, [switch]$Start, [switch]$Stop, [switch]$Status, [switch]$Bench,
  [switch]$Tail, [switch]$Where, [string]$Send, [string]$File, [int]$Port = 8731,
  [string]$HomePath, [string]$Python
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
# Interpreter, best first: -Python / %ACTOR_PY%, DSH's bundled runtime (python 3.12 + numpy 2.3.5 -
# the one the actor is tested on), whatever "python" resolves to, then the py launcher.  No absolute
# path is committed here (CONTRIBUTING, ground rule 2); the bundled runtime is a candidate, not a
# requirement, so a checkout on another box still starts.
$pyCandidates = @()
if ($Python) { $pyCandidates += $Python }
if ($env:ACTOR_PY) { $pyCandidates += $env:ACTOR_PY }
$pyCandidates += (Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe')
$pyCandidates += 'python', 'py'

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
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { Write-Verbose "console encoding left as-is: $($_.Exception.Message)" }

function Get-ActorPython {
  foreach ($c in $pyCandidates) {
    if (-not $c) { continue }
    if ($c -match '[\\/]') { if (Test-Path $c) { return $c } }
    else { $cmd = Get-Command $c -ErrorAction SilentlyContinue; if ($cmd) { return $cmd.Source } }
  }
  throw "No python found. Put python on PATH, or pass -Python <path to python.exe>."
}

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
