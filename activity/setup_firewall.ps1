# Cho phep port cua Activity server CHI tu dai IP Cloudflare (chay lai bat cu luc nao de cap nhat danh sach IP).
# Chay trong PowerShell mo bang "Run as administrator":   powershell -ExecutionPolicy Bypass -File activity\setup_firewall.ps1 [-Port 2083]
param([int]$Port = 2083)
$ErrorActionPreference = "Stop"

$admin = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Can quyen Administrator (mo PowerShell bang Run as administrator roi chay lai)."
}

$ranges = @()
foreach ($url in "https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6") {
    $ranges += (Invoke-WebRequest -UseBasicParsing $url).Content -split "\s+" | Where-Object { $_ }
}
if ($ranges.Count -lt 10) { throw "Danh sach IP Cloudflare bat thuong ($($ranges.Count) dong), dung lai." }

$name = "Arona Activity (Cloudflare only)"
if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -DisplayName $name }
New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort $Port -RemoteAddress $ranges -Profile Any | Out-Null
Write-Host "OK: port $Port chi mo cho $($ranges.Count) dai IP Cloudflare."
