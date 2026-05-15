<#
.SYNOPSIS
  【真の最終版】Rakuten RMSから全6種のレポートをダウンロードする。
  (安定版の5種取得コードに、PHASE 3のアフィリエイト機能のみを追加)
#>

param(
    [Alias('Date')]
    [string[]]$Dates,
    [string]  $DownloadDirBase = "C:\csv_out",
    [switch]  $CleanAndUpload,
    [switch]  $ShowBrowser,
    [int]     $Phase = 0   # 0=全部, 1=RPP, 2=カルテ, 3=アフィ
)

# ==== ログファイル設定（タスクスケジューラでもエラーが残るように） ====
$LogDir  = "C:\csv_out\logs"
$null    = New-Item -Path $LogDir -ItemType Directory -Force -ErrorAction SilentlyContinue
$LogFile = Join-Path $LogDir "download_reports_$(Get-Date -Format 'yyyyMMdd_HHmm').log"
Start-Transcript -Path $LogFile -Append
Write-Host "=== Script started: $(Get-Date) ==="
# 30日より古いログを削除
Get-ChildItem $LogDir -Filter "download_reports_*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# ==== 実行フェーズを判定（0=全部） ====
$RunPhase1 = ($Phase -eq 0 -or $Phase -eq 1)
$RunPhase2 = ($Phase -eq 0 -or $Phase -eq 2)
$RunPhase3 = ($Phase -eq 0 -or $Phase -eq 3)
$RunPhase4 = ($Phase -eq 0 -or $Phase -eq 4)  # 4=RPPEXP

#--------------------------------------------------
# 1. 共通ユーティリティ関数定義
#--------------------------------------------------
function Update-ChromeDriver {
    param([string]$DriverDirectory = "C:\tools\selenium")
    Write-Host "INFO: ChromeDriverのバージョンチェックを開始します..."
    try {
        $chromePath = Get-Item "C:\Program Files\Google\Chrome\Application\chrome.exe"
        $chromeVersionString = $chromePath.VersionInfo.ProductVersion
        $chromeMajorVersion = ($chromeVersionString -split '\.')[0]
        Write-Host "INFO: PCのChromeバージョン: $chromeVersionString (メジャー: $chromeMajorVersion)"
    } catch {
        throw "Google Chromeが見つかりません。バージョンを確認できませんでした。"
    }
    $driverExePath = Join-Path $DriverDirectory "chromedriver.exe"
    $driverMajorVersion = 0
    if (Test-Path $driverExePath) {
        try {
            $versionOutput = (& "$driverExePath" --version)
            $driverVersionString = ($versionOutput -split ' ')[1]
            $driverMajorVersion = ($driverVersionString -split '\.')[0]
            Write-Host "INFO: 現在のChromeDriverバージョン: $driverVersionString (メジャー: $driverMajorVersion)"
        } catch {
            Write-Warning "ChromeDriverのバージョンを特定できませんでした。再ダウンロードを試みます。"
        }
    } else {
        Write-Host "INFO: ChromeDriverが見つかりません。ダウンロードします。"
    }
    if ($chromeMajorVersion -ne $driverMajorVersion) {
        Write-Host "ACTION: バージョンが一致しないため、Chrome v$chromeMajorVersion 用の新しいChromeDriverをダウンロードします..."
        try {
            $jsonUrl = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
            $versionsData = Invoke-RestMethod -Uri $jsonUrl
            $bestMatch = $versionsData.versions | Where-Object { $_.version -like "$chromeMajorVersion.*" } | Sort-Object -Property version -Descending | Select-Object -First 1
            if (-not $bestMatch) { throw "バージョン $chromeMajorVersion に一致するChromeDriverが見つかりませんでした。" }
            $downloadUrl = ($bestMatch.downloads.chromedriver | Where-Object { $_.platform -eq 'win64' }).url
            if (-not $downloadUrl) { throw "win64用のダウンロードURLが見つかりませんでした。" }
            $zipPath = Join-Path $env:TEMP "chromedriver.zip"
            Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath
            $extractPath = Join-Path $env:TEMP "chromedriver_extract"
            if (Test-Path $extractPath) { Remove-Item $extractPath -Recurse -Force }
            Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
            $sourceExe = Get-ChildItem -Path $extractPath -Filter "chromedriver.exe" -Recurse | Select-Object -First 1
            if (-not $sourceExe) { throw "ダウンロードしたzipファイルの中にchromedriver.exeが見つかりません。" }
            if (-not (Test-Path $DriverDirectory)) { New-Item -Path $DriverDirectory -ItemType Directory | Out-Null }
            Move-Item -Path $sourceExe.FullName -Destination $driverExePath -Force
            Write-Host "SUCCESS: ChromeDriverがバージョン $($bestMatch.version) に更新されました。"
            Remove-Item $zipPath, $extractPath -Recurse -Force
        } catch {
            throw "ChromeDriverの自動更新に失敗しました。エラー: $($_.Exception.Message)"
        }
    } else {
        Write-Host "INFO: ChromeDriverは既に最新です。処理を続行します。"
    }
}

function Wait-Elm { param($Driver, $By, $Value, [int]$Timeout=20)
    $end=(Get-Date).AddSeconds($Timeout)
    do {
        try {
            $els = switch ($By) {
                'id'    { $Driver.FindElementsById($Value) }
                'css'   { $Driver.FindElementsByCssSelector($Value) }
                'xpath' { $Driver.FindElementsByXPath($Value) }
                'tagname' { $Driver.FindElementsByTagName($Value) }
            }
            if($els.Count){ return $els[0] }
        } catch {}
        Start-Sleep 0.5
    } until ((Get-Date) -gt $end)
    throw "Element not found (Timeout): [$By=$Value]"
}

function Pick-DateByAriaLabel {
    param($Driver, $InputElement, $DateStr)
    $dateObj = [datetime]::Parse($DateStr)
    $Driver.ExecuteScript('arguments[0].scrollIntoView({block:"center"});', $InputElement)
    $InputElement.Click(); Start-Sleep -Milliseconds 500

    # 照合用：2026年1月20日 / 1月20日 の両方に対応
    $formattedFull = $dateObj.ToString('yyyy年M月d日')
    $formattedShort = $dateObj.ToString('M月d日')
    $targetYM_obj = [datetime]::ParseExact($dateObj.ToString('yyyy年M月'), 'yyyy年M月', $null)

    for ($i = 0; $i -lt 24; $i++) {
        $headerText = $Driver.FindElementByCssSelector('div.react-datepicker__current-month').Text
        # 「2026 1月」などのスペース区切りを「2026年1月」に正規化
        $normalizedHeader = $headerText -replace '\s+', '年'
        if ($normalizedHeader -notmatch '年') { $normalizedHeader = $normalizedHeader.Insert(4, '年') }

        try {
            $currentYM_obj = [datetime]::ParseExact($normalizedHeader, 'yyyy年M月', $null)
        } catch {
            throw "カレンダーの年月解析に失敗: '$headerText'"
        }

        # --- 1. 日付のクリックを試行 ---
        $selector = "div.react-datepicker__day[aria-label*='$formattedFull'], div.react-datepicker__day[aria-label*='$formattedShort']"
        $elems = $Driver.FindElementsByCssSelector($selector)
        if ($elems.Count -gt 0) {
            foreach ($el in $elems) {
                # 当月の数字かつ、無効化されていないものをクリック
                if ($el.GetAttribute('class') -notlike '*--outside-month*' -and $el.GetAttribute('aria-disabled') -ne 'true') {
                    $el.Click(); Start-Sleep -Milliseconds 200
                    try { $Driver.FindElementByTagName('body').Click() } catch {}
                    return
                }
            }
        }

        # --- 2. 月が一致しているのに日付がない場合はループ終了 ---
        if ($currentYM_obj -eq $targetYM_obj) { break }

        # --- 3. 月の移動判定 ---
        $navBtnCss = if ($currentYM_obj -lt $targetYM_obj) { 'button.react-datepicker__navigation--next' } else { 'button.react-datepicker__navigation--previous' }
        try {
            Wait-Elm $Driver 'css' $navBtnCss 5 | ForEach-Object { $_.Click() }
            Start-Sleep -Milliseconds 500
        } catch { break }
    }
    throw "指定された日付 '$DateStr' がカレンダー内で見つかりませんでした。"
}

function Process-ReportFile { param($sourceFile, $finalName)
    Write-Host "INFO: Processing RPP file: $($sourceFile.Name)"
    $maxRetries = 3; $retryCount = 0; $content = $null
    while ($retryCount -lt $maxRetries) {
        try {
            Start-Sleep -Seconds 1
            $encoding = [System.Text.Encoding]::GetEncoding(932)
            $content = [System.IO.File]::ReadAllLines($sourceFile.FullName, $encoding)
            break
        } catch {
            $retryCount++
            if ($retryCount -ge $maxRetries) {
                Write-Error "Failed to read file '$($sourceFile.Name)' after $maxRetries attempts. Error: $($_.Exception.Message)"; return
            }
            Write-Warning "Attempt $retryCount failed to read file '$($sourceFile.Name)'. Retrying in 3 seconds..."
            Start-Sleep -Seconds 3
        }
    }
    if ($null -eq $content) { Write-Error "Could not read content from file '$($sourceFile.Name)'."; return }
    $headerIndex = -1
    for ($i = 0; $i -lt $content.Length; $i++) { if ($content[$i] -like '*"CTR(%)"*') { $headerIndex = $i; break } }
    if ($headerIndex -ne -1) {
        $cleanContent = $content[$headerIndex..($content.Length - 1)]
        [System.IO.File]::WriteAllLines($finalName, $cleanContent, ([System.Text.UTF8Encoding]::new($true)))
    } else {
        Write-Warning "RPP header not found. Saving as-is with UTF8-BOM."
        [System.IO.File]::WriteAllLines($finalName, $content, ([System.Text.UTF8Encoding]::new($true)))
    }
}

function Hover-And-Click { param($Driver, $Element)
    (New-Object OpenQA.Selenium.Interactions.Actions($Driver)).MoveToElement($Element).Click().Build().Perform()
}

function Wait-ForLoadingOverlayToDisappear { param($Driver, [int]$Timeout = 90)
    Write-Host "INFO: Waiting for page data to load..."
    $end = (Get-Date).AddSeconds($Timeout)
    do {
        try {
            if (($Driver.FindElementsByXPath("//div[contains(., 'データを取得中です')]")).Count -eq 0) { Write-Host "INFO: Load complete."; return }
        } catch { Write-Host "INFO: Load complete (exception)."; return }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $end)
    throw "Page load timed out."
}

function Select-DateRangePicker {
    param($Driver, $TargetDate)
    $dateObj = Get-Date $TargetDate; $day = $dateObj.Day
    $targetMonthDate = $dateObj.Date.AddDays(-($dateObj.Day - 1))
    $culture = [System.Globalization.CultureInfo]::GetCultureInfo('ja-JP')
    (Wait-Elm $Driver 'css' 'input[data-toggle="daterangepicker"]').Click(); Start-Sleep -Milliseconds 800
    for ($i = 0; $i -lt 36; $i++) {
        $leftMonthStr  = (Wait-Elm $Driver 'xpath' "(//th[contains(@class, 'month')])[1]").Text
        $rightMonthStr = (Wait-Elm $Driver 'xpath' "(//th[contains(@class, 'month')])[2]").Text
        try {
            $leftMonthDate  = [DateTime]::ParseExact($leftMonthStr,  "M'月' yyyy", $culture).Date
            $rightMonthDate = [DateTime]::ParseExact($rightMonthStr, "M'月' yyyy", $culture).Date
        } catch { throw "Cannot parse calendar month format: '$leftMonthStr' or '$rightMonthStr'" }
        $calendarIndex = 0
        if ($leftMonthDate -eq $targetMonthDate) { $calendarIndex = 1 }
        elseif ($rightMonthDate -eq $targetMonthDate) { $calendarIndex = 2 }
        if ($calendarIndex -gt 0) {
            $dateXPath = "(//div[contains(@class, 'calendar-table')])[$calendarIndex]//td[not(contains(@class, 'off')) and .='$day']"
            Wait-Elm $Driver 'xpath' $dateXPath | % { $_.Click() }; Start-Sleep -Milliseconds 300
            Wait-Elm $Driver 'xpath' $dateXPath | % { $_.Click() }; Start-Sleep -Milliseconds 300
            Wait-Elm $Driver 'css' 'button.applyBtn' | % { $_.Click() }; Start-Sleep -Milliseconds 500
            Write-Host "INFO: Date set to $($dateObj.ToString('yyyy/MM/dd'))."; return
        }
        $navBtn = if ($targetMonthDate -lt $leftMonthDate) { 'th.prev.available' } else { 'th.next.available' }
        Wait-Elm $Driver 'css' $navBtn | % { $_.Click() }; Start-Sleep -Milliseconds 300
    }
    throw "Could not find specified month in datepicker: $($dateObj.ToString('yyyy年M月'))"
}

function Process-NationsCsv { param($filePath)
    Write-Host "INFO: Processing Nations CSV file: $(Split-Path $filePath -Leaf)"
    $encoding = [System.Text.Encoding]::UTF8
    $rawLines = [System.IO.File]::ReadAllLines($filePath, $encoding)
    if ($rawLines.Length -lt 7) { Write-Warning "Invalid file format. Skipping."; return }
    $datePart  = ($rawLines[2] -split ',')[1]
    $dateMatch = [regex]::Match($datePart, '(\d{4}年\d{2}月\d{2}日)').Groups[1].Value
    if (-not $dateMatch) { Write-Warning "Could not find date in line 3."; return }
    $reportDate = (Get-Date $dateMatch).ToString('yyyy-MM-dd')
    $newContent = @("対象日," + $rawLines[5])
    for ($i = 6; $i -lt $rawLines.Length; $i++) { $newContent += ($reportDate + "," + $rawLines[$i]) }
    [System.IO.File]::WriteAllLines($filePath, $newContent, $encoding)
}

function Process-ActionListCsv { param($filePath, $reportDateStr)
    Write-Host "INFO: Processing ActionList CSV file: $(Split-Path $filePath -Leaf)"
    $encoding = [System.Text.Encoding]::GetEncoding(932)
    $rawLines = [System.IO.File]::ReadAllLines($filePath, $encoding)
    if ($rawLines.Length -lt 4) { Write-Warning "Invalid file format. Skipping."; return }
    $reportDate = (Get-Date $reportDateStr).ToString('yyyy-MM-dd')
    $newContent = @("対象日," + $rawLines[2])
    for ($i = 3; $i -lt $rawLines.Length; $i++) { $newContent += ($reportDate + "," + $rawLines[$i]) }
    [System.IO.File]::WriteAllLines($filePath, $newContent, [System.Text.Encoding]::UTF8)
}

function Upload-Drive { param([string]$filePath, [string]$gDriveFolderId, [string]$sharedDriveId)
    $rcloneExe = 'C:\tools\rclone\rclone.exe'
    if (-not (Test-Path $rcloneExe)) { throw "rclone.exe not found: $rcloneExe" }
    Write-Host "INFO: Uploading '$($filePath | Split-Path -Leaf)' via rclone -> FolderID: $gDriveFolderId"
    $rcloneArgs = @('copy', $filePath, "gdrive:", '--drive-root-folder-id', $gDriveFolderId)
    if (-not [string]::IsNullOrEmpty($sharedDriveId)) { $rcloneArgs += '--drive-team-drive', $sharedDriveId }
    & $rcloneExe $rcloneArgs -v
    if ($LASTEXITCODE -ne 0) { throw "rclone upload failed (exit code = $LASTEXITCODE)" }
    Write-Host "SUCCESS: Upload complete."
}

# アフィリエイトレポート専用関数（TargetMonthを外部から受け取る）
function Get-AffiliateReport {
    param(
        $Driver,
        $DownloadDir,
        [string]$TargetMonth  # 例: "2025-10"
    )
    Write-Host "`n--- Downloading: Affiliate Report (pending.csv) ---"
    if ([string]::IsNullOrWhiteSpace($TargetMonth)) { throw "TargetMonth is required (e.g. 2025-10)." }
    Write-Host "INFO: Target month set to $TargetMonth"

    Write-Host "INFO: Navigating to .../rates to establish session..."
    $Driver.Navigate().GoToUrl("https://afl.rms.rakuten.co.jp/rates"); Start-Sleep 3
    Write-Host "INFO: Navigating to .../report..."
    $Driver.Navigate().GoToUrl("https://afl.rms.rakuten.co.jp/report/"); Start-Sleep 3
    Write-Host "INFO: Navigating to .../pending page..."
    $Driver.Navigate().GoToUrl("https://afl.rms.rakuten.co.jp/report/pending?date=$TargetMonth"); Start-Sleep 3
    Write-Host "INFO: Human-viewable page visited successfully."

    $apiUrl = "https://afl.rms.rakuten.co.jp/api/report/download/pending?format=csv&date=$TargetMonth"
    $oldFile = Join-Path $DownloadDir "pending.csv"
    if (Test-Path $oldFile) { Remove-Item $oldFile -Force; Write-Host "INFO: Removed old pending.csv" }
    $Driver.Navigate().GoToUrl($apiUrl)
    Write-Host "INFO: Download requested from API..."

    $deadline = (Get-Date).AddMinutes(5)
    do { Start-Sleep 2; $crdownloadFile = Get-ChildItem $DownloadDir -Filter *.crdownload -ErrorAction SilentlyContinue } until ((-not $crdownloadFile) -or ((Get-Date) -gt $deadline))
    $downloadedFile = Get-ChildItem $DownloadDir -Filter "pending.csv" -ErrorAction SilentlyContinue
    if ($downloadedFile) { Write-Host "✔ [Affiliate Report] Download complete -> pending.csv" }
    else { throw "Affiliate report (pending.csv) download failed or timed out." }
}
function Set-InputDate($el, [string]$value) {
    if (-not $el) { throw "Set-InputDate: element is null" }

    # 画面中央へ
    try { $Driver.ExecuteScript('arguments[0].scrollIntoView({block:"center"});', $el) | Out-Null } catch {}
    Start-Sleep -Milliseconds 150

    # クリック→全選択→入力→確定（Tab）
    try { $Driver.ExecuteScript('arguments[0].click();', $el) | Out-Null } catch { try { $el.Click() } catch {} }
    Start-Sleep -Milliseconds 150

    try { $el.SendKeys([OpenQA.Selenium.Keys]::Control + "a") } catch {}
    Start-Sleep -Milliseconds 50
    try { $el.SendKeys($value) } catch {}

    # 入力イベントが必要なUI向けに JS で input/change を明示発火
    try {
        $Driver.ExecuteScript(@"
arguments[0].value = arguments[1];
arguments[0].dispatchEvent(new Event('input',  { bubbles: true }));
arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
"@, $el, $value) | Out-Null
    } catch {}

    # フォーカス外し（反映トリガ）
    try { $el.SendKeys([OpenQA.Selenium.Keys]::Tab) } catch {}
    Start-Sleep -Milliseconds 250
}
function Get-RppExpItemReport {
    param(
        $Driver,
        [string]$DownloadDirCommon,
        [string]$DownloadDirRppExpFinal,
        [datetime]$TargetDate
    )

    function Click-JS($el) {
        if (-not $el) { throw "Click-JS: element is null" }
        try { $Driver.ExecuteScript('arguments[0].scrollIntoView({block:"center"});', $el) | Out-Null } catch {}
        Start-Sleep -Milliseconds 200
        try { $Driver.ExecuteScript('arguments[0].click();', $el) | Out-Null } catch { $el.Click() }
        Start-Sleep -Milliseconds 300
    }

    $dayDisp = $TargetDate.ToString('yyyy-MM-dd')      # 例: 2026-03-01
    $dayYmd  = $TargetDate.ToString('yyyyMMdd')        # 例: 20260301
    $stamp   = (Get-Date).ToString('yyyyMMdd_HHmm')

    Write-Host "`n--- Downloading: RPPEXP 商品別 (All Items) [$dayDisp] ---"

    # 1) reports 画面へ
    $reportsUrl = "https://ad.rms.rakuten.co.jp/rppexp/reports/"
    $Driver.Navigate().GoToUrl($reportsUrl); Start-Sleep 5

    # 2) 商品別（id="商品別" のradioを確実にクリック）
    try {
        $radio = $Driver.FindElementsByCssSelector("input[type='radio']#商品別") | Select-Object -First 1
        if ($radio) {
            Click-JS $radio
        } else {
            # fallback: label for="商品別"
            $label = $Driver.FindElementsByCssSelector("label[for='商品別']") | Select-Object -First 1
            if ($label) { Click-JS $label }
            else { throw "RPPEXP: 商品別ラジオが見つかりません（input#商品別 / label[for=商品別]）" }
        }
    } catch {
        throw "RPPEXP: 商品別の選択に失敗: $($_.Exception.Message)"
    }

    # 3) 日付入力（type=text が2つ、value が yyyy-MM-dd の想定）
    $inputs = @()
    try {
        $inputs = $Driver.FindElementsByXPath("//input[@type='text' and @value='$dayDisp']")
        if ($inputs.Count -lt 2) {
            $inputs = $Driver.FindElementsByXPath("//input[@type='text']")
        }
    } catch {}

    if (-not $inputs -or $inputs.Count -lt 2) {
        throw "RPPEXP: 日付入力欄を2つ特定できませんでした。"
    }

    Set-InputDate $inputs[0] $dayDisp
    Set-InputDate $inputs[1] $dayDisp
    Start-Sleep 1

    # 4) 全商品レポートダウンロード
    $dlTarget = $null
    try {
        $dlTarget = $Driver.FindElementsByXPath("//button[.//span[contains(., '全商品レポートダウンロード')] or contains(., '全商品レポートダウンロード')]") | Select-Object -First 1
        if (-not $dlTarget) {
            $dlTarget = $Driver.FindElementsByXPath("//span[contains(., '全商品レポートダウンロード')]") | Select-Object -First 1
        }
    } catch {}

    if (-not $dlTarget) { throw "RPPEXP: 『全商品レポートダウンロード』が見つかりませんでした。" }

    Click-JS $dlTarget
    Write-Host "INFO: RPPEXP report generation requested."
    Start-Sleep 3

    # 5) download 履歴へ移動して、対象日付の完了レポートを探してDL
    $downloadUrl = "https://ad.rms.rakuten.co.jp/rppexp/download/"
    $Driver.Navigate().GoToUrl($downloadUrl); Start-Sleep 5

    $found = $false
    $maxRetries = 16
    for ($i=1; $i -le $maxRetries; $i++) {

        # 更新ボタンがあれば押す
        try {
            $refreshBtn = $Driver.FindElementsByXPath("//button[contains(., '更新') or contains(., '再読み込み') or contains(., 'リフレッシュ')]") | Select-Object -First 1
            if ($refreshBtn) { Click-JS $refreshBtn; Start-Sleep 2 }
        } catch {}

        try {
    $jpDate    = $TargetDate.ToString('yyyy年MM月dd日')
    $slashDate = $TargetDate.ToString('yyyy/MM/dd')

    $btns = $Driver.FindElementsByCssSelector("div.download-btn-csv")

    if ($btns -and $btns.Count -gt 0) {

        $picked = $null
        foreach ($b in $btns) {
            try {
                $row = $b.FindElementByXPath("ancestor::tr[1] | ancestor::div[1] | ancestor::li[1]")
                $rowText = $row.Text

                if ($rowText -match [regex]::Escape($dayDisp) -or
                    $rowText -match [regex]::Escape($slashDate) -or
                    $rowText -match [regex]::Escape($jpDate)) {
                    $picked = $b
                    break
                }
            } catch {}
        }

        if (-not $picked) {
            $picked = $btns[0]
        }

        Click-JS $picked
        $found = $true
        Write-Host "INFO: RPPEXP download button clicked from history."
        break
    }

} catch {
    # 何も見つからない間は黙って待機ループ継続
}

        Write-Host "INFO: Waiting RPPEXP report completion... ($i/$maxRetries)"
        Start-Sleep 15
    }

    if (-not $found) {
        throw "RPPEXP: download履歴で完了レポートが見つかりませんでした（$maxRetries回待機）。"
    }

    # 6) DL完了待ち
    $deadline = (Get-Date).AddMinutes(5)
    do { Start-Sleep 2 } while ((Get-ChildItem $DownloadDirCommon -Filter *.crdownload -Ea 0).Count -ne 0 -and (Get-Date) -lt $deadline)

    # 7) "今回落ちたCSV" を特定（最も新しいCSV）
    $csv = Get-ChildItem $DownloadDirCommon -Filter *.csv -Ea 0 | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $csv) { throw "RPPEXP: ダウンロードされたCSVが見つかりませんでした。" }

    $finalName = "RPPEXP商品別_${dayYmd}_${stamp}.csv"
    $finalPath = Join-Path $DownloadDirRppExpFinal $finalName

    Copy-Item -Path $csv.FullName -Destination $finalPath -Force
    Write-Host "✔ [RPPEXP 商品別] Saved -> $finalName"

    try { $script:tempFilesToDelete.Add($csv.FullName) } catch {}
}
#--------------------------------------------------
# 2. 初期設定と準備
#--------------------------------------------------
Write-Host "INFO: Starting script."
$specificDatesProvided = $PSBoundParameters.ContainsKey('Dates')
if (-not $specificDatesProvided) { $Dates = (Get-Date).AddDays(-1).ToString('yyyy/MM/dd') }

try {
    Update-ChromeDriver
    Import-Module Selenium -ErrorAction Stop
    Import-Module CredentialManager -ErrorAction Stop
} catch {
    Write-Error "FATAL: Failed to import required modules.`n$($_.Exception.Message)"; exit 1
}

$DownloadDirCommon  = Join-Path $DownloadDirBase "rms_reports"
$DownloadDirRppFinal= Join-Path $DownloadDirBase "rpp_reports"
$DownloadDirRppExpFinal = Join-Path $DownloadDirBase "rppexp_reports"
if (-not (Test-Path $DownloadDirCommon))  { New-Item $DownloadDirCommon  -ItemType Directory | Out-Null }
if (-not (Test-Path $DownloadDirRppFinal)){ New-Item $DownloadDirRppFinal -ItemType Directory | Out-Null }
if (-not (Test-Path $DownloadDirRppExpFinal)){ New-Item $DownloadDirRppExpFinal -ItemType Directory | Out-Null }

# --- [重要] ここから新規判定用の準備 ---
$scriptStartTime = Get-Date
$tempFilesToDelete = New-Object System.Collections.Generic.List[string]
$preExistingRppCsvs = @()
if (Test-Path $DownloadDirRppFinal) {
    $preExistingRppCsvs = Get-ChildItem $DownloadDirRppFinal -Filter '*.csv' | Select-Object -ExpandProperty FullName
}
# --- ここまで ---

try {
    $cred  = Get-StoredCredential -Target 'rakuten-rms-common' -AsCredentialObject -ErrorAction Stop
    $rmsId = $cred.UserName; $rmsPw = $cred.Password
    $subCred = Get-StoredCredential -Target 'rakuten-rms-user' -AsCredentialObject -ErrorAction SilentlyContinue
    if ($subCred) { $subId = $subCred.UserName; $subPw = $subCred.Password }
} catch {
    Write-Error "FATAL: Could not retrieve credentials."; exit 1
}

$driverPath = "C:\tools\selenium"
$chromeOpts = New-Object OpenQA.Selenium.Chrome.ChromeOptions
$chromeOpts.AddUserProfilePreference('download.default_directory', (Resolve-Path $DownloadDirCommon).Path)
$chromeOpts.AddArgument('--window-size=1920,1080')
$chromeOpts.AddArgument('--force-device-scale-factor=0.5')
if (-not $ShowBrowser) { $chromeOpts.AddArgument('--headless=chrome') }

$svc = [OpenQA.Selenium.Chrome.ChromeDriverService]::CreateDefaultService($driverPath)
$svc.HideCommandPromptWindow = $true
try {
    $drv = New-Object OpenQA.Selenium.Chrome.ChromeDriver($svc, $chromeOpts, [TimeSpan]::FromSeconds(120))
} catch { Write-Error "FATAL: Failed to initialize ChromeDriver.`n$($_.Exception.Message)"; exit 1 }

#--------------------------------------------------
# 3. メイン処理 - ログインセクション
#--------------------------------------------------
try {
    # --- 1次ログイン ---
    Write-Host "INFO: 1次ログイン中..."
    $drv.Navigate().GoToUrl('https://glogin.rms.rakuten.co.jp/?sp_id=1'); Start-Sleep 2
    Wait-Elm $drv 'id' 'rlogin-username-ja' | % { $_.SendKeys($rmsId) }
    Wait-Elm $drv 'id' 'rlogin-password-ja' | % { $_.SendKeys($rmsPw) }
    $submit1 = Wait-Elm $drv 'xpath' "//button[@name='submit']"
    $drv.ExecuteScript("arguments[0].click();", $submit1) | Out-Null

    # --- 2次ログイン ---
    if ($subId) {
        Write-Host "INFO: 2次ログイン処理中..."

        # STEP 1: ユーザID入力
        $idField = Wait-Elm $drv 'id' 'user_id' 20
        $idField.Clear(); $idField.SendKeys($subId)
        Start-Sleep -Milliseconds 300
        $idField.SendKeys([OpenQA.Selenium.Keys]::Enter)

        # パスワード欄の表示を待機
        Write-Host "      - パスワード欄を待機中..."
        $pwField = $null
        for ($i=0; $i -lt 15; $i++) {
            try {
                $pwField = $drv.FindElementById('password_current')
                if ($pwField.Displayed) { break }
            } catch {}
            if ($i -eq 5) {
                try {
                    $nextDiv1 = $drv.FindElementByXPath("//div[text()='次へ']")
                    $drv.ExecuteScript("arguments[0].click();", $nextDiv1) | Out-Null
                } catch {}
            }
            Start-Sleep 1
        }

        # STEP 2: パスワード入力
        if ($null -eq $pwField) { throw "パスワード入力欄が見つかりません" }
        $pwField.SendKeys($subPw)
        Start-Sleep -Milliseconds 300
        Write-Host "      - パスワード入力完了、Enterを送信..."
        $pwField.SendKeys([OpenQA.Selenium.Keys]::Enter)

        Start-Sleep 2.0

        # STEP 3: 最終確認ボタン または 規約同意ボタン を待機
        Write-Host "      - 画面遷移を待機中..."
        for ($i=0; $i -lt 15; $i++) {
            $html = $drv.PageSource
            if ($html -match 'rf-button-primary' -and $html -match '次へ') {
                try {
                    $finalBtn = $drv.FindElementByXPath("//button[contains(@class, 'rf-button-primary') and contains(., '次へ')]")
                    $drv.ExecuteScript("arguments[0].click();", $finalBtn) | Out-Null
                    Write-Host "      - 最終確認ボタンを押しました"
                    Start-Sleep 2
                } catch {}
            }
            if ($html -match 'btn-red' -or $html -match 'RMSを利用します') {
                Write-Host "      - 規約同意画面を確認しました"
                break
            }
            Start-Sleep 1
        }
    }

    # --- STEP 4: 利用規約・遵守確認（赤いボタン） ---
    try {
        $agreeBtn = Wait-Elm $drv 'xpath' "//button[contains(., 'RMSを利用します') or contains(@class, 'btn-red')]" 15
        $drv.ExecuteScript("arguments[0].click();", $agreeBtn) | Out-Null
        Write-Host "INFO: ログイン完了"
    } catch {
        Write-Warning "INFO: 最終同意ボタンが見つかりません。既にRMS画面の可能性があります。"
    }


    # datatool でセッション確立
    Write-Host "INFO: Navigating to RMS datatool page to establish session..."
    $drv.Navigate().GoToUrl('https://datatool.rms.rakuten.co.jp/access'); Start-Sleep 3

    # ========= PHASE 1: RPP =========
    Write-Host "`n========== [PHASE 1] RPP Performance Reports =========="
    if ($RunPhase1) {
        $MAP_RPP = @(
            @{ prefix='RPP商品別12h_';   sheet='RPP商品別12h';   id='rdReportTypeItem';    downloadButtonId='btnAllItemReport';    flow='history'; historyName='全商品レポートダウンロード' },
            @{ prefix='RPPキーワード別12h_'; sheet='RPPキーワード別12h'; id='rdReportTypeKeyword'; downloadButtonId='btnAllKeywordReport'; flow='history'; historyName='全キーワードレポートダウンロード' },
            @{ prefix='RPP日次12h_';     sheet='RPP日次12h';     id='rdReportTypeAllAds';  downloadButtonId='btnDownloadReport';    flow='direct' }
        )
        foreach ($dateString in $Dates) {
            try {
                $targetDate = [datetime]::ParseExact($dateString, "yyyy/MM/dd", $null)
                $dayDisp = $targetDate.ToString('yyyy-MM-dd')
                $stamp = (Get-Date).ToString('yyyyMMdd_HHmm')
                Write-Host "`n===== Processing RPP reports for [$dayDisp] ====="
                foreach ($rep in $MAP_RPP) {
                    Write-Host "`n--- Downloading: $($rep.sheet) ---"
                    $targetUrl = 'https://ad.rms.rakuten.co.jp/rpp/reports'
                    $drv.Navigate().GoToUrl($targetUrl)
                    Start-Sleep 5
                    try { Wait-ForLoadingOverlayToDisappear -Driver $drv -Timeout 90 } catch {}
                    if ($drv.Url -notlike "*rpp/reports*") {
                        $diagStamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
                        $diagBase  = Join-Path $LogDir "rpp_nav_failed_$diagStamp"
                        try { Set-Content -Path "$diagBase.url.txt"   -Value $drv.Url -Encoding utf8 } catch {}
                        try { Set-Content -Path "$diagBase.title.txt" -Value $drv.Title -Encoding utf8 } catch {}
                        try { $drv.PageSource | Out-File "$diagBase.html" -Encoding utf8 } catch {}
                        try { [System.IO.File]::WriteAllBytes("$diagBase.png", $drv.GetScreenshot().AsByteArray) } catch {}
                        Write-Warning "DIAG saved: $diagBase.*  (current URL: $($drv.Url))"
                        throw "Failed to navigate to RPP reports page. Diag: $diagBase"
                    }
                    try { $iframe = Wait-Elm $drv 'tagname' 'iframe' 5; if ($iframe) { $drv.SwitchTo().Frame($iframe) } } catch {}

                    Wait-Elm $drv 'id' $rep.id | % { $_.Click() }
                    $dateInputs = $drv.FindElementsByCssSelector("input.datepicker-input");
                    Pick-DateByAriaLabel $drv $dateInputs[0] $dayDisp
                    Pick-DateByAriaLabel $drv $dateInputs[1] $dayDisp

                    Wait-Elm $drv 'id' $rep.downloadButtonId | % { $drv.ExecuteScript('arguments[0].click()', $_) }
                    Write-Host "INFO: Download requested."; Start-Sleep 5

                    $newName  = "$($rep.prefix)$($dayDisp -replace '-', '')_$stamp.csv"
                    $finalPath= Join-Path $DownloadDirRppFinal $newName
                    if ($rep.flow -eq 'history') {
                        $drv.Navigate().GoToUrl('https://ad.rms.rakuten.co.jp/rpp/download'); Start-Sleep 3
                        $targetJP = $targetDate.ToString('yyyy年MM月dd日'); $foundRow = $false; $maxRetries = 12; $retryCount = 0
                        do {
                            try { $drv.FindElementById('btnDownloadHistoryRefresh').Click(); Start-Sleep 2 } catch {}
                            try {
                                $xpath = "//table/tbody/tr[td[2][. = '完了'] and td[5][. = '$($rep.historyName)'] and td[7][. = '$($targetJP)']]/td[3]//a"
                                $link = $drv.FindElementByXPath($xpath)
                                if ($link) { $drv.ExecuteScript('arguments[0].click()', $link); $foundRow = $true; Write-Host "INFO: Download link clicked from history." }
                            } catch {}
                            if (-not $foundRow) { $retryCount++; if ($retryCount -ge $maxRetries) { break }; Write-Host "INFO: Waiting for report completion... ($retryCount/$maxRetries)"; Start-Sleep 30 }
                        } until ($foundRow)
                        if (-not $foundRow) { Write-Warning "Report '$($rep.sheet)' did not complete in time. Skipping."; continue }
                        $deadline = (Get-Date).AddMinutes(5)
                        do { Start-Sleep 2 } while ((Get-ChildItem $DownloadDirCommon -Filter *.crdownload -Ea 0).Count -ne 0 -and (Get-Date) -lt $deadline)
                        $zipFile = Get-ChildItem $DownloadDirCommon -Filter *.zip -Ea 0 | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                        if($zipFile) {
                            $tempFilesToDelete.Add($zipFile.FullName)
                            $extractDir = Join-Path $DownloadDirCommon ($zipFile.BaseName + "_extract")
                            $tempFilesToDelete.Add($extractDir)
                            Expand-Archive -Path $zipFile.FullName -DestinationPath $extractDir -Force
                            $csvFile = Get-ChildItem $extractDir -Filter *.csv | Select-Object -First 1
                            if($csvFile){ Process-ReportFile -sourceFile $csvFile -finalName $finalPath }
                        } else { throw "Downloaded ZIP file not found in '$DownloadDirCommon'." }
                    } else {
                        $deadline = (Get-Date).AddMinutes(5)
                        do { Start-Sleep 2 } while ((Get-ChildItem $DownloadDirCommon -Filter *.crdownload -Ea 0).Count -ne 0 -and (Get-Date) -lt $deadline)
                        $csvDirect = Get-ChildItem $DownloadDirCommon -Filter *.csv -Ea 0 | Where-Object { $_.Name -notlike "店舗カルテ*" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                        if($csvDirect) { $tempFilesToDelete.Add($csvDirect.FullName); Process-ReportFile -sourceFile $csvDirect -finalName $finalPath }
                        else { throw "Downloaded daily CSV file not found in '$DownloadDirCommon'." }
                    }
                    Write-Host "✔ [$($rep.sheet)] Download complete -> $newName"
                }
            } catch {
                Write-Warning "An error occurred while processing RPP for date [$dateString]: $($_.Exception.Message)"
            }
        }
    } # /if RunPhase1

    # ========= PHASE 2: ショップカルテ =========
    Write-Host "`n========== [PHASE 2] Shop Karte Reports =========="
    if ($RunPhase2) {
        $googleDriveFolderId = "1fgONqByDXnUDYTJVkxDWyCr3h0UNT96M"
        $sharedDriveId       = "0AN9eA6x2XdHJUk9PVA"

        Write-Host "`n===== Processing Karte Report 1: Item Page Analysis ====="
        $karteItemUrl = 'https://datatool.rms.rakuten.co.jp/access/item'
        $drv.Navigate().GoToUrl($karteItemUrl); Start-Sleep 5

        foreach ($dateStr in $Dates) {
            Write-Host "`n--- Processing Date: $dateStr ---"
            try {
                $drv.Navigate().GoToUrl($karteItemUrl); Start-Sleep 3
                try { Wait-ForLoadingOverlayToDisappear -Driver $drv -Timeout 90 } catch {}
                try { $drv.FindElementByTagName('body').Click() } catch {}

                Select-DateRangePicker -Driver $drv -TargetDate $dateStr
                Wait-ForLoadingOverlayToDisappear -Driver $drv

                Hover-And-Click -Driver $drv -Element (Wait-Elm $drv 'xpath' "//button[contains(., '全商品CSV')]"); Start-Sleep 1
                $beforeDownload = Get-ChildItem -Path $DownloadDirCommon -Filter *.csv -EA 0 | Select -Expand FullName

                Hover-And-Click -Driver $drv -Element (Wait-Elm $drv 'css' 'button.rms-btn.btn-red')
                Write-Host "INFO: Download started..."

                $deadline = (Get-Date).AddMinutes(5)
                $downloadedFile = $null
                do {
                    Start-Sleep 3
                    $newFile = (Get-ChildItem -Path $DownloadDirCommon -Filter *.csv -EA 0 |
                                Where-Object { $beforeDownload -notcontains $_.FullName } |
                                Sort-Object LastWriteTime -Desc | Select-Object -First 1)
                    if ($newFile) { $downloadedFile = $newFile; break }
                } while ((Get-Date) -lt $deadline)

                if ($downloadedFile) {
                    Process-NationsCsv -filePath $downloadedFile.FullName
                    $dateForFile = (Get-Date $dateStr).ToString('yyyyMMdd') + '_' + (Get-Date).ToString('HHmm')
                    $finalName = Join-Path $DownloadDirCommon "店舗カルテ_商品ページ分析_$dateForFile.csv"
                    Rename-Item -Path $downloadedFile.FullName -NewName $finalName -Force
                    Write-Host "SUCCESS: File processed and renamed -> $(Split-Path $finalName -Leaf)"
                    if ($CleanAndUpload) { Upload-Drive -filePath $finalName -gDriveFolderId $googleDriveFolderId -sharedDriveId $sharedDriveId }
                } else {
                    Write-Error "FAIL: Download failed for $dateStr."
                }
            } catch {
                Write-Warning "An error occurred while processing Karte 1 for date [$dateStr]: $($_.Exception.Message)"
                try { $drv.Navigate().GoToUrl($karteItemUrl); Start-Sleep 3 } catch {}
            }
        }

        if (-not $specificDatesProvided) {
            Write-Host "`n===== Processing Karte Report 2: Natural Search (Previous Day) ====="
            try {
                $drv.Navigate().GoToUrl('https://datatool.rms.rakuten.co.jp/actionlist/step/1'); Start-Sleep 5
                $dateStr = (Get-Date).AddDays(-1).ToString('yyyy/MM/dd')
                $beforeDownload = Get-ChildItem -Path $DownloadDirCommon -Filter *.csv -EA 0 | Select -Expand FullName
                Wait-Elm $drv 'css' 'a.js_trigger_csv' | % { $_.Click() }; Write-Host "INFO: Download started..."
                $deadline = (Get-Date).AddMinutes(5); $downloadedFile = $null
                do {
                    Start-Sleep 5
                    $newFile = (Get-ChildItem -Path $DownloadDirCommon -Filter *.csv -EA 0 |
                                Where-Object { $beforeDownload -notcontains $_.FullName } |
                                Sort LastWriteTime -Desc | Select -First 1)
                    if ($newFile) { $downloadedFile = $newFile; break }
                } while ((Get-Date) -lt $deadline)
                if ($downloadedFile) {
                    Process-ActionListCsv -filePath $downloadedFile.FullName -reportDateStr $dateStr
                    $dateForFile = (Get-Date $dateStr).ToString('yyyyMMdd') + '_' + (Get-Date).ToString('HHmm')
                    $finalName = Join-Path $DownloadDirCommon "店舗カルテ_自然検索_$dateForFile.csv"
                    Rename-Item -Path $downloadedFile.FullName -NewName $finalName -Force
                    Write-Host "SUCCESS: File processed and renamed -> $(Split-Path $finalName -Leaf)"
                    if ($CleanAndUpload) { Upload-Drive -filePath $finalName -gDriveFolderId $googleDriveFolderId -sharedDriveId $sharedDriveId }
                } else {
                    Write-Error "FAIL: Download failed for $dateStr."
                }
            } catch {
                Write-Warning "An error occurred while processing Karte 2: $($_.Exception.Message)"
            }
        } else {
            Write-Host "`nINFO: Skipping Karte Report 2 because specific dates were provided."
        }
    } # /if RunPhase2

    # ========================== フェーズ3: アフィリエイト ==============================
    Write-Host "`n========== [PHASE 3] Affiliate Report =========="
    if ($RunPhase3) {
        try {
            $nowJst = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([datetime]::UtcNow, 'Tokyo Standard Time')
            if ($specificDatesProvided -and $Dates.Count -gt 0) {
                $anchorJst = ($Dates | ForEach-Object { [datetime]::ParseExact($_, 'yyyy/MM/dd', $null) } | Sort-Object | Select-Object -Last 1)
            } else {
                $anchorJst = $nowJst
            }
            $targetMonth = if ($anchorJst.Day -eq 1) { $anchorJst.AddDays(-1).ToString('yyyy-MM') } else { $anchorJst.ToString('yyyy-MM') }
            Write-Host "INFO: Affiliate TargetMonth (JST) => $targetMonth"

            Get-AffiliateReport -Driver $drv -DownloadDir $DownloadDirCommon -TargetMonth $targetMonth
        } catch {
            Write-Warning "An error occurred during Affiliate processing: $($_.Exception.Message)"
        }
    } # /if RunPhase3
   # ========================== フェーズ4: RPPEXP ==============================
   Write-Host "`n========== [PHASE 4] RPPEXP Report =========="
   if ($RunPhase4) {
    foreach ($dateString in $Dates) {
        try {
            $targetDate = [datetime]::ParseExact($dateString, "yyyy/MM/dd", $null)
            Get-RppExpItemReport -Driver $drv -DownloadDirCommon $DownloadDirCommon -DownloadDirRppExpFinal $DownloadDirRppExpFinal -TargetDate $targetDate
        } catch {
            Write-Warning "An error occurred while processing RPPEXP for date [$dateString]: $($_.Exception.Message)"
        }
    }
    }
}
catch { Write-Error "A fatal error occurred during script execution: $($_.Exception.Message)" }
finally {
    if ($drv) { $drv.Quit() }
    if ($tempFilesToDelete.Count -gt 0) {
        Write-Host "`nINFO: Cleaning up temporary RPP files..."
        foreach ($item in $tempFilesToDelete) {
            if (Test-Path $item) {
                Write-Host "  - Removing: $(Split-Path $item -Leaf)"
                Remove-Item $item -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Write-Host "INFO: Browser closed."
    Write-Host "=== Script ended: $(Get-Date) ==="
    Stop-Transcript
}

if ($CleanAndUpload) {
    $newRppCsvs = Get-ChildItem $DownloadDirRppFinal -Filter '*.csv' | Where-Object {
        $preExistingRppCsvs -notcontains $_.FullName -and
        $_.LastWriteTime -ge $scriptStartTime -and
        $_.Length -gt 0
    }

    if ($newRppCsvs.Count -gt 0) {
        Write-Host "`nINFO: Uploading $($newRppCsvs.Count) new RPP CSV file(s)." -ForegroundColor Green
        $googleDriveFolderId = "1fgONqByDXnUDYTJVkxDWyCr3h0UNT96M"
        $sharedDriveId       = "0AN9eA6x2XdHJUk9PVA"
        foreach($csv in $newRppCsvs) {
            try {
                Upload-Drive -filePath $csv.FullName -gDriveFolderId $googleDriveFolderId -sharedDriveId $sharedDriveId
            }
            catch {
                Write-Warning "Failed to upload $($csv.Name): $($_.Exception.Message)"
            }
        }
    } else {
        Write-Host "`nINFO: 今回の実行で新しく作成されたRPPレポートはないため、アップロードをスキップします。" -ForegroundColor Yellow
    }
}

Write-Host "`n=== All tasks completed. ==="
Write-Host "RPP Reports output: $DownloadDirRppFinal"
Write-Host "Karte Reports output: $DownloadDirCommon"
Write-Host "RPPEXP Reports output: $DownloadDirRppExpFinal"
