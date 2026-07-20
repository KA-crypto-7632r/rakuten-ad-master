# refresh_attribution_window.ps1  (AD-DQ1 fix orchestrator)
# ---------------------------------------------------------------------------
# WHAT: Re-download the RPP per-day item/keyword reports for a trailing window
#       (default: last 30 days = the 720h attribution window) and UPSERT them
#       into BigQuery so that orders/sales that mature after the first-morning
#       fetch are captured. Fixes the AD-DQ1 permanent under-count.
#       (Detailed rationale is in refresh_attribution_notes.md -- kept out of
#        this .ps1 on purpose: Japanese comments in a BOM-less .ps1 get
#        mis-decoded as cp932 by PowerShell and can corrupt the script.)
#
# HOW:  1) Build the target date list (last -Days days, or explicit -Dates).
#       2) Call the EXISTING Download-All-Reports.ps1 -Phase 1 (RPP only) for
#          those dates. Rakuten regenerates each report fresh, so the CSV holds
#          the current matured 720h values. (This script does NOT modify that
#          downloader; it only invokes it.)
#       3) Call refresh_attribution.py to UPSERT (atomic staging + tx
#          DELETE+INSERT) the two attribution tables raw_shohin_betsu /
#          raw_keyword for exactly those dates.
#
# SAFETY: Decoupled from the critical daily run_all.ps1. Idempotent. A partial
#         download just means fewer dates refreshed this run (self-heals next
#         run). Never touches a date it did not freshly re-download.
#
# MUST run in an interactive / S4U scheduled-task context: Download-All-Reports
# reads RMS credentials from Windows Credential Manager, which fails (error
# 1312) when invoked directly over SSH.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File refresh_attribution_window.ps1
#   powershell ... -File refresh_attribution_window.ps1 -Days 14
#   powershell ... -File refresh_attribution_window.ps1 -Dates '2026/06/23','2026/07/04'
#   powershell ... -File refresh_attribution_window.ps1 -SkipDownload   # upsert only
# ---------------------------------------------------------------------------
param(
    [int]$Days = 30,
    [string]$Dates,          # explicit override: comma/space separated yyyy/MM/dd. When set, -Days is ignored.
                             # NOTE: kept as a single [string] on purpose. When a scheduled task launches this
                             # via `powershell.exe -File ... -Dates a,b,c`, PowerShell does NOT split commas
                             # into array elements for -File invocation (unlike -Command). So we take one
                             # string and split it ourselves below -> robust for both task and manual calls.
    [switch]$SkipDownload,   # reuse already-downloaded CSVs (upsert step only)
    [switch]$AnyMtime,       # pass --any-mtime to the uploader (ignore "downloaded today" gate)
    [switch]$ShowBrowser
)

$ErrorActionPreference = "Continue"
$Base = "C:\rakuten-automation\楽天広告分析マスター"
Set-Location -Path $Base

$LogDir = "C:\csv_out\logs"
$null = New-Item -Path $LogDir -ItemType Directory -Force -ErrorAction SilentlyContinue
$Log = Join-Path $LogDir "refresh_attribution_$(Get-Date -Format 'yyyyMMdd_HHmm').log"
Start-Transcript -Path $Log -Append | Out-Null
Write-Host "[refresh_attribution_window] === START $(Get-Date) ==="

# --- Build target date list ---
$explicit = @()
if ($Dates) { $explicit = @($Dates -split '[,\s]+' | Where-Object { $_ -ne '' }) }
if ($explicit.Count -gt 0) {
    $targetDates = $explicit
    Write-Host "[refresh] explicit dates ($($targetDates.Count)): $($targetDates -join ', ')"
} else {
    $targetDates = @()
    for ($i = 1; $i -le $Days; $i++) { $targetDates += (Get-Date).AddDays(-$i).ToString('yyyy/MM/dd') }
    Write-Host "[refresh] trailing window: last $Days days ($($targetDates[-1]) .. $($targetDates[0]))"
}

# --- Step 1: re-download (Phase 1 = RPP item/keyword/daily only) ---
if (-not $SkipDownload) {
    $dlParams = @{ Phase = 1; Dates = $targetDates }
    if ($ShowBrowser) { $dlParams['ShowBrowser'] = $true }
    Write-Host "[refresh] downloading $($targetDates.Count) date(s) via Download-All-Reports.ps1 -Phase 1 ..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\Download-All-Reports.ps1" @dlParams
    Write-Host "[refresh] download step finished (exit=$LASTEXITCODE)."
} else {
    Write-Host "[refresh] -SkipDownload set: reusing already-downloaded CSVs."
}

# --- Step 2: UPSERT into BigQuery ---
$pyArgs = @('refresh_attribution.py')
if ($explicit.Count -gt 0) {
    $isoDates = ($targetDates | ForEach-Object { (Get-Date $_).ToString('yyyy-MM-dd') }) -join ','
    $pyArgs += @('--dates', $isoDates)
} else {
    $pyArgs += @('--days', "$Days")
}
if ($SkipDownload -or $AnyMtime) { $pyArgs += '--any-mtime' }
Write-Host "[refresh] upsert: python $($pyArgs -join ' ')"
& python @pyArgs
$pyExit = $LASTEXITCODE

Write-Host "[refresh_attribution_window] === DONE $(Get-Date) (upsert exit=$pyExit) ==="
Stop-Transcript | Out-Null
exit $pyExit
