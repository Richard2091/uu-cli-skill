#Requires -Version 5.1
<#
.SYNOPSIS
  UU CLI read-only health check: CLI discovery, client communication, login
  state, device online state and leftover terminal sessions.

.DESCRIPTION
  Read-only: it never creates or kills a session, never connects to the device
  and never changes anything on the remote machine.  It emits structured
  NAME=VALUE lines so an agent or script can read it directly.

  Requires no Python.  It is the PowerShell equivalent of `uu-exec.py --check`.

.PARAMETER CliPath
  Path to uuyc-cli.exe.  Discovered automatically when omitted (environment
  variable, registry, PATH, then common install roots).

.PARAMETER DeviceId
  Target device id.  When omitted, every device in the account is listed.

.PARAMETER Password
  The remote Windows account password, needed only to list sessions while the
  remote screen is locked.  Omit it and the session check is skipped.

.EXAMPLE
  pwsh -File uu-doctor.ps1

.EXAMPLE
  pwsh -File uu-doctor.ps1 -DeviceId <DEVICE_ID>

.EXAMPLE
  # keep the password out of the process list and shell history
  $pw = Read-Host -AsSecureString 'Remote password'
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
             [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw))
  pwsh -File uu-doctor.ps1 -DeviceId <DEVICE_ID> -Password $plain
#>
param(
  [string]$CliPath = "",
  [string]$DeviceId = "",
  [string]$Password = ""
)

$ErrorActionPreference = 'Continue'

# Windows PowerShell 5.1 defaults to ASCII for native-command I/O, which would
# mangle the non-ASCII progress text the CLI writes (and any non-ASCII password
# piped on stdin).
try {
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  [Console]::OutputEncoding = $utf8
  [Console]::InputEncoding = $utf8
  $OutputEncoding = $utf8
} catch {
  Write-Output "WARN=could not set UTF-8 console encoding: $($_.Exception.Message)"
}

# ---------------------------------------------------------------- discovery
function Find-UuCli {
  param([string]$Explicit)

  $candidates = @()
  if ($Explicit) { $candidates += $Explicit }
  if ($env:UU_CLI_PATH) { $candidates += $env:UU_CLI_PATH }

  foreach ($key in @(
      'HKLM:\SOFTWARE\Netease\GameViewerSetup',
      'HKLM:\SOFTWARE\WOW6432Node\Netease\GameViewerSetup',
      'HKCU:\SOFTWARE\Netease\GameViewerSetup',
      'HKCU:\SOFTWARE\Netease\GameViewer')) {
    if (Test-Path $key) {
      $item = Get-ItemProperty $key -ErrorAction SilentlyContinue
      foreach ($name in @('InstDir', 'instDir', 'InstallDir', 'installDir')) {
        $dir = $item.$name
        if ($dir) { $candidates += (Join-Path $dir 'bin\uuyc-cli.exe') }
      }
    }
  }

  $onPath = Get-Command 'uuyc-cli.exe' -ErrorAction SilentlyContinue
  if ($onPath) { $candidates += $onPath.Source }

  $roots = @()
  foreach ($name in @('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA', 'APPDATA', 'SystemDrive')) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($value) { $roots += $value }
  }
  foreach ($drive in @('C:', 'D:', 'E:', 'F:', 'G:')) {
    if (Test-Path "$drive\") { $roots += "$drive\" }
  }
  $tails = @(
    'uuRemote\GameViewer\bin\uuyc-cli.exe',
    'GameViewer\bin\uuyc-cli.exe',
    'Netease\GameViewer\bin\uuyc-cli.exe',
    'Netease\uuRemote\GameViewer\bin\uuyc-cli.exe',
    'uu\GameViewer\bin\uuyc-cli.exe'
  )
  foreach ($root in $roots) {
    foreach ($sub in @('', 'Application', 'Programs', 'Netease', 'Program Files')) {
      foreach ($tail in $tails) {
        $candidates += (Join-Path (Join-Path $root $sub) $tail)
      }
    }
  }

  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate -PathType Leaf)) { return $candidate }
  }
  return $null
}

# ---------------------------------------------------------------- invocation
function Invoke-Uu {
  <#
    Run the CLI and keep stdout strictly separate from stderr.

    Windows PowerShell 5.1 wraps a native program's stderr in ErrorRecords, and
    `2>&1 | Out-String` renders those as multi-line blocks complete with the
    script's own source line.  Concatenating that with the JSON makes
    ConvertFrom-Json fail, so a perfectly healthy CLI looked "not installed".
  #>
  param([string[]]$CmdArgs, [string]$Stdin = "")

  if ($Stdin) {
    $all = $Stdin | & $CliPath @CmdArgs 2>&1
  } else {
    $all = & $CliPath @CmdArgs 2>&1
  }
  $code = $LASTEXITCODE

  $outLines = @()
  $errLines = @()
  foreach ($item in @($all)) {
    if ($item -is [System.Management.Automation.ErrorRecord]) {
      $errLines += $item.Exception.Message
    } else {
      $outLines += $item
    }
  }
  return @{
    Code = $code
    Out  = ($outLines | Out-String).Trim()
    Err  = ($errLines -join "`n").Trim()
  }
}

function ConvertFrom-UuJson {
  param([string]$Text)
  if (-not $Text) { return $null }
  $start = $Text.IndexOf('{')
  $end = $Text.LastIndexOf('}')
  if ($start -lt 0 -or $end -le $start) { return $null }
  try {
    return ($Text.Substring($start, $end - $start + 1) | ConvertFrom-Json -ErrorAction Stop)
  } catch {
    return $null
  }
}

function Invoke-UuJson {
  param([string[]]$CmdArgs, [string]$Stdin = "")
  $result = Invoke-Uu -CmdArgs ($CmdArgs + '--json') -Stdin $Stdin
  return @{
    Code = $result.Code
    Data = (ConvertFrom-UuJson $result.Out)
    Err  = $result.Err
    Raw  = $result.Out
  }
}

# ------------------------------------------------------------------- checks
Write-Output "=== uu-doctor (read-only) ==="

$CliPath = Find-UuCli -Explicit $CliPath
if (-not $CliPath) {
  Write-Output "CLI_FOUND=False"
  Write-Output "HINT=Set `$env:UU_CLI_PATH, pass -CliPath, or install UU Remote (GameViewer)."
  Write-Output "READY=False"
  Write-Output "=== doctor complete ==="
  exit 1
}
Write-Output "CLI_FOUND=True"
Write-Output "CLI_PATH=$CliPath"
Write-Output "PYWINPTY=N/A (this script needs no Python)"

$version = Invoke-Uu -CmdArgs @('--version')
Write-Output "CLI_VERSION=$($version.Out)"

$echo = Invoke-UuJson -CmdArgs @('echo', 'doctor')
if ($echo.Data) {
  Write-Output "MAIN_APP=$($echo.Data.success)"
  Write-Output "CONNECTION=$($echo.Data.data.connection)"
} else {
  Write-Output "MAIN_APP=False"
  if ($echo.Err) { Write-Output "MAIN_APP_ERROR=$($echo.Err)" }
}

$status = Invoke-UuJson -CmdArgs @('status')
if ($status.Data -and $status.Data.success) {
  Write-Output "LOGGED_IN=$($status.Data.data.isLoggedIn)"
  Write-Output "SERVICE=$($status.Data.data.serviceStatus)"
  Write-Output "NETWORK=$($status.Data.data.networkStatus)"
  Write-Output "APP_VERSION=$($status.Data.data.appVersion)"
} else {
  Write-Output "LOGGED_IN=False"
  if ($status.Err) { Write-Output "STATUS_ERROR=$($status.Err)" }
}

if ($DeviceId) {
  $device = Invoke-UuJson -CmdArgs @('device', 'info', $DeviceId)
  Write-Output "DEVICE=$DeviceId"
  if ($device.Data -and $device.Data.success) {
    Write-Output "DEVICE_NAME=$($device.Data.data.deviceName)"
    Write-Output "DEVICE_ONLINE=$($device.Data.data.isOnline)"
    Write-Output "DEVICE_PLATFORM=$($device.Data.data.platform)"
    if (-not $device.Data.data.isOnline) {
      Write-Output "WARNING=Device offline: nothing happens on the remote. Do not pretend to operate."
    }
  } else {
    Write-Output "DEVICE_ONLINE=False"
    if ($device.Err) { Write-Output "DEVICE_INFO_ERROR=$($device.Err)" }
  }

  # Listing sessions needs the account password while the remote is locked, and
  # piped stdin is enough -- no PTY required.
  $term = Invoke-UuJson -CmdArgs @('term', '-d', $DeviceId, 'ls') -Stdin $Password
  if ($term.Data -and $term.Data.success) {
    $sessions = @($term.Data.data.items)
    Write-Output "SESSION_COUNT=$($sessions.Count)"
    foreach ($session in $sessions) {
      Write-Output "SESSION=$($session.name) STATE=$($session.state) SHELL=$($session.shell)"
    }
    if ($sessions.Count -gt 0) {
      Write-Output "WARNING=Lingering sessions found. Clean them up with: python uu-exec.py --kill-all"
    }
  } else {
    Write-Output "SESSION_CHECK=failed"
    $detail = "$($term.Err) $($term.Raw)"
    if ($detail -match '密码|password') {
      Write-Output "HINT=The remote screen is locked; pass -Password or let uu-exec.py remember it."
    }
    if ($detail -match '正在被远控|currently being remotely controlled|code\s*127\b') {
      Write-Output "HINT=The device is being remotely controlled."
    }
    if ($detail -match '版本过低') {
      Write-Output "HINT=Protocol incompatible; update the local UU client."
    }
    if ($detail -match '操作过于频繁|rate limit') {
      Write-Output "HINT=Rate limited; wait a few seconds and retry."
    }
  }
} else {
  $devices = Invoke-UuJson -CmdArgs @('device', 'list')
  if ($devices.Data -and $devices.Data.success) {
    $items = @($devices.Data.data.items)
    Write-Output "DEVICE_COUNT=$($items.Count)"
    foreach ($device in $items) {
      Write-Output "DEVICE=$($device.deviceId) NAME=$($device.deviceName) ONLINE=$($device.isOnline) PLATFORM=$($device.platform)"
    }
    Write-Output "HINT=Pass -DeviceId to also check sessions."
  } else {
    Write-Output "DEVICE_LIST_ERROR=True"
    if ($devices.Err) { Write-Output "DEVICE_LIST_DETAIL=$($devices.Err)" }
  }
}

$ready = ($echo.Data -and $echo.Data.success) -and ($status.Data -and $status.Data.data.isLoggedIn)
if ($DeviceId) {
  $ready = $ready -and ($device.Data -and $device.Data.success) -and ($device.Data.data.isOnline)
}
Write-Output "READY=$([bool]$ready)"
Write-Output "READY_SCOPE=local-preflight"
Write-Output "=== doctor complete ==="
if ($ready) { exit 0 } else { exit 1 }
