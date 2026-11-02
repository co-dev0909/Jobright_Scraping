[CmdletBinding()]
param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$outputPath = Join-Path $PSScriptRoot 'artifacts/win-x64'
$venvPath = Join-Path $PSScriptRoot '.build-venv'

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.12 is needed only to build the app. To run it without installing anything, download the Windows package from GitHub Actions.'
}

& $Python -m venv $venvPath
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python build environment.' }
$buildPython = Join-Path $venvPath 'Scripts/python.exe'
& $buildPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the pinned executable builder.' }

& $buildPython -m PyInstaller --onefile --windowed --noupx --clean --noconfirm --name KulaAPP `
    --distpath $outputPath --workpath (Join-Path $PSScriptRoot 'artifacts/build') `
    --specpath (Join-Path $PSScriptRoot 'artifacts/spec') (Join-Path $PSScriptRoot 'kula_app.py')
if ($LASTEXITCODE -ne 0) { throw "Kula APP packaging failed with exit code $LASTEXITCODE." }

Write-Host "Kula APP is ready: $(Join-Path $outputPath 'KulaAPP.exe')"
Write-Host 'The executable includes Python and Tcl/Tk. Users do not need Python, .NET, or an SDK installed.'
