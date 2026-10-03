<#
.SYNOPSIS
    Take a native screenshot through the dsh-selflook-local plugin and print the PNG path.

.DESCRIPTION
    Calls the plugin's `shot` RPC on the DSH web server. Unlike the DOM-serialising
    path, this captures real screen pixels (images, widgets, overlays, other windows).
    Requires the plugin to be installed and the DSH web UI to be running.

    Superseded (2026-10-01): the PC Actor (actor/) captures the same pixels with no plugin
    and no DSH server — `actor\act.cmd '{"op":"shot","path":"shot.png"}'`. This script stays
    for setups that already run the plugin bundle.

.EXAMPLE
    pwsh -File tools/dsh-look-native.ps1
    pwsh -File tools/dsh-look-native.ps1 -Port 3080 -Json
#>
[CmdletBinding()]
param(
    [int]$Port = 19387,
    [string]$Server = '127.0.0.1',
    [switch]$Json,
    [int]$TimeoutSec = 90
)

$uri = "http://${Server}:${Port}/__dsh__/selflook/rpc"
try {
    $r = Invoke-RestMethod -Method Post -Uri $uri -ContentType 'application/json' `
        -Body '{"method":"shot"}' -TimeoutSec $TimeoutSec
} catch {
    Write-Error "shot RPC failed ($uri): $($_.Exception.Message)"
    exit 1
}

if ($Json) { $r | ConvertTo-Json -Depth 4; exit 0 }

if ($r.ok) {
    "{0}  ({1}x{2}, {3:N0} KB, {4} ms)" -f $r.path, $r.width, $r.height, ($r.bytes / 1KB), $r.ms
    exit 0
}

Write-Error "shot RPC returned ok=false: $($r | ConvertTo-Json -Compress)"
exit 1
