[CmdletBinding()]
param(
    [string]$Python = "py",
    [string]$NsisCompiler = "",
    [ValidateSet("PASS", "FAIL", "NOT_VERIFIED")]
    [string]$AutomatedBaseline = "NOT_VERIFIED",
    [ValidateSet("PASS", "FAIL", "NOT_VERIFIED")]
    [string]$LocalPackageSmoke = "NOT_VERIFIED"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$versionDeclaration = Get-Content -Raw -LiteralPath (Join-Path $repositoryRoot "pyproject.toml")
if ($versionDeclaration -notmatch '(?m)^version = "0\.1\.0\.dev7"\s*$') {
    throw "This historical preview build requires the 0.1.0.dev7 source version. Use build-v1.ps1 for the 1.0.0 laboratory package."
}
$releaseEnvironment = Join-Path $repositoryRoot ".venv-release"
$releasePython = Join-Path $releaseEnvironment "Scripts\python.exe"
$metadataDirectory = Join-Path $repositoryRoot "build\release-metadata"
$frozenDirectory = Join-Path $repositoryRoot "build\frozen"
$pyinstallerWork = Join-Path $repositoryRoot "build\pyinstaller"
$outputDirectory = Join-Path $repositoryRoot "dist\feedback-preview"

if (-not (Test-Path -LiteralPath $releasePython)) {
    if ($Python -eq "py") {
        & py -3.11 -m venv $releaseEnvironment
    } else {
        & $Python -m venv $releaseEnvironment
    }
}
if (-not (Test-Path -LiteralPath $releasePython)) {
    throw "A CPython 3.11 release environment could not be created. Pass -Python with a Python 3.11 executable."
}

& $releasePython -m pip install --disable-pip-version-check --require-virtualenv "pip==26.2.1"
if ($LASTEXITCODE -ne 0) { throw "Release pip installation failed." }
& $releasePython -m pip install --disable-pip-version-check --require-virtualenv -r (Join-Path $PSScriptRoot "requirements-release.txt")
if ($LASTEXITCODE -ne 0) { throw "Release dependency installation failed." }
& $releasePython -m pip install --disable-pip-version-check --require-virtualenv --no-deps $repositoryRoot
if ($LASTEXITCODE -ne 0) { throw "LabDAQ package installation failed." }

& $releasePython (Join-Path $PSScriptRoot "release_metadata.py") prepare --root $repositoryRoot --output $metadataDirectory
if ($LASTEXITCODE -ne 0) { throw "Build identity generation failed." }
$buildInfo = Join-Path $metadataDirectory "build-info.json"
$buildIdentity = Get-Content -Raw -LiteralPath $buildInfo | ConvertFrom-Json
$env:LABDAQ_BUILD_INFO = $buildInfo
$env:LABDAQ_BUILD_ID = $buildIdentity.build_id

# Windows PowerShell treats redirected native stderr as errors, including
# PyInstaller's ordinary INFO messages. Check its process exit code instead.
$buildErrorPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    & $releasePython -m PyInstaller --clean --noconfirm --distpath $frozenDirectory --workpath $pyinstallerWork (Join-Path $PSScriptRoot "labdaq-feedback.spec") 2>&1 | ForEach-Object { $_.ToString() }
    $pyinstallerExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $buildErrorPreference
}
if ($pyinstallerExit -ne 0) { throw "PyInstaller failed with exit code $pyinstallerExit." }

$payloadDirectory = Join-Path $frozenDirectory "LabDAQ"
& $releasePython (Join-Path $PSScriptRoot "release_metadata.py") inventory --payload $payloadDirectory --output (Join-Path $metadataDirectory "installed-files.txt")
if ($LASTEXITCODE -ne 0) { throw "Install inventory generation failed." }
$payloadInternal = Join-Path $payloadDirectory "_internal"
foreach ($distribution in @("nidaqmx", "nitypes", "hightime")) {
    $metadata = @(Get-ChildItem -Path (Join-Path $payloadInternal "$distribution-*.dist-info") -Directory -ErrorAction SilentlyContinue)
    if ($metadata.Count -ne 1) {
        throw "The frozen payload must contain exactly one $distribution distribution-metadata directory."
    }
}
$frozenCli = Join-Path $payloadDirectory "LabDAQ-CLI.exe"
$niSmokeLines = @(& $frozenCli hardware list --backend ni --json 2>&1)
$niSmokeExit = $LASTEXITCODE
$niSmokeText = ($niSmokeLines -join [Environment]::NewLine).Trim()
try {
    $niSmoke = $niSmokeText | ConvertFrom-Json
} catch {
    throw "Frozen NI discovery did not emit valid JSON: $niSmokeText"
}
$niSmokeAccepted = (
    ($niSmokeExit -eq 0 -and $niSmoke.overall -eq "ok") -or
    ($niSmokeExit -ne 0 -and $niSmoke.error.code -eq "DAQ.HARDWARE.DRIVER_UNAVAILABLE")
)
if (-not $niSmokeAccepted) {
    throw "Frozen NI discovery failed its packaging gate (exit $niSmokeExit): $niSmokeText"
}

if ([string]::IsNullOrWhiteSpace($NsisCompiler)) {
    $candidate = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($null -ne $candidate) {
        $NsisCompiler = $candidate.Source
    } else {
        $localNsis = Join-Path $repositoryRoot ".tools\nsis\makensis.exe"
        if (Test-Path -LiteralPath $localNsis) {
            $NsisCompiler = $localNsis
        }
    }
}
if ([string]::IsNullOrWhiteSpace($NsisCompiler) -or -not (Test-Path -LiteralPath $NsisCompiler)) {
    throw "NSIS 3.12 makensis.exe was not found. Install it or pass -NsisCompiler."
}
$nsisVersion = (& $NsisCompiler /VERSION).Trim()
if ($nsisVersion -ne "v3.12") {
    throw "NSIS 3.12 is required; found '$nsisVersion' at '$NsisCompiler'."
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
& $NsisCompiler "/INPUTCHARSET" "UTF8" "/DPAYLOAD_DIR=$payloadDirectory" "/DOUTPUT_DIR=$outputDirectory" "/DBUILD_METADATA_DIR=$metadataDirectory" "/DREPOSITORY_ROOT=$repositoryRoot" (Join-Path $PSScriptRoot "LabDAQ-feedback-preview.nsi")
if ($LASTEXITCODE -ne 0) { throw "NSIS installer compilation failed." }

$installer = Join-Path $outputDirectory "LabDAQ-0.1.0.dev7-feedback-win64-setup.exe"
& $releasePython (Join-Path $PSScriptRoot "release_metadata.py") finalize --installer $installer --build-info $buildInfo --output $outputDirectory --automated-baseline $AutomatedBaseline --local-package-smoke $LocalPackageSmoke
if ($LASTEXITCODE -ne 0) { throw "Release manifest generation failed." }
Copy-Item -LiteralPath $buildInfo -Destination (Join-Path $outputDirectory "LabDAQ-0.1.0.dev7-feedback-win64-build-info.json") -Force
Copy-Item -LiteralPath (Join-Path $metadataDirectory "THIRD-PARTY-NOTICES.txt") -Destination (Join-Path $outputDirectory "LabDAQ-0.1.0.dev7-feedback-win64-third-party-notices.txt") -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot "docs\releases\DAQ-0.1.0.dev7-feedback-preview.md") -Destination (Join-Path $outputDirectory "LabDAQ-0.1.0.dev7-feedback-win64-release-notes.md") -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot "docs\releases\INTERNAL-EVALUATION-NOTICE.txt") -Destination (Join-Path $outputDirectory "LabDAQ-0.1.0.dev7-feedback-win64-internal-evaluation-notice.txt") -Force

Write-Output "Installer: $installer"
Write-Output "Release manifest: $(Join-Path $outputDirectory 'LabDAQ-0.1.0.dev7-feedback-win64-release.json')"
Write-Output "Checksum: $(Join-Path $outputDirectory 'LabDAQ-0.1.0.dev7-feedback-win64-setup.exe.sha256')"
