param(
    [Parameter(Mandatory = $true)][string]$Text,
    [string]$Contact,                      # omit = use the chat that is already open
    [int]$SearchY = 195,                   # "联系人" row of the search dropdown (physical px)
    [switch]$DryRun,                       # paste + verify, then clear; never sends
    [string]$Work = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
$engine = Join-Path $Work 'gui-steps.ps1'
$py = if ($env:DSH_PYTHON) { $env:DSH_PYTHON } else { 'python' }

$pre = Join-Path $Work 'qs-pre.png'
$post = Join-Path $Work 'qs-post.png'
$clr = Join-Path $Work 'qs-cleared.png'

$s = New-Object System.Collections.ArrayList
function Add-Step($o) { [void]$s.Add($o) }

# 1. the target app must be above the DSH window, otherwise the keystrokes land in DSH.
Add-Step @{ t = 'top'; pid = 0; on = $true }
Add-Step @{ t = 'sleep'; ms = 250 }

# 2. optional: find the contact (only needed when the wanted chat is not open)
if ($Contact) {
    Add-Step @{ t = 'click'; x = 185; y = 70 }
    Add-Step @{ t = 'sleep'; ms = 300 }
    Add-Step @{ t = 'key'; name = 'ctrl+a' }
    Add-Step @{ t = 'key'; name = 'backspace' }
    Add-Step @{ t = 'paste'; text = $Contact }
    Add-Step @{ t = 'sleep'; ms = 900 }
    Add-Step @{ t = 'click'; x = 170; y = $SearchY }
    Add-Step @{ t = 'sleep'; ms = 800 }
}

Add-Step @{ t = 'shot'; path = $pre }
Add-Step @{ t = 'click'; x = 1400; y = 1400 }      # composer: click both focuses and places the caret
Add-Step @{ t = 'sleep'; ms = 350 }
Add-Step @{ t = 'key'; name = 'ctrl+a' }
Add-Step @{ t = 'sleep'; ms = 120 }
Add-Step @{ t = 'key'; name = 'backspace' }
Add-Step @{ t = 'paste'; text = $Text }
Add-Step @{ t = 'sleep'; ms = 500 }
$draftShot = Join-Path $Work 'qs-draft.png'
if (-not $DryRun) {
    Add-Step @{ t = 'shot'; path = $draftShot }
    Add-Step @{ t = 'key'; name = 'ctrl+enter' }    # QQNT sends on Ctrl+Enter only
    Add-Step @{ t = 'sleep'; ms = 2200 }
}
Add-Step @{ t = 'shot'; path = $post }
if ($DryRun) {
    Add-Step @{ t = 'key'; name = 'ctrl+a' }
    Add-Step @{ t = 'key'; name = 'backspace' }
    Add-Step @{ t = 'sleep'; ms = 350 }
    Add-Step @{ t = 'shot'; path = $clr }
}
Add-Step @{ t = 'top'; pid = 0; on = $false }

$plan = Join-Path $Work 'qs-plan.json'
@{ steps = $s } | ConvertTo-Json -Depth 6 -Compress | Set-Content $plan -Encoding UTF8

$sw = [Diagnostics.Stopwatch]::StartNew()
& $engine -PlanPath $plan
$sw.Stop()

$mode = if ($DryRun) { 'dry' } else { 'send' }
$args2 = @((Join-Path $Work 'pixel-verdict.py'), '--pre', $pre, '--post', $post, '--mode', $mode)
if ($DryRun) { $args2 += @('--cleared', $clr) } else { $args2 += @('--draft', $draftShot) }
$env:PYTHONIOENCODING = 'utf-8'
& $py @args2 2>$null

"total {0:N1}s  (one tool call, no model round trips in between)" -f $sw.Elapsed.TotalSeconds

