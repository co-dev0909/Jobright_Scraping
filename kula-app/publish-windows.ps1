[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectPath = Join-Path $PSScriptRoot 'src/Kula.App/Kula.App.csproj'
$outputPath = Join-Path $PSScriptRoot 'artifacts/win-x64'

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw 'Install the .NET 10 SDK from https://dotnet.microsoft.com/download/dotnet/10.0, then run this script again.'
}

& dotnet publish $projectPath --configuration Release --runtime win-x64 --self-contained true `
    '-p:PublishSingleFile=true' '-p:IncludeNativeLibrariesForSelfExtract=true' --output $outputPath

if ($LASTEXITCODE -ne 0) {
    throw "Kula APP publish failed with exit code $LASTEXITCODE."
}

Write-Host "Kula APP is ready: $(Join-Path $outputPath 'KulaAPP.exe')"
Write-Host 'Run KulaAPP.exe, copy its pairing key, and paste the key into the extension settings.'
