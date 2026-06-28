param(
  [string]$Configuration = 'Release',
  [string]$BaseUrl = 'http://192.168.0.24:27180',
  [string]$CaptureDevice = 'CABLE Output',
  [string]$ShortcutName = 'SensorBridge Speaker.lnk',
  [switch]$Json
)

$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent (Split-Path -Parent $projectDir)
$exe = Join-Path $projectDir "bin\$Configuration\SensorBridge.Speaker.App.exe"
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$shortcutPath = Join-Path $desktop $ShortcutName

$report = [ordered]@{
  ok = $false
  command = 'install_sensorbridge_speaker_shortcut'
  changes_system = $true
  exe = $exe
  shortcut = $shortcutPath
  base_url = $BaseUrl
  capture_device = $CaptureDevice
  errors = @()
}

try {
  if (-not (Test-Path $exe)) { throw "Executable not found: $exe" }
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($shortcutPath)
  $shortcut.TargetPath = $exe
  $shortcut.Arguments = "--project-root `"$root`" --base-url `"$BaseUrl`" --capture-device `"$CaptureDevice`""
  $shortcut.WorkingDirectory = $root
  $shortcut.IconLocation = "$exe,0"
  $shortcut.Description = 'SensorBridge Speaker'
  $shortcut.Save()
  $report.ok = Test-Path $shortcutPath
  $report.shortcut_exists = $report.ok
} catch {
  $report.errors += $_.Exception.Message
  $report.shortcut_exists = Test-Path $shortcutPath
}

if ($Json) {
  $report | ConvertTo-Json -Depth 5
} else {
  if ($report.ok) { Write-Host "Created $shortcutPath" } else { Write-Error ($report.errors -join '; ') }
}
