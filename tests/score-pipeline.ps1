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
    [string]$OutDir = (Join-Path $PSScriptRoot '..\out'),
    [switch]$Strict,          # exit 1 unless every expected edge lands within -TolerancePx
    [double]$TolerancePx = 4  # the run-length detector reports the inner edge of a stroke
)

$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..')

if (-not $Python) {
    # Same chain as actor.ps1 / act.cmd: %ACTOR_PY%, DSH's bundled runtime, python on PATH, py.
    $candidates = @(
        $env:ACTOR_PY,
        (Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'),
        'python', 'python3', 'py'
    )
    foreach ($c in $candidates) {
        if (-not $c) { continue }
        $exe = $c
        if (-not (Test-Path $c)) {
            $cmd = Get-Command $c -ErrorAction SilentlyContinue
            if (-not $cmd) { continue }
            $exe = $cmd.Source
        }
        try { & $exe -c 'import numpy, PIL' 2>$null; if ($LASTEXITCODE -eq 0) { $Python = $exe; break } }
        catch { Write-Verbose "not a usable interpreter: $c ($($_.Exception.Message))" }
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

if ($Strict) {
    # The blob IoU above grades the morphology pass; the border lines are what a window frame
    # turns into.  Score them too: every expected edge must have a detected line within
    # -TolerancePx pixels, and the worst one decides.
    $cv = ($cvJson | Out-String) | ConvertFrom-Json
    $want = [double[]]($Expect -split ',')
    $vx = @($cv.vertical_borders | ForEach-Object { [double]$_.at })
    $hy = @($cv.horizontal_borders | ForEach-Object { [double]$_.at })
    $worst = 0.0
    foreach ($edge in @(
            @{ name = 'x0'; want = $want[0]; pool = $vx },
            @{ name = 'y0'; want = $want[1]; pool = $hy },
            @{ name = 'x1'; want = $want[2]; pool = $vx },
            @{ name = 'y1'; want = $want[3]; pool = $hy })) {
        if ($edge.pool.Count -eq 0) { throw "Strict: no border lines detected on that axis at all" }
        $hit = $edge.pool | Sort-Object { [math]::Abs($_ - $edge.want) } | Select-Object -First 1
        $d = [math]::Abs($hit - $edge.want)
        "{0,-3} want {1,6}  nearest line {2,6}  dpx {3}" -f $edge.name, $edge.want, $hit, $d
        if ($d -gt $worst) { $worst = $d }
    }
    "edges      : worst {0} px (tolerance {1} px)" -f $worst, $TolerancePx
    if ($worst -gt $TolerancePx) { throw "Strict: worst edge error $worst px > $TolerancePx px" }
}

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
