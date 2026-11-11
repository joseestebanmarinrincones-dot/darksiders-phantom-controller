param(
    [string]$ProcessName = 'MandoFantasma',
    [string]$Out = 'docs\screenshot.png',
    [string]$TitleLike = 'Mando Fantasma'
)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinCap {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
$targets = @(Get-Process $ProcessName -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
if ($targets.Count -eq 0) { throw "No se encontro el proceso $ProcessName" }

$script:candidates = New-Object System.Collections.ArrayList
$cb = [WinCap+EnumWindowsProc]{
    param($h, $l)
    [uint32]$winpid = 0
    [WinCap]::GetWindowThreadProcessId($h, [ref]$winpid) | Out-Null
    if ($targets -contains [int]$winpid -and [WinCap]::IsWindowVisible($h)) {
        $len = [WinCap]::GetWindowTextLength($h)
        $sb = New-Object System.Text.StringBuilder ($len + 2)
        [WinCap]::GetWindowText($h, $sb, $sb.Capacity) | Out-Null
        $title = $sb.ToString()
        if ($title -like "*$TitleLike*") {
            [void]$script:candidates.Add([PSCustomObject]@{Handle=$h; Title=$title})
        }
    }
    return $true
}
[WinCap]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

$chosen = $null
$foreground = [WinCap]::GetForegroundWindow()
foreach ($candidate in $script:candidates) {
    if ($candidate.Handle -eq $foreground) { $chosen = $candidate; break }
}
if (-not $chosen -and $script:candidates.Count -gt 0) { $chosen = $script:candidates[0] }
if (-not $chosen) {
    $p = Get-Process -Id $targets[0] -ErrorAction SilentlyContinue
    if ($p -and $p.MainWindowHandle -ne 0) { $chosen = [PSCustomObject]@{Handle=$p.MainWindowHandle; Title=$p.MainWindowTitle} }
}
if (-not $chosen) { throw "No se encontro una ventana de $ProcessName con titulo '$TitleLike'" }

[WinCap]::ShowWindow($chosen.Handle, 9) | Out-Null
[WinCap]::SetForegroundWindow($chosen.Handle) | Out-Null
Start-Sleep -Milliseconds 400

$r = New-Object WinCap+RECT
[WinCap]::GetWindowRect($chosen.Handle, [ref]$r) | Out-Null
$w = $r.Right - $r.Left
$h = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[WinCap]::PrintWindow($chosen.Handle, $hdc, 2) | Out-Null
$g.ReleaseHdc($hdc)
$g.Dispose()
$bmp.Save((Join-Path (Get-Location) $Out), [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "$($chosen.Title) - $w x $h"
