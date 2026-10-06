<#
.SYNOPSIS
    Every offline static check for this repo, in one command.

.DESCRIPTION
    Deterministic and headless: no desktop, no actor, no screen.  This is exactly what the
    static job in .github/workflows/ci.yml runs, so a green run here is a green run there.

        pwsh -File tools/ci-static.ps1
        pwsh -File tools/ci-static.ps1 -SkipAnalyzer -SkipNode    # ruff + mypy only

    Stages
      1. parse every .py under actor/, tools/, tests/   (ast.parse: no .pyc is written)
      2. ruff      (settings from ruff.toml, --no-cache so the working tree stays clean)
      3. mypy      (ONE FILE AT A TIME: separate entry scripts all look like __main__)
      4. PSScriptAnalyzer for every .ps1 (settings from PSScriptAnalyzerSettings.psd1)
      5. node --check for the plugin bundles
      6. skills    (check-skill-ops.py: every op a skill names exists in actor/actor.py)
      7. counts    (check-counts.py: one source per count, EN/ZH numbers, Layout manifest)

    Exits 0 only when every stage passed.  -Python overrides the interpreter; the default
    chain is the same one actor.ps1 and tests/score-pipeline.ps1 use.
#>
[CmdletBinding()]
param(
    [string]$Python,
    [switch]$SkipAnalyzer,
    [switch]$SkipNode
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONPYCACHEPREFIX = Join-Path ([IO.Path]::GetTempPath()) 'dsh-vision-pycache'
$failed = New-Object System.Collections.Generic.List[string]

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Stage($name) { Say '' ; Say "── $name" 'Cyan' }
function Verdict($name, $ok, $detail) {
    if ($ok) { Say ("  ✔ {0}: {1}" -f $name, $detail) 'Green' }
    else { Say ("  ✘ {0}: {1}" -f $name, $detail) 'Red'; $script:failed.Add($name) }
}

function Resolve-PythonPath([string]$want) {
    $cands = @()
    if ($want) { $cands += $want }
    if ($env:ACTOR_PY) { $cands += $env:ACTOR_PY }
    $cands += (Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe')
    $cands += @('python', 'python3', 'py')
    foreach ($c in $cands) {
        try {
            $null = & $c -c 'import sys; print(sys.version_info[:2])' 2>$null
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch { continue }   # not a runnable interpreter: try the next candidate
    }
    return $null
}

Push-Location $repo
try {
    Say "repo:   $repo"
    $py = Resolve-PythonPath $Python
    if (-not $py) {
        Say '✘ no python interpreter found (pass -Python <path>)' 'Red'
        exit 1
    }
    Say "python: $py"

    $pyFiles = @(Get-ChildItem -Recurse -File -Filter *.py |
        Where-Object { $_.FullName -notmatch '\\(out|artifacts|node_modules|__pycache__|\.git)\\' } |
        ForEach-Object { $_.FullName.Substring($repo.Length + 1) } | Sort-Object)
    $psFiles = @(Get-ChildItem -Recurse -File -Filter *.ps1 |
        Where-Object { $_.FullName -notmatch '\\(out|artifacts|node_modules|\.git)\\' } |
        ForEach-Object { $_.FullName.Substring($repo.Length + 1) } | Sort-Object)
    $jsFiles = @(Get-ChildItem -Recurse -File -Filter *.js |
        Where-Object { $_.FullName -notmatch '\\(out|artifacts|node_modules|\.git)\\' } |
        ForEach-Object { $_.FullName.Substring($repo.Length + 1) } | Sort-Object)

    # ── 1. parse
    Stage "parse ($($pyFiles.Count) python files)"
    $bad = @()
    foreach ($f in $pyFiles) {
        $out = & $py -c "import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read(), filename=sys.argv[1])" $f 2>&1
        if ($LASTEXITCODE -ne 0) { $bad += $f; Say ("  $f" ) 'Red'; $out | ForEach-Object { Say "    $_" 'Red' } }
    }
    Verdict 'parse' ($bad.Count -eq 0) "$($pyFiles.Count) files, $($bad.Count) with a syntax error"

    # ── 2. ruff
    Stage 'ruff'
    $ruffOut = & $py -m ruff check --no-cache tools actor tests 2>&1
    $ruffCode = $LASTEXITCODE
    $ruffOut | ForEach-Object { Say "  $_" $(if ($ruffCode -eq 0) { 'Gray' } else { 'Red' }) }
    Verdict 'ruff' ($ruffCode -eq 0) $(if ($ruffCode -eq 0) { 'no findings (E9, F, B)' } else { "exit $ruffCode" })

    # ── 3. mypy, one file at a time
    Stage "mypy ($($pyFiles.Count) files, one at a time)"
    $cache = Join-Path ([IO.Path]::GetTempPath()) 'dsh-vision-mypy-cache'
    $mypyBad = @()
    foreach ($f in $pyFiles) {
        $out = & $py -m mypy --config-file mypy.ini --cache-dir $cache $f 2>&1
        if ($LASTEXITCODE -ne 0) { $mypyBad += $f; Say "  $f" 'Red'; $out | ForEach-Object { Say "    $_" 'Red' } }
    }
    Verdict 'mypy' ($mypyBad.Count -eq 0) "$($pyFiles.Count) files, $($mypyBad.Count) with errors"

    # ── 4. PSScriptAnalyzer
    if ($SkipAnalyzer) {
        Say ''
        Say '── PSScriptAnalyzer (skipped)' 'DarkGray'
    } else {
        Stage "PSScriptAnalyzer ($($psFiles.Count) scripts)"
        if (-not (Get-Module -ListAvailable PSScriptAnalyzer)) {
            Say '  PSScriptAnalyzer is not installed.  Install it with:' 'Red'
            Say '    Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -Scope CurrentUser' 'Red'
            $script:failed.Add('PSScriptAnalyzer')
        } else {
            Import-Module PSScriptAnalyzer -Force
            $settings = Join-Path $repo 'PSScriptAnalyzerSettings.psd1'
            $psBad = @()
            foreach ($f in $psFiles) {
                $found = @(Invoke-ScriptAnalyzer -Path $f -Settings $settings)
                if ($found.Count -gt 0) {
                    $psBad += $f
                    foreach ($h in $found) {
                        Say ("  {0}:{1} [{2}] {3}" -f $f, $h.Line, $h.RuleName, $h.Message) 'Red'
                    }
                }
            }
            Verdict 'PSScriptAnalyzer' ($psBad.Count -eq 0) "$($psFiles.Count) scripts, $($psBad.Count) with findings"
        }
    }

    # ── 5. node --check
    if ($SkipNode) {
        Say ''
        Say '── node --check (skipped)' 'DarkGray'
    } else {
        Stage "node --check ($($jsFiles.Count) bundles)"
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
            Say '  node is not on PATH (pass -SkipNode to ignore the plugin bundles)' 'Red'
            $script:failed.Add('node')
        } else {
            $jsBad = @()
            foreach ($f in $jsFiles) {
                $out = & node --check $f 2>&1
                if ($LASTEXITCODE -ne 0) { $jsBad += $f; Say "  $f" 'Red'; $out | ForEach-Object { Say "    $_" 'Red' } }
            }
            Verdict 'node --check' ($jsBad.Count -eq 0) "$($jsFiles.Count) bundles, $($jsBad.Count) with a syntax error"
        }
    }

    # ── 6. skills
    $skillFiles = @(Get-ChildItem (Join-Path $repo 'skills') -Recurse -File -Filter 'SKILL.md' -ErrorAction SilentlyContinue)
    Stage "skills ($($skillFiles.Count) file(s))"
    $skillOut = & $py tools/check-skill-ops.py 2>&1
    $skillCode = $LASTEXITCODE
    $skillOut | ForEach-Object { Say "  $_" $(if ($skillCode -eq 0) { 'Gray' } else { 'Red' }) }
    Verdict 'skills' ($skillCode -eq 0) $(if ($skillCode -eq 0) { 'op names match actor/actor.py' } else { "exit $skillCode" })

    # ── 7. counts
    Stage 'counts'
    $countOut = & $py tools/check-counts.py 2>&1
    $countCode = $LASTEXITCODE
    $countOut | ForEach-Object { Say "  $_" $(if ($countCode -eq 0) { 'Gray' } else { 'Red' }) }
    Verdict 'counts' ($countCode -eq 0) $(if ($countCode -eq 0) { 'counts, bilingual numbers and the Layout manifest agree' } else { "exit $countCode" })

    Say ''
    if ($failed.Count -eq 0) {
        Say '── static checks: all stages passed' 'Green'
        exit 0
    }
    Say ("── static checks: {0} stage(s) failed: {1}" -f $failed.Count, ($failed -join ', ')) 'Red'
    exit 1
} finally {
    Pop-Location
}
