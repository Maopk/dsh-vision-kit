<#
.SYNOPSIS
    Mirror this repo's skills/ into the DSH skills home, and report drift with -Check.

.DESCRIPTION
    DSH loads a skill from <skills-home>/<name>/SKILL.md.  This repo is the single source of
    truth for that content; the skills home is a mirror of it.  Nothing outside the named
    skills is touched, and nothing is ever deleted.

        pwsh -File tools/install-skills.ps1 -Check         # report only, exit 1 when out of sync
        pwsh -File tools/install-skills.ps1                # sync: symlink when possible, else copy
        pwsh -File tools/install-skills.ps1 -Mode Copy     # force copies
        pwsh -File tools/install-skills.ps1 -SkillsHome D:\somewhere\skills

    Default skills home: $env:DSH_HOME\skills, falling back to %USERPROFILE%\.dsh\skills.
    With -Mode Auto a symbolic link is tried first (needs Developer Mode or an elevated
    shell); when that fails the file is copied and the script says so.  A linked skill stays
    in step with the repo automatically; a copied one must be re-synced after every edit.
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [ValidateSet('Auto', 'Copy', 'Link')][string]$Mode = 'Auto',
    [string]$SkillsHome
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$src = Join-Path $repo 'skills'

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

if (-not (Test-Path $src)) { Say "✘ no skills/ directory in $repo" 'Red'; exit 1 }
if (-not $SkillsHome) {
    if ($env:DSH_HOME) { $SkillsHome = Join-Path $env:DSH_HOME 'skills' }
    else { $SkillsHome = Join-Path $env:USERPROFILE '.dsh\skills' }
}

function Get-Hash($path) {
    if (Test-Path $path) { (Get-FileHash -Algorithm SHA256 -Path $path).Hash } else { $null }
}
function Test-SameFile($a, $b) {
    $ha = Get-Hash $a
    $hb = Get-Hash $b
    return [bool]($ha -and $hb -and ($ha -eq $hb))
}

Say "repo skills : $src"
Say "skills home : $SkillsHome"
Say ''

$dirs = @(Get-ChildItem $src -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'SKILL.md') } | Sort-Object Name)
if ($dirs.Count -eq 0) {
    Say '✘ skills/ has no <name>/SKILL.md — nothing to install' 'Red'
    exit 1
}

$inSync = New-Object System.Collections.Generic.List[string]
$todo = New-Object System.Collections.Generic.List[string]
foreach ($d in $dirs) {
    $from = Join-Path $d.FullName 'SKILL.md'
    $to = Join-Path (Join-Path $SkillsHome $d.Name) 'SKILL.md'
    if (Test-SameFile $from $to) { $inSync.Add($d.Name) } else { $todo.Add($d.Name) }
}

$known = @($dirs | ForEach-Object { $_.Name })
$extra = @()
if (Test-Path $SkillsHome) {
    $extra = @(Get-ChildItem $SkillsHome -Directory |
        Where-Object { $known -notcontains $_.Name } | ForEach-Object { $_.Name })
}

if ($Check) {
    foreach ($n in $inSync) { Say ("  ✔ {0}" -f $n) 'Green' }
    foreach ($n in $todo) { Say ("  ✘ {0}: missing, or differs from the repo copy" -f $n) 'Red' }
    foreach ($n in $extra) { Say ("  · {0}: present in the skills home, not in this repo (left alone)" -f $n) 'DarkGray' }
    Say ''
    Say ("{0} 一致 · {1} 需同步 · {2} 多余" -f $inSync.Count, $todo.Count, $extra.Count) `
        $(if ($todo.Count -eq 0) { 'Green' } else { 'Red' })
    if ($todo.Count -gt 0) { Say 'run: pwsh -File tools/install-skills.ps1' 'Yellow' }
    exit $(if ($todo.Count -eq 0) { 0 } else { 1 })
}

$linked = New-Object System.Collections.Generic.List[string]
$copied = New-Object System.Collections.Generic.List[string]
foreach ($n in $todo) {
    $from = Join-Path (Join-Path $src $n) 'SKILL.md'
    $dir = Join-Path $SkillsHome $n
    $to = Join-Path $dir 'SKILL.md'
    if (-not (Test-Path $dir)) { $null = New-Item -ItemType Directory -Path $dir -Force }

    $did = $null
    if ($Mode -ne 'Copy') {
        if (Test-Path $to) { Remove-Item $to -Force }
        try {
            $null = New-Item -ItemType SymbolicLink -Path $to -Target $from -ErrorAction Stop
            $did = 'linked'
        } catch {
            if ($Mode -eq 'Link') {
                Say ("  ✘ {0}: cannot link ({1})" -f $n, $_.Exception.Message) 'Red'
                exit 1
            }
        }
    }
    if (-not $did) { Copy-Item $from $to -Force; $did = 'copied' }
    if ($did -eq 'linked') { $linked.Add($n) } else { $copied.Add($n) }
    Say ("  {0,-8} {1}" -f $did, $n) $(if ($did -eq 'linked') { 'Green' } else { 'Yellow' })
}

Say ''
Say ("{0} 一致 · {1} 新装（{2} 链接 / {3} 拷贝）· {4} 多余" -f `
        $inSync.Count, $todo.Count, $linked.Count, $copied.Count, $extra.Count) 'Green'
if ($copied.Count -gt 0) {
    Say 'copied skills do not follow later edits — re-run this script after changing the repo copy' 'Yellow'
}
exit 0
