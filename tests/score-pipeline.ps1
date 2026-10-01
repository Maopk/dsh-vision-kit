<#
.SYNOPSIS
    Score the two zero-model detectors against a ground-truth box.

.DESCRIPTION
    Runs tools/cv_ui_geometry.py (run-length border lines + morphology blobs) and,
    when -Template is given, tools/template_match.py (FFT normalized cross
    correlation) on -Image, and reports each result next to -Expect.

    The point of this script is that a geometry tool which was never scored is
    indistinguishable from a correct one — the first border detector in this repo
    returned "0 lines" and looked perfectly plausible.

.EXAMPLE
    pwsh -File tests/score-pipeline.ps1 -Image docs/images/sample-screenshot.png `
        -Expect 2337,1305,2561,1529 `
        -Template "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\dsh-whale-widget\assets\DSniang1.png"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Image,
    [Parameter(Mandatory)][string]$Expect,          # x0,y0,x1,y1 in original pixels
    [string]$Template,
    [double[]]$Scales = @(0.34, 0.3656, 0.40),
    [string]$Python,
    [string]$OutDir = (Join-Path $PSScriptRoot '..\out')
)

$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..')

if (-not $Python) {
    $candidates = @(
        "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
        'python', 'python3'
    )
    foreach ($c in $candidates) {
        try { & $c -c 'import numpy, PIL' 2>$null; if ($LASTEXITCODE -eq 0) { $Python = $c; break } } catch { }
    }
}
if (-not $Python) { throw "No python with numpy + Pillow found. Pass -Python <path>." }
"python     : $Python"
"image      : $Image"
"ground truth: $Expect"
""

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

"=== 1) run-length border lines / blobs (no model) ==="
$cvJson = & $Python (Join-Path $repo 'tools\cv_ui_geometry.py') $Image --noise 3 --min-run 250 `
    --expect $Expect --annotate (Join-Path $OutDir 'cv-annotated.png')
$cvJson | Out-String | Write-Output

if ($Template) {
    ""
    "=== 2) template matching (no model) ==="
    $tmJson = & $Python (Join-Path $repo 'tools\template_match.py') $Image $Template `
        --scales ($Scales -join ',') --expect $Expect --annotate (Join-Path $OutDir 'template-annotated.png')
    $tmJson | Out-String | Write-Output
} else {
    ""
    "(skipped template matching: no -Template)"
}

""
"annotated PNGs in: $OutDir"
