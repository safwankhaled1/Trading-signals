param([string]$InnoCompiler = "")
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Create .venv and install requirements-dev.txt first.' }
& $projectPython -m PyInstaller --noconfirm GoldSignalDesk.spec
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
if (-not $InnoCompiler) {
    $compilerCandidates = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$projectRoot\.tools\inno\ISCC.exe")
    $InnoCompiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ($InnoCompiler) {
    & $InnoCompiler (Join-Path $projectRoot 'installer\GoldSignalDesk.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
} else {
    Write-Output 'Portable application built in dist\GoldSignalDesk-0.1.1. Install Inno Setup to generate the installer.'
}
