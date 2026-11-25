#requires -version 5.1
<#
    Build CS2Stats.exe with PyInstaller.

    Default: onedir (recommended; lower antivirus false-positive rate).
      powershell -ExecutionPolicy Bypass -File build_exe.ps1

    Optional: onefile (single exe; slower first start, more false positives).
      powershell -ExecutionPolicy Bypass -File build_exe.ps1 -OneFile

    Output:
      dist\CS2Stats\CS2Stats.exe                     (onedir)
      dist\CS2Stats-1.4.1-onedir.zip                 (onedir download)
      dist\CS2Stats.exe                              (onefile)
      dist\CS2Stats-1.4.1-onefile.zip                (onefile download)
#>
[CmdletBinding()]
param(
    [switch]$OneFile
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Push-Location $Root
try {
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path $Python)) {
        throw "Missing .venv. See README.txt, section 10 (从源码运行 / 重新打包)."
    }

    Write-Host "Checking build dependencies..." -ForegroundColor Cyan
    & $Python -c "import awpy, jinja2, PyInstaller; print('awpy', awpy.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Run: .venv\Scripts\python.exe -m pip install -r requirements-build.txt"
    }

    $Mode = if ($OneFile) { 'onefile' } else { 'onedir' }
    $DistRoot = Join-Path $Root 'dist'
    $WorkRoot = Join-Path $Root 'build'
    $SpecRoot = Join-Path $Root 'build'
    New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot, $SpecRoot | Out-Null

    $TemplatesSrc = Join-Path $Root 'cs2stats\templates'
    $StaticSrc = Join-Path $Root 'cs2stats\static'
    $Common = @(
        '--noconfirm',
        '--clean',
        '--windowed',
        '--noupx',
        '--name', 'CS2Stats',
        '--distpath', $DistRoot,
        '--workpath', $WorkRoot,
        '--specpath', $SpecRoot,
        '--hidden-import', 'awpy._awpy',
        '--hidden-import', 'demoparser2',
        '--collect-all', 'demoparser2',
        '--exclude-module', 'PIL',
        '--exclude-module', 'matplotlib',
        '--exclude-module', 'pydantic',
        '--exclude-module', 'typer',
        '--exclude-module', 'rich',
        '--exclude-module', 'pygments',
        '--add-data', "$TemplatesSrc;cs2stats\templates",
        '--add-data', "$StaticSrc;cs2stats\static",
        (Join-Path $Root 'run_app.py')
    )

    if ($OneFile) {
        Write-Host "Building onefile..." -ForegroundColor Cyan
        & $Python -m PyInstaller --onefile @Common
    }
    else {
        Write-Host "Building onedir..." -ForegroundColor Cyan
        & $Python -m PyInstaller --onedir @Common
    }
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    $PackageRoot = Join-Path $Root ("package\CS2Stats-1.4.1-" + $Mode)
    if (Test-Path $PackageRoot) {
        Remove-Item -Recurse -Force $PackageRoot
    }
    New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null

    if ($OneFile) {
        $Exe = Join-Path $DistRoot 'CS2Stats.exe'
        if (-not (Test-Path $Exe)) { throw "Missing $Exe" }
        Copy-Item $Exe (Join-Path $PackageRoot 'CS2Stats.exe')
    }
    else {
        $AppDir = Join-Path $DistRoot 'CS2Stats'
        if (-not (Test-Path (Join-Path $AppDir 'CS2Stats.exe'))) {
            throw "Missing onedir output at $AppDir"
        }
        # Flat layout: CS2Stats.exe sits at the package root, so users see it
        # immediately after extracting the zip.
        Copy-Item -Recurse (Join-Path $AppDir '*') $PackageRoot
    }

    # fixtures live next to CS2Stats.exe: find_fixture() searches the
    # executable directory, and a user may unzip the folder anywhere.
    $FixturesOut = Join-Path $PackageRoot 'fixtures'
    New-Item -ItemType Directory -Force -Path $FixturesOut | Out-Null
    Copy-Item 'fixtures\user_dust2_20261122.fixture.json' $FixturesOut
    Copy-Item 'fixtures\user_mirage_20261122.fixture.json' $FixturesOut
    Copy-Item 'fixtures\FIXTURE_FORMAT.txt' $FixturesOut
    Copy-Item 'fixtures\user_demo.fixture.json.template' $FixturesOut
    Copy-Item 'README.txt' $PackageRoot
    Copy-Item 'CHANGELOG.txt' $PackageRoot
    Copy-Item 'validate_stats.py' $PackageRoot

    # Never ship machine-local state created while testing the package.
    foreach ($Name in @('stat.cfg', 'stat_cs2stats.cfg', 'stats.cfg', 'settings.json')) {
        $Junk = Join-Path $PackageRoot $Name
        if (Test-Path $Junk) { Remove-Item -Force $Junk }
    }
    $OutputDir = Join-Path $PackageRoot 'output'
    if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
    $CacheDir = Join-Path $PackageRoot 'cache'
    if (Test-Path $CacheDir) { Remove-Item -Recurse -Force $CacheDir }

    $Zip = Join-Path $DistRoot ("CS2Stats-1.4.1-" + $Mode + ".zip")
    if (Test-Path $Zip) { Remove-Item -Force $Zip }
    Compress-Archive -Path (Join-Path $PackageRoot '*') -DestinationPath $Zip -Force

    Write-Host ""
    Write-Host "Build complete." -ForegroundColor Green
    Write-Host "  Package: $PackageRoot"
    Write-Host "  Zip:     $Zip"
    Write-Host "  Exe:     $(Join-Path $PackageRoot 'CS2Stats.exe')"
}
finally {
    Pop-Location
}
