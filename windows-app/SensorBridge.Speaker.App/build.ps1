param(
  [string]$Configuration = 'Release',
  [switch]$Json
)

$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent (Split-Path -Parent $projectDir)
$binDir = Join-Path $projectDir "bin\$Configuration"
$objDir = Join-Path $projectDir 'obj'
$exe = Join-Path $binDir 'SensorBridge.Speaker.App.exe'
$icon = Join-Path $objDir 'SensorBridgeSpeaker.ico'

New-Item -ItemType Directory -Force -Path $binDir, $objDir | Out-Null

$report = [ordered]@{
  ok = $false
  command = 'build_sensorbridge_speaker_app'
  changes_system = $false
  project_dir = $projectDir
  configuration = $Configuration
  exe = $exe
  icon = $icon
  errors = @()
}

try {
  Add-Type -AssemblyName System.Drawing
  $bitmap = New-Object Drawing.Bitmap 32, 32
  $graphics = [Drawing.Graphics]::FromImage($bitmap)
  $graphics.Clear([Drawing.Color]::FromArgb(77, 83, 42))
  $brush = New-Object Drawing.SolidBrush ([Drawing.Color]::White)
  $font = New-Object Drawing.Font 'Segoe UI', 18, ([Drawing.FontStyle]::Bold), ([Drawing.GraphicsUnit]::Pixel)
  $graphics.DrawString('S', $font, $brush, 8, 4)
  $graphics.Dispose()
  $handle = $bitmap.GetHicon()
  $ico = [Drawing.Icon]::FromHandle($handle)
  $stream = [IO.File]::Create($icon)
  $ico.Save($stream)
  $stream.Dispose()
  $bitmap.Dispose()

  $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
  if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
  }
  if (-not (Test-Path $csc)) {
    throw 'csc.exe was not found.'
  }
  $source = Join-Path $projectDir 'Program.cs'
  $refs = @(
    '/reference:System.dll',
    '/reference:System.Core.dll',
    '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll',
    '/reference:System.Web.Extensions.dll'
  )
  $args = @('/target:winexe', "/out:$exe", "/win32icon:$icon", '/optimize+') + $refs + @($source)
  $output = & $csc @args 2>&1
  $report.compiler_output = ($output -join [Environment]::NewLine)
  if ($LASTEXITCODE -ne 0) {
    throw "csc.exe failed with exit code $LASTEXITCODE."
  }
  $report.ok = Test-Path $exe
  $report.exe_exists = $report.ok
} catch {
  $report.errors += $_.Exception.Message
  $report.exe_exists = Test-Path $exe
}

if ($Json) {
  $report | ConvertTo-Json -Depth 5
} else {
  if ($report.ok) { Write-Host "Built $exe" } else { Write-Error ($report.errors -join '; ') }
}
