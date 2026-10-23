param(
    [string]$Dest = "$env:USERPROFILE\.claude\skills\video-translate"
)

$ErrorActionPreference = "Stop"
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$skip = @(".git", "vendor", ".runtime", "config.json", "__pycache__")
Get-ChildItem -Force $Src | Where-Object { $skip -notcontains $_.Name } | ForEach-Object {
    if ($_.Name -eq "tools") {
        # Keep helper scripts but do not copy the ignored Rubberband binary.
        $target = Join-Path $Dest "tools"
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Get-ChildItem -Force $_.FullName | Where-Object { $_.Name -ne "rubberband" } |
            Copy-Item -Destination $target -Recurse -Force
    } else {
        Copy-Item $_.FullName -Destination $Dest -Recurse -Force
    }
}

Write-Host "Installed to: $Dest"
Write-Host "Next:"
Write-Host "  cd `"$Dest`""
Write-Host "  python scripts/pipeline.py setup --reuse-pvt <pyvideotrans> --reuse-itt <index-tts2-cli>"
Write-Host "  python scripts/pipeline.py doctor"
