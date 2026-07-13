# run_all.ps1 - Rakuten ad ingest pipeline orchestrator
# Replaces the old run_all.bat (which had Shift-JIS / UTF-8 encoding issues with Japanese paths).
# Owner: scheduled task "RakutenDownloadAuto"
#
# Flag-based skip:
#   C:\csv_out\.success_YYYY-MM-DD exists  => skip immediately (return 0)
#   On full success of all 3 steps          => write today's flag

$ErrorActionPreference = "Continue"
$Base   = "C:\rakuten-automation\楽天広告分析マスター"
$CsvOut = "C:\csv_out"
Set-Location -Path $Base

$Today    = Get-Date -Format "yyyy-MM-dd"
$FlagPath = Join-Path $CsvOut ".success_$Today"

if (Test-Path $FlagPath) {
    Write-Host "[run_all] Today's success flag exists: $FlagPath - SKIP"
    exit 0
}

Write-Host "[run_all] === START $(Get-Date) ==="

# Step 1: Download CSVs from Rakuten
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\Download-All-Reports.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[run_all] Download-All-Reports.ps1 FAILED (exit=$LASTEXITCODE). Abort."
    exit 1
}

# Step 1b: 必須CSV(KW実績/商品別RPP/RPP費用/カルテ)の当日分存在チェック(早期警戒・再発防止 2026-07-13)
#   所見a-1対応: Download-All-Reports.ps1 は各フェーズ内で例外を握りつぶし続行するため
#   $LASTEXITCODE だけでは部分失敗(一部レポートのみ欠落)を検知できない。
#   ここでは中断せず、欠落があればChatwork通知のみ行う(最終ゲートはStep4)。
& python check_today_required_csv.py
if ($LASTEXITCODE -eq 2) {
    Write-Host "[run_all] 必須CSVの一部が本日分で見つかりません(通知済み)。処理は継続します。"
}

# Step 2: Upload CSVs to BigQuery raw tables
& python upload_to_bigquery.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[run_all] upload_to_bigquery.py FAILED. Abort."
    exit 1
}

# Step 3: Sync ASCII clean views
& python "C:\rakuten-automation\sync_ascii.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[run_all] sync_ascii.py FAILED. Abort."
    exit 1
}

# Step 4: Verify yesterday's data exists in all 4 raw_* tables in BQ
#   exit 0 = all present (proceed to flag write)
#   exit 2 = some missing (skip flag, next scheduled run will retry)
#   other = unexpected error (treat as missing - retry next run)
& python check_raw_completeness.py
$verifyExit = $LASTEXITCODE
if ($verifyExit -ne 0) {
    Write-Host "[run_all] Yesterday's data is NOT complete (check_raw_completeness exit=$verifyExit)."
    Write-Host "[run_all] Flag NOT written. Next scheduled run will retry."
    exit 0
}

# Step 5: Write success flag (only when all required tables have yesterday's data)
if (-not (Test-Path $CsvOut)) {
    New-Item -ItemType Directory -Path $CsvOut -Force | Out-Null
}
New-Item -ItemType File -Path $FlagPath -Force | Out-Null
Write-Host "[run_all] Success flag written: $FlagPath"

# Step 5: Cleanup flags older than 7 days
Get-ChildItem -Path $CsvOut -Filter ".success_*" -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "[run_all] === DONE $(Get-Date) ==="
exit 0
