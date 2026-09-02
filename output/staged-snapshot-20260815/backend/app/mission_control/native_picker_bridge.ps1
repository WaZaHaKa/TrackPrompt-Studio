[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Folder", "File")]
    [string]$Kind,

    [string]$InitialDirectory = "",

    [ValidateLength(1, 120)]
    [string]$Title = "Choose a folder"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms

if (-not [string]::IsNullOrWhiteSpace($InitialDirectory)) {
    if (-not [IO.Path]::IsPathRooted($InitialDirectory)) { throw "Initial directory must be absolute." }
    $InitialDirectory = [IO.Path]::GetFullPath($InitialDirectory)
    if (-not (Test-Path -LiteralPath $InitialDirectory -PathType Container)) { throw "Initial directory does not exist." }
}

if ($Kind -eq "Folder") {
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = $Title
    $dialog.ShowNewFolderButton = $true
    if (-not [string]::IsNullOrWhiteSpace($InitialDirectory)) { $dialog.SelectedPath = $InitialDirectory }
}
else {
    $dialog = New-Object Windows.Forms.OpenFileDialog
    $dialog.Title = $Title
    $dialog.CheckFileExists = $true
    $dialog.CheckPathExists = $true
    $dialog.Multiselect = $false
    if (-not [string]::IsNullOrWhiteSpace($InitialDirectory)) { $dialog.InitialDirectory = $InitialDirectory }
}

try {
    $result = $dialog.ShowDialog()
    $selected = if ($Kind -eq "Folder") { [string]$dialog.SelectedPath } else { [string]$dialog.FileName }
    [pscustomobject][ordered]@{
        cancelled = ($result -ne [Windows.Forms.DialogResult]::OK)
        path = $(if ($result -eq [Windows.Forms.DialogResult]::OK) { [IO.Path]::GetFullPath($selected) } else { $null })
    } | ConvertTo-Json -Compress
}
finally {
    $dialog.Dispose()
}
