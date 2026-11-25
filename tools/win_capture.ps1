param([string]$Title = "CS2 Stats", [string]$Out = "C:\Users\Beneqin1\AppData\Local\Temp\cap.png")
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class W32C {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
    [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int v);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lp);
    [DllImport("user32.dll")] public static extern int GetWindowTextW(IntPtr h, StringBuilder sb, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    public delegate bool EnumProc(IntPtr h, IntPtr lp);
    public struct RECT { public int Left, Top, Right, Bottom; }
    public static IntPtr Found = IntPtr.Zero;
    public static string Needle = "";
    public static bool OnEnum(IntPtr h, IntPtr lp) {
        if (!IsWindowVisible(h)) return true;
        var sb = new StringBuilder(256);
        GetWindowTextW(h, sb, 256);
        if (sb.ToString().Contains(Needle)) { Found = h; return false; }
        return true;
    }
    public static IntPtr Find(string needle) {
        Needle = needle; Found = IntPtr.Zero;
        EnumWindows(OnEnum, IntPtr.Zero);
        return Found;
    }
}
'@
[W32C]::SetProcessDpiAwareness(1) | Out-Null
Add-Type -AssemblyName System.Drawing
$p = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "*$Title*" } | Select-Object -First 1
if (-not $p) { $p = Get-Process CS2Stats -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "*$Title*" } | Select-Object -First 1 }
if (-not $p) { $h = [W32C]::Find($Title) } else { $h = [System.IntPtr]::new($p.MainWindowHandle.ToInt64()) }
if ($h -eq [System.IntPtr]::Zero) { Write-Error "window not found: $Title"; exit 1 }
$r = New-Object W32C+RECT
[W32C]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $hh = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
[W32C]::PrintWindow($h, $dc, 2) | Out-Null
$g.ReleaseHdc($dc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $Out ($w x $hh)"
