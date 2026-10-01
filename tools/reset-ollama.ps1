# reset-ollama.ps1 — restore a healthy Ollama server on this machine.
#
# Why this exists (root cause found 2026-10-01):
#   Killing `ollama serve` / `ollama app` does NOT kill their `llama-server.exe`
#   children. Those orphans keep holding the model in VRAM (measured: 4 orphans
#   holding 7.6 GB of this laptop's 8.15 GB GPU). Every later model load then
#   thrashes in a nearly full GPU and requests hang, so the DSH vision router
#   reports `local-ollama/*: TIMEOUT` after 60-90 s and calls look "too slow".
#   On a clean GPU the same model answers a 0.23 MP screenshot in ~5 s.
#
# Usage:  pwsh -File C:\Users\28794\ollama-setup\reset-ollama.ps1
param(
  [string]$Model = 'qwen2.5vl:3b',
  [int]$VramFreeTargetMiB = 1000
)

$ErrorActionPreference = 'Continue'
$smi    = 'C:\Windows\System32\nvidia-smi.exe'
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"

function Get-Vram {
  try {
    $line = (& $smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    return [int]($line.Trim())
  } catch { return -1 }
}

Write-Host '[1/4] stopping ollama and orphaned llama-server children ...'
Get-Process 'ollama', 'ollama app', 'llama-server' -ErrorAction SilentlyContinue | ForEach-Object {
  Write-Host ("      kill {0} pid={1}" -f $_.ProcessName, $_.Id)
  Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

Write-Host '[2/4] waiting for VRAM to drain ...'
$deadline = (Get-Date).AddSeconds(30)
do {
  Start-Sleep -Seconds 2
  $used = Get-Vram
} while ($used -gt $VramFreeTargetMiB -and (Get-Date) -lt $deadline)
Write-Host ("      GPU memory in use: {0} MiB" -f $used)
if ($used -gt $VramFreeTargetMiB) {
  Write-Host '      WARNING: something else still holds the GPU (check nvidia-smi --query-compute-apps=pid,process_name)'
}

Write-Host '[3/4] starting one clean server (detached) ...'
$created = ([wmiclass]'Win32_Process').Create("`"$ollama`" serve")
Write-Host ("      pid={0}" -f $created.ProcessId)
$up = $false
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 2
  try {
    $v = (Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2).version
    Write-Host ("      API UP {0}" -f $v)
    $up = $true
    break
  } catch { }
}
if (-not $up) { Write-Host '      API DID NOT COME UP'; exit 1 }

Write-Host ("[4/4] warming {0} ..." -f $Model)
$body = @{ model = $Model; prompt = 'ok'; stream = $false; options = @{ num_predict = 1 } } | ConvertTo-Json -Depth 4
$t0 = Get-Date
try {
  Invoke-RestMethod 'http://127.0.0.1:11434/api/generate' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 300 | Out-Null
  Write-Host ("      warm in {0:N1}s" -f ((Get-Date) - $t0).TotalSeconds)
} catch {
  Write-Host ("      warm-up failed: {0}" -f $_.Exception.Message)
}
& $ollama ps
