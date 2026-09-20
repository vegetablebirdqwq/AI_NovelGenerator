# ============================================================
#   AI 小说生产线 · 实时进度面板
#   双击桌面快捷方式即可打开。Ctrl+C 关闭。
#   数据直接读磁盘，不依赖日志，所以随时打开都是准的。
# ============================================================
param(
    [int]$Refresh = 3,
    [switch]$Once
)

$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root      = $PSScriptRoot
$BooksFile = Join-Path $Root 'books.json'

# ---------- 工具 ----------
function Get-Bar {
    param([double]$Done, [double]$Total, [int]$Width = 24)
    if ($Total -le 0) { return ('·' * $Width) }
    $f = [int][math]::Round(($Done / $Total) * $Width)
    if ($f -gt $Width) { $f = $Width }
    if ($f -lt 0) { $f = 0 }
    return ('█' * $f) + ('░' * ($Width - $f))
}

function Get-DlgPct {
    param([string]$Path)
    $t = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $nows = ($t -replace '\s', '')
    if ($nows.Length -eq 0) { return 0.0 }
    $m = [regex]::Matches($t, '[\u201c]([^\u201d]*)[\u201d]')
    $sum = 0
    foreach ($x in $m) { $sum += $x.Groups[1].Value.Length }
    return [math]::Round($sum / $nows.Length * 100, 1)
}

function Get-CnLen {
    param([string]$Path)
    $t = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    return ($t -replace '\s', '').Length
}

function Format-Ago {
    param($Span)
    if ($null -eq $Span) { return '—' }
    $s = $Span.TotalSeconds
    if ($s -lt 60)   { return ('{0:N0} 秒前' -f $s) }
    if ($s -lt 3600) { return ('{0:N0} 分钟前' -f ($s / 60)) }
    return ('{0:N1} 小时前' -f ($s / 3600))
}

# ---------- 采集 ----------
function Get-Snapshot {
    $now = Get-Date
    $books = @((Get-Content $BooksFile -Raw -Encoding UTF8 | ConvertFrom-Json).books |
               Where-Object { $_.enabled -ne $false })

    # 是否在跑
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
               Where-Object { $_.CommandLine -like '*pipeline.py*' })
    $running = $procs.Count -gt 0

    $rows = @()
    $allWrites = @()
    foreach ($b in $books) {
        $sd = $b.source_dir
        $cdir = Join-Path $sd 'chapters'
        $fdir = Join-Path $sd 'chapters_final'

        $ch = @(Get-ChildItem $cdir -Filter 'chapter_*.txt' -EA SilentlyContinue |
                Where-Object { $_.Name -notlike '*bak*' })
        $fn = @(Get-ChildItem $fdir -Filter 'chapter_*.txt' -EA SilentlyContinue)

        $total = [int]$b.total_chapters
        $nCh = $ch.Count
        $nFn = $fn.Count

        $allWrites += $ch
        $allWrites += $fn

        # 当前阶段判定
        $stage = '等待'
        if ($nCh -lt $total)      { $stage = 'S3 写正文' }
        elseif ($nFn -lt $total)  { $stage = 'S4 文本润色' }
        else                      { $stage = 'S5 导出' }

        $last = $null
        if ($ch.Count -gt 0 -and $fn.Count -gt 0) {
            $last = @($ch + $fn | Sort-Object LastWriteTime -Descending)[0]
        } elseif ($ch.Count -gt 0) { $last = @($ch | Sort-Object LastWriteTime -Descending)[0] }
        elseif ($fn.Count -gt 0)   { $last = @($fn | Sort-Object LastWriteTime -Descending)[0] }

        $rows += [pscustomobject]@{
            Name    = $b.name
            Plat    = $b.platform
            Id      = $b.id
            Dir     = $sd
            Total   = $total
            Ch      = $nCh
            Fn      = $nFn
            Stage   = $stage
            Last    = $last
            Overall = if ($total -gt 0) { ($nCh + $nFn) / (2.0 * $total) } else { 0 }
        }
    }

    # 速度：最近 30 分钟内落盘的章节数
    $cut = $now.AddMinutes(-30)
    $recent = @($allWrites | Where-Object { $_.LastWriteTime -gt $cut })
    $rate = $recent.Count / 30.0    # 章/分钟

    # 剩余工作量（正文 + 润色 各算一单位）
    $remain = 0
    foreach ($r in $rows) { $remain += (($r.Total - $r.Ch) + ($r.Total - $r.Fn)) }

    $eta = $null
    if ($rate -gt 0.001 -and $remain -gt 0) {
        $eta = $now.AddMinutes($remain / $rate)
    }

    return [pscustomobject]@{
        Now     = $now
        Running = $running
        Rows    = $rows
        Rate    = $rate
        Remain  = $remain
        Eta     = $eta
        Writes  = @($allWrites | Sort-Object LastWriteTime -Descending)
        Cut     = $cut
    }
}

# ---------- 渲染 ----------
function Render {
    param($snap)

    $w = 66
    $line = '═' * $w
    $out = New-Object System.Collections.Generic.List[string]

    $out.Add('')
    $out.Add('  ' + $line)
    $out.Add('    AI 小说生产线 · 实时进度            ' + $snap.Now.ToString('HH:mm:ss'))
    $out.Add('  ' + $line)
    $out.Add('')

    if ($snap.Running) {
        $out.Add('   状态：  ● 正在生产')
    } else {
        $out.Add('   状态：  ○ 未运行   （双击「开始生产.cmd」启动）')
    }
    $out.Add('')

    foreach ($r in $snap.Rows) {
        $out.Add(('   {0}《{1}》' -f $r.Plat, $r.Name))

        $pct = [int][math]::Round($r.Overall * 100)
        $out.Add(('     总进度  {0}  {1,3}%' -f (Get-Bar $r.Overall 1 24), $pct))
        $out.Add(('     正文    {0}  {1}/{2}' -f (Get-Bar $r.Ch $r.Total 24), $r.Ch, $r.Total))
        $out.Add(('     润色    {0}  {1}/{2}' -f (Get-Bar $r.Fn $r.Total 24), $r.Fn, $r.Total))

        $ago = '—'
        $when = '—'
        if ($r.Last) {
            $ago = Format-Ago ($snap.Now - $r.Last.LastWriteTime)
            $when = $r.Last.LastWriteTime.ToString('HH:mm:ss')
        }
        $out.Add(('     阶段    {0}      最近落盘 {1}  ({2})' -f $r.Stage, $when, $ago))
        $out.Add('')
    }

    # 速度
    $out.Add('  ' + ('─' * $w))
    if ($snap.Rate -gt 0.001) {
        $perCh = 1.0 / $snap.Rate
        $out.Add(('   速度    最近30分钟 {0:N0} 章   ≈ {1:N1} 分钟/章' -f ($snap.Rate * 30), $perCh))
    } else {
        $out.Add('   速度    最近30分钟无产出')
    }
    $out.Add(('   剩余    {0} 个工序（正文+润色）' -f $snap.Remain))
    if ($snap.Eta) {
        $left = $snap.Eta - $snap.Now
        $out.Add(('   预计    {0}   （还需约 {1:N1} 小时）' -f $snap.Eta.ToString('HH:mm'), $left.TotalHours))
    } else {
        $out.Add('   预计    —')
    }
    $out.Add('')

    # 最近落盘
    $out.Add('  ' + ('─' * $w))
    $out.Add('   最近落盘：')
    $recent = @($snap.Writes | Select-Object -First 6)
    if ($recent.Count -eq 0) {
        $out.Add('     （还没有产出）')
    } else {
        foreach ($f in $recent) {
            $kind = if ($f.Directory.Name -eq 'chapters_final') { '润色' } else { '正文' }
            $book = $f.Directory.Parent.Name
            $out.Add(('     {0}  {1,-6} {2,-22} {3}' -f
                      $f.LastWriteTime.ToString('HH:mm:ss'), $kind, $f.Name, $book))
        }
    }
    $out.Add('')

    # 成品质量（取最新 5 章成品）
    $q = @()
    foreach ($r in $snap.Rows) {
        $fdir = Join-Path $r.Dir 'chapters_final'
        $fs = @(Get-ChildItem $fdir -Filter 'chapter_*.txt' -EA SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 5)
        if ($fs.Count -gt 0) {
            $ws = @(); $ds = @()
            foreach ($f in $fs) {
                $ws += Get-CnLen $f.FullName
                $ds += Get-DlgPct $f.FullName
            }
            $avgW = [int](($ws | Measure-Object -Average).Average)
            $avgD = [math]::Round((($ds | Measure-Object -Average).Average), 1)
            $q += [pscustomobject]@{ Plat = $r.Plat; W = $avgW; D = $avgD; N = $fs.Count }
        }
    }
    if ($q.Count -gt 0) {
        $out.Add('  ' + ('─' * $w))
        $out.Add('   成品质量（最新5章平均）：')
        foreach ($x in $q) {
            $flag = if ($x.D -lt 40) { '  ← 对白偏低' } else { '' }
            $out.Add(('     {0,-7} 字数 {1,5}   对白 {2,5}%{3}' -f $x.Plat, $x.W, $x.D, $flag))
        }
        $out.Add('')
    }

    $out.Add('  ' + $line)
    $out.Add('')

    return ($out -join "`n")
}

# ---------- 主循环 ----------
if ($Once) {
    Write-Host (Render (Get-Snapshot))
    exit 0
}

try { $Host.UI.RawUI.WindowTitle = 'AI 小说生产线 · 实时进度' } catch {}
try {
    $ws = $Host.UI.RawUI.WindowSize
    if ($ws.Width  -lt 76) { $ws.Width  = 76 }
    if ($ws.Height -lt 40) { $ws.Height = 40 }
    $Host.UI.RawUI.WindowSize = $ws
    $bs = $Host.UI.RawUI.BufferSize
    $bs.Width = $ws.Width
    if ($bs.Height -lt 400) { $bs.Height = 400 }
    $Host.UI.RawUI.BufferSize = $bs
} catch {}

while ($true) {
    $frame = Render (Get-Snapshot)
    Clear-Host
    Write-Host $frame
    Write-Host ('   每 {0} 秒刷新  ·  Ctrl+C 退出' -f $Refresh) -ForegroundColor DarkGray
    Start-Sleep -Seconds $Refresh
}
