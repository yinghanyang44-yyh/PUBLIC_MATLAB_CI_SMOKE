[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('before-install','after-install')][string]$Stage,
    [Parameter(Mandatory = $true)][ValidateRange(0,100)][double]$MinimumFreeGiB,
    [switch]$RequirePlatform
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$artifactDir = $env:MATLAB_CI_ARTIFACT_DIR
if ([string]::IsNullOrWhiteSpace($artifactDir)) { throw 'Artifact directory is required.' }
New-Item -ItemType Directory -Force $artifactDir | Out-Null
$os = Get-CimInstance Win32_OperatingSystem
$computer = Get-CimInstance Win32_ComputerSystem
$drives = @(Get-PSDrive -PSProvider FileSystem | Select-Object Name,Root,Used,Free)
# Allowlisted diagnostics only. Never dump the process environment, credentials or licenses.
$paths = [ordered]@{
    workspace = $env:GITHUB_WORKSPACE; runner_temp = $env:RUNNER_TEMP
    process_temp = [IO.Path]::GetTempPath(); artifact_dir = $artifactDir
    system_drive = $env:SystemDrive
}
$pathDisks = foreach ($entry in $paths.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($entry.Value)) { throw "Missing path: $($entry.Key)" }
    $root = [IO.Path]::GetPathRoot($entry.Value)
    if ([string]::IsNullOrWhiteSpace($root)) { $root = "$($entry.Value)\" }
    $drive = [IO.DriveInfo]::new($root)
    [pscustomobject]@{
        purpose = $entry.Key; path = $entry.Value; root = $root
        free_bytes = $drive.AvailableFreeSpace; free_gib = [math]::Round($drive.AvailableFreeSpace / 1GB, 3)
    }
}
$snapshot = [ordered]@{
    stage = $Stage; utc = [DateTime]::UtcNow.ToString('o')
    os_caption = $os.Caption; os_version = $os.Version; os_build = $os.BuildNumber
    logical_processors = $computer.NumberOfLogicalProcessors
    total_memory_bytes = $computer.TotalPhysicalMemory
    image_os = $env:ImageOS; image_version = $env:ImageVersion
    project_minimum_free_gib = $MinimumFreeGiB; paths = $pathDisks; drives = $drives
}
$snapshot | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $artifactDir "runner-$Stage.json")
$snapshot | ConvertTo-Json -Depth 6 | Write-Output
if ($RequirePlatform) {
    if ($os.Caption -notmatch 'Windows Server 2025') {
        throw 'windows-latest has drifted from Windows Server 2025. Review compatibility; no automatic OS fallback.'
    }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (-not (Test-Path $vswhere)) { throw 'Visual Studio discovery tool was not found.' }
    $raw = & $vswhere -products '*' -version '[17.0,18.0)' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json
    if ($LASTEXITCODE -ne 0) { throw 'Visual Studio discovery failed.' }
    $installed = @($raw | ConvertFrom-Json)
    $compilers = @($installed | Select-Object displayName,installationVersion,installationPath)
    $compilers | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $artifactDir 'visual-studio.json')
    if ($compilers.Count -eq 0) { throw 'Visual Studio 2022 with x64 C/C++ tools is absent. Do not install or choose another compiler implicitly.' }
}
$short = @($pathDisks | Where-Object { $_.free_bytes -lt ($MinimumFreeGiB * 1GB) })
if ($short.Count -gt 0) {
    throw "Project headroom guard failed for $($short.purpose -join ', '): require $MinimumFreeGiB GiB free per distinct relevant volume. This is a project safety threshold, not a MathWorks minimum."
}
