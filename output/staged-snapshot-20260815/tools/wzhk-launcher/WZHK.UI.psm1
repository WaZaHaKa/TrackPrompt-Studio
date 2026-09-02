Set-StrictMode -Version Latest

$script:FrameWidth = 100

function Initialize-WzhkConsole {
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    }
    catch {
        # Cosmetic only.
    }

    try {
        $Host.UI.RawUI.WindowTitle = "WZHK Media // TrackPrompt Mission Control"
    }
    catch {
        # Some hosts do not expose a mutable title.
    }

    try {
        $candidateWidth = [Math]::Max(86, [Math]::Min(118, [Console]::WindowWidth - 2))
        $script:FrameWidth = $candidateWidth
    }
    catch {
        $script:FrameWidth = 100
    }
}

function Write-WzhkLogo {
    Write-Host ""
    Write-Host "  ██╗    ██╗  ███████╗  ██╗  ██╗  ██╗  ██╗" -ForegroundColor Cyan
    Write-Host "  ██║    ██║  ╚══███╔╝  ██║  ██║  ██║ ██╔╝" -ForegroundColor Cyan
    Write-Host "  ██║ █╗ ██║    ███╔╝   ███████║  █████╔╝ " -ForegroundColor Magenta
    Write-Host "  ██║███╗██║   ███╔╝    ██╔══██║  ██╔═██╗ " -ForegroundColor Magenta
    Write-Host "  ╚███╔███╔╝  ███████╗  ██║  ██║  ██║  ██╗" -ForegroundColor Cyan
    Write-Host "   ╚══╝╚══╝   ╚══════╝  ╚═╝  ╚═╝  ╚═╝  ╚═╝" -ForegroundColor Cyan
    Write-Host "                         M  E  D  I  A" -ForegroundColor White
    Write-Host ""
}

function Get-WzhkFittedText {
    param(
        [AllowNull()][object]$Text,
        [int]$Width
    )

    $value = if ($null -eq $Text) { "" } else { [string]$Text }
    if ($value.Length -gt $Width) {
        if ($Width -le 1) {
            return $value.Substring(0, $Width)
        }
        return $value.Substring(0, $Width - 1) + "…"
    }
    return $value.PadRight($Width)
}

function Split-WzhkFrameText {
    param(
        [AllowNull()][object]$Text,
        [int]$Width
    )

    $value = if ($null -eq $Text) { "" } else { [string]$Text }
    if ($value.Length -eq 0) {
        return @("")
    }

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($rawLine in ($value -split "`r?`n")) {
        $remaining = [string]$rawLine
        if ($remaining.Length -eq 0) {
            $lines.Add("")
            continue
        }

        while ($remaining.Length -gt $Width) {
            $breakAt = $Width
            $candidate = $remaining.Substring(0, $Width)
            $lastSpace = $candidate.LastIndexOf(" ")
            if ($lastSpace -ge [Math]::Floor($Width * 0.55)) {
                $breakAt = $lastSpace
            }

            $lines.Add($remaining.Substring(0, $breakAt).TrimEnd())
            $remaining = $remaining.Substring($breakAt).TrimStart()
        }
        $lines.Add($remaining)
    }

    return $lines.ToArray()
}

function Write-WzhkFrameTop {
    param([string]$Title = "")

    $inner = $script:FrameWidth - 2
    if ([string]::IsNullOrWhiteSpace($Title)) {
        Write-Host ("╔" + ("═" * $inner) + "╗") -ForegroundColor DarkCyan
        return
    }

    $label = " " + $Title + " "
    if ($label.Length -gt ($inner - 2)) {
        $label = $label.Substring(0, $inner - 2)
    }

    $left = 2
    $right = $inner - $left - $label.Length
    Write-Host ("╔" + ("═" * $left) + $label + ("═" * $right) + "╗") -ForegroundColor DarkCyan
}

function Write-WzhkFrameDivider {
    $inner = $script:FrameWidth - 2
    Write-Host ("╠" + ("═" * $inner) + "╣") -ForegroundColor DarkCyan
}

function Write-WzhkFrameBottom {
    $inner = $script:FrameWidth - 2
    Write-Host ("╚" + ("═" * $inner) + "╝") -ForegroundColor DarkCyan
}

function Write-WzhkFrameLine {
    param(
        [AllowNull()][object]$Text = "",
        [ConsoleColor]$Color = [ConsoleColor]::White,
        [switch]$Selected
    )

    $inner = $script:FrameWidth - 2
    $chunks = @(Split-WzhkFrameText -Text $Text -Width $inner)

    foreach ($chunk in $chunks) {
        Write-Host "║" -NoNewline -ForegroundColor DarkCyan
        $fitted = Get-WzhkFittedText -Text $chunk -Width $inner

        if ($Selected) {
            Write-Host $fitted -NoNewline -ForegroundColor Black -BackgroundColor Cyan
        }
        else {
            Write-Host $fitted -NoNewline -ForegroundColor $Color
        }

        Write-Host "║" -ForegroundColor DarkCyan
    }
}

function Write-WzhkScreenHeader {
    param([string]$Subtitle)

    Clear-Host
    Write-WzhkLogo
    Write-WzhkFrameTop -Title $Subtitle
}

function Get-WzhkNextEnabledIndex {
    param(
        [object[]]$Items,
        [int]$Current,
        [int]$Direction
    )

    if ($Items.Count -eq 0) {
        return 0
    }

    $index = $Current
    for ($attempt = 0; $attempt -lt $Items.Count; $attempt += 1) {
        $index = ($index + $Direction + $Items.Count) % $Items.Count
        if ([bool]$Items[$index].Enabled) {
            return $index
        }
    }

    return $Current
}

function Get-WzhkMenuPage {
    param(
        [Parameter(Mandatory = $true)][object[]]$Items,
        [int]$SelectedIndex = 0,
        [ValidateRange(1, 9)][int]$PageSize = 9
    )

    if ($Items.Count -eq 0) {
        return [pscustomobject]@{
            PageNumber = 0
            PageCount = 0
            StartIndex = 0
            SelectedIndex = 0
            SelectedOffset = 0
            Items = @()
        }
    }

    $selected = [Math]::Max(0, [Math]::Min($Items.Count - 1, $SelectedIndex))
    $pageIndex = [int][Math]::Floor($selected / [double]$PageSize)
    $pageCount = [int][Math]::Ceiling($Items.Count / [double]$PageSize)
    $start = $pageIndex * $PageSize
    return [pscustomobject]@{
        PageNumber = $pageIndex + 1
        PageCount = $pageCount
        StartIndex = $start
        SelectedIndex = $selected
        SelectedOffset = $selected - $start
        Items = @($Items | Select-Object -Skip $start -First $PageSize)
    }
}

function Get-WzhkMenuDigitIndex {
    param(
        [Parameter(Mandatory = $true)][object[]]$Items,
        [int]$SelectedIndex,
        [ValidateRange(1, 9)][int]$Digit,
        [ValidateRange(1, 9)][int]$PageSize = 9
    )

    if ($Items.Count -eq 0) { return -1 }
    $page = Get-WzhkMenuPage -Items $Items -SelectedIndex $SelectedIndex -PageSize $PageSize
    $candidate = $page.StartIndex + $Digit - 1
    if ($candidate -lt 0 -or $candidate -ge $Items.Count) { return -1 }
    return $candidate
}

function Show-WzhkKeypadMenu {
    param(
        [string]$Title,
        [object[]]$Items,
        [string[]]$Context = @(),
        [string]$Hint = "NUMPAD 1–9 selects  •  ↑/↓ navigates  •  ENTER confirms  •  ESC returns"
    )

    if ($Items.Count -eq 0) {
        return $null
    }

    $selected = 0
    if (-not [bool]$Items[$selected].Enabled) {
        $selected = Get-WzhkNextEnabledIndex -Items $Items -Current $selected -Direction 1
    }

    while ($true) {
        $page = Get-WzhkMenuPage -Items $Items -SelectedIndex $selected
        $visibleItems = @($page.Items)
        Write-WzhkScreenHeader -Subtitle $Title

        foreach ($line in $Context) {
            Write-WzhkFrameLine -Text ("  " + $line) -Color DarkGray
        }

        if ($Context.Count -gt 0) {
            Write-WzhkFrameDivider
        }

        for ($offset = 0; $offset -lt $visibleItems.Count; $offset += 1) {
            $index = $page.StartIndex + $offset
            $item = $visibleItems[$offset]
            $number = $offset + 1
            $isSelected = ($index -eq $selected)
            $pointer = if ($isSelected) { "▶" } else { " " }
            $label = [string]::Format("  {0} [{1}] {2}", $pointer, $number, [string]$item.Label)

            if (-not [bool]$item.Enabled) {
                Write-WzhkFrameLine -Text $label -Color DarkGray
            }
            elseif ($isSelected) {
                Write-WzhkFrameLine -Text $label -Selected
            }
            else {
                Write-WzhkFrameLine -Text $label -Color White
            }

            if (-not [string]::IsNullOrWhiteSpace([string]$item.Description)) {
                $description = "       " + [string]$item.Description
                if ([bool]$item.Enabled) {
                    Write-WzhkFrameLine -Text $description -Color DarkCyan
                }
                else {
                    Write-WzhkFrameLine -Text $description -Color DarkGray
                }
            }

            Write-WzhkFrameLine -Text ""
        }

        Write-WzhkFrameDivider
        if ($page.PageCount -gt 1) {
            Write-WzhkFrameLine -Text ([string]::Format("  PAGE {0}/{1}  •  PAGE UP/DOWN or ←/→ changes page", $page.PageNumber, $page.PageCount)) -Color DarkCyan
        }
        Write-WzhkFrameLine -Text ("  " + $Hint) -Color DarkGray
        Write-WzhkFrameBottom

        $key = [Console]::ReadKey($true)

        if ($key.Key -eq [ConsoleKey]::Escape -or $key.Key -eq [ConsoleKey]::Q) {
            return $null
        }
        elseif ($key.Key -eq [ConsoleKey]::UpArrow) {
            $selected = Get-WzhkNextEnabledIndex -Items $Items -Current $selected -Direction -1
        }
        elseif ($key.Key -eq [ConsoleKey]::DownArrow) {
            $selected = Get-WzhkNextEnabledIndex -Items $Items -Current $selected -Direction 1
        }
        elseif ($key.Key -in @([ConsoleKey]::RightArrow, [ConsoleKey]::PageDown)) {
            $target = [Math]::Min($Items.Count - 1, $selected + 9)
            if ([bool]$Items[$target].Enabled) { $selected = $target }
            else { $selected = Get-WzhkNextEnabledIndex -Items $Items -Current $target -Direction 1 }
        }
        elseif ($key.Key -in @([ConsoleKey]::LeftArrow, [ConsoleKey]::PageUp)) {
            $target = [Math]::Max(0, $selected - 9)
            if ([bool]$Items[$target].Enabled) { $selected = $target }
            else { $selected = Get-WzhkNextEnabledIndex -Items $Items -Current $target -Direction -1 }
        }
        elseif ($key.Key -eq [ConsoleKey]::Enter) {
            if ([bool]$Items[$selected].Enabled) {
                return $Items[$selected]
            }
            [Console]::Beep(520, 90)
        }
        elseif ([char]::IsDigit($key.KeyChar)) {
            $digit = [int][string]$key.KeyChar
            if ($digit -lt 1 -or $digit -gt 9) {
                [Console]::Beep(420, 90)
                continue
            }
            $candidate = Get-WzhkMenuDigitIndex -Items $Items -SelectedIndex $selected -Digit $digit

            if ($candidate -ge 0 -and $candidate -lt $Items.Count) {
                if ([bool]$Items[$candidate].Enabled) {
                    $selected = $candidate
                }
                else {
                    [Console]::Beep(420, 90)
                }
            }
        }
    }
}

function Get-WzhkObjectValue {
    param(
        [AllowNull()][object]$InputObject,
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowNull()][object]$Default = $null
    )

    $current = $InputObject
    foreach ($segment in @($Path -split "\.")) {
        if ($null -eq $current) {
            return $Default
        }

        if ($current -is [System.Collections.IDictionary]) {
            if (-not $current.Contains($segment)) {
                return $Default
            }
            $current = $current[$segment]
            continue
        }

        $property = $current.PSObject.Properties[$segment]
        if ($null -eq $property) {
            return $Default
        }
        $current = $property.Value
    }

    if ($null -eq $current) {
        return $Default
    }
    return $current
}

function Get-WzhkFirstObjectValue {
    param(
        [AllowNull()][object]$InputObject,
        [Parameter(Mandatory = $true)][string[]]$Paths,
        [AllowNull()][object]$Default = $null
    )

    $missing = New-Object object
    foreach ($path in $Paths) {
        $value = Get-WzhkObjectValue -InputObject $InputObject -Path $path -Default $missing
        if (-not [object]::ReferenceEquals($value, $missing)) {
            return $value
        }
    }
    return $Default
}

function Get-WzhkResolutionLabel {
    param(
        [int]$Width,
        [int]$Height
    )

    $dimensions = [string]::Format("{0}×{1}", $Width, $Height)
    if ($Width -eq 3840 -and $Height -eq 2160) {
        return "NATIVE 4K — $dimensions"
    }
    if ($Width -eq 2560 -and $Height -eq 1440) {
        return "1440P — $dimensions"
    }
    if ($Width -eq 1920 -and $Height -eq 1080) {
        return "FULL HD — $dimensions"
    }
    if ($Width -eq 4096 -and $Height -eq 2160) {
        return "DCI 4K — $dimensions"
    }
    return $dimensions
}

function Get-WzhkAspectRatioLabel {
    param(
        [int]$Width,
        [int]$Height
    )

    if ($Width -le 0 -or $Height -le 0) {
        return "unknown"
    }

    $left = [Math]::Abs($Width)
    $right = [Math]::Abs($Height)
    while ($right -ne 0) {
        $remainder = $left % $right
        $left = $right
        $right = $remainder
    }
    return [string]::Format("{0}:{1}", [int]($Width / $left), [int]($Height / $left))
}

function Get-WzhkShortHash {
    param(
        [AllowEmptyString()][string]$Hash = "",
        [int]$Length = 12
    )

    $normalized = $Hash.Trim().ToLowerInvariant()
    if ($normalized -notmatch "^[0-9a-f]+$") {
        return $Hash.Trim()
    }
    if ($normalized.Length -le $Length) {
        return $normalized
    }
    return $normalized.Substring(0, $Length)
}

function Get-WzhkStatusColor {
    param([AllowEmptyString()][string]$Status = "UNKNOWN")

    $normalized = $Status.Trim().ToUpperInvariant()
    switch -Regex ($normalized) {
        "^(READY|VALID|SAVED|AUTHORIZED|COMPATIBLE|COMPLETE|COMPLETED|ONLINE)$" {
            return [ConsoleColor]::Green
        }
        "^(INVALID|BLOCKED|ERROR|FAILED|MISMATCH|INCOMPATIBLE|REJECTED|EXPIRED)$" {
            return [ConsoleColor]::Red
        }
        "^(DRAFT|PENDING|WARNING|UNAUTHORIZED|AUTHORIZATION REQUIRED|REVIEW REQUIRED|CHANGED|RENDERING)$" {
            return [ConsoleColor]::Yellow
        }
        default {
            return [ConsoleColor]::Cyan
        }
    }
}

function Write-WzhkStatusLabel {
    param(
        [string]$Label = "STATUS",
        [AllowEmptyString()][string]$Status = "UNKNOWN"
    )

    $displayStatus = if ([string]::IsNullOrWhiteSpace($Status)) { "UNKNOWN" } else { $Status.Trim().ToUpperInvariant() }
    $color = Get-WzhkStatusColor -Status $displayStatus
    Write-WzhkFrameLine -Text ([string]::Format("  {0,-18}: [{1}]", $Label.ToUpperInvariant(), $displayStatus)) -Color $color
}

function Write-WzhkArtifactTypeLabel {
    param(
        [ValidateSet("TEMPLATE", "RESOLVED SETTINGS", "SAVED PROFILE", "AUTHORIZED PROFILE", "ACTIVE RENDER")]
        [string]$Type
    )

    $color = switch ($Type) {
        "TEMPLATE" { [ConsoleColor]::DarkCyan }
        "RESOLVED SETTINGS" { [ConsoleColor]::Cyan }
        "SAVED PROFILE" { [ConsoleColor]::Magenta }
        "AUTHORIZED PROFILE" { [ConsoleColor]::Green }
        "ACTIVE RENDER" { [ConsoleColor]::Yellow }
    }
    Write-WzhkFrameLine -Text ([string]::Format("  ARTIFACT TYPE     : [{0}]", $Type)) -Color $color
}

function Get-WzhkMissionControlMenuItems {
    param(
        [bool]$ProfilesAvailable = $true,
        [bool]$DashboardAvailable = $true,
        [bool]$OutputAvailable = $true
    )

    return @(
        [pscustomobject]@{
            Label = "CALIBRATE THIS PC"
            Description = "Measure bounded production-path candidates and generate machine-specific saved profiles."
            Enabled = $ProfilesAvailable
            Value = "Calibrate"
        },
        [pscustomobject]@{
            Label = "CREATE / EDIT PROFILE"
            Description = "Create a normalized profile or manage an existing exact saved profile."
            Enabled = $true
            Value = "Profiles"
        },
        [pscustomobject]@{
            Label = "GENERATE 720P HYPER PROFILE"
            Description = "Finalize measured, reviewed native 1280x720 calibration evidence."
            Enabled = $ProfilesAvailable
            Value = "Generate720"
        },
        [pscustomobject]@{
            Label = "GENERATE RECOMMENDED PROFILE"
            Description = "Finalize the fastest measured candidate that passed the hard visual gates."
            Enabled = $ProfilesAvailable
            Value = "GenerateRecommended"
        },
        [pscustomobject]@{
            Label = "LOCAL RENDER"
            Description = "Preflight, dry-run, start/resume, monitor, or safely stop a local render."
            Enabled = $ProfilesAvailable
            Value = "LocalRender"
        },
        [pscustomobject]@{
            Label = "NVIDIA BREV CLOUD RENDER"
            Description = "Inspect readiness and prepare an audio-free full-VM cloud plan; never NVIDIA NIM."
            Enabled = $true
            Value = "BrevCloud"
        },
        [pscustomobject]@{
            Label = "CLOUD BENCHMARK TOURNAMENT"
            Description = "Prepare a bounded one-worker GPU comparison and exact budget-bound token."
            Enabled = $true
            Value = "CloudBenchmark"
        },
        [pscustomobject]@{
            Label = "CLOUD JOB STATUS SNAPSHOT"
            Description = "Open an existing scheduler once for an offline snapshot; no provider telemetry or live fleet monitoring."
            Enabled = $true
            Value = "CloudFleet"
        },
        [pscustomobject]@{
            Label = "IMPORT / VERIFY CLOUD OUTPUT"
            Description = "Quarantine and verify returned frames before atomic publication."
            Enabled = $ProfilesAvailable
            Value = "CloudImport"
        },
        [pscustomobject]@{
            Label = "ENCODE / MUX FINAL VIDEO"
            Description = "Plan or run local video encoding and private-audio mux after sequence verification."
            Enabled = $OutputAvailable
            Value = "EncodeMux"
        },
        [pscustomobject]@{
            Label = "LOCAL OPERATIONS / SAFETY"
            Description = "Performance mode, stop markers, dashboards, output viewer, and scene package selection."
            Enabled = $true
            Value = "LocalOperations"
        },
        [pscustomobject]@{
            Label = "OUTSOURCE / REMOTE RENDER"
            Description = "Use provider-neutral package, static assignment, estimate, and import tools."
            Enabled = $ProfilesAvailable
            Value = "Outsource"
        },
        [pscustomobject]@{
            Label = "EXIT MISSION CONTROL"
            Description = "Close the wrapper without changing profiles, authorization, or output."
            Enabled = $true
            Value = "Exit"
        }
    )
}

function Show-WzhkMissionControlMenu {
    param(
        [string[]]$Context = @(),
        [bool]$ProfilesAvailable = $true,
        [bool]$DashboardAvailable = $true,
        [bool]$OutputAvailable = $true
    )

    $items = @(Get-WzhkMissionControlMenuItems `
        -ProfilesAvailable $ProfilesAvailable `
        -DashboardAvailable $DashboardAvailable `
        -OutputAvailable $OutputAvailable)

    return Show-WzhkKeypadMenu `
        -Title "WZHK MEDIA // TRACKPROMPT MISSION CONTROL" `
        -Items $items `
        -Context $Context `
        -Hint "1–9 changes selection  •  ↑/↓ navigates  •  ENTER confirms  •  ESC returns"
}

function Read-WzhkConsoleLine {
    param(
        [string]$Prompt,
        [string]$Default = "",
        [int]$MaximumLength = 240,
        [switch]$AllowCancel
    )

    Write-WzhkFrameDivider
    Write-WzhkFrameLine -Text ("  " + $Prompt) -Color White
    if (-not [string]::IsNullOrEmpty($Default)) {
        Write-WzhkFrameLine -Text ("  Default: " + $Default) -Color DarkCyan
    }
    if ($AllowCancel) {
        Write-WzhkFrameLine -Text "  Type a value and press ENTER; ESC cancels." -Color DarkGray
    }
    else {
        Write-WzhkFrameLine -Text "  Type a value and press ENTER." -Color DarkGray
    }
    Write-WzhkFrameBottom

    Write-Host "  > " -NoNewline -ForegroundColor Cyan
    $builder = New-Object System.Text.StringBuilder

    while ($true) {
        $key = [Console]::ReadKey($true)
        if ($key.Key -eq [ConsoleKey]::Enter) {
            Write-Host ""
            $value = $builder.ToString()
            if ([string]::IsNullOrEmpty($value)) {
                return $Default
            }
            return $value
        }
        if ($key.Key -eq [ConsoleKey]::Escape -and $AllowCancel) {
            Write-Host ""
            return $null
        }
        if ($key.Key -eq [ConsoleKey]::Backspace) {
            if ($builder.Length -gt 0) {
                $null = $builder.Remove($builder.Length - 1, 1)
                Write-Host "`b `b" -NoNewline
            }
            continue
        }

        if (-not [char]::IsControl($key.KeyChar) -and $builder.Length -lt $MaximumLength) {
            $null = $builder.Append($key.KeyChar)
            Write-Host $key.KeyChar -NoNewline
        }
    }
}

function Read-WzhkTextInput {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [string]$Default = "",
        [int]$MinimumLength = 0,
        [int]$MaximumLength = 120,
        [string]$Pattern = "",
        [string]$ValidationMessage = "Enter a valid value.",
        [switch]$Required,
        [switch]$AllowCancel,
        [switch]$PreserveWhitespace
    )

    if ($MinimumLength -lt 0 -or $MaximumLength -lt 1 -or $MinimumLength -gt $MaximumLength) {
        throw "Invalid text input length bounds."
    }

    while ($true) {
        $raw = Read-WzhkConsoleLine `
            -Prompt $Prompt `
            -Default $Default `
            -MaximumLength $MaximumLength `
            -AllowCancel:$AllowCancel

        if ($null -eq $raw) {
            return $null
        }

        $value = [string]$raw
        if (-not $PreserveWhitespace) {
            $value = $value.Trim()
        }

        $valid = $true
        if ($Required -and [string]::IsNullOrWhiteSpace($value)) {
            $valid = $false
        }
        elseif ($value.Length -lt $MinimumLength -or $value.Length -gt $MaximumLength) {
            $valid = $false
        }
        elseif (-not [string]::IsNullOrEmpty($Pattern) -and -not [regex]::IsMatch($value, $Pattern)) {
            $valid = $false
        }

        if ($valid) {
            return $value
        }

        Write-Host ("  INPUT REJECTED: " + $ValidationMessage) -ForegroundColor Red
    }
}

function Read-WzhkIntegerInput {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [int]$Default = 0,
        [int]$Minimum = [int]::MinValue,
        [int]$Maximum = [int]::MaxValue,
        [switch]$AllowCancel
    )

    if ($Minimum -gt $Maximum -or $Default -lt $Minimum -or $Default -gt $Maximum) {
        throw "Invalid integer input bounds or default."
    }

    while ($true) {
        $text = Read-WzhkTextInput `
            -Prompt $Prompt `
            -Default ([string]$Default) `
            -MaximumLength 20 `
            -Required `
            -AllowCancel:$AllowCancel `
            -ValidationMessage ([string]::Format("Enter a whole number from {0} through {1}.", $Minimum, $Maximum))

        if ($null -eq $text) {
            return $null
        }

        [int]$parsed = 0
        if ([int]::TryParse([string]$text, [ref]$parsed) -and $parsed -ge $Minimum -and $parsed -le $Maximum) {
            return $parsed
        }
        Write-Host ([string]::Format("  INPUT REJECTED: enter a whole number from {0} through {1}.", $Minimum, $Maximum)) -ForegroundColor Red
    }
}

function Read-WzhkDecimalInput {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [double]$Default = 0.0,
        [double]$Minimum = [double]::MinValue,
        [double]$Maximum = [double]::MaxValue,
        [switch]$AllowCancel
    )

    if ([double]::IsNaN($Default) -or [double]::IsInfinity($Default) -or
        [double]::IsNaN($Minimum) -or [double]::IsInfinity($Minimum) -or
        [double]::IsNaN($Maximum) -or [double]::IsInfinity($Maximum) -or
        $Minimum -gt $Maximum -or $Default -lt $Minimum -or $Default -gt $Maximum) {
        throw "Invalid decimal input bounds or default."
    }

    $culture = [Globalization.CultureInfo]::InvariantCulture
    $style = [Globalization.NumberStyles]::Float
    $defaultText = $Default.ToString("0.################", $culture)
    while ($true) {
        $text = Read-WzhkTextInput `
            -Prompt ($Prompt + "  (use '.' as the decimal separator)") `
            -Default $defaultText `
            -MaximumLength 32 `
            -Required `
            -AllowCancel:$AllowCancel `
            -ValidationMessage ([string]::Format("Enter a number from {0} through {1}.", $Minimum, $Maximum))

        if ($null -eq $text) {
            return $null
        }

        [double]$parsed = 0.0
        if ([double]::TryParse([string]$text, $style, $culture, [ref]$parsed) -and
            -not [double]::IsNaN($parsed) -and -not [double]::IsInfinity($parsed) -and
            $parsed -ge $Minimum -and $parsed -le $Maximum) {
            return $parsed
        }
        Write-Host ([string]::Format("  INPUT REJECTED: enter a number from {0} through {1}.", $Minimum, $Maximum)) -ForegroundColor Red
    }
}

function Read-WzhkChoice {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][object[]]$Items,
        [string[]]$Context = @(),
        [switch]$ReturnItem
    )

    $selection = Show-WzhkKeypadMenu -Title $Title -Items $Items -Context $Context
    if ($null -eq $selection) {
        return $null
    }
    if ($ReturnItem) {
        return $selection
    }

    $valueProperty = $selection.PSObject.Properties["Value"]
    if ($null -eq $valueProperty) {
        return $selection
    }
    return $valueProperty.Value
}

function Get-WzhkBuilderNavigationItems {
    param(
        [bool]$CanGoBack = $true,
        [bool]$CanGoNext = $true,
        [bool]$CanEdit = $true,
        [bool]$CanResetScene = $false
    )

    return @(
        [pscustomobject]@{ Label = "BACK"; Description = "Return to the previous settings stage."; Enabled = $CanGoBack; Value = "Back" },
        [pscustomobject]@{ Label = "NEXT"; Description = "Accept this stage and continue."; Enabled = $CanGoNext; Value = "Next" },
        [pscustomobject]@{ Label = "EDIT CURRENT SETTINGS"; Description = "Enter or revise this stage's values."; Enabled = $CanEdit; Value = "Edit" },
        [pscustomobject]@{ Label = "USE RECOMMENDED"; Description = "Restore explicit recommended values for this stage."; Enabled = $true; Value = "Recommended" },
        [pscustomobject]@{ Label = "RESET TO SCENE DEFAULTS"; Description = "Restore scene-derived timeline, FPS, resolution, and approved scene values."; Enabled = $CanResetScene; Value = "ResetScene" },
        [pscustomobject]@{ Label = "CANCEL PROFILE BUILDER"; Description = "Return without saving partial profile changes."; Enabled = $true; Value = "Cancel" }
    )
}

function Read-WzhkBuilderNavigation {
    param(
        [Parameter(Mandatory = $true)][string]$StageTitle,
        [int]$StageNumber = 1,
        [int]$StageCount = 13,
        [bool]$CanGoBack = $true,
        [bool]$CanGoNext = $true,
        [bool]$CanEdit = $true,
        [bool]$CanResetScene = $false
    )

    $items = @(Get-WzhkBuilderNavigationItems `
        -CanGoBack $CanGoBack `
        -CanGoNext $CanGoNext `
        -CanEdit $CanEdit `
        -CanResetScene $CanResetScene)

    $title = [string]::Format("PROFILE BUILDER // STEP {0} OF {1} // {2}", $StageNumber, $StageCount, $StageTitle.ToUpperInvariant())
    return Read-WzhkChoice -Title $title -Items $items -Context @(
        "Digits change selection; ENTER confirms.",
        "No settings are written until the final profile save."
    )
}

function ConvertTo-WzhkFieldLines {
    param([object[]]$Fields = @())

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($field in $Fields) {
        $label = [string](Get-WzhkFirstObjectValue -InputObject $field -Paths @("Label", "Name") -Default "SETTING")
        $value = [string](Get-WzhkObjectValue -InputObject $field -Path "Value" -Default "—")
        $lines.Add([string]::Format("{0,-20}: {1}", $label.ToUpperInvariant(), $value))
    }
    return $lines.ToArray()
}

function Show-WzhkBuilderStage {
    param(
        [Parameter(Mandatory = $true)][string]$StageTitle,
        [int]$StageNumber = 1,
        [int]$StageCount = 13,
        [object[]]$Fields = @(),
        [string[]]$Guidance = @(),
        [string]$Status = "DRAFT"
    )

    $title = [string]::Format("PROFILE BUILDER // STEP {0} OF {1} // {2}", $StageNumber, $StageCount, $StageTitle.ToUpperInvariant())
    Write-WzhkScreenHeader -Subtitle $title
    Write-WzhkStatusLabel -Label "PROFILE STATUS" -Status $Status
    foreach ($line in @(ConvertTo-WzhkFieldLines -Fields $Fields)) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }
    if ($Guidance.Count -gt 0) {
        Write-WzhkFrameDivider
        foreach ($line in $Guidance) {
            Write-WzhkFrameLine -Text ("  " + $line) -Color DarkCyan
        }
    }
}

function Get-WzhkTemplateMenuItems {
    param([Parameter(Mandatory = $true)][object[]]$Templates)

    $items = New-Object System.Collections.Generic.List[object]
    foreach ($template in @($Templates | Select-Object -First 9)) {
        $name = [string](Get-WzhkFirstObjectValue -InputObject $template -Paths @("displayName", "DisplayName", "name", "Name") -Default "CUSTOM")
        $width = [int](Get-WzhkFirstObjectValue -InputObject $template -Paths @("resolution.width", "Width") -Default 0)
        $height = [int](Get-WzhkFirstObjectValue -InputObject $template -Paths @("resolution.height", "Height") -Default 0)
        $samples = Get-WzhkFirstObjectValue -InputObject $template -Paths @("render.samples", "renderSettings.samples", "Samples") -Default "explicit custom"
        $description = if ($width -gt 0 -and $height -gt 0) {
            [string]::Format("{0}  •  {1} samples", (Get-WzhkResolutionLabel -Width $width -Height $height), $samples)
        }
        else {
            "All values selected explicitly in the builder."
        }
        $items.Add([pscustomobject]@{
            Label = $name.ToUpperInvariant()
            Description = $description
            Enabled = $true
            Value = $template
        })
    }
    return $items.ToArray()
}

function Show-WzhkTemplateMenu {
    param([Parameter(Mandatory = $true)][object[]]$Templates)

    $items = @(Get-WzhkTemplateMenuItems -Templates $Templates)
    return Read-WzhkChoice `
        -Title "PROFILE BUILDER // SELECT TEMPLATE" `
        -Items $items `
        -Context @(
            "Every template resolves to explicit profile settings.",
            "CUSTOM opens the same staged builder without hidden defaults."
        )
}

function Get-WzhkProfileSummaryLines {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [string]$AuthorizationStatus = "",
        [string]$SavedFileSha256 = ""
    )

    $name = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("displayName", "name") -Default "Unnamed profile"
    $description = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("description") -Default ""
    $id = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("profileId", "id") -Default "unassigned"
    $schema = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("schemaVersion", "schema") -Default "unknown"
    $status = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("validation.status", "status") -Default "DRAFT"
    $template = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("templateId", "template") -Default "CUSTOM"
    $preset = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("preset", "project.preset") -Default "unknown"
    $scenePath = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("approvedScene.path", "approvedScenePath", "scene.path", "scenePath") -Default "not selected"
    $sceneHash = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("approvedScene.sha256", "approvedSceneSha256", "scene.sha256", "sceneSha256") -Default "unknown"
    $manifestPath = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("approvedScene.manifestPath", "sceneManifestPath", "scene.manifest.path", "manifest.path", "manifestPath") -Default "unknown"
    $width = [int](Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("resolution.width", "width") -Default 0)
    $height = [int](Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("resolution.height", "height") -Default 0)
    $percentage = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("resolution.percentage") -Default 100
    $pixelAspectX = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("resolution.pixelAspectX") -Default 1
    $pixelAspectY = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("resolution.pixelAspectY") -Default 1
    $aspect = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("resolution.displayAspect", "aspect.display", "resolution.aspectRatio", "resolution.aspect", "aspectRatio") -Default ""
    $fps = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("timeline.fps", "fps") -Default "unknown"
    $frameStart = [int](Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("timeline.frameStart", "frameStart") -Default 0)
    $frameEnd = [int](Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("timeline.frameEnd", "frameEnd") -Default 0)
    $quality = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.qualityMode", "quality.mode", "qualityMode") -Default "custom"
    $engine = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.engine", "renderSettings.engine", "engine") -Default "unknown"
    $samples = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.samples", "renderSettings.samples", "samples") -Default "unknown"
    $shadowPool = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.shadowPoolSize") -Default "unknown"
    $shadowRays = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.shadowRayCount") -Default "unknown"
    $shadowScale = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.shadowResolutionScale") -Default "unknown"
    $rayTracing = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.rayTracing") -Default $false
    $rayMethod = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.rayTracingMethod") -Default "unknown"
    $volumetricTile = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.volumetricTileSize") -Default "unknown"
    $volumetricSamples = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.volumetricSamples") -Default "unknown"
    $volumetricShadowSamples = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.volumetricShadowSamples") -Default "unknown"
    $volumetricDepth = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.volumetricRayDepth") -Default "unknown"
    $volumetricShadows = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.volumetricShadows") -Default $false
    $motionBlur = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.motionBlur", "motionBlur") -Default $false
    $transparentFilm = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.filmTransparent", "render.transparentFilm") -Default $false
    $dither = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("render.ditherIntensity") -Default "unknown"
    $sequence = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("imageSequence.format", "output.format", "format") -Default "unknown"
    $depth = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("imageSequence.bitDepth", "imageSequence.colorDepth", "output.colorDepth") -Default "unknown"
    $colorMode = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("imageSequence.colorMode") -Default "unknown"
    $compression = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("imageSequence.compression") -Default "unknown"
    $filenamePattern = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("imageSequence.filenamePattern") -Default "unknown"
    $color = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("colorManagement.viewTransform", "color.viewTransform") -Default "unknown"
    $look = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("colorManagement.look", "color.look") -Default "none"
    $displayDevice = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("colorManagement.displayDevice") -Default "unknown"
    $exposure = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("colorManagement.exposure") -Default "unknown"
    $gamma = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("colorManagement.gamma") -Default "unknown"
    $sequencerSpace = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("colorManagement.sequencerColorSpace") -Default "unknown"
    $compositor = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("compositor.enabled", "render.compositorEnabled") -Default $false
    $fogGlow = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("compositor.fogGlow", "render.fogGlow") -Default $false
    $fogQuality = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("compositor.fogGlowQuality") -Default "unknown"
    $fogThreshold = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("compositor.fogGlowThreshold") -Default "unknown"
    $fogStrength = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("compositor.fogGlowStrength") -Default "unknown"
    $fogSize = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("compositor.fogGlowSize") -Default "unknown"
    $fogIterations = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("compositor.fogGlowIterations") -Default "unknown"
    $chunk = [int](Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("chunking.framesPerChunk", "production.framesPerChunk", "production.chunkSize", "resume.chunkSize", "chunkSize") -Default 0)
    $output = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("output.rootDirectory", "production.outputDirectory", "output.directory", "outputPath") -Default "select at render time"
    $outputPolicy = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("output.policy") -Default "create-new"
    $directoryPattern = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("output.directoryPattern") -Default "unknown"
    $framesSubdirectory = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("output.framesSubdirectory") -Default "frames"
    $masterEnabled = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.master.enabled", "encoding.masterEnabled") -Default $false
    $masterCodec = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.master.videoCodec", "encoding.masterCodec") -Default "disabled"
    $masterPixelFormat = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.master.pixelFormat") -Default "disabled"
    $masterAudioCodec = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.master.audioCodec") -Default "disabled"
    $deliveryEnabled = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.enabled", "encoding.deliveryEnabled") -Default $false
    $deliveryCodec = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.videoCodec", "encoding.deliveryCodec") -Default "disabled"
    $deliveryProfile = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.profile") -Default "unknown"
    $deliveryCrf = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.crf") -Default "unknown"
    $deliveryPreset = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.preset") -Default "unknown"
    $deliveryPixelFormat = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.pixelFormat") -Default "unknown"
    $deliveryAudioBitrate = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.audioBitrate") -Default "unknown"
    $deliveryFastStart = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.fastStart") -Default $false
    $deliveryRec709 = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.requireRec709Metadata") -Default $false
    $audioCodec = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("encoding.delivery.audioCodec", "encoding.master.audioCodec", "encoding.audioCodec") -Default "disabled"
    $durationValue = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("timeline.durationSeconds", "estimates.durationSeconds") -Default $null
    $estimateDuration = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("estimates.duration", "estimates.renderTime", "estimates.estimatedDuration") -Default $null
    if ($null -eq $estimateDuration -and $null -ne $durationValue) { $estimateDuration = [TimeSpan]::FromSeconds([double]$durationValue).ToString() }
    if ($null -eq $estimateDuration) { $estimateDuration = "pending preflight" }
    $sequenceGiB = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("storage.plannedFrameSequenceGiB", "estimates.plannedFrameSequenceGiB") -Default $null
    $estimateSequence = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("estimates.frameSequenceSize", "estimates.estimatedFrameSequenceSize") -Default $null
    if ($null -eq $estimateSequence -and $null -ne $sequenceGiB) { $estimateSequence = ([string]$sequenceGiB + " GiB") }
    if ($null -eq $estimateSequence) { $estimateSequence = "pending preflight" }
    $minimumGiB = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("storage.minimumLaunchFreeGiB", "estimates.minimumLaunchFreeGiB") -Default $null
    $estimateStorage = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("estimates.totalStorage", "estimates.estimatedTotalStorage") -Default $null
    if ($null -eq $estimateStorage -and $null -ne $minimumGiB) { $estimateStorage = ([string]$minimumGiB + " GiB minimum free") }
    if ($null -eq $estimateStorage) { $estimateStorage = "pending preflight" }
    $freeDisk = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("output.lastKnownFreeGiB", "estimates.currentFreeDisk", "estimates.freeDisk") -Default "checked at preflight"
    if ($freeDisk -is [double] -or $freeDisk -is [int]) { $freeDisk = ([string]$freeDisk + " GiB") }
    $resumeEnabled = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("production.resumeEnabled", "production.resumeMissingFrames") -Default $false
    $verifyEnabled = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("production.verifyExistingFrames", "production.verifyEachChunk") -Default $false
    $overwriteInvalid = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("production.overwriteInvalidFrames") -Default $false
    $overwriteEnabled = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("production.overwriteValidFrames", "production.overwriteExistingFrames") -Default $false
    $atomicEnabled = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("production.atomicChunkCommit", "production.atomicPublication") -Default $false
    $stopOnFailure = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("production.stopOnValidationFailure") -Default $false
    $dashboardAuto = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.autoLaunch") -Default $false
    $dashboardRefresh = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.refreshSeconds") -Default "unknown"
    $dashboardLatest = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.showLatestFrame") -Default $false
    $dashboardInflight = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.showInflightFrames") -Default $false
    $dashboardPublished = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.showPublishedFrames") -Default $false
    $dashboardEta = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.showEta") -Default $false
    $dashboardSpeed = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.showRollingSecondsPerFrame") -Default $false
    $dashboardStorage = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.showStorageGrowth") -Default $false
    $dashboardOpen = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("dashboard.openOutputWhenComplete") -Default $false
    $authorization = if ([string]::IsNullOrWhiteSpace($AuthorizationStatus)) {
        Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("authorization.status", "validation.authorizationStatus") -Default "UNAUTHORIZED"
    }
    else { $AuthorizationStatus }
    $contentHash = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("integrity.profileSha256", "profileSha256", "sha256") -Default "calculated on save"

    $resolution = if ($width -gt 0 -and $height -gt 0) { Get-WzhkResolutionLabel -Width $width -Height $height } else { "unknown" }
    if ([string]::IsNullOrWhiteSpace([string]$aspect) -and $width -gt 0 -and $height -gt 0) {
        $aspect = Get-WzhkAspectRatioLabel -Width $width -Height $height
    }
    $sceneName = if ([string]$scenePath -eq "not selected") { $scenePath } else { Split-Path -Leaf ([string]$scenePath) }
    $totalFrames = if ($frameEnd -ge $frameStart) { $frameEnd - $frameStart + 1 } else { 0 }
    $chunkCount = if ($chunk -gt 0 -and $totalFrames -gt 0) { [int][Math]::Ceiling($totalFrames / [double]$chunk) } else { "unknown" }
    $motionBlurText = if ([bool]$motionBlur) { "enabled" } else { "disabled" }
    $compositorText = if ([bool]$compositor) { "enabled" } else { "disabled" }
    $fogGlowText = if ([bool]$fogGlow) { "enabled" } else { "disabled" }
    $masterText = if ([bool]$masterEnabled) { [string]$masterCodec } else { "disabled" }
    $deliveryText = if ([bool]$deliveryEnabled) { [string]$deliveryCodec } else { "disabled" }
    $audioText = if ([bool]$masterEnabled -or [bool]$deliveryEnabled) { [string]$audioCodec } else { "disabled" }
    $contentHashText = if ([string]$contentHash -match '^[A-Fa-f0-9]{64}$') { Get-WzhkShortHash -Hash ([string]$contentHash) } else { "CALCULATED ON SAVE" }
    $savedFileHashText = if ($SavedFileSha256 -match '^[A-Fa-f0-9]{64}$') { Get-WzhkShortHash -Hash $SavedFileSha256 } else { "CALCULATED ON SAVE" }
    $resumeText = if ([bool]$resumeEnabled) { "enabled" } else { "disabled" }
    $overwriteText = if ([bool]$overwriteEnabled) { "ENABLED" } else { "disabled" }
    $atomicText = if ([bool]$atomicEnabled) { "enabled" } else { "disabled" }
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add([string]::Format("PROFILE          : {0}", $name))
    $lines.Add([string]::Format("PROFILE ID       : {0}", $id))
    if (-not [string]::IsNullOrWhiteSpace([string]$description)) { $lines.Add([string]::Format("DESCRIPTION      : {0}", $description)) }
    $lines.Add([string]::Format("SCHEMA / STATUS  : {0}  •  {1}  •  template {2}", $schema, ([string]$status).ToUpperInvariant(), $template))
    $lines.Add([string]::Format("APPROVED SCENE   : {0}  •  preset {1}", $sceneName, $preset))
    $lines.Add([string]::Format("SCENE SHA-12     : {0}", (Get-WzhkShortHash -Hash ([string]$sceneHash))))
    $lines.Add([string]::Format("MANIFEST         : {0}", $manifestPath))
    $lines.Add([string]::Format("RESOLUTION       : {0}  •  {1}%  •  pixel {2}:{3}  •  aspect {4}  •  quality {5}", $resolution, $percentage, $pixelAspectX, $pixelAspectY, $aspect, $quality))
    $lines.Add([string]::Format("TIMELINE         : {0} fps  •  frames {1}–{2}", $fps, $frameStart, $frameEnd))
    $lines.Add([string]::Format("RENDER           : {0}  •  {1} samples", $engine, $samples))
    $lines.Add([string]::Format("SHADOWS          : pool {0}  •  rays {1}  •  scale {2}", $shadowPool, $shadowRays, $shadowScale))
    $lines.Add([string]::Format("RAY TRACING      : {0}  •  method {1}", $(if ([bool]$rayTracing) { "enabled" } else { "disabled" }), $rayMethod))
    $lines.Add([string]::Format("VOLUMETRICS      : tile {0}  •  samples {1}/{2}  •  depth {3}  •  shadows {4}", $volumetricTile, $volumetricSamples, $volumetricShadowSamples, $volumetricDepth, $(if ([bool]$volumetricShadows) { "enabled" } else { "disabled" })))
    $lines.Add([string]::Format("MOTION BLUR      : {0}", $motionBlurText))
    $lines.Add([string]::Format("RENDER EFFECTS   : transparent film {0}  •  dither {1}", $(if ([bool]$transparentFilm) { "enabled" } else { "disabled" }), $dither))
    $lines.Add([string]::Format("IMAGE SEQUENCE   : {0}  •  {1}-bit  •  {2}  •  compression {3}", $sequence, $depth, $colorMode, $compression))
    $lines.Add([string]::Format("IMAGE CONTRACT   : {0}  •  subdir {1}", $filenamePattern, $framesSubdirectory))
    $lines.Add([string]::Format("COLOR            : {0}  •  look {1}", $color, $look))
    $lines.Add([string]::Format("COLOR DETAILS    : display {0}  •  exposure {1}  •  gamma {2}  •  sequencer {3}", $displayDevice, $exposure, $gamma, $sequencerSpace))
    $lines.Add([string]::Format("COMPOSITOR       : {0}  •  Fog Glow {1}", $compositorText, $fogGlowText))
    $lines.Add([string]::Format("FOG GLOW         : {0}  •  threshold {1}  •  strength {2}  •  size {3}  •  iterations {4}", $fogQuality, $fogThreshold, $fogStrength, $fogSize, $fogIterations))
    $lines.Add([string]::Format("CHUNK PLAN       : {0} frames per chunk  •  {1} chunks", $chunk, $chunkCount))
    $lines.Add([string]::Format("OUTPUT           : {0}", $output))
    $lines.Add([string]::Format("OUTPUT POLICY    : {0}  •  pattern {1}", $outputPolicy, $directoryPattern))
    $lines.Add([string]::Format("ENCODING         : master {0}  •  delivery {1}  •  audio {2}", $masterText, $deliveryText, $audioText))
    $lines.Add([string]::Format("MASTER DETAILS   : pixel {0}  •  audio {1}", $masterPixelFormat, $masterAudioCodec))
    $lines.Add([string]::Format("DELIVERY DETAILS : profile {0}  •  CRF {1}  •  preset {2}  •  pixel {3}  •  audio {4}  •  fast-start {5}  •  Rec.709 {6}", $deliveryProfile, $deliveryCrf, $deliveryPreset, $deliveryPixelFormat, $deliveryAudioBitrate, $deliveryFastStart, $deliveryRec709))
    $lines.Add([string]::Format("EST. DURATION    : {0}", $estimateDuration))
    $lines.Add([string]::Format("EST. SEQUENCE    : {0}", $estimateSequence))
    $lines.Add([string]::Format("EST. STORAGE     : {0}  •  free {1}", $estimateStorage, $freeDisk))
    $lines.Add([string]::Format("DASHBOARD        : auto {0}  •  refresh {1}s  •  latest {2}  •  in-flight {3}  •  published {4}", $dashboardAuto, $dashboardRefresh, $dashboardLatest, $dashboardInflight, $dashboardPublished))
    $lines.Add([string]::Format("DASHBOARD METRICS: ETA {0}  •  speed {1}  •  storage {2}  •  open complete {3}", $dashboardEta, $dashboardSpeed, $dashboardStorage, $dashboardOpen))
    $lines.Add([string]::Format("SAFETY           : resume {0}  •  verify {1}  •  quarantine invalid {2}  •  overwrite valid {3}", $resumeText, $verifyEnabled, $overwriteInvalid, $overwriteText))
    $lines.Add([string]::Format("SAFETY CONTRACT  : atomic {0}  •  stop on failure {1}", $atomicText, $stopOnFailure))
    $lines.Add([string]::Format("AUTHORIZATION    : {0}", ([string]$authorization).ToUpperInvariant()))
    $lines.Add([string]::Format("CONTENT SHA-12   : {0}", $contentHashText))
    $lines.Add([string]::Format("SAVED-FILE SHA-12: {0}", $savedFileHashText))

    $warnings = @(Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("validation.warnings", "warnings") -Default @())
    if ($warnings.Count -eq 0) {
        $lines.Add("WARNINGS         : none")
    }
    else {
        foreach ($warning in $warnings) {
            $lines.Add([string]::Format("WARNING          : {0}", [string]$warning))
        }
    }
    return $lines.ToArray()
}

function Show-WzhkProfileSummary {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [string]$Title = "PROFILE BUILDER // REVIEW",
        [string[]]$SafetyLines = @(),
        [string]$AuthorizationStatus = "",
        [string]$SavedFileSha256 = ""
    )

    Write-WzhkScreenHeader -Subtitle $Title
    $status = [string](Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("validation.status", "status") -Default "DRAFT")
    Write-WzhkStatusLabel -Label "PROFILE STATUS" -Status $status
    foreach ($line in @(Get-WzhkProfileSummaryLines -Profile $Profile -AuthorizationStatus $AuthorizationStatus -SavedFileSha256 $SavedFileSha256)) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }
    if ($SafetyLines.Count -gt 0) {
        Write-WzhkFrameDivider
        foreach ($line in $SafetyLines) {
            Write-WzhkFrameLine -Text ("  " + $line) -Color Yellow
        }
    }
}

function Get-WzhkDashboardProfileLines {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [AllowNull()][object]$Stats = $null
    )

    $name = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("displayName", "name") -Default "Unnamed profile"
    $id = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("profileId", "id") -Default "unassigned"
    $width = [int](Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("resolution.width", "width") -Default 0)
    $height = [int](Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("resolution.height", "height") -Default 0)
    $fps = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("timeline.fps", "fps") -Default "unknown"
    $frameStart = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("timeline.frameStart", "frameStart") -Default "unknown"
    $frameEnd = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("timeline.frameEnd", "frameEnd") -Default "unknown"
    $imageFormat = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("imageSequence.format", "output.format", "format") -Default "unknown"
    $chunkSize = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("production.chunkSize", "resume.chunkSize", "chunkSize") -Default "unknown"
    $sceneHash = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("approvedScene.sha256", "approvedSceneSha256", "scene.sha256", "sceneSha256") -Default "unknown"
    $profileHash = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("integrity.profileSha256", "profileSha256", "sha256") -Default "unknown"
    $estimatedStorage = Get-WzhkFirstObjectValue -InputObject $Profile -Paths @("estimates.totalStorage", "estimates.estimatedTotalStorage") -Default "pending preflight"
    $resolution = if ($width -gt 0 -and $height -gt 0) { Get-WzhkResolutionLabel -Width $width -Height $height } else { "unknown" }

    $published = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("Published", "published") -Default 0
    $inflight = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("Inflight", "inflight") -Default 0
    $percent = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("Percent", "percent") -Default 0
    $latest = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("LatestFrame", "latestFrame") -Default "none"
    $chunk = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("ActiveChunk", "activeChunk") -Default "idle"
    $eta = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("Eta", "eta") -Default "calculating"
    $output = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("OutputPath", "outputPath") -Default "not selected"
    $movieTime = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("MovieTime", "movieTime") -Default "unknown"
    $renderSpeed = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("RenderSpeed", "renderSpeed", "RollingRenderSpeed", "rollingRenderSpeed") -Default "calculating"
    $storageUsed = Get-WzhkFirstObjectValue -InputObject $Stats -Paths @("StorageUsed", "storageUsed") -Default "unknown"

    return @(
        [string]::Format("PROFILE          : {0}", $name),
        [string]::Format("PROFILE ID       : {0}", $id),
        [string]::Format("SCENE / PROFILE  : SHA-12 {0} / {1}", (Get-WzhkShortHash -Hash ([string]$sceneHash)), (Get-WzhkShortHash -Hash ([string]$profileHash))),
        [string]::Format("RESOLUTION       : {0}", $resolution),
        [string]::Format("TIMELINE         : {0} fps  •  frames {1}–{2}", $fps, $frameStart, $frameEnd),
        [string]::Format("SEQUENCE         : {0}  •  chunk size {1}", $imageFormat, $chunkSize),
        [string]::Format("OUTPUT           : {0}", $output),
        [string]::Format("PROGRESS         : {0:N2}%  •  {1} published  •  {2} in-flight", [double]$percent, $published, $inflight),
        [string]::Format("ACTIVE CHUNK     : {0}", $chunk),
        [string]::Format("LATEST FRAME     : {0}", $latest),
        [string]::Format("MOVIE TIME       : {0}", $movieTime),
        [string]::Format("RENDER SPEED     : {0}", $renderSpeed),
        [string]::Format("ETA              : {0}", $eta),
        [string]::Format("STORAGE          : {0} used  •  estimated final {1}", $storageUsed, $estimatedStorage)
    )
}

function Show-WzhkProfileDashboard {
    param(
        [Parameter(Mandatory = $true)][object]$Profile,
        [AllowNull()][object]$Stats = $null,
        [string]$Title = "VISUAL PROGRESS DASHBOARD"
    )

    Write-WzhkScreenHeader -Subtitle $Title
    foreach ($line in @(Get-WzhkDashboardProfileLines -Profile $Profile -Stats $Stats)) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }
}

function Confirm-WzhkTwoStage {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [string[]]$Details = @(),
        [string]$FirstPrompt = "Lock this configuration?",
        [string]$FirstYesText = "LOCK CONFIGURATION",
        [string]$SecondPrompt = "Final confirmation: continue now?",
        [string]$SecondYesText = "CONTINUE",
        [string[]]$Warnings = @()
    )

    Write-WzhkScreenHeader -Subtitle ("1 // CONFIRM // " + $Title)
    foreach ($line in $Details) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }
    if (-not (Read-WzhkYesNo -Prompt $FirstPrompt -YesText $FirstYesText -NoText "FIX SETTINGS")) {
        return $false
    }

    Write-WzhkScreenHeader -Subtitle ("1 // FINAL CONFIRMATION // " + $Title)
    foreach ($line in $Details) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color White
    }
    if ($Warnings.Count -gt 0) {
        Write-WzhkFrameDivider
        foreach ($warning in $Warnings) {
            Write-WzhkFrameLine -Text ("  " + $warning) -Color Yellow
        }
    }

    return Read-WzhkYesNo -Prompt $SecondPrompt -YesText $SecondYesText -NoText "CANCEL"
}

function Read-WzhkYesNo {
    param(
        [string]$Prompt,
        [string]$YesText = "YES",
        [string]$NoText = "NO"
    )

    Write-WzhkFrameDivider
    Write-WzhkFrameLine -Text ("  " + $Prompt) -Color White
    Write-WzhkFrameLine -Text ("  [Y] " + $YesText + "     [N] " + $NoText) -Color Cyan
    Write-WzhkFrameBottom

    while ($true) {
        $key = [Console]::ReadKey($true)
        if ($key.Key -eq [ConsoleKey]::Y) {
            return $true
        }
        if ($key.Key -eq [ConsoleKey]::N -or $key.Key -eq [ConsoleKey]::Escape) {
            return $false
        }
    }
}

function Show-WzhkMessage {
    param(
        [string]$Title,
        [string[]]$Lines,
        [ConsoleColor]$Color = [ConsoleColor]::Yellow
    )

    Write-WzhkScreenHeader -Subtitle $Title
    foreach ($line in $Lines) {
        Write-WzhkFrameLine -Text ("  " + $line) -Color $Color
    }
    Write-WzhkFrameDivider
    Write-WzhkFrameLine -Text "  Press any key to return to Mission Control…" -Color DarkGray
    Write-WzhkFrameBottom
    $null = [Console]::ReadKey($true)
}

function Show-WzhkDoneAnimation {
    param(
        [string]$Title = "MISSION COMPLETE",
        [string[]]$Details = @()
    )

    $patterns = @(
        "✦      ░▒▓█  SIGNAL LOCKED  █▓▒░      ✦",
        "    ★  ▓▒░  WZHK MEDIA ONLINE  ░▒▓  ★",
        "◆  ◆  ◆  TRANSMISSION COMPLETE  ◆  ◆  ◆",
        "  ░░▒▒▓▓████  ALL SYSTEMS GO  ████▓▓▒▒░░"
    )
    $colors = @(
        [ConsoleColor]::Cyan,
        [ConsoleColor]::Magenta,
        [ConsoleColor]::Yellow,
        [ConsoleColor]::Green
    )

    for ($cycle = 0; $cycle -lt 10; $cycle += 1) {
        Write-WzhkScreenHeader -Subtitle ("3 // DONE // " + $Title)
        Write-WzhkFrameLine -Text ""
        Write-WzhkFrameLine -Text ("                 " + $patterns[$cycle % $patterns.Count]) -Color $colors[$cycle % $colors.Count]
        Write-WzhkFrameLine -Text ""
        Write-WzhkFrameLine -Text "       ┌─────┐   ┌─────┐   ┌─────┐      ▄▄▄▄▄      ┌─────┐   ┌─────┐" -Color Cyan
        Write-WzhkFrameLine -Text "       │ 90s │───│ NEON│───│ DATA│══════█████══════│ WZHK│───│MEDIA│" -Color Magenta
        Write-WzhkFrameLine -Text "       └─────┘   └─────┘   └─────┘      ▀▀▀▀▀      └─────┘   └─────┘" -Color Cyan
        Write-WzhkFrameLine -Text ""
        Write-WzhkFrameLine -Text ("          " + ("░▒▓█" * (($cycle % 4) + 4))) -Color $colors[($cycle + 1) % $colors.Count]
        Write-WzhkFrameLine -Text ""
        Write-WzhkFrameBottom
        Start-Sleep -Milliseconds 95
    }

    Write-WzhkScreenHeader -Subtitle ("3 // DONE // " + $Title)
    Write-WzhkFrameLine -Text ""
    Write-WzhkFrameLine -Text "                         ★  MISSION COMPLETE  ★" -Color Green
    Write-WzhkFrameLine -Text "                 ░▒▓█  WZHK MEDIA CONTROL CENTER  █▓▒░" -Color Magenta
    Write-WzhkFrameLine -Text ""
    foreach ($detail in $Details) {
        Write-WzhkFrameLine -Text ("  " + $detail) -Color White
    }
    Write-WzhkFrameDivider
    Write-WzhkFrameLine -Text "  Press any key to return to Mission Control…" -Color DarkGray
    Write-WzhkFrameBottom

    try {
        [Console]::Beep(659, 80)
        [Console]::Beep(784, 80)
        [Console]::Beep(988, 130)
    }
    catch {
        # Audio celebration is optional.
    }

    $null = [Console]::ReadKey($true)
}

Export-ModuleMember -Function `
    Initialize-WzhkConsole, `
    Write-WzhkLogo, `
    Write-WzhkFrameTop, `
    Write-WzhkFrameDivider, `
    Write-WzhkFrameBottom, `
    Write-WzhkFrameLine, `
    Write-WzhkScreenHeader, `
    Get-WzhkMenuPage, `
    Get-WzhkMenuDigitIndex, `
    Show-WzhkKeypadMenu, `
    Get-WzhkResolutionLabel, `
    Get-WzhkAspectRatioLabel, `
    Get-WzhkShortHash, `
    Get-WzhkStatusColor, `
    Write-WzhkStatusLabel, `
    Write-WzhkArtifactTypeLabel, `
    Get-WzhkMissionControlMenuItems, `
    Show-WzhkMissionControlMenu, `
    Read-WzhkTextInput, `
    Read-WzhkIntegerInput, `
    Read-WzhkDecimalInput, `
    Read-WzhkChoice, `
    Get-WzhkBuilderNavigationItems, `
    Read-WzhkBuilderNavigation, `
    ConvertTo-WzhkFieldLines, `
    Show-WzhkBuilderStage, `
    Get-WzhkTemplateMenuItems, `
    Show-WzhkTemplateMenu, `
    Get-WzhkProfileSummaryLines, `
    Show-WzhkProfileSummary, `
    Get-WzhkDashboardProfileLines, `
    Show-WzhkProfileDashboard, `
    Confirm-WzhkTwoStage, `
    Read-WzhkYesNo, `
    Show-WzhkMessage, `
    Show-WzhkDoneAnimation
